# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Reading flow files: YAML text, or the JSON structure the manager receives.

A flow file and its upload are the same structure, so everything goes through
``parse_flow``. Every problem found is reported at once, each with the path to
where it is.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

from neorc_core import _values
from neorc_core._errors import FlowDefinitionError, InvalidValueError
from neorc_core.flows._definition import (
    DEFAULT_QUEUE,
    FanOutStep,
    FlowDefinition,
    InputType,
    LoopStep,
    Reference,
    Step,
    SubFlowStep,
    TaskStep,
    Version,
    is_name,
    is_queue_name,
)
from neorc_core.flows._validation import check_flow, check_flow_set
from neorc_core.forms import form_problems, inline_form

_TOP_KEYS = {"name", "version", "inputs", "output", "steps", "form"}
_KIND_KEYS = ("handler", "loop", "fan_out", "flow")
_STEP_KEYS = {
    "handler": {"handler", "queue", "params", "fixed_params"},
    "loop": {"loop", "steps"},
    "fan_out": {"fan_out", "steps"},
    "flow": {"flow", "params", "fixed_params"},
}


class _FlowYamlLoader(yaml.SafeLoader):
    """Safe YAML with YAML 1.2 core schema scalars, so a file means what JSON would.

    PyYAML follows YAML 1.1, where unquoted ``no`` is false, ``12:30`` is 750,
    ``010`` is 8 and ``2026-09-13`` is a date. Here only ``true``/``false``,
    ``null``, decimal integers and plain floats are typed; everything else is a
    string, and datetimes are written as ``{"$datetime": ...}`` tags.

    Also records keys repeated within one mapping, which YAML parsers otherwise
    resolve silently by keeping the last value, and rejects anchors and aliases.
    """

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self.duplicates: list[str] = []

    def compose_node(self, parent: Any, index: Any) -> Any:
        # Anchors and aliases have no JSON equivalent, and they let a small file
        # expand into a huge or self-containing value.
        event = self.peek_event()  # type: ignore[no-untyped-call]
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
            raise yaml.composer.ComposerError(
                None,
                None,
                "anchors and aliases are not allowed in flow files",
                event.start_mark,
            )
        return super().compose_node(parent, index)

    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = self.construct_object(key_node, deep=deep)
            try:
                repeated = key in seen
                seen.add(key)
            except TypeError:
                continue  # an unhashable key: construction reports it
            if repeated:
                line = key_node.start_mark.line + 1
                self.duplicates.append(
                    f"line {line}: {key!r} appears more than once in the same mapping"
                )
        return super().construct_mapping(node, deep=deep)

    def construct_yaml_int(self, node: yaml.ScalarNode) -> int:
        return int(self.construct_scalar(node), 10)


_YAML_1_1_TYPES = ("bool", "int", "float", "timestamp")
_FlowYamlLoader.yaml_implicit_resolvers = {
    first: [
        (tag, regexp)
        for tag, regexp in resolvers
        if tag.rpartition(":")[2] not in _YAML_1_1_TYPES
    ]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_FlowYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)
_FlowYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int", re.compile(r"^[-+]?[0-9]+$"), list("-+0123456789")
)
_FlowYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(
        r"^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?"
        r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$"
    ),
    list("-+.0123456789"),
)
_FlowYamlLoader.add_constructor(
    "tag:yaml.org,2002:int", _FlowYamlLoader.construct_yaml_int
)


def read_flow_yaml(text: str) -> Any:
    """Parse YAML into the JSON structure it stands for, not yet validated.

    Raises ``FlowDefinitionError`` for malformed YAML or a key repeated within
    one mapping.
    """
    loader = _FlowYamlLoader(text)
    try:
        data = loader.get_single_data()
    # A bad explicit tag (!!int abc) raises ValueError, deep nesting
    # RecursionError: neither is a YAMLError.
    except (yaml.YAMLError, ValueError, RecursionError) as exc:
        raise FlowDefinitionError([f"not valid YAML: {exc}"]) from None
    finally:
        loader.dispose()
    if loader.duplicates:
        raise FlowDefinitionError(loader.duplicates)
    return data


def read_flow_json(text: str) -> Any:
    """Parse a JSON upload into its structure, not yet validated.

    Raises ``FlowDefinitionError`` for malformed JSON or a key repeated within
    one object.
    """
    duplicates: list[str] = []

    def unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        mapping: dict[str, Any] = {}
        for key, value in pairs:
            if key in mapping:
                duplicates.append(f"{key!r} appears more than once in the same object")
            mapping[key] = value
        return mapping

    try:
        _values.ensure_json_depth(text)
        data = json.loads(text, object_pairs_hook=unique_keys)
    # ValueError covers JSONDecodeError, integers over the digit limit and
    # InvalidValueError for nesting too deep to hand to the parser.
    except ValueError as exc:
        raise FlowDefinitionError([f"not valid JSON: {exc}"]) from None
    if duplicates:
        raise FlowDefinitionError(duplicates)
    return data


def load_flow_yaml(text: str, *, directory: Path | None = None) -> FlowDefinition:
    """Parse and validate one flow from YAML text.

    A flow file also has to start with ``name`` and ``version``, so both read
    first in the repository. JSON object order carries no meaning, so
    ``parse_flow`` does not ask it of uploads.

    ``directory`` is where the text's file is: a form the flow names is read
    from there, and without one a flow cannot name a form.
    """
    data = inline_form(read_flow_yaml(text), directory, read_flow_json)
    order = []
    if isinstance(data, Mapping) and list(data)[:2] != ["name", "version"]:
        order = ["flow: 'name' and 'version' must be the first two fields"]
    try:
        definition = parse_flow(data)
    except FlowDefinitionError as exc:
        raise FlowDefinitionError(order + exc.problems) from None
    if order:
        raise FlowDefinitionError(order)
    return definition


def load_flow_file(path: Path) -> FlowDefinition:
    """Parse and validate one flow file."""
    return load_flow_yaml(path.read_text(encoding="utf-8"), directory=path.parent)


def load_flows(directory: Path) -> list[FlowDefinition]:
    """Load every ``*.yaml`` and ``*.yml`` file in ``directory``, checked as a set."""
    definitions = [load_flow_file(path) for path in flow_files(directory)]
    validate_flow_set(definitions)
    return definitions


def flow_files(directory: Path) -> list[Path]:
    """Every ``*.yaml`` and ``*.yml`` file in ``directory``, in name order."""
    return sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")])


def read_flows(directory: Path) -> list[Any]:
    """The flow files in ``directory`` as the structures an upload sends.

    Each file is validated on its own first, as a file must be, with ``name``
    and ``version`` first; the set is validated by the manager on upload.
    A form a flow names is read in from beside it, so the structure holds the
    form itself. Raises ``FlowDefinitionError`` naming the file for the first
    invalid one.
    """
    contents = []
    for path in flow_files(directory):
        text = path.read_text(encoding="utf-8")
        try:
            load_flow_yaml(text, directory=path.parent)
            content = inline_form(read_flow_yaml(text), path.parent, read_flow_json)
        except FlowDefinitionError as exc:
            raise FlowDefinitionError(
                [f"{path.name}: {problem}" for problem in exc.problems]
            ) from None
        contents.append(content)
    return contents


def validate_flow_set(definitions: Iterable[FlowDefinition]) -> None:
    """Check flows that will be deployed together: names, sub-flows, recursion."""
    problems = check_flow_set(list(definitions))
    if problems:
        raise FlowDefinitionError(problems)


def parse_flow(data: Any) -> FlowDefinition:
    """Build a validated flow definition from its JSON structure.

    The whole structure is what a store keeps, so before anything else every
    string in it, key or value, known field or not, must be one a store can
    hold: text holding NUL is refused on its own, as a value is.
    """
    unstorable = _unstorable_text(data, "flow")
    if unstorable:
        raise FlowDefinitionError(unstorable)
    parser = _Parser()
    definition = parser.flow(data)
    if definition is None or parser.problems:
        raise FlowDefinitionError(parser.problems)
    problems = check_flow(definition)
    if problems:
        raise FlowDefinitionError(problems)
    return definition


def _unstorable_text(data: Any, path: str) -> list[str]:
    """Every string in ``data``, key or value, that no store can hold, by path."""
    problems: list[str] = []
    if isinstance(data, str):
        try:
            _values.check_text(data, path)
        except InvalidValueError as exc:
            problems.append(str(exc))
    elif isinstance(data, Mapping):
        for key, value in data.items():
            if isinstance(key, str):
                bad_key = _unstorable_text(key, f"{path}: key {key!r}")
                if bad_key:  # the key would put the text into every path below
                    problems.extend(bad_key)
                    continue
            problems.extend(_unstorable_text(value, f"{path}.{key}"))
    elif isinstance(data, list):
        for index, item in enumerate(data):
            problems.extend(_unstorable_text(item, f"{path}[{index}]"))
    return problems


class _Parser:
    def __init__(self) -> None:
        self.problems: list[str] = []
        self._names: set[str] = set()

    def problem(self, path: str, message: str) -> None:
        self.problems.append(f"{path}: {message}")

    def flow(self, data: Any) -> FlowDefinition | None:
        if not isinstance(data, Mapping):
            self.problem("flow", "must be a mapping")
            return None
        self.keys(data, "flow", allowed=_TOP_KEYS, required={"name", "version"})
        name = self.name(data.get("name"), "name")
        version = self.version(data.get("version"))
        inputs = self.inputs(data.get("inputs", {}))
        if "form" in data:
            self.problems.extend(form_problems(data["form"], inputs))
        output = None
        if "output" in data:
            output = self.reference(data["output"], "output")
        steps = self.steps(data.get("steps"), "steps")
        if name is None or version is None:
            return None
        return FlowDefinition(name, version, inputs, steps, output)

    def keys(
        self,
        data: Mapping[Any, Any],
        path: str,
        *,
        allowed: set[str],
        required: set[str],
    ) -> None:
        for key in data:
            if key not in allowed:
                self.problem(path, f"unknown field {key!r}")
        for key in sorted(required - set(data)):
            self.problem(path, f"missing field {key!r}")

    def name(self, value: Any, path: str) -> str | None:
        if not isinstance(value, str) or not is_name(value):
            self.problem(path, f"{value!r} is not a name: letters, digits and _")
            return None
        return value

    def version(self, value: Any) -> Version | None:
        if not isinstance(value, str):
            self.problem("version", f"{value!r} must be a string like '1.0.0'")
            return None
        try:
            return Version.parse(value)
        except ValueError as exc:
            self.problem("version", str(exc))
            return None

    def inputs(self, data: Any) -> dict[str, InputType]:
        if not isinstance(data, Mapping):
            self.problem("inputs", "must be a mapping of input names to types")
            return {}
        inputs: dict[str, InputType] = {}
        for key, value in data.items():
            name = self.name(key, f"inputs.{key}")
            try:
                input_type = InputType(value)
            except ValueError:
                known = ", ".join(t.value for t in InputType)
                self.problem(f"inputs.{key}", f"type {value!r} is not one of {known}")
                continue
            if name is not None:
                inputs[name] = input_type
        return inputs

    def steps(self, data: Any, path: str) -> tuple[Step, ...]:
        if not isinstance(data, Mapping) or not data:
            self.problem(path, "must be a non-empty mapping of step names to steps")
            return ()
        steps: list[Step] = []
        for key, value in data.items():
            step = self.step(key, value, f"{path}.{key}")
            if step is not None:
                steps.append(step)
        return tuple(steps)

    def step(self, key: Any, data: Any, path: str) -> Step | None:
        name = self.name(key, path)
        if name is not None:
            if name in self._names:
                self.problem(path, f"step name {name!r} is used more than once")
            self._names.add(name)
        if not isinstance(data, Mapping):
            self.problem(path, "must be a mapping")
            return None
        kinds = [kind for kind in _KIND_KEYS if kind in data]
        if len(kinds) != 1:
            self.problem(path, "must have exactly one of " + ", ".join(_KIND_KEYS))
            return None
        kind = kinds[0]
        self.keys(data, path, allowed=_STEP_KEYS[kind], required=set())
        if name is None:
            return None
        if kind == "handler":
            return TaskStep(
                name,
                handler=self.text(data["handler"], f"{path}.handler"),
                queue=self.queue(data.get("queue", DEFAULT_QUEUE), f"{path}.queue"),
                params=self.params(data.get("params", {}), f"{path}.params"),
                fixed_params=self.fixed(data.get("fixed_params", {}), path),
            )
        if kind == "flow":
            return SubFlowStep(
                name,
                flow=self.text(data["flow"], f"{path}.flow"),
                params=self.params(data.get("params", {}), f"{path}.params"),
                fixed_params=self.fixed(data.get("fixed_params", {}), path),
            )
        if kind == "loop":
            return self.loop(name, data, path)
        return self.fan_out(name, data, path)

    def loop(self, name: str, data: Mapping[str, Any], path: str) -> LoopStep | None:
        config = data["loop"]
        steps = self.steps(data.get("steps"), f"{path}.steps")
        if not isinstance(config, Mapping):
            self.problem(f"{path}.loop", "must be a mapping")
            return None
        self.keys(
            config,
            f"{path}.loop",
            allowed={"max_cycles", "exit_condition"},
            required={"max_cycles", "exit_condition"},
        )
        max_cycles = self.positive(config.get("max_cycles"), f"{path}.loop.max_cycles")
        exit_condition = self.text(
            config.get("exit_condition"), f"{path}.loop.exit_condition"
        )
        if max_cycles is None:
            return None
        return LoopStep(name, max_cycles, exit_condition, steps)

    def fan_out(
        self, name: str, data: Mapping[str, Any], path: str
    ) -> FanOutStep | None:
        config = data["fan_out"]
        steps = self.steps(data.get("steps"), f"{path}.steps")
        if not isinstance(config, Mapping):
            self.problem(f"{path}.fan_out", "must be a mapping")
            return None
        self.keys(config, f"{path}.fan_out", allowed={"range", "over"}, required=set())
        if ("range" in config) == ("over" in config):
            self.problem(f"{path}.fan_out", "must have exactly one of 'range', 'over'")
            return None
        if "range" in config:
            width = self.positive(config["range"], f"{path}.fan_out.range")
            return None if width is None else FanOutStep(name, steps, range=width)
        over = self.reference(config["over"], f"{path}.fan_out.over")
        return None if over is None else FanOutStep(name, steps, over=over)

    def params(self, data: Any, path: str) -> dict[str, Reference]:
        if not isinstance(data, Mapping):
            self.problem(path, "must be a mapping of parameter names to references")
            return {}
        params: dict[str, Reference] = {}
        for key, value in data.items():
            name = self.name(key, f"{path}.{key}")
            reference = self.reference(value, f"{path}.{key}")
            if name is not None and reference is not None:
                params[name] = reference
        return params

    def fixed(self, data: Any, step_path: str) -> dict[str, _values.JsonValue]:
        path = f"{step_path}.fixed_params"
        if not isinstance(data, Mapping):
            self.problem(path, "must be a mapping of parameter names to values")
            return {}
        fixed: dict[str, _values.JsonValue] = {}
        for key, value in data.items():
            name = self.name(key, f"{path}.{key}")
            try:
                # Round-trip to check the value is JSON with valid datetime tags.
                encoded = _values.encode(_values.decode(value))
            except InvalidValueError as exc:
                self.problem(f"{path}.{key}", str(exc))
                continue
            if name is not None:
                fixed[name] = encoded
        return fixed

    def reference(self, value: Any, path: str) -> Reference | None:
        if not isinstance(value, str):
            self.problem(path, f"{value!r} is not a reference like 'tasks.name'")
            return None
        try:
            return Reference.parse(value)
        except ValueError as exc:
            self.problem(path, str(exc))
            return None

    def queue(self, value: Any, path: str) -> str:
        if not isinstance(value, str) or not is_queue_name(value):
            self.problem(
                path, f"{value!r} is not a queue name: letters, digits, _ and -"
            )
            return ""
        return value

    def text(self, value: Any, path: str) -> str:
        if not isinstance(value, str) or not value:
            self.problem(path, "must be a non-empty string")
            return ""
        return value

    def positive(self, value: Any, path: str) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            self.problem(path, f"{value!r} must be a whole number of at least 1")
            return None
        return value
