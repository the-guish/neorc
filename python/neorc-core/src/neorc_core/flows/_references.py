# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""What a reference resolves to, for a consumer at a given place in a run.

The "Resolving references" table of docs/specs/flows.md, as functions. Every
loop or fan-out around the referenced step is, relative to the consumer, one of:

- a fan-out the consumer is in the same branch of: no list, that branch's value
- a fan-out the consumer is outside of: a list of every branch, by index
- a loop the consumer is inside of: a list of the iterations so far, current last
- a loop the consumer comes after: a list of every iteration

Each list level nests inside the previous one, outermost container first.

Pure code: a definition and a ``RunState`` in, a value out.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from neorc_core._errors import ResolutionError
from neorc_core._values import JsonValue
from neorc_core.flows._definition import (
    Container,
    FanOutStep,
    FlowDefinition,
    LoopStep,
    Namespace,
    Reference,
)
from neorc_core.flows._run_state import Address, Outcome, RunState, Scope


class Level(StrEnum):
    """One list level a reference's value arrives in."""

    ITERATIONS_SO_FAR = "iterations_so_far"
    """A loop the consumer is inside of: iterations up to its own, in order."""
    ITERATIONS = "iterations"
    """A loop the consumer comes after: every iteration, in order."""
    BRANCHES = "branches"
    """A fan-out the consumer is outside of: every branch, by index."""


@dataclass(frozen=True, slots=True)
class Resolved:
    """A reference's value, available to its consumer."""

    value: JsonValue


def shape(
    definition: FlowDefinition, consumer: str | None, reference: Reference
) -> tuple[Level, ...]:
    """The list levels ``reference`` arrives in for step ``consumer``, outermost first.

    ``consumer`` is ``None`` for the flow's output. Inputs and metadata are
    always single values.
    """
    if reference.namespace not in (Namespace.TASKS, Namespace.FLOWS):
        return ()
    around = definition.enclosing(consumer) if consumer is not None else ()
    levels = _levels(around, definition.enclosing(reference.name))
    return tuple(level for _, level in levels if level is not None)


def resolve(
    definition: FlowDefinition,
    state: RunState,
    consumer: Address | None,
    reference: Reference,
) -> Resolved | None:
    """The value of ``reference`` for the step instance at ``consumer``.

    ``consumer`` is ``None`` for the flow's output. Returns ``None`` while the
    value is not available: an instance it needs has not succeeded yet, or a
    loop it needs has not finished. A failed instance stays unavailable; noticing
    the failure is the planner's job.

    ``neorc.task_id``, ``neorc.flow_run_id``, ``neorc.started_by`` and
    ``neorc.attempts`` are not in a run's state: the manager and the worker
    fill them in, and asking for them here is a ``ValueError``. So is a
    ``consumer`` whose scope does not match where its step is in ``definition``.

    Raises ``ResolutionError`` when a fan-out's ``over`` value, needed on the
    way, is not a list.
    """
    scope = consumer.scope if consumer is not None else ()
    around = definition.enclosing(consumer.step) if consumer is not None else ()
    if [name for name, _ in scope] != [container.name for container in around]:
        raise ValueError(f"{consumer} does not match where its step is in the flow")

    if reference.namespace is Namespace.INPUTS:
        return Resolved(state.inputs[reference.name])
    if reference.namespace is Namespace.NEORC:
        return _metadata(definition, state, consumer, around, reference)

    target = reference.name
    levels = _levels(around, definition.enclosing(target))
    return _Collector(definition, state, target, levels, dict(scope)).collect(0, ())


def _levels(
    around: tuple[Container, ...], target_around: tuple[Container, ...]
) -> list[tuple[Container, Level | None]]:
    """Each container around the target with its level, ``None`` in a shared branch."""
    shared = 0
    while (
        shared < min(len(around), len(target_around))
        and around[shared].name == target_around[shared].name
    ):
        shared += 1
    levels: list[tuple[Container, Level | None]] = []
    for depth, container in enumerate(target_around):
        if isinstance(container, LoopStep):
            inside = depth < shared
            levels.append(
                (container, Level.ITERATIONS_SO_FAR if inside else Level.ITERATIONS)
            )
        else:
            levels.append((container, None if depth < shared else Level.BRANCHES))
    return levels


@dataclass(frozen=True, slots=True)
class _Collector:
    """Walks the containers around a target, gathering its instances' values."""

    definition: FlowDefinition
    state: RunState
    target: str
    levels: list[tuple[Container, Level | None]]
    consumer_scope: dict[str, int]

    def on_path(self, scope: Scope) -> bool:
        """Whether ``scope`` is the start of the consumer's own scope."""
        return all(self.consumer_scope.get(name) == n for name, n in scope)

    def collect(self, depth: int, scope: Scope) -> Resolved | None:
        if depth == len(self.levels):
            result = self.state.get(Address(self.target, scope))
            if result is None or result.outcome is not Outcome.SUCCEEDED:
                return None
            return Resolved(result.value)

        container, level = self.levels[depth]
        if level is None:
            number = self.consumer_scope[container.name]
            # The consumer's own branch, in every iteration so far of a loop
            # around both. A fan-out over a list can be narrower in an earlier
            # iteration, and a branch that never existed would never succeed.
            if isinstance(container, FanOutStep) and container.over is not None:
                width = fan_out_width(self.definition, self.state, container, scope)
                if width is not None and number > width:
                    missing = Address(self.target, (*scope, (container.name, number)))
                    raise ResolutionError(
                        f"{missing} does not exist: {container.name} is only "
                        f"{width} wide in that iteration"
                    )
            return self.collect(depth + 1, (*scope, (container.name, number)))

        if level is Level.ITERATIONS_SO_FAR and self.on_path(scope):
            count: int | None = self.consumer_scope[container.name]
        elif isinstance(container, LoopStep):
            # A loop the consumer comes after, or one around it but inside an
            # earlier iteration of an outer loop: either way it has finished
            # there, after as many iterations as its exit task allowed.
            count = _finished_iterations(self.state, container, scope)
        else:
            assert isinstance(container, FanOutStep)
            count = fan_out_width(self.definition, self.state, container, scope)
        if count is None:
            return None

        values: list[JsonValue] = []
        for number in range(1, count + 1):
            item = self.collect(depth + 1, (*scope, (container.name, number)))
            if item is None:
                return None
            values.append(item.value)
        return Resolved(values)


def fan_out_width(
    definition: FlowDefinition, state: RunState, fan_out: FanOutStep, scope: Scope
) -> int | None:
    """How many branches ``fan_out`` has within ``scope``, or ``None`` if not known yet.

    Raises ``ResolutionError`` if its ``over`` value is not a list.
    """
    if fan_out.over is None:
        assert fan_out.range is not None
        return fan_out.range
    items = _over_items(definition, state, fan_out, scope)
    return None if items is None else len(items)


def _over_items(
    definition: FlowDefinition, state: RunState, fan_out: FanOutStep, scope: Scope
) -> list[JsonValue] | None:
    assert fan_out.over is not None
    address = Address(fan_out.name, scope)
    resolved = resolve(definition, state, address, fan_out.over)
    if resolved is None:
        return None
    if not isinstance(resolved.value, list):
        kind = type(resolved.value).__name__
        raise ResolutionError(
            f"{address}: fan_out.over {fan_out.over} is a {kind}, not a list"
        )
    return resolved.value


def _finished_iterations(state: RunState, loop: LoopStep, scope: Scope) -> int | None:
    """How many iterations ``loop`` ran within ``scope``, once its exit task said so."""
    last = state.last(scope, loop.name)
    if last == 0:
        return None
    exit_task = state.get(Address(loop.exit_condition, (*scope, (loop.name, last))))
    if (
        exit_task is None
        or exit_task.outcome is not Outcome.SUCCEEDED
        or exit_task.value is not True
    ):
        return None
    return last


def _metadata(
    definition: FlowDefinition,
    state: RunState,
    consumer: Address | None,
    around: tuple[Container, ...],
    reference: Reference,
) -> Resolved | None:
    name = reference.name
    if consumer is None or name not in ("loop_count", "index", "item"):
        raise ValueError(f"{reference} is not resolved from a run's state")
    kind = LoopStep if name == "loop_count" else FanOutStep
    depths = [i for i, container in enumerate(around) if isinstance(container, kind)]
    if not depths:
        raise ValueError(f"{reference} needs {consumer.step} inside a {kind.__name__}")
    depth = depths[-1]
    number = consumer.scope[depth][1]
    if name != "item":
        return Resolved(number)

    fan_out = around[depth]
    assert isinstance(fan_out, FanOutStep)
    items = _over_items(definition, state, fan_out, consumer.scope[:depth])
    if items is None:
        return None
    if number > len(items):
        raise ResolutionError(
            f"{consumer}: branch {number} of {fan_out.name} is past the end of "
            f"its {len(items)}-element list"
        )
    return Resolved(items[number - 1])
