# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""What every ``Store`` must do: versions, run trees, tasks, leases and events."""

from __future__ import annotations

import asyncio
import math
import uuid
from datetime import UTC, datetime

import pytest

from neorc_core._errors import (
    FlowDefinitionError,
    FlowNotFoundError,
    FlowVersionError,
    RunNotFoundError,
    RunStateError,
    TaskNotFoundError,
    TaskStateError,
)
from neorc_core._queues import Queue, QueueKind
from neorc_core._runs import (
    EventKind,
    Run,
    RunStatus,
    StoredFlow,
    Task,
    sub_run_id_for,
)
from neorc_core._task import TaskStatus
from neorc_core._values import JsonValue
from neorc_core.flows import (
    Address,
    FlowDefinition,
    Outcome,
    Reference,
    StepResult,
    Version,
    parse_flow,
)
from neorc_core.ports._store import Store


def _content(name: str, version: str, handler: str = "tasks:work") -> JsonValue:
    return {
        "name": name,
        "version": version,
        "steps": {"work": {"handler": handler}},
    }


def _flow(content: JsonValue) -> FlowDefinition:
    return parse_flow(content)


WORK = Address("work")
ELSEWHERE = Address("elsewhere")


class StoreContract:
    """Subclass and provide a ``store`` fixture holding nothing."""

    @pytest.fixture
    def store(self) -> Store:
        raise NotImplementedError("a StoreContract subclass provides `store`")

    async def upload(
        self, store: Store, name: str = "a", version: str = "1.0.0", **kwargs: str
    ) -> bool:
        content = _content(name, version, **kwargs)
        (stored,) = await store.store_flows([StoredFlow(_flow(content), content)])
        return stored

    async def start(self, store: Store, name: str = "a") -> Run:
        latest = await store.get_flow(name)
        return await store.start_run(name, latest.version, {"x": 1})

    # Flow versions.

    async def test_a_new_flow_version_is_stored(self, store: Store) -> None:
        assert await self.upload(store) is True

        stored = await store.get_flow("a")

        assert stored.version == Version(1, 0, 0)
        assert stored.content == _content("a", "1.0.0")
        assert stored.definition.name == "a"

    async def test_an_identical_upload_changes_nothing(self, store: Store) -> None:
        await self.upload(store)
        run = await self.start(store)

        assert await self.upload(store) is False
        assert (await store.get_run(run.id)).status is RunStatus.ACTIVE

    async def test_a_reformatted_upload_is_the_same_version(self, store: Store) -> None:
        """Key order is formatting: the structure decides, not the text as sent."""
        content: JsonValue = {
            "name": "a",
            "version": "1.0.0",
            "steps": {"work": {"handler": "tasks:work", "fixed_params": {"n": 1}}},
        }
        reformatted: JsonValue = {
            "steps": {"work": {"fixed_params": {"n": 1}, "handler": "tasks:work"}},
            "version": "1.0.0",
            "name": "a",
        }
        await store.store_flows([StoredFlow(_flow(content), content)])
        run = await self.start(store)

        stored = await store.store_flows([StoredFlow(_flow(reformatted), reformatted)])

        assert stored == [False]
        assert (await store.get_run(run.id)).status is RunStatus.ACTIVE
        assert (await store.get_flow("a")).content == content

    async def test_changed_content_on_a_stored_version_is_rejected(
        self, store: Store
    ) -> None:
        await self.upload(store)

        with pytest.raises(FlowVersionError, match="new version"):
            await self.upload(store, handler="tasks:other")

    @pytest.mark.parametrize(("before", "after"), [(1, True), (1, 1.0), (0, False)])
    async def test_a_changed_json_type_is_changed_content(
        self, store: Store, before: JsonValue, after: JsonValue
    ) -> None:
        def content(value: JsonValue) -> JsonValue:
            work: JsonValue = {"handler": "tasks:work", "fixed_params": {"flag": value}}
            return {"name": "a", "version": "1.0.0", "steps": {"work": work}}

        await store.store_flows([StoredFlow(_flow(content(before)), content(before))])

        with pytest.raises(FlowVersionError, match="new version"):
            await store.store_flows([StoredFlow(_flow(content(after)), content(after))])

    async def test_a_lower_version_is_rejected(self, store: Store) -> None:
        await self.upload(store, version="1.2.0")

        with pytest.raises(FlowVersionError, match="only move forward"):
            await self.upload(store, version="1.1.9")

    async def test_the_latest_version_is_the_highest(self, store: Store) -> None:
        await self.upload(store, version="1.0.0")
        await self.upload(store, version="1.10.0")
        await self.upload(store, "b", "0.1.0")

        latest = await store.latest_flows()

        assert [(f.name, str(f.version)) for f in latest] == [
            ("a", "1.10.0"),
            ("b", "0.1.0"),
        ]
        assert (await store.get_flow("a")).version == Version(1, 10, 0)
        older = await store.get_flow("a", Version(1, 0, 0))
        assert older.version == Version(1, 0, 0)

    async def test_an_unknown_flow_or_version_is_reported_as_missing(
        self, store: Store
    ) -> None:
        with pytest.raises(FlowNotFoundError):
            await store.get_flow("a")
        await self.upload(store)
        with pytest.raises(FlowNotFoundError):
            await store.get_flow("a", Version(9, 9, 9))

    def caller(self, version: str, calls_with: str) -> StoredFlow:
        content: JsonValue = {
            "name": "caller",
            "version": version,
            "steps": {"call": {"flow": "b", "fixed_params": {calls_with: 1}}},
        }
        return StoredFlow(_flow(content), content)

    def callee(self, version: str, takes: str) -> StoredFlow:
        content: JsonValue = {
            "name": "b",
            "version": version,
            "inputs": {takes: "number"},
            "steps": {"work": {"handler": "tasks:work"}},
        }
        return StoredFlow(_flow(content), content)

    async def test_flows_deployed_together_are_stored_together(
        self, store: Store
    ) -> None:
        stored = await store.store_flows(
            [self.caller("1.0.0", "x"), self.callee("1.0.0", "x")]
        )

        assert stored == [True, True]
        assert [f.name for f in await store.latest_flows()] == ["b", "caller"]

    async def test_one_rejected_upload_stores_none_of_the_set(
        self, store: Store
    ) -> None:
        await store.store_flows([self.callee("1.0.0", "x"), self.caller("1.0.0", "x")])
        await self.upload(store, "c")
        run = await self.start(store, "c")

        with pytest.raises(FlowVersionError):
            await store.store_flows(
                [
                    self.callee("2.0.0", "y"),
                    self.caller("1.0.0", "y"),
                    self.upload_of("c", "0.1.0"),
                ]
            )

        assert (await store.get_flow("b")).version == Version(1, 0, 0)
        assert (await store.get_run(run.id)).status is RunStatus.ACTIVE

    def upload_of(self, name: str, version: str) -> StoredFlow:
        content = _content(name, version)
        return StoredFlow(_flow(content), content)

    async def test_the_set_is_checked_as_it_will_be_once_stored(
        self, store: Store
    ) -> None:
        await store.store_flows([self.callee("1.0.0", "x")])
        await store.store_flows([self.callee("2.0.0", "y")])

        # b 1.0.0 is identical to what is stored, but 2.0.0 stays the latest.
        with pytest.raises(FlowDefinitionError, match="b has no input 'x'"):
            await store.store_flows(
                [self.callee("1.0.0", "x"), self.caller("1.0.0", "x")]
            )

        with pytest.raises(FlowNotFoundError):
            await store.get_flow("caller")

    async def test_a_new_version_must_still_suit_the_flows_calling_it(
        self, store: Store
    ) -> None:
        await store.store_flows([self.callee("1.0.0", "x"), self.caller("1.0.0", "x")])

        with pytest.raises(FlowDefinitionError, match="missing input 'y'"):
            await store.store_flows([self.callee("2.0.0", "y")])

    async def test_a_flow_uploaded_twice_in_one_set_is_rejected(
        self, store: Store
    ) -> None:
        with pytest.raises(FlowDefinitionError, match="more than once"):
            await store.store_flows(
                [self.upload_of("a", "1.0.0"), self.upload_of("a", "2.0.0")]
            )

    # Runs.

    async def test_a_run_starts_active_with_an_event(self, store: Store) -> None:
        await self.upload(store)

        run = await self.start(store)

        assert run.status is RunStatus.ACTIVE
        assert run.root_id == run.id
        assert run.inputs == {"x": 1}
        assert await store.get_run(run.id) == run
        events = await store.events_after(0)
        assert [(e.run_id, e.kind) for e in events] == [(run.id, EventKind.RUN_STARTED)]

    async def test_a_run_needs_the_latest_version_of_a_known_flow(
        self, store: Store
    ) -> None:
        with pytest.raises(FlowNotFoundError):
            await store.start_run("a", Version(1, 0, 0), {})
        await self.upload(store, version="1.0.0")
        await self.upload(store, version="2.0.0")

        with pytest.raises(FlowVersionError, match="latest"):
            await store.start_run("a", Version(1, 0, 0), {})

    async def test_a_sub_flow_run_joins_its_parents_tree_once(
        self, store: Store
    ) -> None:
        await self.upload(store, "a")
        await self.upload(store, "b")
        parent = await self.start(store, "a")
        version = (await store.get_flow("b")).version

        child = await store.start_run(
            "b", version, {}, parent_id=parent.id, parent_address=WORK
        )
        again = await store.start_run(
            "b", version, {}, parent_id=parent.id, parent_address=WORK
        )

        assert child.id == sub_run_id_for(parent.id, WORK)
        assert again == child
        assert child.root_id == parent.id
        assert child.parent_id == parent.id
        assert child.parent_address == WORK
        kinds = [e.kind for e in await store.events_after(0)]
        assert kinds == [EventKind.RUN_STARTED, EventKind.RUN_STARTED]

    async def test_no_sub_flow_run_starts_in_an_inactive_run(
        self, store: Store
    ) -> None:
        await self.upload(store)
        parent = await self.start(store)
        await store.cancel_run_tree(parent.id, "by hand")

        with pytest.raises(RunStateError):
            await store.start_run(
                "a", parent.version, {}, parent_id=parent.id, parent_address=WORK
            )

    async def test_a_run_succeeds_once_with_its_output(self, store: Store) -> None:
        await self.upload(store)
        run = await self.start(store)

        succeeded = await store.succeed_run(run.id, ["out"])

        assert succeeded.status is RunStatus.SUCCEEDED
        assert (await store.get_run(run.id)).output == ["out"]
        with pytest.raises(RunStateError):
            await store.succeed_run(run.id, None)
        kinds = [e.kind for e in await store.events_after(0)]
        assert kinds == [EventKind.RUN_STARTED, EventKind.RUN_FINISHED]

    async def tree(self, store: Store) -> tuple[Run, Run, Run]:
        """A top-level run of ``a`` with two sub-flow runs of ``b``, one finished."""
        await self.upload(store, "a")
        await self.upload(store, "b")
        root = await self.start(store, "a")
        version = (await store.get_flow("b")).version
        active = await store.start_run(
            "b", version, {}, parent_id=root.id, parent_address=WORK
        )
        finished = await store.start_run(
            "b", version, {}, parent_id=root.id, parent_address=ELSEWHERE
        )
        await store.succeed_run(finished.id, 1)
        return root, active, finished

    async def test_failing_a_run_fails_every_active_run_in_its_tree(
        self, store: Store
    ) -> None:
        root, active, finished = await self.tree(store)
        before = len(await store.events_after(0))

        await store.fail_run_tree(active.id, "a task failed")

        assert (await store.get_run(root.id)).status is RunStatus.FAILED
        assert (await store.get_run(root.id)).reason == "a task failed"
        assert (await store.get_run(active.id)).status is RunStatus.FAILED
        assert (await store.get_run(finished.id)).status is RunStatus.SUCCEEDED
        events = (await store.events_after(0))[before:]
        assert sorted((e.run_id, e.kind) for e in events) == sorted(
            [(root.id, EventKind.RUN_FINISHED), (active.id, EventKind.RUN_FINISHED)]
        )

    async def test_cancelling_a_run_cancels_every_active_run_in_its_tree(
        self, store: Store
    ) -> None:
        root, active, finished = await self.tree(store)

        await store.cancel_run_tree(root.id, "by hand")

        assert (await store.get_run(root.id)).status is RunStatus.CANCELLED
        assert (await store.get_run(active.id)).status is RunStatus.CANCELLED
        assert (await store.get_run(active.id)).reason == "by hand"
        assert (await store.get_run(finished.id)).status is RunStatus.SUCCEEDED

    async def test_a_new_version_cancels_every_tree_running_the_flow(
        self, store: Store
    ) -> None:
        root, active, _ = await self.tree(store)
        await self.upload(store, "c")
        unrelated = await self.start(store, "c")

        assert await self.upload(store, "b", "1.1.0") is True

        assert (await store.get_run(active.id)).status is RunStatus.CANCELLED
        # The run calling b is cancelled with it: its whole tree goes.
        assert (await store.get_run(root.id)).status is RunStatus.CANCELLED
        assert (await store.get_run(unrelated.id)).status is RunStatus.ACTIVE

    async def test_a_reason_is_stored_with_nul_replaced(self, store: Store) -> None:
        """A reason is a message, not a value: kept, its NUL replaced."""
        await self.upload(store)
        failed = await self.start(store)
        cancelled = await self.start(store)

        await store.fail_run_tree(failed.id, "boom\x00!")
        await store.cancel_run_tree(cancelled.id, "\x00")

        assert (await store.get_run(failed.id)).reason == "boom�!"
        assert (await store.get_run(cancelled.id)).reason == "�"

    async def test_numbers_read_back_as_they_were_written(self, store: Store) -> None:
        """What JSON tells apart, the store keeps apart: no numeric normalisation."""
        await self.upload(store)
        inputs: dict[str, JsonValue] = {
            "big": 1e16,
            "negative_zero": -0.0,
            "huge": 10**20,
            "whole": 1.0,
        }

        run = await store.start_run("a", Version(1, 0, 0), inputs)
        task_id = await self.publish(store, run)
        await store.receive_task("default", lease_seconds=30)
        await store.finish_task(task_id, result=list(inputs.values()))

        read = (await store.get_run(run.id)).inputs
        result = (await store.get_task(task_id)).result
        assert isinstance(result, list)
        for value in (read["big"], result[0]):
            assert isinstance(value, float) and value == 1e16
        for value in (read["negative_zero"], result[1]):
            assert isinstance(value, float) and math.copysign(1, value) == -1
        for value in (read["huge"], result[2]):
            assert isinstance(value, int) and value == 10**20
        for value in (read["whole"], result[3]):
            assert isinstance(value, float)

    async def test_an_unknown_run_is_reported_as_missing(self, store: Store) -> None:
        missing = uuid.uuid4()
        with pytest.raises(RunNotFoundError):
            await store.get_run(missing)
        with pytest.raises(RunNotFoundError):
            await store.run_state(missing)
        with pytest.raises(RunNotFoundError):
            await store.fail_run_tree(missing, "gone")

    # Tasks.

    async def publish(
        self, store: Store, run: Run, address: Address = WORK, queue: str = "default"
    ) -> uuid.UUID:
        """Publish a task, on a queue declared a worker queue unless declared."""
        if await store.queue_kind(queue) is None:
            await store.declare_queue(queue, QueueKind.WORKER)
        task = await store.publish_task(
            run.id,
            address,
            queue=queue,
            handler="tasks:work",
            params={"x": Reference.parse("inputs.x")},
            fixed_params={"n": 3},
        )
        return task.id

    async def test_a_published_task_is_pending_under_its_address(
        self, store: Store
    ) -> None:
        await self.upload(store)
        run = await self.start(store)

        task_id = await self.publish(store, run)
        task = await store.get_task(task_id)

        assert task.status is TaskStatus.PENDING
        assert task.run_id == run.id
        assert task.address == WORK
        assert task.params == {"x": Reference.parse("inputs.x")}
        assert task.fixed_params == {"n": 3}
        assert task.attempts == 0

    async def test_task_ids_are_random_not_derived_from_the_address(
        self, store: Store
    ) -> None:
        await self.upload(store)
        run = await self.start(store)
        other_run = await self.start(store)

        ids = [
            await self.publish(store, run),
            await self.publish(store, run, ELSEWHERE),
            await self.publish(store, other_run),
        ]

        assert len(set(ids)) == 3
        assert all(task_id.version == 4 for task_id in ids)

    async def test_publishing_an_address_again_changes_nothing(
        self, store: Store
    ) -> None:
        await self.upload(store)
        run = await self.start(store)
        task_id = await self.publish(store, run)
        await store.receive_task("default", lease_seconds=30)

        again = await self.publish(store, run)

        assert again == task_id
        assert (await store.get_task(task_id)).status is TaskStatus.RECEIVED

    async def test_no_task_is_published_into_an_inactive_run(
        self, store: Store
    ) -> None:
        await self.upload(store)
        run = await self.start(store)
        await store.cancel_run_tree(run.id, "by hand")

        with pytest.raises(RunStateError):
            await self.publish(store, run)

    async def test_tasks_are_received_per_queue_oldest_first(
        self, store: Store
    ) -> None:
        await self.upload(store)
        run = await self.start(store)
        first = await self.publish(store, run, Address("one"))
        await self.publish(store, run, Address("two"), queue="scoring")
        second = await self.publish(store, run, Address("three"))

        received = await store.receive_task("default", lease_seconds=30)
        after = await store.receive_task("default", lease_seconds=30)
        empty = await store.receive_task("default", lease_seconds=30)

        assert received is not None and received.id == first
        assert received.status is TaskStatus.RECEIVED
        assert received.attempts == 1
        assert received.lease_expires_at is not None
        assert received.lease_expires_at > datetime.now(UTC)
        assert after is not None and after.id == second
        assert empty is None
        assert await store.receive_task("nobody", lease_seconds=30) is None

    async def test_concurrent_receives_never_share_a_task(self, store: Store) -> None:
        await self.upload(store)
        run = await self.start(store)
        for index in range(5):
            await self.publish(store, run, Address(f"t{index}"))

        received = await asyncio.gather(
            *(store.receive_task("default", lease_seconds=30) for _ in range(15))
        )

        ids = [task.id for task in received if task is not None]
        assert len(ids) == len(set(ids)) == 5

    async def test_a_lapsed_lease_hands_the_task_on(self, store: Store) -> None:
        await self.upload(store)
        run = await self.start(store)
        task_id = await self.publish(store, run)
        await store.receive_task("default", lease_seconds=0.05)
        await store.claim_task(task_id)

        await asyncio.sleep(0.06)
        again = await store.receive_task("default", lease_seconds=30)

        assert again is not None and again.id == task_id
        assert again.attempts == 2

    async def test_a_heartbeat_keeps_the_task(self, store: Store) -> None:
        await self.upload(store)
        run = await self.start(store)
        task_id = await self.publish(store, run)
        await store.receive_task("default", lease_seconds=0.05)

        expires_at = await store.extend_task_lease(task_id, lease_seconds=30)
        await asyncio.sleep(0.06)

        assert expires_at > datetime.now(UTC)
        assert await store.receive_task("default", lease_seconds=30) is None

    async def test_a_task_finishes_with_its_result_and_an_event(
        self, store: Store
    ) -> None:
        await self.upload(store)
        run = await self.start(store)
        task_id = await self.publish(store, run)
        await store.receive_task("default", lease_seconds=30)
        claimed = await store.claim_task(task_id)

        finished = await store.finish_task(task_id, result={"words": ["red"]})

        assert claimed.status is TaskStatus.RUNNING
        assert finished.status is TaskStatus.SUCCEEDED
        assert (await store.get_task(task_id)).result == {"words": ["red"]}
        assert finished.lease_expires_at is None
        events = await store.events_after(1)
        assert [(e.run_id, e.kind) for e in events] == [
            (run.id, EventKind.TASK_FINISHED)
        ]

    async def test_a_task_fails_with_its_reason(self, store: Store) -> None:
        await self.upload(store)
        run = await self.start(store)
        task_id = await self.publish(store, run)
        await store.receive_task("default", lease_seconds=30)

        failed = await store.finish_task(task_id, error="ValueError: nope")

        assert failed.status is TaskStatus.FAILED
        assert failed.error == "ValueError: nope"

    async def test_an_error_is_stored_with_nul_replaced(self, store: Store) -> None:
        await self.upload(store)
        run = await self.start(store)
        task_id = await self.publish(store, run)
        await store.receive_task("default", lease_seconds=30)

        failed = await store.finish_task(task_id, error="Error: \x00 in \ud800")

        assert failed.error == "Error: � in �"
        assert (await store.get_task(task_id)).error == "Error: � in �"

    async def test_task_transitions_are_enforced(self, store: Store) -> None:
        await self.upload(store)
        run = await self.start(store)
        task_id = await self.publish(store, run)

        with pytest.raises(TaskStateError):
            await store.claim_task(task_id)
        await store.receive_task("default", lease_seconds=30)
        await store.finish_task(task_id, result=1)
        with pytest.raises(TaskStateError):
            await store.finish_task(task_id, error="too late")
        with pytest.raises(TaskStateError):
            await store.extend_task_lease(task_id)

    async def test_a_task_of_an_inactive_run_is_refused_its_claim(
        self, store: Store
    ) -> None:
        await self.upload(store)
        run = await self.start(store)
        task_id = await self.publish(store, run)
        await store.receive_task("default", lease_seconds=0.05)
        await store.cancel_run_tree(run.id, "by hand")

        with pytest.raises(RunStateError):
            await store.claim_task(task_id)

        assert (await store.get_task(task_id)).status is TaskStatus.FAILED
        await asyncio.sleep(0.06)
        assert await store.receive_task("default", lease_seconds=30) is None

    async def test_a_task_in_flight_may_finish_after_its_run_was_cancelled(
        self, store: Store
    ) -> None:
        await self.upload(store)
        run = await self.start(store)
        task_id = await self.publish(store, run)
        await store.receive_task("default", lease_seconds=30)
        await store.claim_task(task_id)
        await store.cancel_run_tree(run.id, "by hand")

        finished = await store.finish_task(task_id, result=1)

        assert finished.status is TaskStatus.SUCCEEDED
        assert (await store.events_after(0))[-1].kind is EventKind.TASK_FINISHED

    async def test_an_unknown_task_is_reported_as_missing(self, store: Store) -> None:
        missing = uuid.uuid4()
        with pytest.raises(TaskNotFoundError):
            await store.get_task(missing)
        with pytest.raises(TaskNotFoundError):
            await store.claim_task(missing)
        with pytest.raises(TaskNotFoundError):
            await store.finish_task(missing, result=1)

    # Completion: ending a pending task by its id, with no lease.

    async def test_pending_tasks_are_listed_per_queue_oldest_first(
        self, store: Store
    ) -> None:
        await self.upload(store)
        run = await self.start(store)
        first = await self.publish(store, run, Address("one"))
        await self.publish(store, run, Address("two"), queue="scoring")
        second = await self.publish(store, run, Address("three"))

        listed = await store.pending_tasks("default")

        assert [task.id for task in listed] == [first, second]
        assert [task.id for task in await store.pending_tasks("default", limit=1)] == [
            first
        ]
        assert await store.pending_tasks("nobody") == []

    async def test_listing_does_not_lease_or_change_any_task(
        self, store: Store
    ) -> None:
        await self.upload(store)
        run = await self.start(store)
        task_id = await self.publish(store, run)
        before = await store.get_task(task_id)

        await store.pending_tasks("default")
        await store.pending_tasks("default")

        assert await store.get_task(task_id) == before
        assert before.status is TaskStatus.PENDING
        assert (before.attempts, before.lease_expires_at) == (0, None)
        assert len(await store.events_after(0)) == 1  # the run's start alone

    async def test_a_task_leaves_the_list_when_received_completed_or_its_run_ends(
        self, store: Store
    ) -> None:
        await self.upload(store)
        run = await self.start(store)
        ended = await self.start(store)
        received = await self.publish(store, run, Address("one"))
        completed = await self.publish(store, run, Address("two"))
        waiting = await self.publish(store, run, Address("three"))
        await self.publish(store, ended)

        taken = await store.receive_task("default", lease_seconds=30)
        await store.complete_task(completed, result=1)
        await store.cancel_run_tree(ended.id, "by hand")

        assert taken is not None and taken.id == received
        assert [task.id for task in await store.pending_tasks("default")] == [waiting]

    async def test_a_pending_task_is_completed_with_its_result_and_an_event(
        self, store: Store
    ) -> None:
        await self.upload(store)
        run = await self.start(store)
        task_id = await self.publish(store, run)

        completed = await store.complete_task(task_id, result={"approved": True})

        assert completed.status is TaskStatus.SUCCEEDED
        assert completed.result == {"approved": True}
        assert completed.finished_at is not None
        assert (completed.attempts, completed.lease_expires_at) == (0, None)
        assert completed.started_at is None  # no worker began it
        assert await store.get_task(task_id) == completed
        events = await store.events_after(1)
        assert [(e.run_id, e.kind) for e in events] == [
            (run.id, EventKind.TASK_FINISHED)
        ]
        state = await store.run_state(run.id)
        assert state.steps[WORK] == StepResult(Outcome.SUCCEEDED, {"approved": True})

    async def test_a_completion_with_an_error_fails_the_task(
        self, store: Store
    ) -> None:
        await self.upload(store)
        run = await self.start(store)
        task_id = await self.publish(store, run)

        failed = await store.complete_task(task_id, error="refused: \x00")

        assert failed.status is TaskStatus.FAILED
        assert (failed.result, failed.error) == (None, "refused: \ufffd")
        assert (await store.events_after(0))[-1].kind is EventKind.TASK_FINISHED

    async def test_a_task_is_completed_once(self, store: Store) -> None:
        await self.upload(store)
        run = await self.start(store)
        task_id = await self.publish(store, run)
        await store.complete_task(task_id, result="first")

        with pytest.raises(TaskStateError, match="only a pending one"):
            await store.complete_task(task_id, result="second")
        with pytest.raises(TaskStateError):
            await store.complete_task(task_id, error="second")

        assert (await store.get_task(task_id)).result == "first"
        assert len(await store.events_after(1)) == 1

    async def test_of_completions_at_once_one_is_taken(self, store: Store) -> None:
        await self.upload(store)
        run = await self.start(store)
        task_id = await self.publish(store, run)

        outcomes = await asyncio.gather(
            *(store.complete_task(task_id, result=n) for n in range(8)),
            return_exceptions=True,
        )

        taken = [o for o in outcomes if not isinstance(o, BaseException)]
        refused = [o for o in outcomes if isinstance(o, TaskStateError)]
        assert (len(taken), len(refused)) == (1, 7)
        assert (await store.get_task(task_id)).result == taken[0].result
        assert len(await store.events_after(1)) == 1

    async def test_a_task_a_worker_received_is_the_workers_to_finish(
        self, store: Store
    ) -> None:
        await self.upload(store)
        run = await self.start(store)
        task_id = await self.publish(store, run)
        await store.receive_task("default", lease_seconds=30)

        with pytest.raises(TaskStateError, match="a received task"):
            await store.complete_task(task_id, result=1)
        await store.claim_task(task_id)
        with pytest.raises(TaskStateError, match="a running task"):
            await store.complete_task(task_id, result=1)

        finished = await store.finish_task(task_id, result="the worker's")
        assert finished.result == "the worker's"

    async def test_a_completion_and_a_receive_at_once_do_not_both_take_the_task(
        self, store: Store
    ) -> None:
        await self.upload(store)
        run = await self.start(store)
        for index in range(10):
            task_id = await self.publish(store, run, Address(f"t{index}"))

            received, completed = await asyncio.gather(
                store.receive_task("default", lease_seconds=30),
                store.complete_task(task_id, result=1),
                return_exceptions=True,
            )

            task = await store.get_task(task_id)
            if isinstance(completed, TaskStateError):
                assert isinstance(received, Task) and received.id == task_id
                assert task.status is TaskStatus.RECEIVED
                await store.finish_task(task_id, result=2)
            else:
                assert received is None
                assert task.status is TaskStatus.SUCCEEDED

    async def test_a_run_takes_completions_only_while_it_is_active(
        self, store: Store
    ) -> None:
        await self.upload(store)
        run = await self.start(store)
        task_id = await self.publish(store, run)
        await store.cancel_run_tree(run.id, "by hand")

        with pytest.raises(RunStateError):
            await store.complete_task(task_id, result=1)

        task = await store.get_task(task_id)
        assert (task.status, task.result) == (TaskStatus.PENDING, None)
        assert (await store.events_after(0))[-1].kind is EventKind.RUN_FINISHED

    async def test_an_unknown_task_cannot_be_completed(self, store: Store) -> None:
        with pytest.raises(TaskNotFoundError):
            await store.complete_task(uuid.uuid4(), result=1)

    # Queues.

    async def test_a_queue_nobody_declared_is_no_queue(self, store: Store) -> None:
        await self.upload(store)
        run = await self.start(store)
        await store.publish_task(
            run.id,
            WORK,
            queue="nobody",
            handler="tasks:work",
            params={},
            fixed_params={},
        )

        assert await store.queue_kind("nobody") is None
        assert await store.queues() == []
        assert await store.receive_task("nobody", lease_seconds=30) is None

        await store.declare_queue("nobody", QueueKind.WORKER)
        assert await store.receive_task("nobody", lease_seconds=30) is not None

    async def test_a_queue_is_declared_and_declared_again(self, store: Store) -> None:
        await store.declare_queue("editors", QueueKind.EXTERNAL)
        await store.declare_queue("billing", QueueKind.EXTERNAL)
        await store.declare_queue("default", QueueKind.WORKER)
        await store.declare_queue("billing", QueueKind.WORKER)
        await store.declare_queue("editors", QueueKind.EXTERNAL)

        assert await store.queue_kind("editors") is QueueKind.EXTERNAL
        assert await store.queue_kind("billing") is QueueKind.WORKER
        assert await store.queues() == [
            Queue("billing", QueueKind.WORKER),
            Queue("default", QueueKind.WORKER),
            Queue("editors", QueueKind.EXTERNAL),
        ]

    async def test_an_external_queue_hands_no_task_to_a_worker(
        self, store: Store
    ) -> None:
        await self.upload(store)
        run = await self.start(store)
        await store.declare_queue("editors", QueueKind.EXTERNAL)
        external = await self.publish(store, run, Address("one"), queue="editors")
        ordinary = await self.publish(store, run, Address("two"))

        assert await store.receive_task("editors", lease_seconds=30) is None
        received = await store.receive_task("default", lease_seconds=30)
        completed = await store.complete_task(external, result="by a responder")

        assert received is not None and received.id == ordinary
        assert [task.id for task in await store.pending_tasks("editors")] == []
        assert completed.status is TaskStatus.SUCCEEDED

    async def test_a_queue_declared_a_worker_queue_again_hands_its_tasks_out(
        self, store: Store
    ) -> None:
        await self.upload(store)
        run = await self.start(store)
        await store.declare_queue("editors", QueueKind.EXTERNAL)
        task_id = await self.publish(store, run, queue="editors")
        assert await store.receive_task("editors", lease_seconds=30) is None

        await store.declare_queue("editors", QueueKind.WORKER)
        received = await store.receive_task("editors", lease_seconds=30)

        assert received is not None and received.id == task_id

    # Listing and times.

    async def test_runs_are_listed_newest_first_by_page(self, store: Store) -> None:
        await self.upload(store)
        runs = [await self.start(store) for _ in range(5)]
        newest_first = list(reversed(runs))

        everything = await store.list_runs()
        first = await store.list_runs(limit=2)
        second = await store.list_runs(limit=2, before=first[-1].id)
        last = await store.list_runs(limit=2, before=second[-1].id)

        assert everything == newest_first
        assert first + second + last == newest_first
        assert await store.list_runs(before=last[-1].id) == []
        with pytest.raises(RunNotFoundError):
            await store.list_runs(before=uuid.uuid4())

    async def test_runs_are_filtered_by_flow_status_and_depth(
        self, store: Store
    ) -> None:
        root, active, finished = await self.tree(store)
        await self.upload(store, "c")
        other = await self.start(store, "c")
        await store.cancel_run_tree(other.id, "by hand")

        roots = await store.list_runs()
        everything = await store.list_runs(root_only=False)
        of_b = await store.list_runs(flow="b", root_only=False)
        succeeded = await store.list_runs(status=RunStatus.SUCCEEDED, root_only=False)
        cancelled_c = await store.list_runs(flow="c", status=RunStatus.CANCELLED)

        assert {run.id for run in roots} == {root.id, other.id}
        assert {run.id for run in everything} == {
            root.id,
            active.id,
            finished.id,
            other.id,
        }
        assert {run.id for run in of_b} == {active.id, finished.id}
        assert [run.id for run in succeeded] == [finished.id]
        assert [run.id for run in cancelled_c] == [other.id]
        assert await store.list_runs(flow="nobody") == []

    async def test_a_flows_versions_are_listed_newest_first(self, store: Store) -> None:
        await self.upload(store, version="1.0.0")
        await self.upload(store, version="1.2.0", handler="tasks:other")
        await self.upload(store, version="1.10.0")
        await self.upload(store, "b")

        versions = await store.flow_versions("a")

        assert [str(flow.version) for flow in versions] == ["1.10.0", "1.2.0", "1.0.0"]
        assert versions[1].content == _content("a", "1.2.0", "tasks:other")
        with pytest.raises(FlowNotFoundError):
            await store.flow_versions("nobody")

    async def test_a_runs_tasks_are_listed_in_publishing_order(
        self, store: Store
    ) -> None:
        await self.upload(store)
        run = await self.start(store)
        other = await self.start(store)
        published = [
            await self.publish(store, run, Address(name)) for name in ("c", "a", "b")
        ]
        await self.publish(store, other, Address("elsewhere"))
        await store.receive_task("default", lease_seconds=30)

        tasks = await store.run_tasks(run.id)

        assert [task.id for task in tasks] == published
        assert tasks[0].status is TaskStatus.RECEIVED
        assert await store.run_tasks(other.id) != []
        with pytest.raises(RunNotFoundError):
            await store.run_tasks(uuid.uuid4())

    async def test_a_runs_sub_runs_are_its_direct_children(self, store: Store) -> None:
        root, active, finished = await self.tree(store)

        children = await store.sub_runs(root.id)

        # ``active`` started first; ``finished`` is read as it is now, succeeded.
        assert [run.id for run in children] == [active.id, finished.id]
        assert [run.status for run in children] == [
            RunStatus.ACTIVE,
            RunStatus.SUCCEEDED,
        ]
        assert await store.sub_runs(active.id) == []
        with pytest.raises(RunNotFoundError):
            await store.sub_runs(uuid.uuid4())

    async def test_the_store_times_runs_and_tasks(self, store: Store) -> None:
        """Times come from the store's clock, with a zone, and follow the events."""
        await self.upload(store)
        run = await self.start(store)
        task_id = await self.publish(store, run)
        received = await store.receive_task("default", lease_seconds=30)
        claimed = await store.claim_task(task_id)
        finished = await store.finish_task(task_id, result=1)
        succeeded = await store.succeed_run(run.id, None)
        cancelled = await self.start(store)
        await store.cancel_run_tree(cancelled.id, "by hand")

        assert run.created_at is not None and run.finished_at is None
        assert run.created_at.tzinfo is not None
        assert succeeded.finished_at is not None
        assert run.created_at <= succeeded.finished_at
        assert (await store.get_run(run.id)).finished_at == succeeded.finished_at
        assert (await store.get_run(cancelled.id)).finished_at is not None

        published = await store.get_task(task_id)
        assert received is not None
        assert received.created_at is not None and received.started_at is None
        assert run.created_at <= received.created_at
        assert claimed.started_at is not None and claimed.finished_at is None
        assert finished.started_at == claimed.started_at
        assert finished.finished_at is not None
        assert received.created_at <= claimed.started_at <= finished.finished_at
        assert published.finished_at == finished.finished_at

    async def test_a_task_refused_its_start_is_timed_as_finished(
        self, store: Store
    ) -> None:
        await self.upload(store)
        run = await self.start(store)
        task_id = await self.publish(store, run)
        await store.receive_task("default", lease_seconds=30)
        await store.cancel_run_tree(run.id, "by hand")

        with pytest.raises(RunStateError):
            await store.claim_task(task_id)

        task = await store.get_task(task_id)
        assert task.started_at is None and task.finished_at is not None

    # State and events.

    async def test_a_runs_state_holds_its_tasks_and_sub_flow_runs(
        self, store: Store
    ) -> None:
        root, active, _ = await self.tree(store)
        done = await self.publish(store, root, Address("done"))
        failed = await self.publish(store, root, Address("failed"))
        await self.publish(store, root, Address("waiting"))
        for _ in (done, failed):  # oldest first: the two about to finish
            await store.receive_task("default", lease_seconds=30)
        await store.finish_task(done, result=["r"])
        await store.finish_task(failed, error="boom")

        state = await store.run_state(root.id)

        assert state.inputs == {"x": 1}
        assert dict(state.steps) == {
            Address("done"): StepResult(Outcome.SUCCEEDED, ["r"]),
            Address("failed"): StepResult(Outcome.FAILED),
            Address("waiting"): StepResult(Outcome.RUNNING),
            WORK: StepResult(Outcome.RUNNING),
            ELSEWHERE: StepResult(Outcome.SUCCEEDED, 1),
        }
        assert dict((await store.run_state(active.id)).steps) == {}

    async def test_events_are_read_in_order_after_a_sequence(
        self, store: Store
    ) -> None:
        await self.upload(store)
        runs = [await self.start(store) for _ in range(5)]

        everything = await store.events_after(0)
        page = await store.events_after(everything[1].sequence, limit=2)

        assert [e.run_id for e in everything] == [run.id for run in runs]
        sequences = [e.sequence for e in everything]
        assert sequences == sorted(sequences) and len(set(sequences)) == 5
        assert [e.run_id for e in page] == [runs[2].id, runs[3].id]
        assert await store.events_after(everything[-1].sequence) == []

    async def test_the_last_sequence_is_where_a_new_reader_starts(
        self, store: Store
    ) -> None:
        assert await store.last_sequence() == 0
        await self.upload(store)
        runs = [await self.start(store) for _ in range(3)]

        last = await store.last_sequence()
        so_far = await store.events_after(0)
        later = await self.start(store)

        assert last == so_far[-1].sequence
        assert [e.run_id for e in so_far] == [run.id for run in runs]
        assert [e.run_id for e in await store.events_after(last)] == [later.id]
