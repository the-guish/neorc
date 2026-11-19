# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""``queues.toml``: the queues of a folder of flow files, each with its kind.

Flows and queues are many to many: a flow names several queues, and a queue
serves several flows. A queue's kind belongs to the deployment, so it is
written once, beside the flow files, rather than in any one of them::

    [queues.editors]
    kind = "external"

An upload declares each queue of the file before the flows, which may then
name it. A folder without the file names no queue.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from neorc_core._errors import FlowDefinitionError
from neorc_core._queues import QueueKind
from neorc_core.flows._definition import is_queue_name

QUEUE_FILE = "queues.toml"
"""The file's name, beside the flow files."""

_QUEUE_KEYS = frozenset({"kind"})


def read_queues(directory: Path) -> dict[str, QueueKind]:
    """The queues ``directory``'s ``queues.toml`` names, by name, with their kinds.

    Raises ``FlowDefinitionError`` listing every problem, each naming the file.
    """
    path = directory / QUEUE_FILE
    if not path.is_file():
        return {}
    try:
        settings = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise FlowDefinitionError([f"{QUEUE_FILE}: {exc}"]) from None
    problems = [
        f"{QUEUE_FILE} {key}: unknown key"
        for key in sorted(settings)
        if key != "queues"
    ]
    queues = _queues(settings.get("queues", {}), problems)
    if problems:
        raise FlowDefinitionError(problems)
    return queues


def _queues(tables: Any, problems: list[str]) -> dict[str, QueueKind]:
    if not isinstance(tables, dict):
        problems.append(f"{QUEUE_FILE} queues: a table of queues by name")
        return {}
    found: dict[str, QueueKind] = {}
    for name, table in tables.items():
        here = f"{QUEUE_FILE} [queues.{name}]"
        if not is_queue_name(name):
            problems.append(
                f"{here}: {name!r} is not a queue name: letters, digits, _ and -"
            )
            continue
        if not isinstance(table, dict):
            problems.append(f"{here}: a table with kind")
            continue
        problems += [
            f"{here} {key}: unknown key" for key in sorted(table.keys() - _QUEUE_KEYS)
        ]
        kind = table.get("kind")
        if kind not in {k.value for k in QueueKind}:
            problems.append(f"{here} kind: worker or external")
            continue
        found[name] = QueueKind(kind)
    return found
