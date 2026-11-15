# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Rows of the SQLite tables to and from the core's dataclasses.

Addresses, references and values are written as the Postgres store writes
them: their ``neorc_core._wire`` forms and the compact JSON of
``_values.dumps_json``. Ids are UUID text, times microseconds since the epoch.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from neorc_core import _values, _wire
from neorc_core._runs import (
    Event,
    EventKind,
    Run,
    RunStarter,
    RunStatus,
    StarterKind,
    StoredFlow,
    Task,
)
from neorc_core._runs import canonical_content as _canonical_content
from neorc_core._task import TaskStatus
from neorc_core._values import JsonValue
from neorc_core.flows import Address, Version, parse_flow

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)

FLOW_COLUMNS = "name, major, minor, patch, content"
RUN_COLUMNS = (
    "id, flow, version, inputs, status, root_id, parent_id, parent_address, "
    "output, reason, created_at, finished_at, starter_kind, starter_id, starter_name"
)
TASK_COLUMNS = (
    "id, run_id, address, queue, handler, params, fixed_params, status, "
    "attempts, lease_expires_at, result, error, created_at, started_at, finished_at"
)
EVENT_COLUMNS = "sequence, run_id, kind"


def placeholders(columns: str) -> str:
    """``:a, :b, ...``, a named placeholder for each of ``columns``."""
    return ", ".join(f":{column.strip()}" for column in columns.split(","))


def time_to(moment: datetime) -> int:
    """A time as stored: whole microseconds since the epoch."""
    return (moment - EPOCH) // _MICROSECOND


def time_from(stored: int | None) -> datetime | None:
    return None if stored is None else EPOCH + stored * _MICROSECOND


def id_to(value: uuid.UUID | None) -> str | None:
    return None if value is None else str(value)


def address_to(address: Address) -> str:
    return _values.dumps_json(_wire.address_to(address))


def flow_to_row(flow: StoredFlow) -> dict[str, Any]:
    return {
        "name": flow.name,
        "major": flow.version.major,
        "minor": flow.version.minor,
        "patch": flow.version.patch,
        "content": _canonical_content(flow.content),
    }


def flow_from_row(row: sqlite3.Row) -> StoredFlow:
    content = json.loads(row["content"])
    return StoredFlow(parse_flow(content), content)


def run_to_row(run: Run, created_at: datetime) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "flow": run.flow,
        "version": str(run.version),
        "inputs": _values.dumps_json(dict(run.inputs)),
        "status": run.status.value,
        "root_id": str(run.root_id),
        "parent_id": id_to(run.parent_id),
        "parent_address": (
            address_to(run.parent_address) if run.parent_address is not None else None
        ),
        "output": _values.dumps_json(run.output),
        "reason": run.reason,
        "created_at": time_to(created_at),
        "finished_at": None,
        "starter_kind": run.started_by.kind.value if run.started_by else None,
        "starter_id": run.started_by.id if run.started_by else None,
        "starter_name": run.started_by.name if run.started_by else None,
    }


def starter_from_row(row: sqlite3.Row) -> RunStarter | None:
    if row["starter_kind"] is None:
        return None
    return RunStarter(
        StarterKind(row["starter_kind"]), row["starter_id"], row["starter_name"]
    )


def run_from_row(row: sqlite3.Row) -> Run:
    created_at = time_from(row["created_at"])
    assert created_at is not None
    return Run(
        id=uuid.UUID(row["id"]),
        flow=row["flow"],
        version=Version.parse(row["version"]),
        inputs=json.loads(row["inputs"]),
        status=RunStatus(row["status"]),
        root_id=uuid.UUID(row["root_id"]),
        parent_id=None if row["parent_id"] is None else uuid.UUID(row["parent_id"]),
        parent_address=(
            _wire.address_from(json.loads(row["parent_address"]))
            if row["parent_address"] is not None
            else None
        ),
        output=json.loads(row["output"]),
        reason=row["reason"],
        created_at=created_at,
        finished_at=time_from(row["finished_at"]),
        started_by=starter_from_row(row),
    )


def task_to_row(task: Task, created_at: datetime) -> dict[str, Any]:
    return {
        "id": str(task.id),
        "run_id": str(task.run_id),
        "address": address_to(task.address),
        "queue": task.queue,
        "handler": task.handler,
        "params": _values.dumps_json(
            dict[str, JsonValue](_wire.params_to(task.params))
        ),
        "fixed_params": _values.dumps_json(dict(task.fixed_params)),
        "status": task.status.value,
        "attempts": task.attempts,
        "lease_expires_at": None,
        "result": _values.dumps_json(task.result),
        "error": task.error,
        "created_at": time_to(created_at),
        "started_at": None,
        "finished_at": None,
    }


def task_from_row(row: sqlite3.Row) -> Task:
    return Task(
        id=uuid.UUID(row["id"]),
        run_id=uuid.UUID(row["run_id"]),
        address=_wire.address_from(json.loads(row["address"])),
        queue=row["queue"],
        handler=row["handler"],
        params=_wire.params_from(json.loads(row["params"])),
        fixed_params=json.loads(row["fixed_params"]),
        status=TaskStatus(row["status"]),
        attempts=row["attempts"],
        lease_expires_at=time_from(row["lease_expires_at"]),
        result=json.loads(row["result"]),
        error=row["error"],
        created_at=time_from(row["created_at"]),
        started_at=time_from(row["started_at"]),
        finished_at=time_from(row["finished_at"]),
    )


def event_from_row(row: sqlite3.Row) -> Event:
    return Event(row["sequence"], uuid.UUID(row["run_id"]), EventKind(row["kind"]))
