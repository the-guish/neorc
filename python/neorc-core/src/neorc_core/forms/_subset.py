# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The subset of A2UI v0.9 a form may use, and what a form asks of what it submits.

A form is the list of messages an A2UI agent would send to draw a surface:
one ``createSurface``, the components, and optionally the data to start from.
Of A2UI's basic catalog a form may use these components, and nothing they do
not need; ``FlowPicker`` is this catalog's own:

================  ========================================  ==================
component         properties                                submits
================  ========================================  ==================
``Column``        ``children``: the ids below it            nothing
``Row``           ``children``                              nothing
``Card``          ``child``: the id of the one inside it    nothing
``List``          ``children``; for a task, a template      nothing
``Text``          ``text``, ``variant``                     nothing
``TextField``     ``label``, ``value``, ``variant``,        a string; a number
                  ``checks``                                when its variant
                                                            is ``number``
``CheckBox``      ``label``, ``value``                      a boolean
``ChoicePicker``  ``label``, ``value``, ``options``,        a string: the one
                  ``variant``: ``mutuallyExclusive``,       option chosen
                  ``displayStyle``, ``checks``
``Button``        ``child``, ``variant``, ``action``;       the value its
                  for a task                                action sets
``FlowPicker``    ``label``, ``value``, ``checks``          a string: the name
                                                            of the flow chosen
================  ========================================  ==================

What a form submits is the surface's data: an input named ``repo`` is what
the form wrote at ``/repo``. So each control is bound to
``{"path": "/<name>"}``, and its kind decides the type of what it submits.

A task's form reads more than it writes. The task's inputs seed its data
under ``/inputs``, read only: a ``Text`` may show ``{"path":
"/inputs/review/summary"}``, and a ``List`` may repeat a template once per
item of ``{"path": "/inputs/review/issues"}``. Inside the template, a path
with no leading slash is relative to the item: a ``Text`` shows
``{"path": "title"}``, and a control bound to ``{"path": "decision"}``
submits a list, one value per item, as the result's ``decision``. A
``Button`` completes the task, setting the one field its action's context
names: ``{"verdict": "ready"}``.

A ``FlowPicker`` is a choice of the manager's own flows, which the page
searches as a person types. What it submits names a flow the manager holds:
the checks are given those names (``picked_flows``).

A check is one the manager can keep by itself: ``required`` and ``length`` on
a text field, ``required`` and ``numeric`` on a number field, ``required`` on
a choice and on a choice of a flow. Each is about the control's own value.

Labels and options are plain strings: no binding, and no function call
outside a check.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeGuard

from neorc_core._errors import InvalidValueError
from neorc_core.forms._files import flow_form

VERSION = "v0.9"
"""The A2UI version every message of a form states."""

CATALOG_ID = "https://github.com/open-shipyard/neorc/forms/v1"
"""The catalog a form's surface names: this subset, and no other."""

INPUTS = "inputs"
"""Where a task's form reads the task's inputs: ``/inputs/<input>``."""

_MESSAGES = {
    "createSurface": {"surfaceId", "catalogId"},
    "updateComponents": {"surfaceId", "components"},
    "updateDataModel": {"surfaceId", "path", "value"},
}
_PROPERTIES = {
    "Column": {"children"},
    "Row": {"children"},
    "List": {"children"},
    "Card": {"child"},
    "Text": {"text", "variant"},
    "TextField": {"label", "value", "variant", "checks"},
    "CheckBox": {"label", "value"},
    "ChoicePicker": {"label", "value", "variant", "displayStyle", "options", "checks"},
    "Button": {"child", "variant", "action"},
    "FlowPicker": {"label", "value", "checks"},
}
_TEXT_VARIANTS = ("h1", "h2", "h3", "h4", "h5", "caption", "body")
_FIELD_VARIANTS = ("shortText", "longText", "number")
_DISPLAY_STYLES = ("checkbox", "chips")
_BUTTON_VARIANTS = ("default", "primary", "borderless")

# What a control is, for what follows: the type it submits, and the checks it
# may carry.
_SUBMITS = {
    "text": "string",
    "number": "number",
    "boolean": "boolean",
    "choice": "string",
    "flow": "string",
}
_CHECKS = {
    "text": ("required", "length"),
    "number": ("required", "numeric"),
    "boolean": (),
    "choice": ("required",),
    "flow": ("required",),
}
_PLURAL = {"string": "strings", "number": "numbers", "boolean": "booleans"}


def _kind(component: Mapping[str, Any]) -> str | None:
    """What a component is as a control; ``None`` for one that submits nothing."""
    match component.get("component"):
        case "TextField":
            return "number" if component.get("variant") == "number" else "text"
        case "CheckBox":
            return "boolean"
        case "ChoicePicker":
            return "choice"
        case "FlowPicker":
            return "flow"
    return None


def _path(value: Any) -> tuple[bool, list[str]] | None:
    """A binding read: whether it is absolute, and its segments.

    ``(True, ["inputs", "review"])`` for ``{"path": "/inputs/review"}``, and
    ``(False, ["title"])`` for ``{"path": "title"}``. ``None`` for what is not
    a binding with at least one segment, none of them empty.
    """
    if not isinstance(value, Mapping) or set(value) != {"path"}:
        return None
    path = value["path"]
    if not isinstance(path, str):
        return None
    absolute = path.startswith("/")
    segments = (path[1:] if absolute else path).split("/")
    if not all(segments):
        return None
    return absolute, segments


def _field_of(component: Mapping[str, Any], in_template: bool) -> str | None:
    """The field a control is bound to: ``repo`` for ``{"path": "/repo"}``.

    Inside a template the binding is relative to the item: ``{"path":
    "decision"}``.
    """
    read = _path(component.get("value"))
    if read is None or len(read[1]) != 1 or read[0] == in_template:
        return None
    return read[1][0]


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


def _by_id(form: Any) -> dict[str, Mapping[str, Any]]:
    """The components with an id, the first of each id."""
    by_id: dict[str, Mapping[str, Any]] = {}
    for component in _components(form):
        cid = component.get("id")
        if isinstance(cid, str) and cid:
            by_id.setdefault(cid, component)
    return by_id


def _template(children: Any) -> tuple[str, list[str]] | None:
    """A List's template: the id it repeats, and the segments of its list."""
    if not isinstance(children, Mapping) or set(children) != {"componentId", "path"}:
        return None
    cid, read = children["componentId"], _path({"path": children["path"]})
    if not isinstance(cid, str) or read is None or not read[0]:
        return None
    return cid, read[1]


def _below(component: Mapping[str, Any]) -> list[Any]:
    """The ids a component places under it, as it gives them."""
    match component.get("component"):
        case "Column" | "Row" | "List":
            children = component.get("children")
            if isinstance(children, list):
                return children
            template = _template(children)
            return [template[0]] if template else []
        case "Card" | "Button":
            return [component.get("child")]
    return []


@dataclass
class _Tree:
    """Where each component is: placed under ``root``, and in which template."""

    problems: list[str] = field(default_factory=list)
    lists: dict[str, list[str]] = field(default_factory=dict)
    """For each component inside a template, the segments of its List's path."""


def _tree(by_id: Mapping[str, Mapping[str, Any]]) -> _Tree:
    """``root`` is a Column, and every component is under it exactly once."""
    tree = _Tree()
    root = by_id.get("root")
    if root is None or root.get("component") != "Column":
        tree.problems.append("form: needs a Column with the id 'root'")
        return tree
    placed: set[str] = set()
    pending: list[tuple[str, list[str] | None]] = [("root", None)]
    while pending:
        cid, scope = pending.pop()
        if cid in placed:
            tree.problems.append(f"form.{cid}: placed more than once")
            continue
        placed.add(cid)
        component = by_id[cid]
        if scope is not None:
            tree.lists[cid] = scope
        template = (
            _template(component.get("children"))
            if component.get("component") == "List"
            else None
        )
        if template is not None and scope is not None:
            tree.problems.append(
                f"form.{cid}: a List repeating a template inside another "
                "List's template is left out"
            )
            continue
        inner = template[1] if template is not None else scope
        pending.extend(
            (child, inner)
            for child in _below(component)
            if isinstance(child, str) and child in by_id
        )
    tree.problems.extend(
        f"form.{cid}: not placed under 'root'" for cid in by_id if cid not in placed
    )
    return tree


@dataclass(frozen=True)
class _Control:
    """A control as a result holds it."""

    component: Mapping[str, Any]
    kind: str
    name: str
    items: list[str] | None
    """For a control in a template, the segments of its List's path."""


@dataclass(frozen=True)
class _Buttons:
    """What a form's buttons set: one field, to one of their values."""

    name: str
    values: list[str]


def _controls(form: Any) -> list[_Control]:
    """The controls of a form that is within the subset, each bound once."""
    by_id = _by_id(form)
    lists = _tree(by_id).lists
    controls: list[_Control] = []
    for cid, component in by_id.items():
        kind = _kind(component)
        name = _field_of(component, cid in lists)
        if kind is not None and name is not None:
            controls.append(_Control(component, kind, name, lists.get(cid)))
    return controls


def _action(component: Mapping[str, Any]) -> tuple[str, str] | None:
    """What a Button's action sets: ``("verdict", "ready")``."""
    action = component.get("action")
    event = action.get("event") if isinstance(action, Mapping) else None
    if (
        not isinstance(action, Mapping)
        or set(action) != {"event"}
        or not isinstance(event, Mapping)
        or set(event) != {"name", "context"}
        or not isinstance(event["name"], str)
        or not event["name"]
        or not isinstance(event["context"], Mapping)
        or len(event["context"]) != 1
    ):
        return None
    ((name, value),) = event["context"].items()
    if not isinstance(name, str) or not name or not isinstance(value, str):
        return None
    return name, value


def _buttons(form: Any) -> _Buttons | None:
    """What the buttons of a form within the subset set; ``None`` with none."""
    sets = [
        each
        for component in _by_id(form).values()
        if component.get("component") == "Button"
        and (each := _action(component)) is not None
    ]
    if not sets:
        return None
    return _Buttons(sets[0][0], [value for _, value in sets])


def form_problems(
    form: Any,
    inputs: Mapping[str, str] | None,
    *,
    params: Collection[str] | None = None,
) -> list[str]:
    """Every reason ``form`` is not a form for a flow declaring ``inputs``.

    ``inputs`` maps each input's name to its type, as the flow declares them.
    ``None`` is for a task's form, which declares nothing beside it: its
    controls and buttons say what the fields of the task's result are
    (``form_fields``). ``params`` are then the names of the task's inputs,
    which the form may read under ``/inputs``; ``None`` leaves them unchecked.
    An empty list is a form the UI draws whole, and one ``check_form_inputs``
    and ``check_form_result`` read.
    """
    if isinstance(form, str):
        return [
            f"form: {form!r} names a file, which is read in where the flow's "
            "file is read; a flow that is uploaded holds the form itself"
        ]
    of_task = inputs is None
    if not isinstance(form, list) or not form:
        return ["form: must be a list of A2UI messages"]
    problems = _message_problems(form)

    by_id: dict[str, Mapping[str, Any]] = {}
    for component in _components(form):
        cid = component.get("id")
        if not isinstance(cid, str) or not cid:
            problems.append("form: every component needs an id")
        elif cid in by_id:
            problems.append(f"form.{cid}: id used more than once")
        else:
            by_id[cid] = component
    tree = _tree(by_id)
    context = _Context(of_task, by_id, tree.lists, params)

    controls: dict[str, Mapping[str, Any]] = {}  # by the field each is bound to
    per_item: set[str] = set()  # the fields of controls in a template
    buttons: dict[str, str] = {}  # the value each sets, by the button's id
    for cid, component in by_id.items():
        where = f"form.{cid}"
        name = component.get("component")
        if not isinstance(name, str) or name not in _PROPERTIES:
            problems.append(f"{where}: component {name!r} is not in the subset")
            continue
        for key in component:
            if key not in _PROPERTIES[name] | {"id", "component"}:
                problems.append(f"{where}: {name} has no {key!r} in the subset")
        problems.extend(_component_problems(where, cid, component, context))
        if name == "Button" and (sets := _action(component)) is not None:
            buttons[cid] = sets[1]
        kind = _kind(component)
        if kind is None:
            continue
        in_template = cid in tree.lists
        bound = _field_of(component, in_template)
        what = "field" if of_task else "input"
        if bound is None:
            problems.append(
                f"{where}: value must be {{'path': '<field>'}}, a field of the "
                "item, without a leading slash"
                if in_template
                else f"{where}: value must be {{'path': '/<field>'}}, a field of the "
                "task's result"
                if of_task
                else f"{where}: value must be {{'path': '/<input>'}}, one of the "
                "flow's inputs"
            )
        elif of_task and bound == INPUTS:
            problems.append(f"{where}: the task's inputs at /inputs are read only")
        elif inputs is not None and bound not in inputs:
            problems.append(f"{where}: the flow declares no input {bound!r}")
        elif bound in controls:
            problems.append(f"{where}: {what} {bound!r} already has a control")
        else:
            controls[bound] = component
            if in_template:
                per_item.add(bound)
            if inputs is not None and inputs[bound] != _SUBMITS[kind]:
                problems.append(
                    f"{where}: input {bound!r} is a {inputs[bound]}, and this "
                    f"control submits a {_SUBMITS[kind]}"
                )
    for name in inputs or {}:
        if name not in controls:
            problems.append(f"form: no control for input {name!r}")
    problems.extend(_buttons_problems(by_id, buttons, controls))

    if of_task and not controls and not buttons:
        problems.append(
            "form: needs at least one control or button, for the result to hold"
        )
    problems.extend(tree.problems)
    data = [
        message["updateDataModel"].get("value")
        for message in form
        if isinstance(message, Mapping)
        and isinstance(message.get("updateDataModel"), Mapping)
    ]
    if data and not problems:
        problems.extend(_data_problems(data[0], controls, per_item))
    return problems


def _message_problems(form: list[Any]) -> list[str]:
    """What is wrong with the messages themselves, beside their components."""
    problems: list[str] = []
    surfaces: set[Any] = set()
    created = updates = 0
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
            updates += 1
            if body.get("path", "/") != "/":
                problems.append(f"{where}.{kind}: path must be '/', the whole data")
    if created != 1:
        problems.append("form: must create exactly one surface")
    if len(surfaces) > 1:
        problems.append("form: every message must name the same surface")
    if updates > 1:
        problems.append("form: at most one updateDataModel")
    return problems


@dataclass(frozen=True)
class _Context:
    """What a component's own check needs to know of the form around it."""

    of_task: bool
    by_id: Mapping[str, Mapping[str, Any]]
    lists: Mapping[str, list[str]]
    params: Collection[str] | None


def _input_problem(where: str, segments: list[str], context: _Context) -> str | None:
    """What is wrong with a path into the task's inputs, read from ``where``."""
    if segments[0] != INPUTS or len(segments) < 2:
        return (
            f"{where}: a path from the root reads the task's inputs: "
            "'/inputs/<input>', and below it"
        )
    if context.params is not None and segments[1] not in context.params:
        return f"{where}: the task has no input {segments[1]!r}"
    return None


def _component_problems(
    where: str, cid: str, component: Mapping[str, Any], context: _Context
) -> list[str]:
    """What is wrong with one component's own properties."""
    problems: list[str] = []
    name = component["component"]
    in_template = cid in context.lists
    if "label" in _PROPERTIES[name] and not isinstance(component.get("label"), str):
        problems.append(f"{where}: label must be a string")
    if name in ("Column", "Row", "List"):
        children = component.get("children")
        template = _template(children) if name == "List" else None
        if template is not None:
            child, segments = template
            if not context.of_task:
                problems.append(
                    f"{where}: a template is for a task's form, which reads the "
                    "task's inputs"
                )
            elif problem := _input_problem(where, segments, context):
                problems.append(problem)
            if child not in context.by_id:
                problems.append(f"{where}: no component with id {child!r}")
        elif not isinstance(children, list):
            problems.append(
                f"{where}: children must be a list of component ids"
                + (", or {'componentId': ..., 'path': ...}" if name == "List" else "")
            )
        else:
            for child in children:
                if not isinstance(child, str) or child not in context.by_id:
                    problems.append(f"{where}: no component with id {child!r}")
    if name == "Card" and (
        not isinstance(component.get("child"), str)
        or component["child"] not in context.by_id
    ):
        problems.append(f"{where}: no component with id {component.get('child')!r}")
    if name == "Text":
        problems.extend(_text_problems(where, component, in_template, context))
    if name == "Button":
        problems.extend(_button_problems(where, component, in_template, context))
    if (
        name == "TextField"
        and component.get("variant", "shortText") not in _FIELD_VARIANTS
    ):
        problems.append(f"{where}: variant must be one of {', '.join(_FIELD_VARIANTS)}")
    if name == "ChoicePicker":
        problems.extend(_choice_problems(where, component))
    kind = _kind(component)
    if "checks" in component and kind is not None:
        problems.extend(_check_problems(where, component, kind))
    return problems


def _text_problems(
    where: str, component: Mapping[str, Any], in_template: bool, context: _Context
) -> list[str]:
    problems: list[str] = []
    if component.get("variant", "body") not in _TEXT_VARIANTS:
        problems.append(f"{where}: variant must be one of {', '.join(_TEXT_VARIANTS)}")
    text = component.get("text")
    if isinstance(text, str):
        return problems
    read = _path(text)
    if not context.of_task or read is None:
        problems.append(
            f"{where}: text must be a string, or in a task's form "
            "{'path': ...} into the task's inputs"
            if not context.of_task
            else f"{where}: text must be a string or {{'path': ...}}"
        )
    elif read[0]:
        if problem := _input_problem(where, read[1], context):
            problems.append(problem)
    elif not in_template:
        problems.append(
            f"{where}: a path without a leading slash is relative to an item, "
            "inside a List's template"
        )
    return problems


def _button_problems(
    where: str, component: Mapping[str, Any], in_template: bool, context: _Context
) -> list[str]:
    if not context.of_task:
        return [
            f"{where}: a Button is for a task's form, which it completes; the "
            "page starts a run"
        ]
    problems: list[str] = []
    if in_template:
        problems.append(f"{where}: a Button inside a List's template is left out")
    cid = component.get("child")
    child = context.by_id.get(cid) if isinstance(cid, str) else None
    if (
        child is None
        or child.get("component") != "Text"
        or not isinstance(child.get("text"), str)
    ):
        problems.append(f"{where}: child must be the id of a Text with a string text")
    if component.get("variant", "default") not in _BUTTON_VARIANTS:
        problems.append(
            f"{where}: variant must be one of {', '.join(_BUTTON_VARIANTS)}"
        )
    sets = _action(component)
    if sets is None:
        problems.append(
            f"{where}: action must be {{'event': {{'name': ..., 'context': "
            "{'<field>': '<value>'}}}, one field set to a string"
        )
    elif sets[0] == INPUTS:
        problems.append(f"{where}: the task's inputs at /inputs are read only")
    return problems


def _buttons_problems(
    by_id: Mapping[str, Mapping[str, Any]],
    buttons: Mapping[str, str],
    controls: Mapping[str, Any],
) -> list[str]:
    """The buttons of a form set one field, each to a value of its own."""
    problems: list[str] = []
    seen: dict[str, str] = {}
    first: str | None = None
    for cid, value in buttons.items():
        sets = _action(by_id[cid])
        assert sets is not None
        name = sets[0]
        if first is None:
            first = name
            if name in controls:
                problems.append(f"form.{cid}: field {name!r} already has a control")
        elif name != first:
            problems.append(
                f"form.{cid}: sets {name!r}, and every Button sets the same "
                f"field, here {first!r}"
            )
        if value in seen:
            problems.append(
                f"form.{cid}: {seen[value]} sets {first!r} to {value!r} already"
            )
        seen.setdefault(value, cid)
    return problems


def _choice_problems(where: str, component: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    if component.get("variant") != "mutuallyExclusive":
        problems.append(f"{where}: variant must be 'mutuallyExclusive'")
    if component.get("displayStyle", "checkbox") not in _DISPLAY_STYLES:
        problems.append(
            f"{where}: displayStyle must be one of {', '.join(_DISPLAY_STYLES)}"
        )
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
    return problems


def form_fields(form: Any) -> dict[str, str]:
    """What a form submits: each field's name, and the type of its value.

    ``{"approved": "boolean", "comment": "string"}`` for a check box bound to
    ``/approved`` and a text field bound to ``/comment``. A control in a
    List's template submits a list: ``"list of strings"``. The field the
    buttons set is a ``"string"``. For a task's form these are the fields of
    the task's result.
    """
    fields: dict[str, str] = {}
    for control in _controls(form):
        submits = _SUBMITS[control.kind]
        if control.items is not None:
            submits = f"list of {_PLURAL[submits]}"
        fields.setdefault(control.name, submits)
    buttons = _buttons(form)
    if buttons is not None:
        fields.setdefault(buttons.name, "string")
    return fields


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


def _data_problems(
    value: Any,
    controls: Mapping[str, Mapping[str, Any]],
    per_item: Collection[str],
) -> list[str]:
    """What a form starts from: each control's value, in A2UI's shape for it."""
    if not isinstance(value, Mapping):
        return ["form: updateDataModel's value must be a mapping of inputs to values"]
    problems: list[str] = []
    for name, start in value.items():
        control = controls.get(name) if isinstance(name, str) else None
        if name == INPUTS:
            problems.append(
                "form: updateDataModel sets 'inputs', which are the task's own"
            )
            continue
        if control is None:
            problems.append(
                f"form: updateDataModel sets {name!r}, which has no control"
            )
            continue
        if name in per_item:
            problems.append(
                f"form: updateDataModel sets {name!r}, which has one value per "
                "item of a list"
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


def picked_flows(form: Any, values: Any) -> set[str]:
    """The flows ``values`` name where ``form`` has a FlowPicker.

    ``values`` are a run's inputs for a flow's form, and a result for a
    task's. A check of them is given the ones of these names the manager
    holds, as its ``flows``: the form cannot look a flow up by itself.
    """
    picked: set[str] = set()
    for control in _controls(form):
        if control.kind != "flow" or not isinstance(values, Mapping):
            continue
        value = values.get(control.name)
        for each in value if isinstance(value, list) else [value]:
            if isinstance(each, str):
                picked.add(each)
    return picked


def check_form_inputs(
    flow: str, content: Any, inputs: Mapping[str, Any], *, flows: Collection[str] = ()
) -> None:
    """Raise ``InvalidValueError`` unless ``inputs`` keep what the form asks.

    ``content`` is the flow as it is stored, and ``inputs`` a run's inputs,
    already of their declared types. A flow with no form asks nothing here.
    What is asked is what the form's controls say: a choice is one of its
    options, a choice of a flow names one of ``flows``, and each check holds.
    ``flows`` are the flows the manager holds, of those ``picked_flows`` names.
    """
    problems: list[str] = []
    for control in _controls(flow_form(content)):
        if control.name in inputs:
            problems.extend(
                f"input {control.name!r} {problem}"
                for problem in _unkept(control, inputs[control.name], flows)
            )
    if problems:
        raise InvalidValueError(f"{flow}: " + "; ".join(problems))


def _items_of(segments: list[str], inputs: Mapping[str, Any]) -> list[Any] | None:
    """The list a template repeats over, read from a task's inputs; ``None``
    when there is no list there.

    ``segments`` are the List's path, ``["inputs", "review", "issues"]``.
    """
    value: Any = {INPUTS: inputs}
    for segment in segments:
        value = value.get(segment) if isinstance(value, Mapping) else None
    return value if isinstance(value, list) else None


def check_form_result(
    step: str,
    form: Any,
    result: Any,
    inputs: Mapping[str, Any] | None = None,
    *,
    flows: Collection[str] = (),
) -> None:
    """Raise ``InvalidValueError`` unless ``result`` is what a task's form submits.

    ``form`` is the form of the task's step, ``result`` the result a
    completion gives, in its JSON form, and ``inputs`` the task's inputs, as a
    worker would receive them. The result is a mapping with the form's fields
    and no other, each of the type its control submits; a choice is one of
    its options, a choice of a flow names one of ``flows``, and each check
    holds. A control in a List's template submits one value per item of the
    list in ``inputs``. The field the buttons set is one of their values. That
    is for every completion, whoever sends it. ``flows`` are the flows the
    manager holds, of those ``picked_flows`` names.
    """
    fields = form_fields(form)
    if not isinstance(result, Mapping):
        listed = ", ".join(fields)
        raise InvalidValueError(
            f"{step}: the result must be a mapping with the form's fields: {listed}"
        )
    problems = [f"no field {name!r}" for name in fields if name not in result]
    problems.extend(
        f"the form has no field {name!r}" for name in result if name not in fields
    )
    for control in _controls(form):
        if control.name not in result:
            continue
        value = result[control.name]
        submits = _SUBMITS[control.kind]
        if control.items is None:
            if not _is_a(value, submits):
                problems.append(f"field {control.name!r} must be a {submits}")
            else:
                problems.extend(
                    f"field {control.name!r} {problem}"
                    for problem in _unkept(control, value, flows)
                )
            continue
        where = "/" + "/".join(control.items)
        items = _items_of(control.items, inputs or {})
        if items is None:
            problems.append(f"the task's inputs have no list at {where}")
        elif not isinstance(value, list) or not all(_is_a(v, submits) for v in value):
            problems.append(
                f"field {control.name!r} must be a list of {_PLURAL[submits]}, "
                f"one per item of {where}"
            )
        elif len(value) != len(items):
            problems.append(
                f"field {control.name!r} has {len(value)} values, and {where} "
                f"has {len(items)} items"
            )
        else:
            problems.extend(
                f"field {control.name!r}, item {index}, {problem}"
                for index, each in enumerate(value, start=1)
                for problem in _unkept(control, each, flows)
            )
    buttons = _buttons(form)
    if (
        buttons is not None
        and buttons.name in result
        and result[buttons.name] not in buttons.values
    ):
        listed = ", ".join(buttons.values)
        problems.append(f"field {buttons.name!r} is not one of {listed}")
    if problems:
        raise InvalidValueError(f"{step}: " + "; ".join(problems))


def _is_a(value: Any, submits: str) -> bool:
    if submits == "boolean":
        return isinstance(value, bool)
    if submits == "number":
        return _is_number(value)
    return isinstance(value, str)


def _unkept(control: _Control, value: Any, flows: Collection[str]) -> list[str]:
    """How one value breaks what its control says, each the end of a sentence.

    ``flows`` are the flows the manager holds, for a choice of one.
    """
    component = control.component
    if control.kind == "flow":
        return [] if value in flows else [f"names no flow {value!r}"]
    if control.kind == "choice":
        options = [
            option.get("value")
            for option in component.get("options", [])
            if isinstance(option, Mapping)
        ]
        if value not in options:
            listed = ", ".join(str(option) for option in options)
            return [f"is not one of {listed}"]
        return []
    checks = component.get("checks")
    return [
        problem
        for check in (checks if isinstance(checks, list) else [])
        if (problem := _broken(check, value))
    ]


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
