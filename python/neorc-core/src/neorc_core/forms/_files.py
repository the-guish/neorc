# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Reading a form file in, where a flow file names one.

A flow file says ``form: code_task.form.json``; what is uploaded, stored and
validated holds the form itself under ``form``. So a form is versioned with
its flow, and a manager never reads a file a flow names.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from neorc_core._errors import FlowDefinitionError


def inline_form(
    data: Any, directory: Path | None, read_json: Callable[[str], Any]
) -> Any:
    """``data``, a flow file's structure, with the form files it names read in.

    The flow's own form, and the form of each task that names one, in loops,
    fan-outs and the cases of switches too. ``directory`` is where the flow
    file is, ``None`` for a flow that was not read from a file. ``read_json``
    parses a form's text and raises ``FlowDefinitionError`` for what is not
    JSON. A structure that names no file is returned as it is.

    Raises ``FlowDefinitionError`` for a name that is not a file next to the
    flow, and for a file that cannot be read.
    """
    if not isinstance(data, Mapping):
        return data
    read = _read_named(data, "form", directory, read_json)
    if isinstance(read.get("steps"), Mapping):
        read = {
            **read,
            "steps": _inline_steps(read["steps"], "steps", directory, read_json),
        }
    return read


def step_forms(content: Any) -> dict[str, Any]:
    """The form of each step of a flow that has one, by the step's name.

    ``content`` is a flow as it is uploaded and stored. Steps in loops,
    fan-outs and the cases of switches are read too: a step's name is its own
    within a flow.
    """
    forms: dict[str, Any] = {}
    pending = [content.get("steps") if isinstance(content, Mapping) else None]
    while pending:
        steps = pending.pop()
        for name, step in steps.items() if isinstance(steps, Mapping) else ():
            if not isinstance(step, Mapping):
                continue
            if "form" in step and isinstance(name, str):
                forms[name] = step["form"]
            pending.append(step.get("steps"))
            pending.extend(
                case.get("steps")
                for case in _cases(step).values()
                if isinstance(case, Mapping)
            )
    return forms


def _cases(step: Mapping[Any, Any]) -> Mapping[Any, Any]:
    """The cases of a step that is a switch, by name; none for any other step."""
    switch = step.get("switch")
    cases = switch.get("cases") if isinstance(switch, Mapping) else None
    return cases if isinstance(cases, Mapping) else {}


def _inline_steps(
    steps: Mapping[Any, Any],
    path: str,
    directory: Path | None,
    read_json: Callable[[str], Any],
) -> dict[Any, Any]:
    read: dict[Any, Any] = {}
    for name, step in steps.items():
        where = f"{path}.{name}"
        if isinstance(step, Mapping):
            step = _read_named(step, f"{where}.form", directory, read_json)
            if isinstance(step.get("steps"), Mapping):
                step = {
                    **step,
                    "steps": _inline_steps(
                        step["steps"], f"{where}.steps", directory, read_json
                    ),
                }
            cases = _cases(step)
            if cases:
                at = f"{where}.switch.cases"
                switch = {
                    **step["switch"],
                    "cases": {
                        key: _inline_case(case, f"{at}.{key}", directory, read_json)
                        for key, case in cases.items()
                    },
                }
                step = {**step, "switch": switch}
        read[name] = step
    return read


def _inline_case(
    case: Any, where: str, directory: Path | None, read_json: Callable[[str], Any]
) -> Any:
    if not isinstance(case, Mapping) or not isinstance(case.get("steps"), Mapping):
        return case
    steps = _inline_steps(case["steps"], f"{where}.steps", directory, read_json)
    return {**case, "steps": steps}


def _read_named(
    data: Mapping[Any, Any],
    where: str,
    directory: Path | None,
    read_json: Callable[[str], Any],
) -> Mapping[Any, Any]:
    """``data`` with the file its ``form`` names read in; ``where`` is for errors."""
    if not isinstance(data.get("form"), str):
        return data
    name: str = data["form"]
    if directory is None:
        raise FlowDefinitionError(
            [f"{where}: names the file {name!r}, and this flow was not read from one"]
        )
    if name in ("", ".", "..") or Path(name).name != name:
        raise FlowDefinitionError(
            [f"{where}: {name!r} must be the name of a file next to the flow"]
        )
    try:
        text = (directory / name).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        reason = exc.strerror if isinstance(exc, OSError) else "it is not UTF-8"
        raise FlowDefinitionError([f"{where}: cannot read {name}: {reason}"]) from None
    try:
        form = read_json(text)
    except FlowDefinitionError as exc:
        raise FlowDefinitionError(
            [f"{where}: {name}: {problem}" for problem in exc.problems]
        ) from None
    return {**data, "form": form}
