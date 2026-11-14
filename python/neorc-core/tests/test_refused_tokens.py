# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""A token the manager refuses ends the scheduler and the worker, and fails nothing.

Refused as unknown, or as a role that may not make the request: both alike.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime
from pathlib import Path
from textwrap import dedent

import pytest

from neorc_core import (
    AccessError,
    AuthenticationError,
    Manager,
    PermissionDeniedError,
    QueueKind,
    RunStatus,
    Scheduler,
    TaskStatus,
    Worker,
)
from neorc_core._runs import Task, TaskDelivery
from neorc_core._task import TaskId
from neorc_core._values import JsonValue
from neorc_core.flows import Address
from neorc_core.local import (
    DirectManagerClient,
    DirectQueueClient,
    MemoryStore,
    MemoryTaskNotifier,
)
from neorc_core.ports._clients import DEFAULT_LEASE_SECONDS

REFUSALS = [
    AuthenticationError("the API token is unknown, revoked or expired"),
    PermissionDeniedError("a ci token may not runs:schedule"),
]


@pytest.fixture(params=REFUSALS, ids=["unknown", "no-permission"])
def refusal(request: pytest.FixtureRequest) -> AccessError:
    error: AccessError = request.param
    return error


@pytest.fixture
def flows() -> Manager:
    return Manager(
        MemoryStore(queues=dict.fromkeys(("default",), QueueKind.WORKER)),
        tasks=MemoryTaskNotifier(),
        events=MemoryTaskNotifier(),
    )


async def task_at(flows: Manager, run_id: uuid.UUID, address: Address) -> Task:
    """The task published at ``address`` in a run: ids say nothing about it."""
    (task,) = [t for t in await flows.run_tasks(run_id) if t.address == address]
    return task


class RefusingManagerClient(DirectManagerClient):
    """Refuses the scheduler's requests to publish, as a revoked token would be."""

    def __init__(self, manager: Manager, refusal: AccessError) -> None:
        super().__init__(manager)
        self.refusal = refusal

    async def publish_task(self, run_id: uuid.UUID, address: Address) -> None:
        raise self.refusal


class RefusingPoll(DirectQueueClient):
    def __init__(self, manager: Manager, refusal: AccessError) -> None:
        super().__init__(manager)
        self.refusal = refusal

    async def receive_task(
        self,
        queue: str,
        *,
        timeout: float,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> TaskDelivery | None:
        raise self.refusal


class RefusingHeartbeat(DirectQueueClient):
    """Hands out tasks, and refuses the token once a handler is under way."""

    def __init__(self, manager: Manager, refusal: AccessError) -> None:
        super().__init__(manager)
        self.refusal = refusal
        self.reports: list[TaskId] = []

    async def extend_lease(
        self, task_id: TaskId, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> datetime:
        raise self.refusal

    async def report_finished(
        self, task_id: TaskId, *, result: JsonValue = None, error: str | None = None
    ) -> None:
        self.reports.append(task_id)


async def started(flows: Manager, tmp_path: Path, code: str) -> uuid.UUID:
    module = f"handlers_{uuid.uuid4().hex}"
    (tmp_path / f"{module}.py").write_text(dedent(code))
    await flows.upload_flows(
        [
            {
                "name": "f",
                "version": "1.0.0",
                "steps": {"work": {"handler": f"{module}:work"}},
            }
        ]
    )
    run = await flows.start_run("f", {})
    return run.id


async def test_a_refused_scheduler_fails_no_run_and_stops(
    flows: Manager, refusal: AccessError
) -> None:
    await flows.upload_flows(
        [{"name": "f", "version": "1.0.0", "steps": {"work": {"handler": "m:work"}}}]
    )
    run = await flows.start_run("f", {})
    scheduler = Scheduler(RefusingManagerClient(flows, refusal), poll_timeout=0.1)

    with pytest.raises(type(refusal)):
        await asyncio.wait_for(scheduler.run(), timeout=5)

    assert (await flows.get_run(run.id)).status is RunStatus.ACTIVE


async def test_a_refused_poll_stops_the_worker(
    flows: Manager, tmp_path: Path, refusal: AccessError
) -> None:
    worker = Worker(
        RefusingPoll(flows, refusal), code_location=tmp_path, poll_timeout=0.1
    )

    with pytest.raises(type(refusal)):
        await asyncio.wait_for(worker.run(), timeout=5)


async def test_a_refused_heartbeat_cancels_an_async_handler_before_its_lease_lapses(
    flows: Manager, tmp_path: Path, refusal: AccessError
) -> None:
    run_id = await started(
        flows,
        tmp_path,
        """
        import asyncio

        async def work():
            await asyncio.sleep(30)
            return "never"
        """,
    )
    await flows.publish_task(run_id, Address("work"))
    client = RefusingHeartbeat(flows, refusal)
    lease = 0.9
    worker = Worker(
        client, code_location=tmp_path, poll_timeout=0.1, lease_seconds=lease
    )

    begun = time.monotonic()
    with pytest.raises(type(refusal)):
        await asyncio.wait_for(worker.run(), timeout=5)

    # The first heartbeat, a third into the lease, is refused, and the handler
    # ends there, before the lease could lapse and hand the task on.
    assert time.monotonic() - begun < lease
    assert client.reports == []
    task = await task_at(flows, run_id, Address("work"))
    assert task.status is TaskStatus.RUNNING


async def test_a_refused_heartbeat_leaves_a_thread_handler_behind(
    flows: Manager, tmp_path: Path, refusal: AccessError
) -> None:
    release = tmp_path / "release"
    run_id = await started(
        flows,
        tmp_path,
        f"""
        import pathlib
        import time

        def work():
            while not pathlib.Path({str(release)!r}).exists():
                time.sleep(0.01)
            return "late"
        """,
    )
    await flows.publish_task(run_id, Address("work"))
    client = RefusingHeartbeat(flows, refusal)
    worker = Worker(client, code_location=tmp_path, poll_timeout=0.1, lease_seconds=0.3)

    try:
        with pytest.raises(type(refusal)):
            await asyncio.wait_for(worker.run(), timeout=5)
        # run ended without waiting for the handler, which is still waiting.
        assert client.reports == []
    finally:
        release.touch()
