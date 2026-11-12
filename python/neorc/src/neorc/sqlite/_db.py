# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""One SQLite connection, used from one thread, behind an async face.

SQLite lets one connection write at a time, across threads and processes alike.
So every operation of a store is one transaction on one connection, run on one
thread of its own: ``BEGIN IMMEDIATE`` for an operation that writes, which
takes the database's write lock before it reads anything, so what it read is
still true when it writes. That is the memory store's one lock, held by the
file, and it holds between processes too.

The journal is a write-ahead log: readers in other processes never wait for a
writer, and a writer waits for another up to ``busy_seconds``. A file that
cannot keep one is refused at ``open``.

What SQLite refuses, a file still locked after that wait first of all, is
raised as ``ManagerUnavailableError``, as a manager that did not answer is:
a worker and a scheduler try again, and a scheduler fails no run for it.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import TracebackType
from typing import Self, TypeVar

from neorc_core import ManagerUnavailableError, NeorcError

_T = TypeVar("_T")

DEFAULT_BUSY_SECONDS = 30.0
"""How long an operation waits for another process's write to finish."""

MIN_SQLITE = (3, 35, 0)
"""``RETURNING``, which the stores' statements use."""

_RETRY_SECONDS = 0.01
"""The pause between two tries at what SQLite does not wait for by itself."""


class Database:
    """A SQLite file, opened once, whose operations run one at a time."""

    def __init__(
        self, path: Path | str, *, busy_seconds: float = DEFAULT_BUSY_SECONDS
    ) -> None:
        self._path = str(path)
        self._busy_seconds = busy_seconds
        self._thread: ThreadPoolExecutor | None = None
        self._connection: sqlite3.Connection | None = None

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def open(self) -> None:
        """Open the file, creating it if it is not there."""
        if self._thread is not None:
            return
        if sqlite3.sqlite_version_info < MIN_SQLITE:
            raise NeorcError(
                f"SQLite {sqlite3.sqlite_version} is too old: neorc needs "
                f"{'.'.join(map(str, MIN_SQLITE))} or later"
            )
        self._thread = ThreadPoolExecutor(1, thread_name_prefix="neorc-sqlite")
        try:
            self._connection = await self._on_thread(self._connect)
        except BaseException:
            self._thread.shutdown(wait=False)
            self._thread = None
            raise

    async def aclose(self) -> None:
        """Close the file, after the operation in hand."""
        thread, self._thread = self._thread, None
        connection, self._connection = self._connection, None
        if thread is None:
            return
        if connection is not None:
            await asyncio.get_running_loop().run_in_executor(thread, connection.close)
        thread.shutdown(wait=False)

    async def read(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        """Run ``operation`` on one snapshot of the database."""
        return await self._run(operation, "BEGIN")

    async def write(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        """Run ``operation`` holding the write lock; commit, or undo if it raises."""
        return await self._run(operation, "BEGIN IMMEDIATE")

    async def script(self, statements: str) -> None:
        """Run ``statements``, which begin and commit their own transaction."""
        connection = self._connected()

        def run() -> None:
            try:
                connection.executescript(statements)
            except BaseException:
                _undo(connection)
                raise

        await self._on_thread(run)

    async def _run(
        self, operation: Callable[[sqlite3.Connection], _T], begin: str
    ) -> _T:
        connection = self._connected()

        def transaction() -> _T:
            connection.execute(begin)
            try:
                result = operation(connection)
                connection.execute("COMMIT")
            except BaseException:
                _undo(connection)
                raise
            return result

        return await self._on_thread(transaction)

    def _connected(self) -> sqlite3.Connection:
        if self._connection is None:
            raise NeorcError("the store is not open")
        return self._connection

    async def _on_thread(self, call: Callable[[], _T]) -> _T:
        if self._thread is None:
            raise NeorcError("the store is not open")
        try:
            return await asyncio.get_running_loop().run_in_executor(self._thread, call)
        except sqlite3.Error as exc:
            raise ManagerUnavailableError(f"SQLite, on {self._path}: {exc}") from exc

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._path,
            timeout=self._busy_seconds,
            isolation_level=None,  # transactions are begun and ended by hand
            check_same_thread=False,  # one thread, but not the one that opened it
        )
        try:
            connection.row_factory = sqlite3.Row
            mode = self._write_ahead(connection)
            if mode != "wal":
                raise NeorcError(
                    f"{self._path} cannot keep a write-ahead log, which the store "
                    f"needs: its journal stays {mode!r}. It must be a file on a "
                    "local disk"
                )
            connection.execute("PRAGMA synchronous = NORMAL")
        except BaseException:
            connection.close()
            raise
        return connection

    def _write_ahead(self, connection: sqlite3.Connection) -> str:
        """Ask for a write-ahead log, and return the journal the file has then.

        SQLite answers with the mode in use, not an error, when it could not
        change it. And for this one statement it does not wait on a lock: of
        several processes opening a new file at once, all but one are told the
        file is locked, at once. So this waits for them, up to ``busy_seconds``.
        """
        deadline = time.monotonic() + self._busy_seconds
        while True:
            try:
                (mode,) = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            except sqlite3.OperationalError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(_RETRY_SECONDS)
            else:
                return str(mode)


def _undo(connection: sqlite3.Connection) -> None:
    """Roll back what a failed transaction left open, a failed ``COMMIT`` too.

    Left open, it would have every later transaction refused: one cannot
    begin inside another.
    """
    if connection.in_transaction:
        connection.execute("ROLLBACK")
