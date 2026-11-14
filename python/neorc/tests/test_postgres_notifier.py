# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""LISTEN/NOTIFY, against a real server.

What matters here: a waiter costs no connection, an announcement from another
manager process still wakes it, and nothing published in the gap between a
receive and a wait is slept through.
"""

from __future__ import annotations

import asyncio

import pytest

from neorc.postgres import PostgresStore, PostgresTaskNotifier
from neorc.postgres._driver import connected
from neorc_core import Manager, QueueKind
from neorc_core._values import JsonValue
from neorc_core.flows import Address

pytestmark = pytest.mark.postgres

FLOW: JsonValue = {
    "name": "f",
    "version": "1.0.0",
    "steps": {
        "one": {"handler": "tasks:one"},
        "two": {"handler": "tasks:two"},
        "three": {"handler": "tasks:three"},
    },
}


async def test_a_waiter_is_woken_by_an_announcement(
    pg_notifier: PostgresTaskNotifier,
) -> None:
    async with pg_notifier.subscribe() as subscription:

        async def announce() -> None:
            await asyncio.sleep(0.05)
            await pg_notifier.notify()

        async with asyncio.TaskGroup() as group:
            group.create_task(announce())
            woken = await subscription.wait(timeout=5)

    assert woken is True


async def test_a_waiter_gives_up_at_its_timeout(
    pg_notifier: PostgresTaskNotifier,
) -> None:
    async with pg_notifier.subscribe() as subscription:
        assert await subscription.wait(timeout=0.1) is False


async def test_an_announcement_between_waits_is_not_lost(
    pg_notifier: PostgresTaskNotifier,
) -> None:
    """The lost-wakeup case: it arrives while the caller is off receiving."""
    async with pg_notifier.subscribe() as subscription:
        await pg_notifier.notify()
        await asyncio.sleep(0.1)  # the announcement lands with nobody waiting

        assert await subscription.wait(timeout=0.5) is True


async def test_a_task_published_on_another_process_wakes_this_one(
    database_url: str, pg_notifier: PostgresTaskNotifier
) -> None:
    """Two manager processes share one channel, so one wakes the other's workers."""
    async with (
        PostgresTaskNotifier(database_url) as elsewhere,
        pg_notifier.subscribe() as subscription,
    ):

        async def announce() -> None:
            await asyncio.sleep(0.05)
            await elsewhere.notify()

        async with asyncio.TaskGroup() as group:
            group.create_task(announce())
            woken = await subscription.wait(timeout=5)

    assert woken is True


async def test_many_waiters_cost_no_connections(
    pg_schema: str, pg_notifier: PostgresTaskNotifier
) -> None:
    """Fifty idle workers on a pool of two, which is the whole point."""
    async with PostgresStore(pg_schema, min_size=1, max_size=2) as small_pool:
        manager = Manager(small_pool, tasks=pg_notifier, events=pg_notifier)
        await manager.declare_queue("default", QueueKind.WORKER)
        await manager.upload_flows([FLOW])
        run = await manager.start_run("f", {})
        waiting = [
            asyncio.create_task(manager.receive_task("default", timeout=2))
            for _ in range(50)
        ]
        await asyncio.sleep(0.2)  # let them all reach the wait

        for name in ("one", "two", "three"):
            await manager.publish_task(run.id, Address(name))

        done, pending = await asyncio.wait(waiting, timeout=10)
        received = [task.result() for task in done if task.result() is not None]
        for task in pending:
            task.cancel()

    assert len(received) == 3


async def test_a_waiting_worker_is_woken_by_a_publish(
    pg_store: PostgresStore, pg_notifier: PostgresTaskNotifier
) -> None:
    manager = Manager(pg_store, tasks=pg_notifier, events=pg_notifier)
    await manager.declare_queue("default", QueueKind.WORKER)
    await manager.upload_flows([FLOW])
    run = await manager.start_run("f", {})

    async def publish_shortly() -> None:
        await asyncio.sleep(0.05)
        await manager.publish_task(run.id, Address("one"))

    loop = asyncio.get_running_loop()
    started = loop.time()
    async with asyncio.TaskGroup() as group:
        group.create_task(publish_shortly())
        received = await manager.receive_task("default", timeout=10)

    assert received is not None
    # Woken by the announcement, not by falling out of the poll timeout.
    assert loop.time() - started < 2


async def _cut(database_url: str, query_like: str) -> None:
    """End every server backend running a query like ``query_like``: a dropped link."""
    async with connected(database_url) as conn:
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
            " WHERE pid <> pg_backend_pid() AND query LIKE $1",
            query_like,
        )


async def test_an_announcement_survives_its_connection_breaking(
    database_url: str, pg_notifier: PostgresTaskNotifier
) -> None:
    """A hint must not turn a committed write into an error: reconnect and send."""
    async with (
        PostgresTaskNotifier(database_url) as elsewhere,
        pg_notifier.subscribe() as subscription,
    ):
        await elsewhere.notify()  # so the sending connection has run a query
        assert await subscription.wait(timeout=5) is True
        await _cut(database_url, "SELECT pg_notify%")
        await asyncio.sleep(0.1)

        await asyncio.gather(*(elsewhere.notify() for _ in range(20)))

        assert await subscription.wait(timeout=5) is True
        # One sending connection, reopened; none left behind.
        async with connected(database_url) as conn:
            count = await conn.fetchval(
                "SELECT count(*) FROM pg_stat_activity WHERE query LIKE $1",
                "SELECT pg_notify%",
            )
        assert count <= 2  # this notifier's, and the fixture's


async def test_a_mark_to_announce_never_waits_on_the_link(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The write has committed; a stuck link costs waiters latency, not callers."""
    stuck = asyncio.Event()

    class Stuck:
        closed = False

        def is_closed(self) -> bool:
            return self.closed

        async def execute(self, *args: object, **kwargs: object) -> None:
            stuck.set()
            await asyncio.sleep(3600)

        async def close(self, *, timeout: float | None = None) -> None:
            self.closed = True

        def terminate(self) -> None:
            self.closed = True

    async def connect_stuck() -> Stuck:
        return Stuck()

    notifier = PostgresTaskNotifier(database_url)
    async with notifier, notifier.subscribe() as subscription:
        await notifier.notify()  # so the sending connection has run a query
        assert await subscription.wait(timeout=5) is True
        monkeypatch.setattr(notifier, "_connect", connect_stuck)
        await _cut(database_url, "SELECT pg_notify%")
        await asyncio.sleep(0.1)

        loop = asyncio.get_running_loop()
        begun = loop.time()
        await notifier.notify()
        await notifier.notify()
        elapsed = loop.time() - begun

        assert elapsed < 0.5
        await asyncio.wait_for(stuck.wait(), timeout=5)
    # aclose returned: a stuck announcement does not hold the manager's shutdown.


async def test_a_listener_that_breaks_is_reopened_and_wakes_its_waiters(
    database_url: str,
    pg_notifier: PostgresTaskNotifier,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from neorc.postgres import _notifier

    monkeypatch.setattr(_notifier, "RECONNECT_SECONDS", 0.05)
    async with (
        PostgresTaskNotifier(database_url) as elsewhere,
        pg_notifier.subscribe() as subscription,
    ):
        await _cut(database_url, "LISTEN %")  # every listener, server-side
        # Reopening wakes waiters once, for what was missed meanwhile.
        assert await subscription.wait(timeout=5) is True

        async def announce() -> None:
            await asyncio.sleep(0.2)
            await elsewhere.notify()

        async with asyncio.TaskGroup() as group:
            group.create_task(announce())
            woken = await subscription.wait(timeout=5)

    assert woken is True
