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
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from types import TracebackType
from typing import Self

from neorc_core import Subscription, TaskNotifier
from neorc_core._subscriptions import LocalSubscriptions

DEFAULT_POLL_SECONDS = 0.05
"""How often the file is asked whether another process wrote to it."""


class SqliteTaskNotifier(TaskNotifier):
    """Announces within this process, and hears the commits of the others."""

    def __init__(
        self, path: Path | str, *, poll_seconds: float = DEFAULT_POLL_SECONDS
    ) -> None:
        self._path = str(path)
        self._poll_seconds = poll_seconds
        self._subscriptions = LocalSubscriptions()
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
        """Begin watching the file, which is created if it is not there yet."""
        if self._watching is None:
            # No wait for a lock: the watch runs on the loop's own thread, and
            # a file it finds locked is one somebody is writing to.
            connection = sqlite3.connect(self._path, timeout=0, isolation_level=None)
            self._watching = asyncio.create_task(self._watch(connection))

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

    async def _watch(self, connection: sqlite3.Connection) -> None:
        # Read on the loop's own thread: it takes microseconds, never waits
        # for a lock, and happens only while somebody waits.
        try:
            seen = _data_version(connection)
            while True:
                await asyncio.sleep(self._poll_seconds)
                if not len(self._subscriptions):
                    continue
                version = _data_version(connection)
                if version != seen:
                    seen = version
                    self._subscriptions.wake_all()
        finally:
            connection.close()


def _data_version(connection: sqlite3.Connection) -> int | None:
    """The file's version as this connection sees it; ``None`` if it cannot say.

    It cannot while another process holds the file locked, which is a write
    under way: ``None`` differs from every version, so the waiters are woken
    then, and again once the version can be read.
    """
    try:
        version: int = connection.execute("PRAGMA data_version").fetchone()[0]
    except sqlite3.OperationalError:
        return None
    return version
