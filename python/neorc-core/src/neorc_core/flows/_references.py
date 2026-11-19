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

A switch adds no list level, docs/specs/switch.md: ``switches.<name>`` is the
output of the case it chose, once per iteration and branch the switch is in.
A step of a case is read from inside that case, and in an iteration where the
case did not run it reads as ``null``.

Pure code: a definition and a ``RunState`` in, a value out.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from neorc_core._errors import ResolutionError
from neorc_core._values import JsonValue
from neorc_core.flows._definition import (
    STEP_NAMESPACES,
    Around,
    Case,
    Container,
    FanOutStep,
    FlowDefinition,
    InCase,
    LoopStep,
    Namespace,
    Reference,
    SwitchStep,
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
    always single values. A switch has the levels a task in its place would.
    """
    if reference.namespace not in STEP_NAMESPACES:
        return ()
    around = definition.around(consumer) if consumer is not None else ()
    levels = _levels(around, definition.around(reference.name))
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
    way, is not a list, or a switch's ``on`` value is not the name of a case.
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
    inside = definition.around(consumer.step) if consumer is not None else ()
    levels = _levels(inside, definition.around(target))
    return _Collector(definition, state, target, levels, dict(scope)).collect(0, ())


def switch_case(
    definition: FlowDefinition, state: RunState, switch: SwitchStep, scope: Scope
) -> Case | None:
    """The case ``switch`` runs within ``scope``, or ``None`` if not known yet.

    ``scope`` is the switch's own: ``on`` is read in that iteration and branch,
    one value and no list. Raises ``ResolutionError`` if that value is not the
    name of one of the switch's cases, as a string.
    """
    address = Address(switch.name, scope)
    chosen = _read_own(definition, state, address, switch.on)
    if chosen is None:
        return None
    value = chosen.value
    if isinstance(value, str):
        case = switch.case(value)
        if case is not None:
            return case
        given = repr(value) if len(value) <= _SHOWN else repr(value[:_SHOWN]) + "..."
    else:
        given = "null" if value is None else f"a {type(value).__name__}"
    # Sorted: a store may keep a flow's keys in another order than its file.
    names = ", ".join(sorted(case.name for case in switch.cases))
    raise ResolutionError(
        f"{address}: switch.on {switch.on} is {given}, not one of its cases: {names}"
    )


_SHOWN = 60
"""How much of a string that names no case an error shows."""


def _switch_output(
    definition: FlowDefinition, state: RunState, switch: SwitchStep, scope: Scope
) -> Resolved | None:
    """The output of the case ``switch`` chose within ``scope``: ``switches.<name>``."""
    # Known limitation: this evaluates switch_case twice for the same switch,
    # here and again at the case's own level when its output is read. For
    # switches that each choose on the previous one's output, the cost of one
    # plan() doubles with every switch in the chain: about 1 s at 16 in a row,
    # 13 s at 20, and half a second at 10 inside a loop at its 100th iteration.
    # The scheduler plans runs one after another, so a slow plan holds the
    # others back. If it causes trouble: keep the chosen case per switch and
    # scope on RunState, as RunState keeps _last.
    case = switch_case(definition, state, switch, scope)
    if case is None:
        return None
    if case.output is None:
        return Resolved(None)
    return _read_own(definition, state, Address(switch.name, scope), case.output)


def _read_own(
    definition: FlowDefinition, state: RunState, reader: Address, reference: Reference
) -> Resolved | None:
    """What ``on`` or a case's ``output`` reads, for the switch at ``reader``.

    The referenced step's result in the switch's own iteration and branch: a
    loop around both adds no list of iterations so far, as for a loop's exit
    condition. A loop or fan-out the switch is outside of, which only a
    case's output can reach into, adds its level as usual.
    """
    if reference.namespace is Namespace.INPUTS:
        return Resolved(state.inputs[reference.name])
    levels = _levels(
        definition.around(reader.step),
        definition.around(reference.name),
        own_iteration=True,
    )
    collector = _Collector(
        definition, state, reference.name, levels, dict(reader.scope)
    )
    return collector.collect(0, ())


def _levels(
    around: tuple[Around, ...],
    target_around: tuple[Around, ...],
    *,
    own_iteration: bool = False,
) -> list[tuple[Around, Level | None]]:
    """Everything around the target, each with the list level it adds for a reader.

    ``around`` is what the reader is inside of. The level is ``None`` for a
    case, which adds none, for a fan-out branch the reader shares and, with
    ``own_iteration``, for a loop iteration it shares.
    """
    shared = 0
    while shared < min(len(around), len(target_around)) and _same(
        around[shared], target_around[shared]
    ):
        shared += 1
    levels: list[tuple[Around, Level | None]] = []
    for depth, one in enumerate(target_around):
        inside = depth < shared
        if isinstance(one, InCase):
            levels.append((one, None))
        elif isinstance(one, LoopStep):
            so_far = None if own_iteration else Level.ITERATIONS_SO_FAR
            levels.append((one, so_far if inside else Level.ITERATIONS))
        else:
            levels.append((one, None if inside else Level.BRANCHES))
    return levels


def _same(one: Around, other: Around) -> bool:
    if isinstance(one, InCase) or isinstance(other, InCase):
        return (
            isinstance(one, InCase)
            and isinstance(other, InCase)
            and one.switch.name == other.switch.name
            and one.case.name == other.case.name
        )
    return one.name == other.name


@dataclass(frozen=True, slots=True)
class _Collector:
    """Walks what is around a target, gathering its instances' values."""

    definition: FlowDefinition
    state: RunState
    target: str
    levels: list[tuple[Around, Level | None]]
    consumer_scope: dict[str, int]

    def on_path(self, scope: Scope) -> bool:
        """Whether ``scope`` is the start of the consumer's own scope."""
        return all(self.consumer_scope.get(name) == n for name, n in scope)

    def instance(self, scope: Scope) -> Resolved | None:
        """The target's one value within ``scope``, every level above it walked."""
        target = self.definition.step(self.target)
        if isinstance(target, SwitchStep):
            return _switch_output(self.definition, self.state, target, scope)
        result = self.state.get(Address(self.target, scope))
        if result is None or result.outcome is not Outcome.SUCCEEDED:
            return None
        return Resolved(result.value)

    def collect(self, depth: int, scope: Scope) -> Resolved | None:
        if depth == len(self.levels):
            return self.instance(scope)

        container, level = self.levels[depth]
        if isinstance(container, InCase):
            chosen = switch_case(self.definition, self.state, container.switch, scope)
            if chosen is None:
                return None
            if chosen.name != container.case.name:
                # The switch ran another case in that iteration: position i
                # stays iteration i, with nothing in it.
                return Resolved(None)
            return self.collect(depth + 1, scope)
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
