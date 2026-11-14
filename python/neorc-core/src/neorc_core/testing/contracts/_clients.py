# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""What every ``ManagerClient`` and ``QueueClient`` must do."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from neorc_core._errors import (
    FlowDefinitionError,
    FlowNotFoundError,
    FlowVersionError,
    InvalidValueError,
    PayloadTooLargeError,
    PermissionDeniedError,
    ResolutionError,
    RunNotFoundError,
    RunStateError,
    TaskNotFoundError,
    TaskStateError,
)
from neorc_core._runs import EventKind, RunStatus, TaskDelivery
from neorc_core._values import MAX_PAYLOAD_BYTES, MAX_VALUE_DEPTH, JsonValue
from neorc_core._worker import Worker
from neorc_core.flows import (
    Address,
    Outcome,
    Reference,
    StepResult,
    Version,
    parse_flow,
)
from neorc_core.ports._clients import (
    MAX_LEASE_SECONDS,
    ManagerClient,
    QueueClient,
)

WHEN: JsonValue = {"$datetime": "2026-09-13T10:00:00+00:00"}

MAIN: JsonValue = {
    "name": "main",
    "version": "1.0.0",
    "inputs": {"word": "string", "when": "datetime"},
    "output": "flows.call",
    "steps": {
        "work": {
            "handler": "tasks:work",
            "params": {"word": "inputs.word", "when": "inputs.when"},
            "fixed_params": {"n": 2},
        },
        "call": {"flow": "echo", "params": {"text": "tasks.work"}},
    },
}

ECHO: JsonValue = {
    "name": "echo",
    "version": "1.0.0",
    "inputs": {"text": "string"},
    "output": "tasks.say",
    "steps": {
        "say": {
            "queue": "voice",
            "handler": "tasks:say",
            "params": {"t": "inputs.text"},
        }
    },
}


def _nested(depth: int) -> JsonValue:
    value: JsonValue = []
    for _ in range(depth - 1):
        value = [value]
    return value


INPUTS: dict[str, JsonValue] = {"word": "red", "when": WHEN}
WORK = Address("work")


class _Clients:
    """Both client fixtures, on one manager holding nothing."""

    @pytest.fixture
    def manager_client(self) -> ManagerClient:
        raise NotImplementedError("the subclass provides `manager_client`")

    @pytest.fixture
    def queue_client(self) -> QueueClient:
        raise NotImplementedError("the subclass provides `queue_client`")

    async def started(self, manager_client: ManagerClient) -> uuid.UUID:
        await manager_client.upload_flows([MAIN, ECHO])
        run = await manager_client.start_run("main", INPUTS)
        return run.id

    async def take(
        self, queue_client: QueueClient, queue: str = "default"
    ) -> TaskDelivery:
        delivery = await queue_client.receive_task(queue, timeout=1)
        assert delivery is not None
        return delivery


class ManagerClientContract(_Clients):
    """Subclass and provide ``manager_client`` and ``queue_client`` on one manager."""

    async def test_flows_are_uploaded_under_the_version_rules(
        self, manager_client: ManagerClient
    ) -> None:
        assert await manager_client.upload_flows([MAIN, ECHO]) == [True, True]
        assert await manager_client.upload_flows([ECHO]) == [False]
        assert isinstance(ECHO, dict)
        changed = {**ECHO, "output": None}
        del changed["output"]

        with pytest.raises(FlowVersionError):
            await manager_client.upload_flows([changed])
        with pytest.raises(FlowDefinitionError):
            await manager_client.upload_flows([{"name": "broken"}])

    async def test_a_flow_definition_reads_back_as_uploaded(
        self, manager_client: ManagerClient
    ) -> None:
        await manager_client.upload_flows([MAIN, ECHO])

        assert await manager_client.get_flow("main") == parse_flow(MAIN)
        assert await manager_client.get_flow("echo", Version(1, 0, 0)) == parse_flow(
            ECHO
        )
        with pytest.raises(FlowNotFoundError):
            await manager_client.get_flow("nothing")

    async def test_a_run_starts_and_reads_back(
        self, manager_client: ManagerClient
    ) -> None:
        run_id = await self.started(manager_client)

        run = await manager_client.get_run(run_id)

        assert run.flow == "main"
        assert run.version == Version(1, 0, 0)
        assert run.inputs == INPUTS
        assert run.status is RunStatus.ACTIVE
        assert run.parent_id is None
        with pytest.raises(RunNotFoundError):
            await manager_client.get_run(uuid.uuid4())

    async def test_inputs_that_are_not_json_or_not_declared_are_refused(
        self, manager_client: ManagerClient
    ) -> None:
        await manager_client.upload_flows([MAIN, ECHO])

        with pytest.raises(InvalidValueError):
            await manager_client.start_run(
                "main",
                {"word": "red", "when": datetime.now(UTC)},  # type: ignore[dict-item]
            )
        with pytest.raises(InvalidValueError):
            await manager_client.start_run("main", {"word": "red"})

    async def test_text_no_store_can_hold_is_refused(
        self, manager_client: ManagerClient
    ) -> None:
        """NUL in an input or a flow: refused here, as the deployed store would."""
        await manager_client.upload_flows([MAIN, ECHO])
        assert isinstance(ECHO, dict)

        with pytest.raises(InvalidValueError, match="NUL"):
            await manager_client.start_run("main", {"word": "re\x00d", "when": WHEN})
        with pytest.raises(FlowDefinitionError, match="NUL"):
            await manager_client.upload_flows(
                [{**ECHO, "version": "1.1.0", "output": "tasks.say\x00"}]
            )
        assert (await manager_client.get_flow("echo")).version == Version(1, 0, 0)
        # A name to look up, too: a deployed store cannot even ask for NUL,
        # and a URL would read a slash or a dot segment as part of the route.
        for name in ("echo\x00", "echo/x", "..", ""):
            with pytest.raises(InvalidValueError, match="flow name"):
                await manager_client.get_flow(name)
            with pytest.raises(InvalidValueError, match="flow name"):
                await manager_client.start_run(name, INPUTS)

    async def test_events_are_waited_for(self, manager_client: ManagerClient) -> None:
        assert await manager_client.wait_for_events(0, timeout=0) == []
        run_id = await self.started(manager_client)

        (event,) = await manager_client.wait_for_events(0, timeout=1)

        assert (event.run_id, event.kind) == (run_id, EventKind.RUN_STARTED)
        assert await manager_client.wait_for_events(event.sequence, timeout=0.05) == []

    async def test_a_run_is_driven_to_its_end(
        self, manager_client: ManagerClient, queue_client: QueueClient
    ) -> None:
        run_id = await self.started(manager_client)

        await manager_client.publish_task(run_id, WORK)
        work = await self.take(queue_client)
        await queue_client.report_finished(work.task.id, result="hello")
        await manager_client.start_sub_run(run_id, Address("call"))
        sub_run_id = next(
            e.run_id
            for e in await manager_client.wait_for_events(0, timeout=1, limit=100)
            if e.run_id != run_id
        )
        await manager_client.publish_task(sub_run_id, Address("say"))
        say = await self.take(queue_client, "voice")
        await queue_client.report_finished(say.task.id, result="hello!")
        await manager_client.succeed_run(sub_run_id, Reference.parse("tasks.say"))
        await manager_client.succeed_run(run_id, Reference.parse("flows.call"))

        state = await manager_client.run_state(run_id)
        run = await manager_client.get_run(run_id)
        assert state.inputs == INPUTS
        assert state.steps[WORK].outcome is Outcome.SUCCEEDED
        assert state.steps[WORK].value == "hello"
        assert state.steps[Address("call")].value == "hello!"
        assert run.status is RunStatus.SUCCEEDED
        assert run.output == "hello!"

    async def test_requests_the_manager_rejects_are_rejected(
        self, manager_client: ManagerClient
    ) -> None:
        run_id = await self.started(manager_client)

        with pytest.raises(InvalidValueError):
            await manager_client.publish_task(run_id, Address("call"))  # not a task
        with pytest.raises(RunStateError):
            await manager_client.start_sub_run(run_id, Address("call"))  # no input yet
        with pytest.raises(RunStateError):
            await manager_client.succeed_run(run_id, Reference.parse("flows.call"))
        for output in ("tasks.nothing", "inputs.word", "neorc.task_id", "tasks.call"):
            with pytest.raises(InvalidValueError):  # parses, names no task output
                await manager_client.succeed_run(run_id, Reference.parse(output))

    async def test_a_payload_over_the_limit_is_refused(
        self, manager_client: ManagerClient
    ) -> None:
        await manager_client.upload_flows([MAIN, ECHO])
        run = await manager_client.start_run(
            "main", {"word": "x" * MAX_PAYLOAD_BYTES, "when": WHEN}
        )

        with pytest.raises(PayloadTooLargeError):
            await manager_client.publish_task(run.id, WORK)

    async def test_a_fan_out_over_something_not_a_list_cannot_publish(
        self, manager_client: ManagerClient, queue_client: QueueClient
    ) -> None:
        """The scheduler fails the run on ``ResolutionError``, so it must cross."""
        fanned: JsonValue = {
            "name": "fanned",
            "version": "1.0.0",
            "steps": {
                "items": {"handler": "tasks:items"},
                "each": {
                    "fan_out": {"over": "tasks.items"},
                    "steps": {
                        "one": {"handler": "tasks:one", "params": {"x": "neorc.item"}}
                    },
                },
            },
        }
        await manager_client.upload_flows([fanned])
        run = await manager_client.start_run("fanned", {})
        await manager_client.publish_task(run.id, Address("items"))
        items = await self.take(queue_client)
        await queue_client.report_finished(items.task.id, result="not a list")

        with pytest.raises(ResolutionError):
            await manager_client.publish_task(run.id, Address("one", (("each", 1),)))

    async def test_failing_and_cancelling_end_a_run(
        self, manager_client: ManagerClient
    ) -> None:
        failed = await self.started(manager_client)
        cancelled = (await manager_client.start_run("main", INPUTS)).id

        await manager_client.fail_run(failed, "a task failed")
        await manager_client.cancel_run(cancelled)

        assert (await manager_client.get_run(failed)).status is RunStatus.FAILED
        assert (await manager_client.get_run(failed)).reason == "a task failed"
        assert (await manager_client.get_run(cancelled)).status is RunStatus.CANCELLED
        with pytest.raises(RunStateError):
            await manager_client.cancel_run(cancelled)
        with pytest.raises(RunStateError):
            await manager_client.publish_task(failed, WORK)


class QueueClientContract(_Clients):
    """Subclass and provide ``manager_client`` and ``queue_client`` on one manager.

    The manager has the worker queues ``default`` and ``voice`` declared.
    """

    async def test_a_queues_task_definitions_are_listed(
        self, manager_client: ManagerClient, queue_client: QueueClient
    ) -> None:
        await manager_client.upload_flows([MAIN, ECHO])

        (work,) = await queue_client.task_definitions("default")
        (say,) = await queue_client.task_definitions("voice")

        assert (work.name, work.handler, work.queue) == (
            "work",
            "tasks:work",
            "default",
        )
        assert work.params == {
            "word": Reference.parse("inputs.word"),
            "when": Reference.parse("inputs.when"),
        }
        assert work.fixed_params == {"n": 2}
        assert say.handler == "tasks:say"
        # A queue nobody declared serves no worker; the refusal ends a worker.
        with pytest.raises(PermissionDeniedError, match="'nobody' is not declared"):
            await queue_client.task_definitions("nobody")
        with pytest.raises(PermissionDeniedError, match="'nobody' is not declared"):
            await queue_client.receive_task("nobody", timeout=0)
        # What cannot name a queue is refused, not looked up: a slash or a
        # dot could not travel in a URL path, and NUL in no store.
        for queue in ("voice\x00", "gpu/large", ".."):
            with pytest.raises(InvalidValueError, match="queue name"):
                await queue_client.task_definitions(queue)
            with pytest.raises(InvalidValueError, match="queue name"):
                await queue_client.receive_task(queue, timeout=0)

    async def test_a_delivery_carries_the_filled_in_inputs(
        self, manager_client: ManagerClient, queue_client: QueueClient
    ) -> None:
        run_id = await self.started(manager_client)
        await manager_client.publish_task(run_id, WORK)

        delivery = await self.take(queue_client)

        assert delivery.inputs == {"word": "red", "when": WHEN, "n": 2}
        assert delivery.task.run_id == run_id
        assert delivery.task.address == WORK
        assert delivery.task.handler == "tasks:work"
        assert delivery.task.attempts == 1
        assert await queue_client.receive_task("default", timeout=0) is None

    async def test_a_waiting_worker_gets_the_task_when_it_is_published(
        self, manager_client: ManagerClient, queue_client: QueueClient
    ) -> None:
        run_id = await self.started(manager_client)

        async def publish_shortly() -> None:
            await asyncio.sleep(0.05)
            await manager_client.publish_task(run_id, WORK)

        loop = asyncio.get_running_loop()
        begun = loop.time()
        async with asyncio.TaskGroup() as group:
            group.create_task(publish_shortly())
            delivery = await queue_client.receive_task("default", timeout=5)

        assert delivery is not None
        assert loop.time() - begun < 2

    async def test_a_task_is_claimed_heartbeated_and_finished(
        self, manager_client: ManagerClient, queue_client: QueueClient
    ) -> None:
        run_id = await self.started(manager_client)
        await manager_client.publish_task(run_id, WORK)
        delivery = await self.take(queue_client)

        await queue_client.claim_task(delivery.task.id)
        expires_at = await queue_client.extend_lease(delivery.task.id, lease_seconds=30)
        await queue_client.report_finished(delivery.task.id, result={"when": WHEN})

        assert expires_at > datetime.now(UTC)
        state = await manager_client.run_state(run_id)
        assert state.steps[WORK].value == {"when": WHEN}
        with pytest.raises(TaskStateError):
            await queue_client.extend_lease(delivery.task.id)

    @pytest.mark.parametrize(
        "lease_seconds",
        [0, -1, float("inf"), float("nan"), MAX_LEASE_SECONDS + 1],
        ids=["zero", "negative", "infinite", "nan", "over-the-bound"],
    )
    async def test_a_lease_no_store_could_grant_is_refused(
        self,
        manager_client: ManagerClient,
        queue_client: QueueClient,
        lease_seconds: float,
    ) -> None:
        run_id = await self.started(manager_client)
        await manager_client.publish_task(run_id, WORK)

        # Refused by the manager, or by the client's own transit first: JSON
        # cannot carry an infinity either way.
        with pytest.raises(InvalidValueError):
            await queue_client.receive_task(
                "default", timeout=0, lease_seconds=lease_seconds
            )
        delivery = await self.take(queue_client)
        with pytest.raises(InvalidValueError):
            await queue_client.extend_lease(
                delivery.task.id, lease_seconds=lease_seconds
            )

    async def test_a_failure_is_reported(
        self, manager_client: ManagerClient, queue_client: QueueClient
    ) -> None:
        run_id = await self.started(manager_client)
        await manager_client.publish_task(run_id, WORK)
        delivery = await self.take(queue_client)

        await queue_client.report_finished(delivery.task.id, error="ValueError: no")

        state = await manager_client.run_state(run_id)
        assert state.steps[WORK].outcome is Outcome.FAILED

    async def test_a_result_json_cannot_carry_is_refused(
        self, manager_client: ManagerClient, queue_client: QueueClient
    ) -> None:
        run_id = await self.started(manager_client)
        await manager_client.publish_task(run_id, WORK)
        delivery = await self.take(queue_client)

        with pytest.raises(InvalidValueError):
            await queue_client.report_finished(
                delivery.task.id,
                result={"words": {"red", "blue"}},  # type: ignore[dict-item]
            )

    @pytest.mark.parametrize(
        "result",
        ["\ud800", _nested(5000), _nested(201)],
        ids=["lone-surrogate", "past-recursion", "past-depth-limit"],
    )
    async def test_a_result_that_cannot_travel_is_refused(
        self,
        manager_client: ManagerClient,
        queue_client: QueueClient,
        result: JsonValue,
    ) -> None:
        run_id = await self.started(manager_client)
        await manager_client.publish_task(run_id, WORK)
        delivery = await self.take(queue_client)

        with pytest.raises(InvalidValueError):
            await queue_client.report_finished(delivery.task.id, result=result)

    async def test_a_result_holding_nul_fails_the_task(
        self, manager_client: ManagerClient, queue_client: QueueClient
    ) -> None:
        """JSON carries NUL, so it reaches the manager, where no store may take it."""
        run_id = await self.started(manager_client)
        await manager_client.publish_task(run_id, WORK)
        delivery = await self.take(queue_client)

        await queue_client.report_finished(delivery.task.id, result=["ok", "\x00"])

        state = await manager_client.run_state(run_id)
        assert state.steps[WORK].outcome is Outcome.FAILED

    async def test_an_error_holding_nul_is_taken(
        self, manager_client: ManagerClient, queue_client: QueueClient
    ) -> None:
        """A message is not a value: it is stored, NUL replaced, and fails the task."""
        run_id = await self.started(manager_client)
        await manager_client.publish_task(run_id, WORK)
        delivery = await self.take(queue_client)

        await queue_client.report_finished(delivery.task.id, error="bad\x00byte")

        state = await manager_client.run_state(run_id)
        assert state.steps[WORK].outcome is Outcome.FAILED

    async def test_a_task_of_a_cancelled_run_is_refused_its_claim(
        self, manager_client: ManagerClient, queue_client: QueueClient
    ) -> None:
        run_id = await self.started(manager_client)
        await manager_client.publish_task(run_id, WORK)
        delivery = await self.take(queue_client)
        await manager_client.cancel_run(run_id)

        with pytest.raises(RunStateError):
            await queue_client.claim_task(delivery.task.id)

    async def test_a_lapsed_lease_hands_the_task_on(
        self, manager_client: ManagerClient, queue_client: QueueClient
    ) -> None:
        run_id = await self.started(manager_client)
        await manager_client.publish_task(run_id, WORK)
        first = await queue_client.receive_task(
            "default", timeout=1, lease_seconds=0.05
        )
        assert first is not None

        await asyncio.sleep(0.1)
        second = await self.take(queue_client)

        assert second.task.id == first.task.id
        assert second.task.attempts == 2

    async def test_a_workers_over_limit_result_fails_its_task_once(
        self,
        manager_client: ManagerClient,
        queue_client: QueueClient,
        tmp_path: Path,
    ) -> None:
        """The worker fails it before reporting: a transport might refuse the
        report first, and the task would run again on every lease."""
        module = f"handlers_{uuid.uuid4().hex}"
        (tmp_path / f"{module}.py").write_text(
            f"def big():\n    return 'x' * ({MAX_PAYLOAD_BYTES} + 1)\n"
        )
        content: JsonValue = {
            "name": "big",
            "version": "1.0.0",
            "steps": {"work": {"handler": f"{module}:big"}},
        }
        await manager_client.upload_flows([content])
        run = await manager_client.start_run("big", {})
        await manager_client.publish_task(run.id, WORK)
        worker = Worker(queue_client, code_location=tmp_path, poll_timeout=1)

        delivery = await worker.run_once()

        assert delivery is not None
        state = await manager_client.run_state(run.id)
        assert state.steps[WORK].outcome is Outcome.FAILED
        assert await queue_client.receive_task("default", timeout=0) is None

    async def test_a_result_at_the_depth_limit_can_be_read_by_the_next_task(
        self,
        manager_client: ManagerClient,
        queue_client: QueueClient,
        tmp_path: Path,
    ) -> None:
        """What a worker was allowed to return, the next worker can be given."""
        module = f"handlers_{uuid.uuid4().hex}"
        (tmp_path / f"{module}.py").write_text(
            "def deep():\n"
            "    value = []\n"
            f"    for _ in range({MAX_VALUE_DEPTH} - 1):\n"
            "        value = [value]\n"
            "    return value\n"
            "def count(x):\n"
            "    return len(x)\n"
        )
        content: JsonValue = {
            "name": "deep",
            "version": "1.0.0",
            "steps": {
                "a": {"handler": f"{module}:deep"},
                "b": {"handler": f"{module}:count", "params": {"x": "tasks.a"}},
            },
        }
        await manager_client.upload_flows([content])
        run = await manager_client.start_run("deep", {})
        worker = Worker(queue_client, code_location=tmp_path, poll_timeout=1)

        await manager_client.publish_task(run.id, Address("a"))
        assert await worker.run_once() is not None
        await manager_client.publish_task(run.id, Address("b"))
        assert await worker.run_once() is not None

        state = await manager_client.run_state(run.id)
        assert state.steps[Address("a")].outcome is Outcome.SUCCEEDED
        assert state.steps[Address("b")] == StepResult(Outcome.SUCCEEDED, 1)

    async def test_an_unknown_task_is_reported_as_missing(
        self, queue_client: QueueClient
    ) -> None:
        with pytest.raises(TaskNotFoundError):
            await queue_client.claim_task(uuid.uuid4())
