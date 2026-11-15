# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""A flow definition, as neorc holds it once a flow file has been read and checked.

Build one with ``neorc_core.flows.parse_flow`` or the loaders next to it, which
validate as they go; the classes here trust what they are given.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cached_property

from neorc_core._values import JsonValue

_VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")

MAX_VERSION_PART = 2**31 - 1
"""The largest number in a version: what a store's ``integer`` column holds."""

DEFAULT_QUEUE = "default"

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_QUEUE_NAME = re.compile(r"[A-Za-z0-9_-]+")


def is_name(text: str) -> bool:
    """Whether ``text`` may name a flow, step, input or param: an identifier."""
    return _NAME.fullmatch(text) is not None


def is_queue_name(text: str) -> bool:
    """Whether ``text`` may name a queue: letters, digits, ``_`` and ``-``.

    A queue name travels in a URL path, where a ``/`` or a ``.`` segment would
    be read as part of the route, and names a queue in every store.
    """
    return _QUEUE_NAME.fullmatch(text) is not None


class InputType(StrEnum):
    """The type a flow declares for one of its inputs."""

    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATETIME = "datetime"


class Namespace(StrEnum):
    """What a reference points at: the part before the dot."""

    INPUTS = "inputs"
    TASKS = "tasks"
    FLOWS = "flows"
    NEORC = "neorc"


NEORC_METADATA = frozenset(
    {
        "task_id",
        "attempts",
        "flow_run_id",
        "started_by",
        "loop_count",
        "index",
        "item",
    }
)
"""What a task can request under ``neorc.``."""


@dataclass(frozen=True, slots=True)
class Reference:
    """A value a step takes from elsewhere: ``tasks.pick_word``, ``inputs.sentence``."""

    namespace: Namespace
    name: str

    @classmethod
    def parse(cls, text: str) -> Reference:
        """Read ``namespace.name``; raise ``ValueError`` if it is not one."""
        namespace, dot, name = text.partition(".")
        if not dot or not name or "." in name:
            raise ValueError(f"{text!r} is not a reference like 'tasks.name'")
        try:
            return cls(Namespace(namespace), name)
        except ValueError:
            known = ", ".join(n.value for n in Namespace)
            raise ValueError(
                f"{text!r} starts with {namespace!r}, not one of {known}"
            ) from None

    def __str__(self) -> str:
        return f"{self.namespace.value}.{self.name}"


@dataclass(frozen=True, order=True, slots=True)
class Version:
    """A flow version: ``MAJOR.MINOR.PATCH``, compared numerically.

    Each part is at most ``MAX_VERSION_PART``, so every store holds the same
    versions: ``ValueError`` otherwise.
    """

    major: int
    minor: int
    patch: int

    def __post_init__(self) -> None:
        for part in (self.major, self.minor, self.patch):
            if not 0 <= part <= MAX_VERSION_PART:
                raise ValueError(
                    f"version part {part} is not between 0 and {MAX_VERSION_PART}"
                )

    @classmethod
    def parse(cls, text: str) -> Version:
        """Read ``MAJOR.MINOR.PATCH``; raise ``ValueError`` if it is not one."""
        match = _VERSION.fullmatch(text)
        if match is None:
            raise ValueError(f"{text!r} is not a version like '1.0.0'")
        major, minor, patch = (int(part) for part in match.groups())
        return cls(major, minor, patch)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True, slots=True)
class TaskStep:
    """A task: a handler run by a worker on ``queue``."""

    name: str
    handler: str
    queue: str = DEFAULT_QUEUE
    params: Mapping[str, Reference] = field(default_factory=dict)
    fixed_params: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LoopStep:
    """Steps repeated until ``exit_condition``, a task in the body, returns true."""

    name: str
    max_cycles: int
    exit_condition: str
    steps: tuple[Step, ...]


@dataclass(frozen=True, slots=True)
class FanOutStep:
    """Steps run once per index: ``range`` copies, or one per element of ``over``."""

    name: str
    steps: tuple[Step, ...]
    range: int | None = None
    over: Reference | None = None


@dataclass(frozen=True, slots=True)
class SubFlowStep:
    """A run of another flow, always its latest version."""

    name: str
    flow: str
    params: Mapping[str, Reference] = field(default_factory=dict)
    fixed_params: Mapping[str, JsonValue] = field(default_factory=dict)


Step = TaskStep | LoopStep | FanOutStep | SubFlowStep
Container = LoopStep | FanOutStep


@dataclass(frozen=True)
class FlowDefinition:
    """A flow file, validated."""

    name: str
    version: Version
    inputs: Mapping[str, InputType]
    steps: tuple[Step, ...]
    output: Reference | None = None

    def walk(self) -> Iterator[tuple[Step, tuple[Container, ...]]]:
        """Every step at any depth, with its enclosing loops and fan-outs.

        Enclosing containers come outermost first. Steps come in file order,
        a container before the steps inside it.
        """
        yield from _walk(self.steps, ())

    def step(self, name: str) -> Step:
        """The step called ``name``, at any depth. ``KeyError`` if there is none."""
        return self._index[name][0]

    def enclosing(self, name: str) -> tuple[Container, ...]:
        """The loops and fan-outs around step ``name``, outermost first."""
        return self._index[name][1]

    def waits_on(self, name: str) -> frozenset[str]:
        """The steps that must finish before step ``name`` can start.

        All of them are siblings of ``name``, in the scope the two share. A step
        referring to one inside a loop or fan-out it is not part of waits for
        that whole container; a loop or fan-out waits for whatever the steps
        inside it refer to outside it. References that name no step are left
        out: validation reports them.
        """
        return self._dependencies.get(name, frozenset())

    def tasks(self) -> Iterator[TaskStep]:
        """Every task step, at any depth."""
        for step, _ in self.walk():
            if isinstance(step, TaskStep):
                yield step

    @cached_property
    def _dependencies(self) -> dict[str, frozenset[str]]:
        edges: dict[str, set[str]] = {}
        for consumer, enclosing in self.walk():
            consumer_chain = [c.name for c in enclosing] + [consumer.name]
            for _, reference in step_references(consumer):
                if reference.namespace not in (Namespace.TASKS, Namespace.FLOWS):
                    continue
                if reference.name not in self._index:
                    continue
                target_enclosing = self._index[reference.name][1]
                target_chain = [c.name for c in target_enclosing] + [reference.name]
                shared = 0
                while (
                    shared < min(len(consumer_chain), len(target_chain))
                    and consumer_chain[shared] == target_chain[shared]
                ):
                    shared += 1
                if shared in (len(consumer_chain), len(target_chain)):
                    continue  # itself, or containment: validation reports both
                waiting = edges.setdefault(consumer_chain[shared], set())
                waiting.add(target_chain[shared])
        return {name: frozenset(targets) for name, targets in edges.items()}

    @cached_property
    def _index(self) -> dict[str, tuple[Step, tuple[Container, ...]]]:
        return {step.name: (step, enclosing) for step, enclosing in self.walk()}


def step_references(step: Step) -> Iterator[tuple[str, Reference]]:
    """Every reference a step makes, with where in the file it is."""
    if isinstance(step, TaskStep | SubFlowStep):
        for name, reference in step.params.items():
            yield f"{step.name}.params.{name}", reference
    if isinstance(step, FanOutStep) and step.over is not None:
        yield f"{step.name}.fan_out.over", step.over


def _walk(
    steps: tuple[Step, ...], enclosing: tuple[Container, ...]
) -> Iterator[tuple[Step, tuple[Container, ...]]]:
    for step in steps:
        yield step, enclosing
        if isinstance(step, LoopStep | FanOutStep):
            yield from _walk(step.steps, (*enclosing, step))
