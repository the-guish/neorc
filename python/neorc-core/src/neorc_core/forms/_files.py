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
    """``data``, a flow file's structure, with the form file it names read in.

    ``directory`` is where the flow file is, ``None`` for a flow that was not
    read from a file. ``read_json`` parses the form's text and raises
    ``FlowDefinitionError`` for what is not JSON. A structure that names no
    file is returned as it is.

    Raises ``FlowDefinitionError`` for a name that is not a file next to the
    flow, and for a file that cannot be read.
    """
    if not isinstance(data, Mapping) or not isinstance(data.get("form"), str):
        return data
    name: str = data["form"]
    if directory is None:
        raise FlowDefinitionError(
            [f"form: names the file {name!r}, and this flow was not read from one"]
        )
    if name in ("", ".", "..") or Path(name).name != name:
        raise FlowDefinitionError(
            [f"form: {name!r} must be the name of a file next to the flow"]
        )
    try:
        text = (directory / name).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        reason = exc.strerror if isinstance(exc, OSError) else "it is not UTF-8"
        raise FlowDefinitionError([f"form: cannot read {name}: {reason}"]) from None
    try:
        form = read_json(text)
    except FlowDefinitionError as exc:
        raise FlowDefinitionError(
            [f"form: {name}: {problem}" for problem in exc.problems]
        ) from None
    return {**data, "form": form}
