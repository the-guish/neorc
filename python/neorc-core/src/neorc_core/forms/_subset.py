# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The subset of A2UI v0.9 a form may use, and what a form asks of a run's inputs.

A form is the list of messages an A2UI agent would send to draw a surface:
one ``createSurface``, the components, and optionally the data to start from.
Of A2UI's basic catalog a form may use five components, and nothing they do
not need:

==============  ==========================================  ================
component       properties                                  submits
==============  ==========================================  ================
``Column``      ``children``: the ids below it              nothing
``Text``        ``text``, ``variant``                       nothing
``TextField``   ``label``, ``value``, ``variant``,          a string; a
                ``checks``                                  number when its
                                                            variant is
                                                            ``number``
``CheckBox``    ``label``, ``value``                        a boolean
``ChoicePicker``  ``label``, ``value``, ``options``,        a string: the one
                ``variant``: ``mutuallyExclusive``,         option chosen
                ``checks``
==============  ==========================================  ================

A form has no button. The page that draws it submits, and what it submits is
the surface's data: an input named ``repo`` is what the form wrote at
``/repo``. So each of the flow's inputs has exactly one control, bound to
``{"path": "/<input>"}``, and a control's kind decides the type the input must
declare.

A check is one the manager can keep for a run started with no form in
between: ``required`` and ``length`` on a text field, ``required`` and
``numeric`` on a number field, ``required`` on a choice. Each is about the
control's own value.

Labels, texts and options are plain strings: no binding and no function call
outside a check.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeGuard

from neorc_core._errors import InvalidValueError

VERSION = "v0.9"
"""The A2UI version every message of a form states."""

CATALOG_ID = "https://github.com/open-shipyard/neorc/forms/v1"
"""The catalog a form's surface names: this subset, and no other."""

_MESSAGES = {
    "createSurface": {"surfaceId", "catalogId"},
    "updateComponents": {"surfaceId", "components"},
    "updateDataModel": {"surfaceId", "path", "value"},
}
_PROPERTIES = {
    "Column": {"children"},
    "Text": {"text", "variant"},
    "TextField": {"label", "value", "variant", "checks"},
    "CheckBox": {"label", "value"},
    "ChoicePicker": {"label", "value", "variant", "options", "checks"},
}
_TEXT_VARIANTS = ("h1", "h2", "h3", "h4", "h5", "caption", "body")
_FIELD_VARIANTS = ("shortText", "longText", "number")

# What a control is, for what follows: the input type it submits, and the
# checks it may carry.
_SUBMITS = {
    "text": "string",
    "number": "number",
    "boolean": "boolean",
    "choice": "string",
}
_CHECKS = {
    "text": ("required", "length"),
    "number": ("required", "numeric"),
    "boolean": (),
    "choice": ("required",),
}


def _kind(component: Mapping[str, Any]) -> str | None:
    """What a component is as a control; ``None`` for one that submits nothing."""
    match component.get("component"):
        case "TextField":
            return "number" if component.get("variant") == "number" else "text"
        case "CheckBox":
            return "boolean"
        case "ChoicePicker":
            return "choice"
    return None


def _input_of(component: Mapping[str, Any]) -> str | None:
    """The input a control is bound to: ``repo`` for ``{"path": "/repo"}``."""
    value = component.get("value")
    if not isinstance(value, Mapping) or set(value) != {"path"}:
        return None
    path = value["path"]
    if not isinstance(path, str) or not path.startswith("/") or "/" in path[1:]:
        return None
    return path[1:] or None


def _is_number(value: Any) -> TypeGuard[float]:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _components(form: Any) -> list[Mapping[str, Any]]:
    """Every component a form lists, as far as it can be read."""
    listed: list[Mapping[str, Any]] = []
    for message in form if isinstance(form, list) else []:
        body = message.get("updateComponents") if isinstance(message, Mapping) else None
        components = body.get("components") if isinstance(body, Mapping) else None
        for component in components if isinstance(components, list) else []:
            if isinstance(component, Mapping):
                listed.append(component)
    return listed


def form_problems(form: Any, inputs: Mapping[str, str]) -> list[str]:
    """Every reason ``form`` is not a form for a flow declaring ``inputs``.

    ``inputs`` maps each input's name to its type, as the flow declares them.
    An empty list is a form the UI draws whole and ``check_form_inputs`` reads.
    """
    if isinstance(form, str):
        return [
            f"form: {form!r} names a file, which is read in where the flow's "
            "file is read; a flow that is uploaded holds the form itself"
        ]
    if not isinstance(form, list) or not form:
        return ["form: must be a list of A2UI messages"]
    problems: list[str] = []
    surfaces: set[Any] = set()
    created = 0
    data: list[Any] = []
    for index, message in enumerate(form):
        where = f"form[{index}]"
        if not isinstance(message, Mapping):
            problems.append(f"{where}: must be an A2UI message")
            continue
        if message.get("version") != VERSION:
            problems.append(f"{where}: version must be {VERSION!r}")
        kinds = [key for key in message if key != "version"]
        if len(kinds) != 1 or kinds[0] not in _MESSAGES:
            problems.append(f"{where}: must be one of {', '.join(_MESSAGES)}")
            continue
        kind = kinds[0]
        body = message[kind]
        if not isinstance(body, Mapping):
            problems.append(f"{where}.{kind}: must be a mapping")
            continue
        for key in body:
            if key not in _MESSAGES[kind]:
                problems.append(f"{where}.{kind}: {key!r} is not in the subset")
        surface = body.get("surfaceId")
        if not isinstance(surface, str) or not surface:
            problems.append(f"{where}.{kind}: surfaceId must be a non-empty string")
        else:
            surfaces.add(surface)
        if kind == "createSurface":
            created += 1
            if body.get("catalogId") != CATALOG_ID:
                problems.append(f"{where}.{kind}: catalogId must be {CATALOG_ID!r}")
        elif kind == "updateComponents":
            if not isinstance(body.get("components"), list):
                problems.append(f"{where}.{kind}: components must be a list")
        else:
            if body.get("path", "/") != "/":
                problems.append(f"{where}.{kind}: path must be '/', the whole data")
            data.append(body.get("value"))
    if created != 1:
        problems.append("form: must create exactly one surface")
    if len(surfaces) > 1:
        problems.append("form: every message must name the same surface")
    if len(data) > 1:
        problems.append("form: at most one updateDataModel")

    by_id: dict[str, Mapping[str, Any]] = {}
    for component in _components(form):
        cid = component.get("id")
        if not isinstance(cid, str) or not cid:
            problems.append("form: every component needs an id")
        elif cid in by_id:
            problems.append(f"form.{cid}: id used more than once")
        else:
            by_id[cid] = component

    controls: dict[str, Mapping[str, Any]] = {}  # by the input each is bound to
    for cid, component in by_id.items():
        where = f"form.{cid}"
        name = component.get("component")
        if not isinstance(name, str) or name not in _PROPERTIES:
            problems.append(f"{where}: component {name!r} is not in the subset")
            continue
        for key in component:
            if key not in _PROPERTIES[name] | {"id", "component"}:
                problems.append(f"{where}: {name} has no {key!r} in the subset")
        problems.extend(_component_problems(where, component, by_id))
        kind = _kind(component)
        if kind is None:
            continue
        bound = _input_of(component)
        if bound is None:
            problems.append(
                f"{where}: value must be {{'path': '/<input>'}}, one of the "
                "flow's inputs"
            )
        elif bound not in inputs:
            problems.append(f"{where}: the flow declares no input {bound!r}")
        elif bound in controls:
            problems.append(f"{where}: input {bound!r} already has a control")
        else:
            controls[bound] = component
            if inputs[bound] != _SUBMITS[kind]:
                problems.append(
                    f"{where}: input {bound!r} is a {inputs[bound]}, and this "
                    f"control submits a {_SUBMITS[kind]}"
                )
    for name in inputs:
        if name not in controls:
            problems.append(f"form: no control for input {name!r}")

    problems.extend(_tree_problems(by_id))
    if data and not problems:
        problems.extend(_data_problems(data[0], controls))
    return problems


def _component_problems(
    where: str, component: Mapping[str, Any], by_id: Mapping[str, Any]
) -> list[str]:
    """What is wrong with one component's own properties."""
    problems: list[str] = []
    name = component["component"]
    for key in ("label", "text"):
        if key in _PROPERTIES[name] and not isinstance(component.get(key), str):
            problems.append(f"{where}: {key} must be a string")
    if name == "Column":
        children = component.get("children")
        if not isinstance(children, list):
            problems.append(f"{where}: children must be a list of component ids")
        else:
            for child in children:
                if not isinstance(child, str) or child not in by_id:
                    problems.append(f"{where}: no component with id {child!r}")
    if name == "Text" and component.get("variant", "body") not in _TEXT_VARIANTS:
        problems.append(f"{where}: variant must be one of {', '.join(_TEXT_VARIANTS)}")
    if (
        name == "TextField"
        and component.get("variant", "shortText") not in _FIELD_VARIANTS
    ):
        problems.append(f"{where}: variant must be one of {', '.join(_FIELD_VARIANTS)}")
    if name == "ChoicePicker":
        if component.get("variant") != "mutuallyExclusive":
            problems.append(f"{where}: variant must be 'mutuallyExclusive'")
        options = component.get("options")
        values = [
            option.get("value")
            for option in (options if isinstance(options, list) else [])
            if isinstance(option, Mapping)
            and set(option) == {"label", "value"}
            and isinstance(option["label"], str)
            and isinstance(option["value"], str)
        ]
        if not isinstance(options, list) or not options or len(values) != len(options):
            problems.append(
                f"{where}: options must be a non-empty list of "
                "{'label': ..., 'value': ...}, both strings"
            )
        elif len(set(values)) != len(values):
            problems.append(f"{where}: two options have the same value")
    kind = _kind(component)
    if "checks" in component and kind is not None:
        problems.extend(_check_problems(where, component, kind))
    return problems


def _check_problems(where: str, component: Mapping[str, Any], kind: str) -> list[str]:
    checks = component["checks"]
    if not isinstance(checks, list):
        return [f"{where}: checks must be a list"]
    problems: list[str] = []
    for index, check in enumerate(checks):
        at = f"{where}.checks[{index}]"
        condition = check.get("condition") if isinstance(check, Mapping) else None
        if (
            not isinstance(check, Mapping)
            or set(check) != {"condition", "message"}
            or not isinstance(check["message"], str)
            or not isinstance(condition, Mapping)
            or not set(condition) <= {"call", "args", "returnType"}
            or not isinstance(condition.get("args"), Mapping)
        ):
            problems.append(
                f"{at}: must be {{'condition': {{'call': ..., 'args': ...}}, "
                "'message': ...}"
            )
            continue
        call = condition.get("call")
        args = condition["args"]
        if call not in _CHECKS[kind]:
            allowed = ", ".join(_CHECKS[kind]) or "none"
            problems.append(f"{at}: {call!r} is not a check of this control: {allowed}")
            continue
        if args.get("value") != component.get("value"):
            problems.append(f"{at}: must be about the control's own value")
        limits = {key: args[key] for key in args if key != "value"}
        if call == "required":
            wanted = not limits
        elif call == "length":
            wanted = bool(limits) and all(
                key in ("min", "max")
                and isinstance(limit, int)
                and not isinstance(limit, bool)
                and limit >= 0
                for key, limit in limits.items()
            )
        else:
            wanted = bool(limits) and all(
                key in ("min", "max") and _is_number(limit)
                for key, limit in limits.items()
            )
        if not wanted:
            problems.append(f"{at}: {call} takes {_ARGS[str(call)]}")
    return problems


_ARGS = {
    "required": "the value and nothing else",
    "length": "min, max or both, whole numbers of at least 0",
    "numeric": "min, max or both, numbers",
}


def _tree_problems(by_id: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """``root`` is a Column, and every component is under it exactly once."""
    root = by_id.get("root")
    if root is None or root.get("component") != "Column":
        return ["form: needs a Column with the id 'root'"]
    problems: list[str] = []
    placed: set[str] = set()
    pending = ["root"]
    while pending:
        cid = pending.pop()
        if cid in placed:
            problems.append(f"form.{cid}: placed more than once")
            continue
        placed.add(cid)
        children = by_id[cid].get("children")
        if by_id[cid].get("component") == "Column" and isinstance(children, list):
            pending.extend(
                child for child in children if isinstance(child, str) and child in by_id
            )
    problems.extend(
        f"form.{cid}: not placed under 'root'" for cid in by_id if cid not in placed
    )
    return problems


def _data_problems(value: Any, controls: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """What a form starts from: each control's value, in A2UI's shape for it."""
    if not isinstance(value, Mapping):
        return ["form: updateDataModel's value must be a mapping of inputs to values"]
    problems: list[str] = []
    for name, start in value.items():
        control = controls.get(name) if isinstance(name, str) else None
        if control is None:
            problems.append(
                f"form: updateDataModel sets {name!r}, which has no control"
            )
            continue
        kind = _kind(control)
        if kind == "choice":
            options = [option["value"] for option in control["options"]]
            fits = (
                isinstance(start, list)
                and len(start) <= 1
                and all(chosen in options for chosen in start)
            )
            shape = "a list of at most one of its options' values"
        elif kind == "boolean":
            fits, shape = isinstance(start, bool), "true or false"
        elif kind == "number":
            fits, shape = isinstance(start, str) or _is_number(start), "a number"
        else:
            fits, shape = isinstance(start, str), "a string"
        if not fits:
            problems.append(f"form: updateDataModel must set {name!r} to {shape}")
    return problems


def check_form_inputs(flow: str, content: Any, inputs: Mapping[str, Any]) -> None:
    """Raise ``InvalidValueError`` unless ``inputs`` keep what the form asks.

    ``content`` is the flow as it is stored, and ``inputs`` a run's inputs,
    already of their declared types. A flow with no form asks nothing here.
    What is asked is what the form's controls say: a choice is one of its
    options, and each check holds.
    """
    form = content.get("form") if isinstance(content, Mapping) else None
    problems: list[str] = []
    for component in _components(form):
        kind, name = _kind(component), _input_of(component)
        if kind is None or name is None or name not in inputs:
            continue
        value = inputs[name]
        if kind == "choice":
            options = [
                option.get("value")
                for option in component.get("options", [])
                if isinstance(option, Mapping)
            ]
            if value not in options:
                listed = ", ".join(str(option) for option in options)
                problems.append(f"input {name!r} is not one of {listed}")
            continue
        checks = component.get("checks")
        for check in checks if isinstance(checks, list) else []:
            problem = _broken(check, value)
            if problem:
                problems.append(f"input {name!r} {problem}")
    if problems:
        raise InvalidValueError(f"{flow}: " + "; ".join(problems))


def _broken(check: Any, value: Any) -> str | None:
    """How ``value`` breaks ``check``, as the end of a sentence; ``None`` if not."""
    condition = check.get("condition") if isinstance(check, Mapping) else None
    args = condition.get("args") if isinstance(condition, Mapping) else None
    if not isinstance(condition, Mapping) or not isinstance(args, Mapping):
        return None
    low, high = args.get("min"), args.get("max")
    match condition.get("call"):
        case "required" if value == "":
            return "is empty"
        case "length" if isinstance(value, str):
            if _is_number(low) and len(value) < low:
                return f"has fewer than {low} characters"
            if _is_number(high) and len(value) > high:
                return f"has more than {high} characters"
        case "numeric" if _is_number(value):
            if _is_number(low) and value < low:
                return f"is below {low}"
            if _is_number(high) and value > high:
                return f"is above {high}"
    return None
