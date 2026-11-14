# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Task completion: docs/specs/task-completion.md, on the memory adapters.

What a store does for a completion is in the store contract. Here: what the
manager adds, the rule between tokens and queue kinds, and a run that waits on
a responder from start to end.
"""

import asyncio
import uuid
from pathlib import Path
from textwrap import dedent

import pytest

from neorc_core import (
    Access,
    EventKind,
    InvalidValueError,
    Manager,
    PayloadTooLargeError,
    PermissionDeniedError,
    Queue,
    QueueKind,
    Role,
    RunStateError,
    RunStatus,
    TaskNotFoundError,
    TaskStateError,
    TaskStatus,
    Worker,
)
from neorc_core._values import MAX_PAYLOAD_BYTES, JsonValue
from neorc_core.flows import Address, Reference
from neorc_core.local import (
    DirectQueueClient,
    LocalCluster,
    MemoryCredentialStore,
    MemoryStore,
    MemoryTaskNotifier,
)

TIMEOUT = 10

FLOW: JsonValue = {
    "name": "review",
    "version": "1.0.0",
    "inputs": {"topic": "string"},
    "output": "tasks.approve",
    "steps": {
        "write": {"handler": "tasks:write", "params": {"topic": "inputs.topic"}},
        "approve": {
            "queue": "editors",
            "handler": "approve-draft",
            "params": {
                "draft": "tasks.write",
                "id": "neorc.task_id",
                "tries": "neorc.attempts",
            },
            "fixed_params": {"form": "approval"},
        },
    },
}

WRITE = Address("write")
APPROVE = Address("approve")
FLOW_OUTPUT = Reference.parse("tasks.approve")


QUEUES = {"default": QueueKind.WORKER, "editors": QueueKind.EXTERNAL}
"""What an operator declared before the flow was uploaded."""


@pytest.fixture
def manager() -> Manager:
    return Manager(
        MemoryStore(queues=QUEUES),
        tasks=MemoryTaskNotifier(),
        events=MemoryTaskNotifier(),
    )


async def waiting_for_approval(manager: Manager, topic: str = "tea") -> uuid.UUID:
    """A run whose draft is written and whose approval is published; the task's id."""
    await manager.upload_flows([FLOW])
    run = await manager.start_run("review", {"topic": topic})
    await manager.publish_task(run.id, WRITE)
    written = await manager.receive_task("default", timeout=0)
    assert written is not None
    await manager.report_finished(written.task.id, result=f"a draft about {topic}")
    return (await manager.publish_task(run.id, APPROVE)).id


# Listing.


async def test_pending_tasks_come_with_their_inputs_filled_in(
    manager: Manager,
) -> None:
    task_id = await waiting_for_approval(manager)

    (delivery,) = await manager.pending_tasks("editors")

    assert delivery.task.id == task_id
    assert delivery.task.handler == "approve-draft"
    assert delivery.task.status is TaskStatus.PENDING
    assert delivery.inputs == {
        "form": "approval",
        "draft": "a draft about tea",
        "id": str(task_id),
    }
    assert await manager.pending_tasks("default") == []


async def test_listing_leaves_the_tasks_as_they_are(manager: Manager) -> None:
    task_id = await waiting_for_approval(manager)
    before = await manager.get_task(task_id)

    await manager.pending_tasks("editors")
    again = await manager.pending_tasks("editors")

    assert [delivery.task for delivery in again] == [before]
    assert await manager.get_task(task_id) == before


async def test_pending_tasks_of_several_runs_are_listed_oldest_first(
    manager: Manager,
) -> None:
    first = await waiting_for_approval(manager, "tea")
    second = await waiting_for_approval(manager, "rye")

    listed = await manager.pending_tasks("editors")
    one = await manager.pending_tasks("editors", limit=1)

    assert [d.task.id for d in listed] == [first, second]
    assert [d.inputs["draft"] for d in listed] == [
        "a draft about tea",
        "a draft about rye",
    ]
    assert [d.task.id for d in one] == [first]


@pytest.mark.parametrize("queue", ["a/b", "", "a.b"])
async def test_what_cannot_name_a_queue_is_refused(
    manager: Manager, queue: str
) -> None:
    with pytest.raises(InvalidValueError, match="not a queue name"):
        await manager.pending_tasks(queue)
    with pytest.raises(InvalidValueError, match="not a queue name"):
        await manager.declare_queue(queue, QueueKind.EXTERNAL)
    with pytest.raises(InvalidValueError, match="at least 1"):
        await manager.pending_tasks("editors", limit=0)


# Completing.


async def test_a_completion_is_recorded_and_wakes_the_scheduler(
    manager: Manager,
) -> None:
    task_id = await waiting_for_approval(manager)
    last = await manager.last_sequence()
    waiting = asyncio.ensure_future(manager.wait_for_events(last, timeout=TIMEOUT))
    await asyncio.sleep(0.01)

    completed = await manager.complete_task(task_id, result={"approved": True})

    assert completed.status is TaskStatus.SUCCEEDED
    assert [event.kind for event in await waiting] == [EventKind.TASK_FINISHED]
    assert await manager.pending_tasks("editors") == []
    run = await manager.succeed_run(completed.run_id, FLOW_OUTPUT)
    assert (run.status, run.output) == (RunStatus.SUCCEEDED, {"approved": True})


async def test_a_completion_with_an_error_fails_the_task(manager: Manager) -> None:
    task_id = await waiting_for_approval(manager)

    failed = await manager.complete_task(task_id, error="the draft is libellous")

    assert (failed.status, failed.error) == (
        TaskStatus.FAILED,
        "the draft is libellous",
    )


async def test_a_task_is_completed_once(manager: Manager) -> None:
    task_id = await waiting_for_approval(manager)
    await manager.complete_task(task_id, result="first")

    with pytest.raises(TaskStateError, match="a succeeded task cannot be completed"):
        await manager.complete_task(task_id, result="second")

    assert (await manager.get_task(task_id)).result == "first"


@pytest.mark.parametrize(
    ("result", "error", "problem"),
    [
        ({"$oops": 1}, InvalidValueError, "reserved"),
        ({"when": {"$datetime": "yesterday"}}, InvalidValueError, "datetime"),
        ("x" * (MAX_PAYLOAD_BYTES + 1), PayloadTooLargeError, "limit"),
    ],
)
async def test_an_invalid_result_is_refused_and_the_task_still_waits(
    manager: Manager, result: JsonValue, error: type[Exception], problem: str
) -> None:
    """A worker's invalid result fails its task; a person gets to send another."""
    task_id = await waiting_for_approval(manager)

    with pytest.raises(error, match=problem):
        await manager.complete_task(task_id, result=result)

    assert (await manager.get_task(task_id)).status is TaskStatus.PENDING
    completed = await manager.complete_task(task_id, result="a valid one")
    assert completed.status is TaskStatus.SUCCEEDED


async def test_a_datetime_in_a_result_is_kept_as_a_datetime(manager: Manager) -> None:
    task_id = await waiting_for_approval(manager)
    when: JsonValue = {"$datetime": "2026-09-13T10:00:00+00:00"}

    completed = await manager.complete_task(task_id, result={"when": when})

    assert completed.result == {"when": when}


async def test_a_run_takes_completions_only_while_it_is_active(
    manager: Manager,
) -> None:
    task_id = await waiting_for_approval(manager)
    run_id = (await manager.get_task(task_id)).run_id
    await manager.cancel_run(run_id)

    assert await manager.pending_tasks("editors") == []
    with pytest.raises(RunStateError, match="no longer active"):
        await manager.complete_task(task_id, result=1)


async def test_a_new_version_of_the_flow_takes_its_pending_tasks_off_the_list(
    manager: Manager,
) -> None:
    await waiting_for_approval(manager)
    assert isinstance(FLOW, dict)

    await manager.upload_flows([{**FLOW, "version": "1.1.0"}])

    assert await manager.pending_tasks("editors") == []


async def test_a_task_on_another_queue_is_not_found_by_a_responder_bound_to_one(
    manager: Manager,
) -> None:
    task_id = await waiting_for_approval(manager)

    with pytest.raises(TaskNotFoundError):
        await manager.complete_task(task_id, result=1, queue="billing")
    with pytest.raises(TaskNotFoundError):
        await manager.complete_task(uuid.uuid4(), result=1, queue="editors")

    assert (await manager.get_task(task_id)).status is TaskStatus.PENDING
    completed = await manager.complete_task(task_id, result=1, queue="editors")
    assert completed.status is TaskStatus.SUCCEEDED


# A responder on a worker queue: the task is completed before a worker takes it.


async def test_a_task_on_a_worker_queue_can_be_completed_until_a_worker_receives_it(
    manager: Manager,
) -> None:
    await manager.upload_flows([FLOW])
    run = await manager.start_run("review", {"topic": "tea"})
    forced = (await manager.publish_task(run.id, WRITE)).id

    (listed,) = await manager.pending_tasks("default")
    completed = await manager.complete_task(forced, result="written by hand")

    assert listed.task.id == forced
    assert completed.status is TaskStatus.SUCCEEDED
    assert await manager.receive_task("default", timeout=0) is None

    other = await manager.start_run("review", {"topic": "rye"})
    taken = (await manager.publish_task(other.id, WRITE)).id
    assert await manager.receive_task("default", timeout=0) is not None
    assert await manager.pending_tasks("default") == []
    with pytest.raises(TaskStateError, match="a received task cannot be completed"):
        await manager.complete_task(taken, result="too late")


# An external queue and workers.


async def test_no_worker_is_served_from_an_external_queue(manager: Manager) -> None:
    task_id = await waiting_for_approval(manager)

    with pytest.raises(PermissionDeniedError, match="queue 'editors' is external"):
        await manager.receive_task("editors", timeout=0)
    with pytest.raises(PermissionDeniedError, match="queue 'editors' is external"):
        await manager.task_definitions("editors")

    assert (await manager.get_task(task_id)).status is TaskStatus.PENDING
    assert [step.name for step in await manager.task_definitions("default")] == [
        "write"
    ]


async def test_a_worker_does_not_start_on_an_external_queue(
    manager: Manager, tmp_path: Path
) -> None:
    await waiting_for_approval(manager)
    worker = Worker(DirectQueueClient(manager), queue="editors", code_location=tmp_path)

    with pytest.raises(PermissionDeniedError, match="queue 'editors' is external"):
        await worker.prepare()


async def test_a_worker_stops_when_its_queue_is_declared_external(
    manager: Manager, tmp_path: Path
) -> None:
    """Refused as for a token: it ends, rather than poll a queue it is kept off."""
    await manager.declare_queue("reviews", QueueKind.WORKER)
    worker = Worker(
        DirectQueueClient(manager),
        queue="reviews",
        code_location=tmp_path,
        poll_timeout=0.05,
    )
    running = asyncio.ensure_future(worker.run())
    await asyncio.sleep(0.1)
    assert not running.done()

    await manager.declare_queue("reviews", QueueKind.EXTERNAL)

    with pytest.raises(PermissionDeniedError, match="queue 'reviews' is external"):
        await asyncio.wait_for(running, timeout=TIMEOUT)


async def test_no_worker_is_served_from_a_queue_nobody_declared(
    manager: Manager, tmp_path: Path
) -> None:
    worker = Worker(DirectQueueClient(manager), queue="typo", code_location=tmp_path)

    with pytest.raises(PermissionDeniedError, match="queue 'typo' is not declared"):
        await worker.prepare()
    with pytest.raises(PermissionDeniedError, match="queue 'typo' is not declared"):
        await manager.receive_task("typo", timeout=0)
    assert await manager.pending_tasks("typo") == []


async def test_queues_are_declared_and_listed(manager: Manager) -> None:
    assert await manager.queue_kind("billing") is None

    await manager.declare_queue("billing", QueueKind.EXTERNAL)

    assert await manager.queue_kind("billing") is QueueKind.EXTERNAL
    assert await manager.queue_kind("default") is QueueKind.WORKER
    assert await manager.queues() == [
        Queue("billing", QueueKind.EXTERNAL),
        Queue("default", QueueKind.WORKER),
        Queue("editors", QueueKind.EXTERNAL),
    ]


# Tokens and queue kinds.


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore(queues=QUEUES)


@pytest.fixture
def access(store: MemoryStore) -> Access:
    return Access(MemoryCredentialStore(), queues=store)


async def test_a_token_is_bound_to_a_declared_queue(access: Access) -> None:
    for role in (Role.WORKER, Role.RESPONDER):
        with pytest.raises(InvalidValueError, match="queue 'typo' is not declared"):
            await access.create_token("t", role, queue="typo")
    assert await access.tokens() == []

    await access.declare_queue("typo", QueueKind.WORKER)
    await access.create_token("t", Role.WORKER, queue="typo")


async def test_a_worker_token_is_bound_to_a_worker_queue(access: Access) -> None:
    with pytest.raises(InvalidValueError, match="queue 'editors' is external"):
        await access.create_token("w", Role.WORKER, queue="editors")
    await access.create_token("w", Role.WORKER, queue="default")

    assert [(t.name, t.queue) for t in await access.tokens()] == [("w", "default")]


async def test_a_responder_token_is_bound_to_a_queue_of_either_kind(
    access: Access,
) -> None:
    _, external = await access.create_token("r1", Role.RESPONDER, queue="editors")
    _, worker = await access.create_token("r2", Role.RESPONDER, queue="default")

    assert (external.role, external.queue) == (Role.RESPONDER, "editors")
    assert (worker.role, worker.queue) == (Role.RESPONDER, "default")


async def test_a_queue_with_worker_tokens_cannot_be_declared_external(
    access: Access, store: MemoryStore
) -> None:
    await access.declare_queue("reviews", QueueKind.WORKER)
    await access.create_token("w1", Role.WORKER, queue="reviews")
    await access.create_token("w2", Role.WORKER, queue="reviews")
    await access.create_token("r", Role.RESPONDER, queue="reviews")
    await access.create_token("elsewhere", Role.WORKER, queue="default")

    with pytest.raises(InvalidValueError, match="revoke w1, w2 first"):
        await access.declare_queue("reviews", QueueKind.EXTERNAL)
    assert await store.queue_kind("reviews") is QueueKind.WORKER

    await access.revoke_token("w1")
    await access.revoke_token("w2")
    await access.declare_queue("reviews", QueueKind.EXTERNAL)

    assert await store.queue_kind("reviews") is QueueKind.EXTERNAL


async def test_a_queue_is_declared_a_worker_queue_whatever_its_tokens(
    access: Access,
) -> None:
    await access.create_token("r", Role.RESPONDER, queue="editors")

    await access.declare_queue("editors", QueueKind.WORKER)
    await access.create_token("w", Role.WORKER, queue="editors")

    assert Queue("editors", QueueKind.WORKER) in await access.queues()


async def test_a_queue_name_is_checked_where_it_is_declared(access: Access) -> None:
    with pytest.raises(InvalidValueError, match="not a queue name"):
        await access.declare_queue("a/b", QueueKind.EXTERNAL)


async def test_an_access_without_declarations_checks_no_queue() -> None:
    access = Access(MemoryCredentialStore())

    await access.create_token("w", Role.WORKER, queue="anything")
    with pytest.raises(RuntimeError, match="no store of queue declarations"):
        await access.declare_queue("editors", QueueKind.EXTERNAL)


# A run that waits on a responder, from start to end.

REVISING = """\
    name: revising
    version: 1.0.0
    inputs:
      topic: string
    output: tasks.publish
    steps:
      rounds:
        loop:
          max_cycles: 5
          exit_condition: approve
        steps:
          write:
            handler: MODULE:write
            params: {topic: inputs.topic, round: neorc.loop_count}
          approve:
            queue: editors
            handler: approve-draft
            params: {drafts: tasks.write}
      publish:
        handler: MODULE:publish
        params: {drafts: tasks.write, approvals: tasks.approve}
    """

HANDLERS = """\
    def write(topic, round):
        return f"{topic}, draft {round}"

    def publish(drafts, approvals):
        return {"published": drafts[-1], "rounds": len(approvals)}
    """


def project(tmp_path: Path) -> Path:
    module = f"handlers_{uuid.uuid4().hex}"
    (tmp_path / f"{module}.py").write_text(dedent(HANDLERS))
    flows_dir = tmp_path / "flows"
    flows_dir.mkdir()
    (flows_dir / "revising.yaml").write_text(dedent(REVISING).replace("MODULE", module))
    return flows_dir


async def next_pending(manager: Manager, queue: str) -> tuple[uuid.UUID, JsonValue]:
    """The one pending task of ``queue`` once there is one: its id and its inputs."""
    async with asyncio.timeout(TIMEOUT):
        while True:
            listed = await manager.pending_tasks(queue)
            if listed:
                (delivery,) = listed
                return delivery.task.id, dict(delivery.inputs)
            await asyncio.sleep(0.01)


async def test_a_run_waits_on_a_responder_round_after_round(tmp_path: Path) -> None:
    """Workers write, a responder answers, and the answer is the loop's exit.

    The journey of the spec: an external queue beside a worker queue, pending
    tasks listed with their inputs, a completion read downstream, a second
    completion refused, and workers kept off the external queue.
    """
    flows_dir = project(tmp_path)
    async with LocalCluster(code_location=tmp_path, poll_timeout=0.2) as cluster:
        manager = cluster.manager
        await manager.declare_queue("editors", QueueKind.EXTERNAL)
        await cluster.upload(flows_dir)
        await cluster.start()  # no worker for editors, or its handler would not fit
        run = await cluster.client.start_run("revising", {"topic": "tea"})

        # Round 1: the draft is written by a worker, then the run waits.
        first, inputs = await next_pending(manager, "editors")
        assert inputs == {"drafts": ["tea, draft 1"]}
        await asyncio.sleep(0.3)  # longer than a poll: nothing takes the task
        assert (await manager.get_task(first)).status is TaskStatus.PENDING
        assert (await cluster.client.get_run(run.id)).status is RunStatus.ACTIVE
        with pytest.raises(PermissionDeniedError):
            await DirectQueueClient(manager).receive_task("editors", timeout=0)

        # Not approved: the loop goes round again.
        await manager.complete_task(first, result=False, queue="editors")
        with pytest.raises(TaskStateError):
            await manager.complete_task(first, result=True, queue="editors")

        # Round 2: every draft so far, and this time it is approved.
        second, inputs = await next_pending(manager, "editors")
        assert second != first
        assert inputs == {"drafts": ["tea, draft 1", "tea, draft 2"]}
        await manager.complete_task(second, result=True, queue="editors")

        finished = await cluster.wait(run.id, timeout=TIMEOUT)

    assert finished.status is RunStatus.SUCCEEDED
    assert finished.output == {"published": "tea, draft 2", "rounds": 2}
    assert await manager.pending_tasks("editors") == []


async def test_a_run_fails_when_a_responder_completes_with_an_error(
    tmp_path: Path,
) -> None:
    flows_dir = project(tmp_path)
    async with LocalCluster(code_location=tmp_path, poll_timeout=0.2) as cluster:
        await cluster.manager.declare_queue("editors", QueueKind.EXTERNAL)
        await cluster.upload(flows_dir)
        await cluster.start()
        run = await cluster.client.start_run("revising", {"topic": "tea"})
        task_id, _ = await next_pending(cluster.manager, "editors")

        await cluster.manager.complete_task(task_id, error="not fit to print")
        finished = await cluster.wait(run.id, timeout=TIMEOUT)

    assert finished.status is RunStatus.FAILED
    assert (await cluster.manager.get_task(task_id)).error == "not fit to print"
