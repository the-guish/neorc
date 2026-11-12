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
writer, and a writer waits for another up to ``busy_seconds``.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import TracebackType
from typing import Self, TypeVar

from neorc_core import NeorcError

_T = TypeVar("_T")

DEFAULT_BUSY_SECONDS = 30.0
"""How long an operation waits for another process's write to finish."""

MIN_SQLITE = (3, 35, 0)
"""``RETURNING``, which the stores' statements use."""


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
        await self._on_thread(lambda: connection.executescript(statements))

    async def _run(
        self, operation: Callable[[sqlite3.Connection], _T], begin: str
    ) -> _T:
        connection = self._connected()

        def transaction() -> _T:
            connection.execute(begin)
            try:
                result = operation(connection)
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
            return result

        return await self._on_thread(transaction)

    def _connected(self) -> sqlite3.Connection:
        if self._connection is None:
            raise NeorcError("the store is not open")
        return self._connection

    async def _on_thread(self, call: Callable[[], _T]) -> _T:
        if self._thread is None:
            raise NeorcError("the store is not open")
        return await asyncio.get_running_loop().run_in_executor(self._thread, call)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._path,
            timeout=self._busy_seconds,
            isolation_level=None,  # transactions are begun and ended by hand
            check_same_thread=False,  # one thread, but not the one that opened it
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection
