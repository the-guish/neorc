# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""A manager, a scheduler and workers on a SQLite file, with nothing else.

The example scenarios of ``neorc_core.testing.examples`` run here as they run
in memory, in the two shapes an application embeds neorc in: everything on one
manager, and the workers on a manager of their own, as a second process on the
same file would have it. And what a file is for: the processes stop, start
again, and a run goes on from where it was.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import pytest
from conftest import EXAMPLES

from neorc.sqlite import SqliteStore, SqliteTaskNotifier
from neorc_core import Manager, RunStatus, Scheduler, TaskNotifier, TaskStatus, Worker
from neorc_core.flows import Address, read_flows
from neorc_core.local import (
    DirectManagerClient,
    DirectQueueClient,
    MemoryTaskNotifier,
)
from neorc_core.testing import examples

pytestmark = pytest.mark.usefixtures("own_tasks_module")

WATCH_SECONDS = 0.02

POLL_SECONDS = 10.0
"""Long enough that a run moved by poll timeouts alone would not finish in it."""


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "neorc.db"


@asynccontextmanager
async def process(
    path: Path, *, alone: bool, busy_seconds: float = 30.0
) -> AsyncIterator[Manager]:
    """What one process holds: a manager on a store and notifiers of its own.

    ``alone`` on the file, it wakes its waiters in memory; otherwise it
    watches the file for the other processes' commits.
    """
    async with AsyncExitStack() as stack:
        store = await stack.enter_async_context(
            SqliteStore(path, busy_seconds=busy_seconds)
        )
        tasks: TaskNotifier
        events: TaskNotifier
        if alone:
            tasks, events = MemoryTaskNotifier(), MemoryTaskNotifier()
        else:
            tasks = await stack.enter_async_context(
                SqliteTaskNotifier(path, poll_seconds=WATCH_SECONDS)
            )
            events = await stack.enter_async_context(
                SqliteTaskNotifier(path, poll_seconds=WATCH_SECONDS)
            )
        yield Manager(store, tasks=tasks, events=events)


@asynccontextmanager
async def running(
    *,
    scheduler_on: Manager,
    workers_on: Manager,
    code_location: Path,
    poll_seconds: float = POLL_SECONDS,
    lease_seconds: float = 60.0,
) -> AsyncIterator[None]:
    """A scheduler, and a worker for every queue of the flows already uploaded."""
    queues = sorted(
        {
            task.queue
            for flow in await workers_on.latest_flows()
            for task in flow.definition.tasks()
        }
    )
    scheduler = Scheduler(DirectManagerClient(scheduler_on), poll_timeout=poll_seconds)
    workers = [
        Worker(
            DirectQueueClient(workers_on),
            queue=queue,
            code_location=code_location,
            poll_timeout=poll_seconds,
            lease_seconds=lease_seconds,
        )
        for queue in queues
    ]
    for worker in workers:
        await worker.prepare()
    loops = [asyncio.create_task(scheduler.run())]
    loops += [asyncio.create_task(worker.run()) for worker in workers]
    try:
        yield
    finally:
        scheduler.stop()
        for worker in workers:
            worker.stop()
        await asyncio.gather(*loops)


@pytest.mark.parametrize("flow", ["a", "b"])
async def test_hello_runs_on_one_manager(
    path: Path, flow: str, capfd: pytest.CaptureFixture[str]
) -> None:
    async with process(path, alone=True) as manager:
        client = DirectManagerClient(manager)
        await client.upload_flows(read_flows(EXAMPLES / "hello" / "flows"))
        async with running(
            scheduler_on=manager, workers_on=manager, code_location=EXAMPLES / "hello"
        ):
            await examples.hello(client, EXAMPLES, flow)

    assert capfd.readouterr().out == f"{flow}\n"


async def test_word_picker_rounds_runs_on_one_manager(path: Path) -> None:
    async with process(path, alone=True) as manager:
        client = DirectManagerClient(manager)
        await client.upload_flows(read_flows(EXAMPLES / "wordplay" / "flows"))
        async with running(
            scheduler_on=manager,
            workers_on=manager,
            code_location=EXAMPLES / "wordplay",
        ):
            await examples.word_picker_rounds(client, EXAMPLES)


async def test_word_picker_rounds_runs_with_the_workers_on_a_manager_of_their_own(
    path: Path,
) -> None:
    """Loops, fan-outs and a sub-flow, every step a wakeup across the file.

    The scheduler's manager publishes and the workers' receives; the workers'
    records what finished and the scheduler's hears of it. Neither is told by
    the other: each watches the file.
    """
    loop = asyncio.get_running_loop()
    async with (
        process(path, alone=False) as application,
        process(path, alone=False) as worker_process,
    ):
        client = DirectManagerClient(application)
        await client.upload_flows(read_flows(EXAMPLES / "wordplay" / "flows"))
        async with running(
            scheduler_on=application,
            workers_on=worker_process,
            code_location=EXAMPLES / "wordplay",
        ):
            begun = loop.time()
            await examples.word_picker_rounds(client, EXAMPLES)

    # Woken by the watch at every step, not by falling out of a poll.
    assert loop.time() - begun < POLL_SECONDS


async def test_a_task_published_through_one_manager_wakes_a_worker_on_another(
    path: Path,
) -> None:
    async with (
        process(path, alone=False) as one,
        process(path, alone=False) as other,
    ):
        client = DirectManagerClient(one)
        await client.upload_flows(read_flows(EXAMPLES / "hello" / "flows"))
        run = await client.start_run("a", {})

        async def publish_shortly() -> None:
            await asyncio.sleep(0.2)
            await client.publish_task(run.id, Address("a"))

        loop = asyncio.get_running_loop()
        begun = loop.time()
        async with asyncio.TaskGroup() as group:
            group.create_task(publish_shortly())
            delivery = await DirectQueueClient(other).receive_task("default", timeout=5)

    assert delivery is not None and delivery.task.run_id == run.id
    assert loop.time() - begun < 3


async def test_an_event_recorded_through_one_manager_wakes_a_scheduler_on_another(
    path: Path,
) -> None:
    async with (
        process(path, alone=False) as one,
        process(path, alone=False) as other,
    ):
        client = DirectManagerClient(one)
        await client.upload_flows(read_flows(EXAMPLES / "hello" / "flows"))

        async def start_shortly() -> None:
            await asyncio.sleep(0.2)
            await client.start_run("a", {})

        loop = asyncio.get_running_loop()
        begun = loop.time()
        async with asyncio.TaskGroup() as group:
            group.create_task(start_shortly())
            events = await DirectManagerClient(other).wait_for_events(0, timeout=5)

    assert [event.kind.value for event in events] == ["run_started"]
    assert loop.time() - begun < 3


async def test_a_run_goes_on_after_everything_stopped_and_started_again(
    path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """An application closed with a task in a worker's hands, and opened again.

    The task's lease lapses, as any lost worker's does, and the next worker
    runs it: the handler is called once here, on the second attempt.
    """
    async with process(path, alone=True) as manager:
        client = DirectManagerClient(manager)
        await client.upload_flows(read_flows(EXAMPLES / "hello" / "flows"))
        run = await client.start_run("a", {})
        scheduler = Scheduler(client, poll_timeout=0.2)
        await scheduler.run_once()  # publishes the run's one task
        delivery = await manager.receive_task("default", timeout=5, lease_seconds=0.2)
        assert delivery is not None
        await manager.claim_task(delivery.task.id)

    async with process(path, alone=True) as manager:
        client = DirectManagerClient(manager)
        assert (await client.get_run(run.id)).status is RunStatus.ACTIVE
        async with running(
            scheduler_on=manager,
            workers_on=manager,
            code_location=EXAMPLES / "hello",
            poll_seconds=0.1,
        ):
            finished = await examples.wait_for_run(client, run.id)
        (task,) = await manager.run_tasks(run.id)

    assert finished.status is RunStatus.SUCCEEDED
    assert task.status is TaskStatus.SUCCEEDED
    assert task.attempts == 2
    assert capfd.readouterr().out == "a\n"


async def test_a_worker_and_a_scheduler_outlast_a_locked_file(path: Path) -> None:
    """Another process writing for longer than they wait: they ask again.

    The scheduler's request to publish the task is not one the manager
    rejected, so it fails no run; and neither loop ends, or the run would
    never finish once the file is free.
    """
    async with process(path, alone=True, busy_seconds=0.05) as manager:
        client = DirectManagerClient(manager)
        await client.upload_flows(read_flows(EXAMPLES / "hello" / "flows"))
        writing = sqlite3.connect(path, isolation_level=None)
        try:
            async with running(
                scheduler_on=manager,
                workers_on=manager,
                code_location=EXAMPLES / "hello",
                poll_seconds=0.1,
            ):
                run = await client.start_run("a", {})
                writing.execute("BEGIN IMMEDIATE")  # before either loop goes on
                await asyncio.sleep(0.5)
                assert (await client.get_run(run.id)).status is RunStatus.ACTIVE
                assert await manager.run_tasks(run.id) == []

                writing.execute("ROLLBACK")
                finished = await examples.wait_for_run(client, run.id)
        finally:
            writing.close()

    assert finished.status is RunStatus.SUCCEEDED
