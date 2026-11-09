# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Rows of the flow tables to and from the core's dataclasses.

Addresses and references are written in their ``neorc_core._wire`` forms, so
there is one textual form of each, in the database as on the wire. Values are
the compact JSON of ``_values.dumps_json``, read back with ``json.loads``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from neorc.postgres._driver import Record
from neorc_core import _values, _wire
from neorc_core._runs import Event, EventKind, Run, RunStatus, StoredFlow, Task
from neorc_core._runs import canonical_content as _canonical_content
from neorc_core._task import TaskStatus
from neorc_core._values import JsonValue
from neorc_core.flows import Version, parse_flow

Row = Record
"""A row as asyncpg hands it over: read by column name."""

FLOW_COLUMNS = "name, major, minor, patch, content"
RUN_COLUMNS = (
    "id, flow, version, inputs, status, root_id, parent_id, parent_address, "
    "output, reason, created_at, finished_at"
)
TASK_COLUMNS = (
    "id, run_id, address, queue, handler, params, fixed_params, status, "
    "attempts, lease_expires_at, result, error, created_at, started_at, finished_at"
)
EVENT_COLUMNS = "sequence, run_id, kind"


def placeholders(columns: str) -> str:
    """``$1, $2, ...``, one for each of ``columns``."""
    return ", ".join(f"${n}" for n in range(1, len(columns.split(",")) + 1))


def values(row: Mapping[str, Any], columns: str) -> tuple[Any, ...]:
    """The values of ``row`` for ``columns``, in their order, to bind as arguments."""
    return tuple(row[column.strip()] for column in columns.split(","))


def flow_to_row(flow: StoredFlow) -> dict[str, Any]:
    return {
        "name": flow.name,
        "major": flow.version.major,
        "minor": flow.version.minor,
        "patch": flow.version.patch,
        "content": _canonical_content(flow.content),
    }


def flow_from_row(row: Row) -> StoredFlow:
    content = json.loads(row["content"])
    return StoredFlow(parse_flow(content), content)


def run_to_row(run: Run) -> dict[str, Any]:
    return {
        "id": run.id,
        "flow": run.flow,
        "version": str(run.version),
        "inputs": _values.dumps_json(dict(run.inputs)),
        "status": run.status.value,
        "root_id": run.root_id,
        "parent_id": run.parent_id,
        "parent_address": (
            _values.dumps_json(_wire.address_to(run.parent_address))
            if run.parent_address is not None
            else None
        ),
        "output": _values.dumps_json(run.output),
        "reason": run.reason,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
    }


def run_from_row(row: Row) -> Run:
    return Run(
        id=row["id"],
        flow=row["flow"],
        version=Version.parse(row["version"]),
        inputs=json.loads(row["inputs"]),
        status=RunStatus(row["status"]),
        root_id=row["root_id"],
        parent_id=row["parent_id"],
        parent_address=(
            _wire.address_from(json.loads(row["parent_address"]))
            if row["parent_address"] is not None
            else None
        ),
        output=json.loads(row["output"]),
        reason=row["reason"],
        created_at=row["created_at"],
        finished_at=row["finished_at"],
    )


def task_to_row(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "run_id": task.run_id,
        "address": _values.dumps_json(_wire.address_to(task.address)),
        "queue": task.queue,
        "handler": task.handler,
        "params": _values.dumps_json(
            dict[str, JsonValue](_wire.params_to(task.params))
        ),
        "fixed_params": _values.dumps_json(dict(task.fixed_params)),
        "status": task.status.value,
        "attempts": task.attempts,
        "lease_expires_at": task.lease_expires_at,
        "result": _values.dumps_json(task.result),
        "error": task.error,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
    }


def task_from_row(row: Row) -> Task:
    return Task(
        id=row["id"],
        run_id=row["run_id"],
        address=_wire.address_from(json.loads(row["address"])),
        queue=row["queue"],
        handler=row["handler"],
        params=_wire.params_from(json.loads(row["params"])),
        fixed_params=json.loads(row["fixed_params"]),
        status=TaskStatus(row["status"]),
        attempts=row["attempts"],
        lease_expires_at=row["lease_expires_at"],
        result=json.loads(row["result"]),
        error=row["error"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def event_to_row(event: Event) -> dict[str, Any]:
    return {
        "sequence": event.sequence,
        "run_id": event.run_id,
        "kind": event.kind.value,
    }


def event_from_row(row: Row) -> Event:
    return Event(row["sequence"], row["run_id"], EventKind(row["kind"]))
