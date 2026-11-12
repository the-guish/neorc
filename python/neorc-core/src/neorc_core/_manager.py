# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""What the manager does for flows, against the store port.

The manager records and queues; it does not decide what happens next, which is
the scheduler's job. It validates what it is asked to record and rejects what is
invalid.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import TypeVar

from neorc_core import _values
from neorc_core._errors import (
    FlowDefinitionError,
    FlowVersionError,
    InvalidValueError,
    RunStateError,
    TaskNotFoundError,
)
from neorc_core._runs import (
    Event,
    Run,
    RunId,
    RunStatus,
    StoredFlow,
    Task,
    TaskDelivery,
    ensure_active,
)
from neorc_core._task import TaskId
from neorc_core._values import JsonValue
from neorc_core.flows import (
    Address,
    FlowDefinition,
    InputType,
    Namespace,
    Reference,
    RunState,
    SubFlowStep,
    TaskStep,
    Version,
    is_name,
    is_queue_name,
    parse_flow,
    resolve,
)
from neorc_core.flows._validation import check_output
from neorc_core.forms import check_form_inputs
from neorc_core.ports._clients import DEFAULT_LEASE_SECONDS, check_lease_seconds
from neorc_core.ports._store import DEFAULT_PAGE, Store
from neorc_core.ports._task_notifier import TaskNotifier

CANCELLED_BY_HAND = "cancelled by hand"

_START_ATTEMPTS = 3
"""Starts to try while uploads keep replacing a flow's latest version."""

ABANDON_POLL_SECONDS = 0.25
"""How often a waiting ``receive_task`` asks whether its caller has gone."""

_UNSTORED_TASK_ID = uuid.UUID(int=0)
"""Stands in for the id of a task not stored yet: every id encodes as long."""


class Manager:
    """Serves flow uploads and runs, the scheduler's requests, and workers.

    ``tasks`` wakes workers waiting for a task; ``events`` wakes a scheduler
    waiting for events. Both wakeups are hints: a waiter that finds nothing
    waits again.
    """

    def __init__(
        self, store: Store, *, tasks: TaskNotifier, events: TaskNotifier
    ) -> None:
        self._store = store
        self._tasks = tasks
        self._events = events

    # Flows and runs.

    async def upload_flows(self, contents: Sequence[JsonValue]) -> list[bool]:
        """Validate flows deployed together, then store their versions as one.

        ``contents`` are flow files as their JSON structure. The flows that will
        be latest are checked as a set, so a sub-flow may be uploaded with its
        caller or before it. Returns, per flow, whether a new version was
        stored; ``False`` for an identical upload.

        Raises ``FlowDefinitionError`` for an invalid flow or set, and
        ``FlowVersionError`` for a version rule broken; either way nothing is
        stored.
        """
        definitions: list[FlowDefinition] = []
        problems: list[str] = []
        for index, content in enumerate(contents):
            try:
                definitions.append(parse_flow(content))
            except FlowDefinitionError as exc:
                problems.extend(f"flow {index + 1}: {p}" for p in exc.problems)
        if problems:
            raise FlowDefinitionError(problems)

        stored = await self._store.store_flows(
            [
                StoredFlow(definition, content)
                for definition, content in zip(definitions, contents, strict=True)
            ]
        )
        await self._events.notify()  # a new version cancels runs
        return stored

    async def get_flow(self, name: str, version: Version | None = None) -> StoredFlow:
        """A flow version, the latest when ``version`` is ``None``."""
        _lookup(name, "flow name")
        return await self._store.get_flow(name, version)

    async def latest_flows(self) -> list[StoredFlow]:
        """The latest version of every flow."""
        return await self._store.latest_flows()

    async def flow_versions(self, name: str) -> list[StoredFlow]:
        """Every stored version of a flow, newest first."""
        _lookup(name, "flow name")
        return await self._store.flow_versions(name)

    async def list_runs(
        self,
        *,
        flow: str | None = None,
        status: RunStatus | None = None,
        root_only: bool = True,
        before: RunId | None = None,
        limit: int = DEFAULT_PAGE,
    ) -> list[Run]:
        """A page of runs, newest first, as ``Store.list_runs`` gives it."""
        if flow is not None:
            _lookup(flow, "flow name")
        if limit < 1:
            raise InvalidValueError(f"limit is at least 1, not {limit}")
        return await self._store.list_runs(
            flow=flow, status=status, root_only=root_only, before=before, limit=limit
        )

    async def run_tasks(self, run_id: RunId) -> list[Task]:
        """A run's tasks, in publishing order, for a status query."""
        return await self._store.run_tasks(run_id)

    async def sub_runs(self, run_id: RunId) -> list[Run]:
        """A run's direct sub-flow runs, in the order they started."""
        return await self._store.sub_runs(run_id)

    async def start_run(self, flow: str, inputs: Mapping[str, JsonValue]) -> Run:
        """Start a run of ``flow``'s latest version.

        ``inputs`` are in their JSON form, and must be exactly the flow's
        declared inputs, each of its declared type: ``InvalidValueError``
        otherwise. ``FlowNotFoundError`` if there is no such flow.
        """
        _lookup(flow, "flow name")
        run = await self._start_latest(flow, inputs)
        await self._events.notify()
        return run

    async def get_run(self, run_id: RunId) -> Run:
        """A run, for a status query."""
        return await self._store.get_run(run_id)

    async def run_state(self, run_id: RunId) -> RunState:
        """What a run's tasks and sub-flow runs have produced so far."""
        return await self._store.run_state(run_id)

    async def cancel_run(self, run_id: RunId, reason: str = CANCELLED_BY_HAND) -> None:
        """Cancel a run by hand, and with it every active run in its tree.

        Raises ``RunStateError`` if the run has already finished.
        """
        run = await self._store.get_run(run_id)
        if run.status is not RunStatus.ACTIVE:
            raise RunStateError(f"run {run_id} is already {run.status.value}")
        await self._store.cancel_run_tree(run_id, reason)
        await self._events.notify()

    # The scheduler's requests.

    async def fail_run(self, run_id: RunId, reason: str) -> None:
        """Fail every active run in a run's tree; nothing changes if none is."""
        await self._store.fail_run_tree(run_id, reason)
        await self._events.notify()

    async def succeed_run(self, run_id: RunId, output: Reference | None) -> Run:
        """Mark an active run succeeded, with the value of ``output`` if any.

        Raises ``RunStateError`` if the run is not active, or its output is not
        available yet.
        """
        value: JsonValue = None
        if output is not None:
            _, definition, state = await self._context(run_id)
            problems = check_output(definition, output)
            if problems:
                raise InvalidValueError(f"{definition.name}: " + "; ".join(problems))
            resolved = resolve(definition, state, None, output)
            if resolved is None:
                raise RunStateError(f"run {run_id}: {output} is not available yet")
            value = resolved.value
        succeeded = await self._store.succeed_run(run_id, value)
        await self._events.notify()
        return succeeded

    async def publish_task(self, run_id: RunId, address: Address) -> Task:
        """Publish the task at ``address`` in an active run.

        The task's queue, handler and inputs come from the run's version of the
        flow; the inputs are stored as references. Rejected with
        ``PayloadTooLargeError`` if the complete encoded payload a worker would
        receive is over the limit, ``RunStateError`` if the run is not active or
        an input is not available yet, and ``InvalidValueError`` if the flow has
        no task at ``address``. Publishing an address again changes nothing.
        """
        run, definition, state = await self._context(run_id)
        ensure_active(run)
        step = _step_at(definition, address, TaskStep)
        inputs = _inputs(run, definition, state, step, address)
        _values.ensure_fits(_encoded_delivery(inputs))
        task = await self._store.publish_task(
            run_id,
            address,
            queue=step.queue,
            handler=step.handler,
            params=step.params,
            fixed_params=step.fixed_params,
        )
        await self._tasks.notify()
        return task

    async def start_sub_run(self, parent_id: RunId, address: Address) -> Run:
        """Start the sub-flow run at ``address`` in an active parent run.

        It runs the called flow's latest version, with its inputs resolved now
        and checked against that version's declared types. Starting the same
        address again returns the run already started.
        """
        run, definition, state = await self._context(parent_id)
        ensure_active(run)
        step = _step_at(definition, address, SubFlowStep)
        inputs = _inputs(run, definition, state, step, address)
        sub_run = await self._start_latest(
            step.flow, inputs, parent_id=parent_id, parent_address=address
        )
        await self._events.notify()
        return sub_run

    async def wait_for_events(
        self, after: int, *, timeout: float, limit: int = 100
    ) -> list[Event]:
        """Events with a sequence above ``after``, waiting up to ``timeout`` for one.

        Returns an empty list when the wait ends with nothing new.
        """
        deadline = time.monotonic() + timeout
        async with self._events.subscribe() as subscription:
            while True:
                events = await self._store.events_after(after, limit=limit)
                if events:
                    return events
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                await subscription.wait(timeout=remaining)

    async def last_sequence(self) -> int:
        """The latest event's sequence, or 0: where a reader wanting news starts."""
        return await self._store.last_sequence()

    # Workers.

    async def task_definitions(self, queue: str) -> list[TaskStep]:
        """Every task on ``queue`` in the latest flows, for a worker to check.

        One task step per flow that defines it, flows in name order.
        """
        _queue(queue)
        return [
            task
            for flow in await self._store.latest_flows()
            for task in flow.definition.tasks()
            if task.queue == queue
        ]

    async def receive_task(
        self,
        queue: str,
        *,
        timeout: float,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        abandoned: Callable[[], Awaitable[bool]] | None = None,
    ) -> TaskDelivery | None:
        """Lease the next task on ``queue``, waiting up to ``timeout`` for one.

        The delivery carries the task's inputs with every reference filled in,
        and the metadata known at publish time.

        ``abandoned`` says whether the caller has gone away meanwhile: a server
        does not end a handler when its client leaves. It is asked every
        ``ABANDON_POLL_SECONDS`` of the wait and before every receive, and the
        wait ends with ``None`` once it says so, so a task is received only for
        a worker known to be there a moment before. A worker lost between that
        moment and its reply keeps the lease until it lapses.
        """
        _queue(queue)
        check_lease_seconds(lease_seconds)
        deadline = time.monotonic() + timeout
        async with self._tasks.subscribe() as subscription:
            while True:
                if abandoned is not None and await abandoned():
                    return None
                task = await self._store.receive_task(
                    queue, lease_seconds=lease_seconds
                )
                if task is not None:
                    return await self._delivery(task)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                    if abandoned is None:
                        await subscription.wait(timeout=remaining)
                        break
                    slice_ = min(remaining, ABANDON_POLL_SECONDS)
                    if await subscription.wait(timeout=slice_):
                        break  # woken: receive again
                    if await abandoned():
                        return None

    # A worker's claim, heartbeat and report name the queue its token is bound
    # to, when it has one: a task on another queue is not found, as an id that
    # does not exist, so a token learns nothing about other queues' tasks.

    async def claim_task(self, task_id: TaskId, *, queue: str | None = None) -> None:
        """Record that a worker claimed a task and is starting it.

        Raises ``RunStateError`` if the task's run is no longer active: the
        worker drops the task, and it is never handed out again.
        """
        await self._ensure_on_queue(task_id, queue)
        await self._store.claim_task(task_id)

    async def extend_lease(
        self,
        task_id: TaskId,
        *,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        queue: str | None = None,
    ) -> datetime:
        """Take a worker's heartbeat; return the lease's new expiry."""
        check_lease_seconds(lease_seconds)
        await self._ensure_on_queue(task_id, queue)
        return await self._store.extend_task_lease(task_id, lease_seconds=lease_seconds)

    async def report_finished(
        self,
        task_id: TaskId,
        *,
        result: JsonValue = None,
        error: str | None = None,
        queue: str | None = None,
    ) -> Task:
        """Record a task's result, or its failure when ``error`` is set.

        A result that is not a valid value, or is over the size limit, fails the
        task instead, with the reason as its error.
        """
        await self._ensure_on_queue(task_id, queue)
        if error is None:
            try:
                result = _values.encode(_values.decode(result))
                _values.ensure_fits(_values.dumps_json(result))
            except InvalidValueError as exc:
                result, error = None, f"invalid result: {exc}"
        finished = await self._store.finish_task(task_id, result=result, error=error)
        await self._events.notify()
        return finished

    async def get_task(self, task_id: TaskId) -> Task:
        """A task, for a status query."""
        return await self._store.get_task(task_id)

    async def _start_latest(
        self,
        flow: str,
        inputs: Mapping[str, JsonValue],
        *,
        parent_id: RunId | None = None,
        parent_address: Address | None = None,
    ) -> Run:
        """Start a run on the latest version, checking inputs against that version.

        A version uploaded between reading the latest and starting the run is
        not a reason to refuse: check against the new latest and start again.
        """
        for attempt in range(_START_ATTEMPTS):
            stored = await self._store.get_flow(flow)
            check_inputs(stored.definition, inputs)
            check_form_inputs(flow, stored.content, inputs)
            try:
                return await self._store.start_run(
                    flow,
                    stored.version,
                    inputs,
                    parent_id=parent_id,
                    parent_address=parent_address,
                )
            except FlowVersionError:
                if attempt == _START_ATTEMPTS - 1:
                    raise
        raise AssertionError("unreachable")

    async def _context(self, run_id: RunId) -> tuple[Run, FlowDefinition, RunState]:
        run = await self._store.get_run(run_id)
        definition = (await self._store.get_flow(run.flow, run.version)).definition
        return run, definition, await self._store.run_state(run_id)

    async def _ensure_on_queue(self, task_id: TaskId, queue: str | None) -> None:
        """Raise ``TaskNotFoundError`` for a task that is not on ``queue``, if named.

        A task's queue never changes, so the answer holds for the write after.
        """
        if queue is not None and (await self._store.get_task(task_id)).queue != queue:
            raise TaskNotFoundError(str(task_id))

    async def _delivery(self, task: Task) -> TaskDelivery:
        run, definition, state = await self._context(task.run_id)
        inputs = _filled_in(
            run, definition, state, task.address, task.params, task_id=task.id
        )
        return TaskDelivery(task, {**task.fixed_params, **inputs})


def _queue(name: str) -> None:
    """Refuse what cannot name a queue, before a store or a URL sees it."""
    if not is_queue_name(name):
        raise InvalidValueError(
            f"{name!r} is not a queue name: letters, digits, _ and -"
        )


def _lookup(name: str, what: str) -> None:
    """Refuse what cannot name a flow, before a store or a URL sees it.

    A deployed store would refuse NUL with an error of its own, and a URL would
    read a slash or a dot segment as part of the route, where the in-memory
    store would quietly find nothing. Every client is refused alike instead.
    """
    if not is_name(name):
        raise InvalidValueError(f"{name!r} is not a {what}: letters, digits and _")


def check_inputs(definition: FlowDefinition, inputs: Mapping[str, JsonValue]) -> None:
    """Raise ``InvalidValueError`` unless ``inputs`` match the flow's declared inputs.

    Inputs are in their JSON form, so a datetime is a ``$datetime`` tag.
    """
    problems = [
        f"missing input {name!r}" for name in definition.inputs if name not in inputs
    ]
    for name, value in inputs.items():
        declared = definition.inputs.get(name)
        if declared is None:
            problems.append(f"{definition.name} has no input {name!r}")
        elif not _is_of_type(value, declared):
            problems.append(f"input {name!r} is not a {declared.value}: {value!r}")
        else:
            try:  # a value of the right type may still be one no store holds
                _values.decode(value, path=f"input {name!r}")
            except InvalidValueError as exc:
                problems.append(str(exc))
    if problems:
        raise InvalidValueError(f"{definition.name}: " + "; ".join(problems))


def _is_of_type(value: JsonValue, declared: InputType) -> bool:
    if declared is InputType.STRING:
        return isinstance(value, str)
    if declared is InputType.BOOLEAN:
        return isinstance(value, bool)
    if declared is InputType.NUMBER:
        return isinstance(value, int | float) and not isinstance(value, bool)
    try:
        return isinstance(_values.decode(value), datetime)
    except InvalidValueError:
        return False


_S = TypeVar("_S", TaskStep, SubFlowStep)


def _step_at(definition: FlowDefinition, address: Address, kind: type[_S]) -> _S:
    """The step of ``kind`` at ``address``, or ``InvalidValueError``."""
    try:
        step = definition.step(address.step)
    except KeyError:
        step = None
    if not isinstance(step, kind):
        raise InvalidValueError(
            f"{definition.name} has no {kind.__name__} called {address.step!r}"
        )
    around = [container.name for container in definition.enclosing(address.step)]
    if [name for name, _ in address.scope] != around:
        raise InvalidValueError(f"{address} is not where {address.step} is")
    if any(number < 1 for _, number in address.scope):
        # Iterations and indexes count from 1; a lower number would pick a
        # fan-out item from the wrong end, and name an instance that is not.
        raise InvalidValueError(f"{address}: iterations and indexes start at 1")
    return step


def _inputs(
    run: Run,
    definition: FlowDefinition,
    state: RunState,
    step: TaskStep | SubFlowStep,
    address: Address,
) -> dict[str, JsonValue]:
    """A step's fixed params and its params filled in; ``RunStateError`` if not yet.

    Before a task is stored it has no id: ``neorc.task_id`` is filled in with a
    stand-in of the same length, which is all the size check needs.
    """
    filled = _filled_in(
        run, definition, state, address, step.params, task_id=_UNSTORED_TASK_ID
    )
    return {**step.fixed_params, **filled}


def _filled_in(
    run: Run,
    definition: FlowDefinition,
    state: RunState,
    address: Address,
    params: Mapping[str, Reference],
    *,
    task_id: TaskId,
) -> dict[str, JsonValue]:
    """Params with every reference resolved, except ``neorc.attempts``."""
    values: dict[str, JsonValue] = {}
    for name, reference in params.items():
        if reference.namespace is Namespace.NEORC:
            if reference.name == "attempts":
                continue  # the worker's to fill in
            if reference.name == "task_id":
                values[name] = str(task_id)
                continue
            if reference.name == "flow_run_id":
                values[name] = str(run.id)
                continue
        resolved = resolve(definition, state, address, reference)
        if resolved is None:
            raise RunStateError(f"{address}: {reference} is not available yet")
        values[name] = resolved.value
    return values


def _encoded_delivery(inputs: Mapping[str, JsonValue]) -> str:
    """Roughly what a worker receives, for the size check: id, attempts, inputs."""
    return _values.dumps_json(
        {"task_id": str(_UNSTORED_TASK_ID), "attempts": 1, "inputs": dict(inputs)}
    )
