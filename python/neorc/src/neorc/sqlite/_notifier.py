# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Waking waiters when another process wrote to the SQLite file.

SQLite has no ``LISTEN``/``NOTIFY``. What it has is ``PRAGMA data_version``: a
number, read in microseconds, that changes on a connection whenever
another connection commits, in this process or another. One background task
reads it every ``poll_seconds`` and wakes every waiter when it moved. A wakeup
is a hint, so one for a commit that published nothing costs a waiter one read
of the store.

Announcements made in this process wake its waiters at once, as the memory
notifier's do; the watch is for the other processes'. A manager that is the
only process on its file needs no watch: ``MemoryTaskNotifier`` is enough.

The watch never ends by itself. A reading SQLite refuses, of a file locked
whole or one that is no database any more, closes its connection, and the
next reading opens another: the waiters are woken when the readings stop and
again when they come back, and wait out their poll timeout in between.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from types import TracebackType
from typing import Self

from neorc_core import ManagerUnavailableError, Subscription, TaskNotifier
from neorc_core._subscriptions import LocalSubscriptions

DEFAULT_POLL_SECONDS = 0.05
"""How often the file is asked whether another process wrote to it."""

_log = logging.getLogger(__name__)


class SqliteTaskNotifier(TaskNotifier):
    """Announces within this process, and hears the commits of the others."""

    def __init__(
        self, path: Path | str, *, poll_seconds: float = DEFAULT_POLL_SECONDS
    ) -> None:
        self._path = str(path)
        self._poll_seconds = poll_seconds
        self._subscriptions = LocalSubscriptions()
        self._connection: sqlite3.Connection | None = None
        self._watching: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def start(self) -> None:
        """Begin watching the file, which is created if it is not there yet.

        Fails if the file cannot be opened now, so a wrong path is said at the
        start; one that stops answering later is opened again.
        """
        if self._watching is None:
            try:
                self._connection = self._connect()
            except sqlite3.Error as exc:
                raise ManagerUnavailableError(
                    f"SQLite, on {self._path}: {exc}"
                ) from exc
            self._watching = asyncio.create_task(self._watch())

    async def aclose(self) -> None:
        watching, self._watching = self._watching, None
        if watching is not None:
            watching.cancel()
            with suppress(asyncio.CancelledError):
                await watching

    async def notify(self) -> None:
        self._subscriptions.wake_all()

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[Subscription]:
        async with self._subscriptions.subscribe() as subscription:
            yield subscription

    async def _watch(self) -> None:
        # Read on the loop's own thread: it takes microseconds, never waits
        # for a lock, and happens only while somebody waits.
        try:
            seen = self._version(None)
            while True:
                await asyncio.sleep(self._poll_seconds)
                if not len(self._subscriptions):
                    continue
                version = self._version(seen)
                if version != seen:
                    seen = version
                    self._subscriptions.wake_all()
        finally:
            self._disconnect()

    def _version(self, seen: int | None) -> int | None:
        """The file's version as the connection sees it; ``None`` if it cannot say.

        ``None`` differs from every version, and a new connection's version
        from ``None``: so the waiters are woken when a reading fails, and
        again at the first one that succeeds, whatever was committed between.
        ``seen`` is the reading before, so the first failure of several is the
        one logged.
        """
        try:
            if self._connection is None:
                self._connection = self._connect()
            row = self._connection.execute("PRAGMA data_version").fetchone()
        except sqlite3.Error as exc:
            if seen is not None:
                _log.warning(
                    "cannot watch %s (%s); trying again every %ss",
                    self._path,
                    exc,
                    self._poll_seconds,
                )
            self._disconnect()
            return None
        version: int = row[0]
        return version

    def _connect(self) -> sqlite3.Connection:
        # No wait for a lock: the watch runs on the loop's own thread, and a
        # file it finds locked is one somebody is writing to.
        return sqlite3.connect(self._path, timeout=0, isolation_level=None)

    def _disconnect(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()
