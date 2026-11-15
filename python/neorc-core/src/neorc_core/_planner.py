# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""What happens next for a run: pure, no I/O.

``plan`` looks at a run's definition and its state and returns the actions that
move it forward. It decides from the state alone, never from what happened
last, so asking twice, or after a redelivered event, plans the same actions;
publishing a task is idempotent by its address, so applying them twice is safe.

The scheduler gathers the input, calls ``plan`` and applies the actions. See
contributing/in-memory-first.md, rule 3.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from neorc_core._errors import ResolutionError
from neorc_core._values import JsonValue
from neorc_core.flows import (
    Address,
    FanOutStep,
    FlowDefinition,
    LoopStep,
    Namespace,
    Outcome,
    Reference,
    RunState,
    Scope,
    Step,
    SubFlowStep,
    TaskStep,
    fan_out_width,
    resolve,
)

_FILLED_IN_LATER = frozenset({"task_id", "flow_run_id", "started_by", "attempts"})
"""Metadata the manager or the worker fills in, not the run's state."""


@dataclass(frozen=True, slots=True)
class PublishTask:
    """Publish the task at ``address``, its inputs still references."""

    address: Address
    queue: str
    handler: str
    params: Mapping[str, Reference]
    fixed_params: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class StartSubRun:
    """Start a run of ``flow``'s latest version as the sub-flow at ``address``."""

    address: Address
    flow: str
    params: Mapping[str, Reference]
    fixed_params: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class SucceedRun:
    """Mark the run succeeded, with ``output`` as its value if the flow has one."""

    output: Reference | None


@dataclass(frozen=True, slots=True)
class FailRun:
    """Mark the run failed, and with it the rest of its run tree."""

    reason: str


Action = PublishTask | StartSubRun | SucceedRun | FailRun


def plan(definition: FlowDefinition, state: RunState) -> list[Action]:
    """The actions that move a run forward from ``state``.

    Every task and sub-flow instance that is ready and not started yet is
    published or started, in file order, branches and iterations in increasing
    order. Once every step has finished, the run succeeds. A run that can go no
    further, because a task or sub-flow run failed or the flow cannot carry on,
    gets a single ``FailRun`` instead of anything else.
    """
    failed = sorted(
        address
        for address, result in state.steps.items()
        if result.outcome is Outcome.FAILED
    )
    if failed:
        return [FailRun(f"{failed[0]} failed")]
    planner = _Planner(definition, state)
    try:
        if all(planner.finished(step, ()) for step in definition.steps):
            return [SucceedRun(definition.output)]
        planner.scope(definition.steps, ())
    except _RunFailed as failure:
        return [FailRun(failure.reason)]
    except ResolutionError as exc:
        return [FailRun(str(exc))]
    return planner.actions


class _RunFailed(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Planner:
    def __init__(self, definition: FlowDefinition, state: RunState) -> None:
        self.definition = definition
        self.state = state
        self.actions: list[Action] = []

    def scope(self, steps: tuple[Step, ...], scope: Scope) -> None:
        """Advance every step of one scope whose dependencies have finished."""
        finished = {step.name: self.finished(step, scope) for step in steps}
        for step in steps:
            if finished[step.name]:
                continue
            if all(finished[name] for name in self.definition.waits_on(step.name)):
                self.advance(step, scope)

    def finished(self, step: Step, scope: Scope) -> bool:
        """Whether a step instance, and everything inside it, has succeeded."""
        if isinstance(step, TaskStep | SubFlowStep):
            result = self.state.get(Address(step.name, scope))
            return result is not None and result.outcome is Outcome.SUCCEEDED
        if isinstance(step, LoopStep):
            last = self.state.last(scope, step.name)
            if last == 0:
                return False
            iteration = (*scope, (step.name, last))
            return self.body_finished(step, iteration) and (
                self.exit_value(step, iteration) is True
            )
        width = fan_out_width(self.definition, self.state, step, scope)
        if width is None:
            return False
        return all(
            self.body_finished(step, (*scope, (step.name, index)))
            for index in range(1, width + 1)
        )

    def body_finished(self, container: LoopStep | FanOutStep, scope: Scope) -> bool:
        return all(self.finished(step, scope) for step in container.steps)

    def exit_value(self, loop: LoopStep, iteration: Scope) -> JsonValue:
        result = self.state.get(Address(loop.exit_condition, iteration))
        assert result is not None and result.outcome is Outcome.SUCCEEDED
        return result.value

    def advance(self, step: Step, scope: Scope) -> None:
        """Move a ready, unfinished step instance forward."""
        if isinstance(step, TaskStep | SubFlowStep):
            self.start(step, Address(step.name, scope))
        elif isinstance(step, LoopStep):
            self.advance_loop(step, scope)
        elif isinstance(step, FanOutStep):
            width = fan_out_width(self.definition, self.state, step, scope)
            for index in range(1, (width or 0) + 1):
                self.scope(step.steps, (*scope, (step.name, index)))

    def advance_loop(self, loop: LoopStep, scope: Scope) -> None:
        """Run the current iteration, or start the next one once it finished."""
        last = self.state.last(scope, loop.name)
        if last == 0:
            self.scope(loop.steps, (*scope, (loop.name, 1)))
            return
        iteration = (*scope, (loop.name, last))
        if not self.body_finished(loop, iteration):
            self.scope(loop.steps, iteration)
            return
        exit_value = self.exit_value(loop, iteration)
        where = Address(loop.exit_condition, iteration)
        if exit_value is not False:
            raise _RunFailed(
                f"{where}: the exit condition of {loop.name} returned "
                f"{exit_value!r}, not true or false"
            )
        if last >= loop.max_cycles:
            raise _RunFailed(
                f"{loop.name}: still not done after max_cycles, {loop.max_cycles} "
                "iterations"
            )
        self.scope(loop.steps, (*scope, (loop.name, last + 1)))

    def start(self, step: TaskStep | SubFlowStep, address: Address) -> None:
        """Publish a task, or start a sub-flow run, once its inputs resolve."""
        if self.state.get(address) is not None:
            return  # already started: running, or finished
        for reference in step.params.values():
            if (
                reference.namespace is Namespace.NEORC
                and reference.name in _FILLED_IN_LATER
            ):
                continue
            if resolve(self.definition, self.state, address, reference) is None:
                return
        if isinstance(step, SubFlowStep):
            self.actions.append(
                StartSubRun(
                    address,
                    flow=step.flow,
                    params=step.params,
                    fixed_params=step.fixed_params,
                )
            )
            return
        self.actions.append(
            PublishTask(
                address,
                queue=step.queue,
                handler=step.handler,
                params=step.params,
                fixed_params=step.fixed_params,
            )
        )
