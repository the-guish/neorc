# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Waking waiters on a SQLite file.

What matters here: an announcement in this process wakes at once, a commit
through another connection wakes within a poll rather than at the waiter's
timeout, and nothing committed in the gap between a receive and a wait is
slept through.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from neorc.sqlite import SqliteStore, SqliteTaskNotifier
from neorc_core import ManagerUnavailableError, StoredFlow
from neorc_core._values import JsonValue
from neorc_core.flows import parse_flow

CONTENT: JsonValue = {
    "name": "f",
    "version": "1.0.0",
    "steps": {"work": {"handler": "tasks:work"}},
}

POLL_SECONDS = 0.02


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "neorc.db"


@pytest.fixture
async def store(path: Path) -> AsyncIterator[SqliteStore]:
    async with SqliteStore(path) as store:
        yield store


@pytest.fixture
async def notifier(path: Path, store: SqliteStore) -> AsyncIterator[SqliteTaskNotifier]:
    async with SqliteTaskNotifier(path, poll_seconds=POLL_SECONDS) as notifier:
        yield notifier


async def _commit(store: SqliteStore) -> None:
    """A write through another connection: what another process's would be."""
    await store.store_flows([StoredFlow(parse_flow(CONTENT), CONTENT)])
    version = parse_flow(CONTENT).version
    await store.start_run("f", version, {})


async def test_a_waiter_is_woken_by_an_announcement(
    notifier: SqliteTaskNotifier,
) -> None:
    async with notifier.subscribe() as subscription:

        async def announce() -> None:
            await asyncio.sleep(0.05)
            await notifier.notify()

        async with asyncio.TaskGroup() as group:
            group.create_task(announce())
            woken = await subscription.wait(timeout=5)

    assert woken is True


async def test_a_waiter_gives_up_at_its_timeout(notifier: SqliteTaskNotifier) -> None:
    async with notifier.subscribe() as subscription:
        assert await subscription.wait(timeout=0.1) is False


async def test_a_commit_through_another_connection_wakes_a_waiter(
    notifier: SqliteTaskNotifier, store: SqliteStore
) -> None:
    loop = asyncio.get_running_loop()
    async with notifier.subscribe() as subscription:

        async def commit() -> None:
            await asyncio.sleep(0.05)
            await _commit(store)

        begun = loop.time()
        async with asyncio.TaskGroup() as group:
            group.create_task(commit())
            woken = await subscription.wait(timeout=5)

    assert woken is True
    # Woken by the watch, not by falling out of the timeout.
    assert loop.time() - begun < 3


async def test_a_commit_between_waits_is_not_lost(
    notifier: SqliteTaskNotifier, store: SqliteStore
) -> None:
    """The lost-wakeup case: it lands while the caller is off receiving."""
    async with notifier.subscribe() as subscription:
        await _commit(store)
        await asyncio.sleep(POLL_SECONDS * 5)  # seen with nobody waiting

        assert await subscription.wait(timeout=0.5) is True
        # Consumed: nothing new, so the next wait runs to its timeout.
        assert await subscription.wait(timeout=POLL_SECONDS * 5) is False


async def test_a_locked_file_wakes_the_waiters_and_the_watch_goes_on(
    tmp_path: Path,
) -> None:
    """A file locked whole cannot say its version: a hint, and no crash.

    On a file of its own, with a rollback journal: the stores' write-ahead
    log never locks readers out, but a backup tool or another program may.
    """
    path = tmp_path / "locked.db"
    locking = sqlite3.connect(path, isolation_level=None)
    try:
        locking.execute("CREATE TABLE t (x)")
        async with (
            SqliteTaskNotifier(path, poll_seconds=POLL_SECONDS) as notifier,
            notifier.subscribe() as subscription,
        ):
            await asyncio.sleep(POLL_SECONDS * 2)  # the watch has its first reading
            locking.execute("BEGIN EXCLUSIVE")
            assert await subscription.wait(timeout=5) is True
            locking.execute("INSERT INTO t VALUES (1)")
            locking.execute("COMMIT")
            assert await subscription.wait(timeout=5) is True

            assert await subscription.wait(timeout=POLL_SECONDS * 5) is False
            locking.execute("INSERT INTO t VALUES (2)")
            assert await subscription.wait(timeout=5) is True
    finally:
        locking.close()


async def test_the_watch_comes_back_once_the_file_is_a_database_again(
    path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A reading SQLite refuses for good ends no watch: the next opens anew."""
    async with SqliteStore(path):
        pass
    notifier = SqliteTaskNotifier(path, poll_seconds=POLL_SECONDS)
    await notifier.start()
    async with notifier.subscribe() as subscription:
        await asyncio.sleep(POLL_SECONDS * 2)  # the watch has its first reading
        path.write_bytes(b"not a database at all. " * 200)  # written over, in place
        for beside in ("-wal", "-shm"):
            Path(f"{path}{beside}").unlink(missing_ok=True)
        assert await subscription.wait(timeout=5) is True
        await asyncio.sleep(POLL_SECONDS * 10)  # many readings, all refused

        path.unlink()
        async with SqliteStore(path) as store:
            assert await subscription.wait(timeout=5) is True
            await subscription.wait(timeout=POLL_SECONDS * 5)  # the store's opening
            await _commit(store)
            assert await subscription.wait(timeout=5) is True

    await notifier.aclose()  # nothing to raise: the watch never ended
    said = [record for record in caplog.records if "cannot watch" in record.message]
    assert len(said) == 1  # the first refusal of the many, not each


async def test_a_file_that_cannot_be_opened_is_said_at_the_start(
    tmp_path: Path,
) -> None:
    notifier = SqliteTaskNotifier(tmp_path / "no-such-directory" / "neorc.db")

    with pytest.raises(ManagerUnavailableError, match="unable to open"):
        await notifier.start()

    await notifier.aclose()


async def test_starting_and_closing_are_safe_to_repeat(path: Path) -> None:
    notifier = SqliteTaskNotifier(path, poll_seconds=POLL_SECONDS)
    await notifier.start()
    await notifier.start()
    await notifier.aclose()
    await notifier.aclose()

    # Without its watch it still announces within the process.
    async with notifier.subscribe() as subscription:
        await notifier.notify()
        assert await subscription.wait(timeout=0.5) is True
