# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Rows of the SQLite tables to and from the core's dataclasses."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

from neorc_core import _values, _wire
from neorc_core._runs import Event, EventKind, Run, RunStatus, StoredFlow, Task
from neorc_core._runs import canonical_content as _canonical_content
from neorc_core._task import TaskStatus
from neorc_core._values import JsonValue
from neorc_core.flows import Address, Version, parse_flow

Row = sqlite3.Row

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

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def placeholders(columns: str) -> str:
    """``?, ?, ...``, one for each of ``columns``."""
    return ", ".join("?" for _ in columns.split(","))


def to_time(moment: datetime | None) -> int | None:
    """A time as the tables hold it: microseconds since the epoch."""
    if moment is None:
        return None
    return (moment - _EPOCH) // timedelta(microseconds=1)


def from_time(stored: int | None) -> datetime | None:
    return None if stored is None else _EPOCH + timedelta(microseconds=stored)


def address_text(address: Address) -> str:
    return _values.dumps_json(_wire.address_to(address))


def flow_values(flow: StoredFlow) -> tuple[object, ...]:
    version = flow.version
    return (
        flow.name,
        version.major,
        version.minor,
        version.patch,
        _canonical_content(flow.content),
    )


def flow_from_row(row: Row) -> StoredFlow:
    content = json.loads(row["content"])
    return StoredFlow(parse_flow(content), content)


def run_values(run: Run) -> tuple[object, ...]:
    return (
        str(run.id),
        run.flow,
        str(run.version),
        _values.dumps_json(dict(run.inputs)),
        run.status.value,
        str(run.root_id),
        str(run.parent_id) if run.parent_id is not None else None,
        address_text(run.parent_address) if run.parent_address is not None else None,
        _values.dumps_json(run.output),
        run.reason,
        to_time(run.created_at),
        to_time(run.finished_at),
    )


def run_from_row(row: Row) -> Run:
    parent_id, parent_address = row["parent_id"], row["parent_address"]
    return Run(
        id=uuid.UUID(row["id"]),
        flow=row["flow"],
        version=Version.parse(row["version"]),
        inputs=json.loads(row["inputs"]),
        status=RunStatus(row["status"]),
        root_id=uuid.UUID(row["root_id"]),
        parent_id=uuid.UUID(parent_id) if parent_id is not None else None,
        parent_address=(
            _wire.address_from(json.loads(parent_address))
            if parent_address is not None
            else None
        ),
        output=json.loads(row["output"]),
        reason=row["reason"],
        created_at=from_time(row["created_at"]),
        finished_at=from_time(row["finished_at"]),
    )


def task_values(task: Task) -> tuple[object, ...]:
    return (
        str(task.id),
        str(task.run_id),
        address_text(task.address),
        task.queue,
        task.handler,
        _values.dumps_json(dict[str, JsonValue](_wire.params_to(task.params))),
        _values.dumps_json(dict(task.fixed_params)),
        task.status.value,
        task.attempts,
        to_time(task.lease_expires_at),
        _values.dumps_json(task.result),
        task.error,
        to_time(task.created_at),
        to_time(task.started_at),
        to_time(task.finished_at),
    )


def task_from_row(row: Row) -> Task:
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
        lease_expires_at=from_time(row["lease_expires_at"]),
        result=json.loads(row["result"]),
        error=row["error"],
        created_at=from_time(row["created_at"]),
        started_at=from_time(row["started_at"]),
        finished_at=from_time(row["finished_at"]),
    )


def event_from_row(row: Row) -> Event:
    return Event(row["sequence"], uuid.UUID(row["run_id"]), EventKind(row["kind"]))
