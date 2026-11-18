# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The checks a flow must pass beyond its shape: references, cycles, sub-flows.

Each check returns the problems it found instead of raising, so a flow file with
several mistakes reports all of them.
"""

from __future__ import annotations

from neorc_core.flows._definition import (
    NEORC_METADATA,
    STEP_NAMESPACES,
    Case,
    Container,
    FanOutStep,
    FlowDefinition,
    InCase,
    InputType,
    LoopStep,
    Namespace,
    Reference,
    Step,
    SubFlowStep,
    SwitchStep,
    TaskStep,
    step_references,
)

_ROOT = "the flow"

_KINDS: dict[Namespace, tuple[type, str]] = {
    Namespace.TASKS: (TaskStep, "is not a task; tasks. refers to tasks"),
    Namespace.FLOWS: (SubFlowStep, "is not a sub-flow; flows. refers to them"),
    Namespace.SWITCHES: (SwitchStep, "is not a switch; switches. refers to them"),
}
"""For each namespace that names a step, the kind of step, and what to say otherwise."""


def check_flow(definition: FlowDefinition) -> list[str]:
    """Every problem in one flow that its shape alone does not reveal."""
    problems: list[str] = []
    for step, enclosing in definition.walk():
        if isinstance(step, TaskStep | SubFlowStep):
            for name in sorted(set(step.params) & set(step.fixed_params)):
                problems.append(
                    f"{step.name}: {name!r} is in both params and fixed_params"
                )
        if isinstance(step, LoopStep):
            problems.extend(_check_exit_condition(step))
        if isinstance(step, SwitchStep):
            problems.extend(_check_switch(definition, step))
            continue  # its one reference, on, is checked with it
        for where, reference in step_references(step):
            problems.extend(
                _check_reference(definition, step, enclosing, where, reference)
            )
    if definition.output is not None:
        problems.extend(check_output(definition, definition.output))
    problems.extend(_check_cycles(definition))
    return problems


def check_output(definition: FlowDefinition, output: Reference) -> list[str]:
    """Problems with ``output`` as a flow's output: a task, sub-flow or switch of it.

    For the flow's own output, and for the reference a run is succeeded with.
    """
    if output.namespace not in STEP_NAMESPACES:
        return [f"output: {output} must refer to tasks., flows. or switches."]
    return _check_target(definition, "output", output) or _check_cases(
        definition, "output", (), output
    )


def check_flow_set(definitions: list[FlowDefinition]) -> list[str]:
    """Problems in flows deployed together: duplicates, sub-flows, recursion."""
    problems: list[str] = []
    by_name: dict[str, FlowDefinition] = {}
    for definition in definitions:
        if definition.name in by_name:
            problems.append(f"flow {definition.name!r} is defined more than once")
        by_name[definition.name] = definition

    calls: dict[str, set[str]] = {name: set() for name in by_name}
    for definition in by_name.values():
        for step, _ in definition.walk():
            if not isinstance(step, SubFlowStep):
                continue
            where = f"{definition.name}.{step.name}"
            called = by_name.get(step.flow)
            if called is None:
                problems.append(f"{where}: calls unknown flow {step.flow!r}")
                continue
            calls[definition.name].add(called.name)
            given = set(step.params) | set(step.fixed_params)
            for name in sorted(set(called.inputs) - given):
                problems.append(f"{where}: missing input {name!r} of {called.name}")
            for name in sorted(given - set(called.inputs)):
                problems.append(f"{where}: {called.name} has no input {name!r}")

    for cycle in _cycles(calls):
        problems.append("flows call each other in a cycle: " + " -> ".join(cycle))
    return problems


def _check_exit_condition(loop: LoopStep) -> list[str]:
    for step in loop.steps:
        if step.name == loop.exit_condition:
            if isinstance(step, TaskStep):
                return []
            return [f"{loop.name}: exit condition {step.name!r} must be a task"]
    return [
        f"{loop.name}: exit condition {loop.exit_condition!r} "
        "is not a task directly in the loop"
    ]


def _check_reference(
    definition: FlowDefinition,
    consumer: Step,
    enclosing: tuple[Container, ...],
    where: str,
    reference: Reference,
) -> list[str]:
    if reference.namespace is Namespace.INPUTS:
        if reference.name not in definition.inputs:
            return [f"{where}: the flow has no input {reference.name!r}"]
        return []
    if reference.namespace is Namespace.NEORC:
        return _check_metadata(consumer, enclosing, where, reference)
    return _check_step_reference(definition, consumer, where, reference)


def _check_step_reference(
    definition: FlowDefinition, consumer: Step, where: str, reference: Reference
) -> list[str]:
    """Problems with a reference from ``consumer`` to another step of the flow."""
    problems = _check_target(definition, where, reference)
    if problems:
        return problems
    if reference.name == consumer.name:
        return [f"{where}: a step cannot refer to itself"]
    if isinstance(consumer, FanOutStep) and consumer.name in (
        c.name for c in definition.enclosing(reference.name)
    ):
        return [f"{where}: {reference} is inside the fan-out it feeds"]
    inside = definition.cases_around(consumer.name)
    if reference.name in (around.switch.name for around in inside):
        return [
            f"{where}: {reference} is the switch this step is inside of, and it "
            "would wait on itself"
        ]
    return _check_cases(definition, where, inside, reference)


def _check_target(
    definition: FlowDefinition, where: str, reference: Reference
) -> list[str]:
    try:
        target = definition.step(reference.name)
    except KeyError:
        return [f"{where}: there is no step {reference.name!r}"]
    kind, otherwise = _KINDS[reference.namespace]
    if not isinstance(target, kind):
        return [f"{where}: {reference.name!r} {otherwise}"]
    return []


def _check_cases(
    definition: FlowDefinition,
    where: str,
    inside: tuple[InCase, ...],
    reference: Reference,
) -> list[str]:
    """A step of a case is read from inside that case only; outside, its switch is.

    ``inside`` are the cases the reader is in, outermost first.
    """
    reader = [(around.switch.name, around.case.name) for around in inside]
    for depth, around in enumerate(definition.cases_around(reference.name)):
        switch = around.switch.name
        own = reader[depth : depth + 1]
        if own == [(switch, around.case.name)]:
            continue
        if own and own[0][0] == switch:
            return [
                f"{where}: {reference} is inside {around}, and a step of "
                f"another case of {switch} cannot read it"
            ]
        return [
            f"{where}: {reference} is inside {around}; outside {switch}, read "
            f"switches.{switch}"
        ]
    return []


def _check_switch(definition: FlowDefinition, switch: SwitchStep) -> list[str]:
    """Problems with a switch's ``on`` and with each of its cases' ``output``."""
    problems = _check_on(definition, switch)
    inside = definition.cases_around(switch.name)
    for case in switch.cases:
        where = f"{switch.name}.switch.cases.{case.name}.output"
        problems.extend(
            _check_case_output(definition, where, (*inside, InCase(switch, case)))
        )
    return problems


def _check_on(definition: FlowDefinition, switch: SwitchStep) -> list[str]:
    """``on`` reads one value: a string input, or a step around the switch."""
    where = f"{switch.name}.switch.on"
    on = switch.on
    if on.namespace is Namespace.INPUTS:
        declared = definition.inputs.get(on.name)
        if declared is None:
            return [f"{where}: the flow has no input {on.name!r}"]
        if declared is not InputType.STRING:
            return [
                f"{where}: {on} is a {declared.value}, and on takes only string inputs"
            ]
        return []
    if on.namespace not in STEP_NAMESPACES:
        return [f"{where}: {on} must refer to inputs., tasks., flows. or switches."]
    problems = _check_target(definition, where, on)
    if problems:
        return problems
    if switch.name in (a.switch.name for a in definition.cases_around(on.name)):
        return [f"{where}: {on} is inside the switch it chooses a case of"]
    problems = _check_step_reference(definition, switch, where, on)
    if problems:
        return problems
    around = [container.name for container in definition.enclosing(switch.name)]
    for container in definition.enclosing(on.name):
        if container.name not in around:
            kind = "loop" if isinstance(container, LoopStep) else "fan-out"
            return [
                f"{where}: {on} is inside the {kind} {container.name}, which "
                f"{switch.name} is outside of, and on reads one value"
            ]
    return []


def _check_case_output(
    definition: FlowDefinition, where: str, inside: tuple[InCase, ...]
) -> list[str]:
    """A case's output is ``null``, or a step inside the case and no deeper case."""
    case: Case = inside[-1].case
    output = case.output
    if output is None:
        return []
    if output.namespace not in STEP_NAMESPACES:
        return [f"{where}: {output} must be null, or refer to a step inside the case"]
    problems = _check_target(definition, where, output)
    if problems:
        return problems
    target = definition.cases_around(output.name)
    if len(target) < len(inside) or any(
        (a.switch.name, a.case.name) != (b.switch.name, b.case.name)
        for a, b in zip(target, inside, strict=False)
    ):
        return [f"{where}: {output} is not a step inside {inside[-1]}"]
    return _check_cases(definition, where, inside, output)


def _check_metadata(
    consumer: Step, enclosing: tuple[Container, ...], where: str, reference: Reference
) -> list[str]:
    name = reference.name
    if not isinstance(consumer, TaskStep):
        return [f"{where}: only tasks can request neorc metadata"]
    if name not in NEORC_METADATA:
        known = ", ".join(sorted(NEORC_METADATA))
        return [f"{where}: {reference} is not one of neorc.{{{known}}}"]
    fan_outs = [c for c in enclosing if isinstance(c, FanOutStep)]
    if name == "index" and not fan_outs:
        return [f"{where}: {reference} needs the task to be inside a fan-out"]
    if name == "item" and (not fan_outs or fan_outs[-1].over is None):
        return [f"{where}: {reference} needs the task inside a fan-out over a list"]
    if name == "loop_count" and not any(isinstance(c, LoopStep) for c in enclosing):
        return [f"{where}: {reference} needs the task to be inside a loop"]
    return []


def _check_cycles(definition: FlowDefinition) -> list[str]:
    """Steps that wait on each other, within the scope they share."""
    graphs: dict[str, dict[str, set[str]]] = {}
    for step, _ in definition.walk():
        waits_on = definition.waits_on(step.name)
        if waits_on:
            around = definition.around(step.name)
            scope = _ROOT
            if around:
                last = around[-1]
                scope = str(last) if isinstance(last, InCase) else repr(last.name)
            graphs.setdefault(scope, {})[step.name] = set(waits_on)

    problems = []
    for scope, edges in graphs.items():
        for cycle in _cycles(edges):
            problems.append(
                f"steps wait on each other in {scope}: " + " -> ".join(cycle)
            )
    return problems


def _cycles(edges: dict[str, set[str]]) -> list[list[str]]:
    """One cycle per strongly connected loop found by depth-first search."""
    found: list[list[str]] = []
    done: set[str] = set()

    def visit(node: str, path: list[str]) -> None:
        if node in path:
            found.append([*path[path.index(node) :], node])
            return
        if node in done:
            return
        path.append(node)
        for following in sorted(edges.get(node, ())):
            visit(following, path)
        path.pop()
        done.add(node)

    for node in sorted(edges):
        visit(node, [])
    return found
