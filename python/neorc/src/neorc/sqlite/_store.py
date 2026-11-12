# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The SQLite store for flows, runs, their tasks and events.

Every operation is one transaction, and one that writes holds the database's
write lock from before its first read (``neorc.sqlite._db``). So the rules
``MemoryStore`` keeps under one lock hold here the same way, between threads
and between processes, with none of the lock order the Postgres store needs:
there is one lock. Events and runs are numbered by their row id, allocated
under that lock, so sequences and positions commit in order.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Self

from neorc.sqlite._db import DEFAULT_BUSY_SECONDS, Database
from neorc.sqlite._rows import (
    EVENT_COLUMNS,
    FLOW_COLUMNS,
    RUN_COLUMNS,
    TASK_COLUMNS,
    address_text,
    event_from_row,
    flow_from_row,
    flow_values,
    from_time,
    placeholders,
    run_from_row,
    run_values,
    task_from_row,
    task_values,
    to_time,
)
from neorc.sqlite._schema import (
    CREATE_SCHEMA_SCRIPT,
    EVENTS_TABLE,
    FLOW_TASKS_TABLE,
    FLOW_VERSIONS_TABLE,
    RUNS_TABLE,
)
from neorc_core import (
    LEASED_STATUSES,
    Event,
    EventKind,
    FlowNotFoundError,
    FlowVersionError,
    Run,
    RunId,
    RunNotFoundError,
    RunStatus,
    Store,
    StoredFlow,
    Task,
    TaskId,
    TaskNotFoundError,
    TaskStateError,
    TaskStatus,
    ensure_transition,
)
from neorc_core._runs import (
    check_uploads,
    ensure_active,
    run_state_of,
    storable_text,
    sub_run_id_for,
)
from neorc_core._values import JsonValue, dumps_json
from neorc_core.flows import Address, Reference, RunState, Version
from neorc_core.ports._clients import DEFAULT_LEASE_SECONDS
from neorc_core.ports._store import DEFAULT_PAGE

_LATEST_ORDER = "ORDER BY major DESC, minor DESC, patch DESC"

_SELECT_ALL_FLOWS = f"SELECT {FLOW_COLUMNS} FROM {FLOW_VERSIONS_TABLE}"

_SELECT_LATEST_FLOW = f"{_SELECT_ALL_FLOWS} WHERE name = ? {_LATEST_ORDER} LIMIT 1"

_SELECT_FLOW = (
    f"{_SELECT_ALL_FLOWS} WHERE name = ? AND major = ? AND minor = ? AND patch = ?"
)

_SELECT_FLOW_VERSIONS = f"{_SELECT_ALL_FLOWS} WHERE name = ? {_LATEST_ORDER}"

_SELECT_LATEST_FLOWS = f"""
SELECT {FLOW_COLUMNS} FROM {FLOW_VERSIONS_TABLE} AS f
 WHERE NOT EXISTS (
       SELECT 1 FROM {FLOW_VERSIONS_TABLE} AS g
        WHERE g.name = f.name
          AND (g.major, g.minor, g.patch) > (f.major, f.minor, f.patch))
 ORDER BY name
"""

_INSERT_FLOW = f"""
INSERT INTO {FLOW_VERSIONS_TABLE} ({FLOW_COLUMNS})
VALUES ({placeholders(FLOW_COLUMNS)})
"""

_SELECT_RUN = f"SELECT {RUN_COLUMNS} FROM {RUNS_TABLE} WHERE id = ?"

_INSERT_RUN = f"""
INSERT INTO {RUNS_TABLE} ({RUN_COLUMNS}) VALUES ({placeholders(RUN_COLUMNS)})
"""

# The trees an upload cancels, in the order they started.
_ACTIVE_ROOTS_OF_FLOW = f"""
SELECT root_id, MIN(position) FROM {RUNS_TABLE}
 WHERE flow = ? AND status = 'active'
 GROUP BY root_id ORDER BY 2
"""

_ACTIVE_OF_TREE = f"""
SELECT id FROM {RUNS_TABLE}
 WHERE root_id = ? AND status = 'active' ORDER BY position
"""

_FINISH_TREE = f"""
UPDATE {RUNS_TABLE} SET status = ?, reason = ?, finished_at = ?
 WHERE root_id = ? AND status = 'active'
"""

_SUCCEED_RUN = f"""
UPDATE {RUNS_TABLE} SET status = 'succeeded', output = ?, finished_at = ?
 WHERE id = ?
RETURNING {RUN_COLUMNS}
"""

_LIST_RUNS = f"SELECT {RUN_COLUMNS} FROM {RUNS_TABLE}"

_STARTED_BEFORE = f"position < (SELECT position FROM {RUNS_TABLE} WHERE id = ?)"

_TASKS_OF_RUN = f"""
SELECT {TASK_COLUMNS} FROM {FLOW_TASKS_TABLE} WHERE run_id = ? ORDER BY position
"""

_SUB_RUNS_OF_RUN = f"""
SELECT {RUN_COLUMNS} FROM {RUNS_TABLE} WHERE parent_id = ? ORDER BY position
"""

_SELECT_TASK = f"SELECT {TASK_COLUMNS} FROM {FLOW_TASKS_TABLE} WHERE id = ?"

_SELECT_TASK_AT = f"""
SELECT {TASK_COLUMNS} FROM {FLOW_TASKS_TABLE} WHERE run_id = ? AND address = ?
"""

_INSERT_TASK = f"""
INSERT INTO {FLOW_TASKS_TABLE} ({TASK_COLUMNS})
VALUES ({placeholders(TASK_COLUMNS)})
"""

# Ready is pending, or leased past the lease's end; the oldest first.
_RECEIVE_TASK = f"""
UPDATE {FLOW_TASKS_TABLE}
   SET status = 'received', attempts = attempts + 1, lease_expires_at = ?
 WHERE position = (
       SELECT position FROM {FLOW_TASKS_TABLE}
        WHERE queue = ?
          AND (status = 'pending'
               OR (status IN ('received', 'running') AND lease_expires_at <= ?))
        ORDER BY position
        LIMIT 1)
RETURNING {TASK_COLUMNS}
"""

_CLAIM_TASK = f"""
UPDATE {FLOW_TASKS_TABLE}
   SET status = 'running', started_at = COALESCE(started_at, ?)
 WHERE id = ?
RETURNING {TASK_COLUMNS}
"""

_EXTEND_TASK_LEASE = f"""
UPDATE {FLOW_TASKS_TABLE} SET lease_expires_at = ? WHERE id = ?
"""

_FINISH_TASK = f"""
UPDATE {FLOW_TASKS_TABLE}
   SET status = ?, result = ?, error = ?, lease_expires_at = NULL, finished_at = ?
 WHERE id = ?
RETURNING {TASK_COLUMNS}
"""

_INSERT_EVENT = f"INSERT INTO {EVENTS_TABLE} (run_id, kind) VALUES (?, ?)"

_SELECT_EVENTS = f"""
SELECT {EVENT_COLUMNS} FROM {EVENTS_TABLE}
 WHERE sequence > ? ORDER BY sequence LIMIT ?
"""

_LAST_SEQUENCE = f"SELECT COALESCE(MAX(sequence), 0) FROM {EVENTS_TABLE}"

Connection = sqlite3.Connection


class SqliteStore(Store):
    """Flows, runs, tasks and events in one SQLite file.

    Several processes may open the same file: a manager in one, workers with
    managers of their own in others.
    """

    def __init__(
        self, path: Path | str, *, busy_seconds: float = DEFAULT_BUSY_SECONDS
    ) -> None:
        self._database = Database(path, busy_seconds=busy_seconds)

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def open(self) -> None:
        """Open the file and create the tables if they are not there."""
        await self._database.open()
        await self._database.script(CREATE_SCHEMA_SCRIPT)

    async def aclose(self) -> None:
        await self._database.aclose()

    async def store_flows(self, uploads: Sequence[StoredFlow]) -> list[bool]:
        def operation(conn: Connection) -> list[bool]:
            stored: dict[str, list[StoredFlow]] = {}
            for row in conn.execute(_SELECT_ALL_FLOWS):
                flow = flow_from_row(row)
                stored.setdefault(flow.name, []).append(flow)
            results = check_uploads(stored, uploads)
            for upload, is_new in zip(uploads, results, strict=True):
                if not is_new:
                    continue
                conn.execute(_INSERT_FLOW, flow_values(upload))
                reason = f"{upload.name} {upload.version} was uploaded"
                roots = conn.execute(_ACTIVE_ROOTS_OF_FLOW, (upload.name,)).fetchall()
                for root in roots:
                    _finish_tree(conn, root["root_id"], RunStatus.CANCELLED, reason)
            return results

        return await self._database.write(operation)

    async def get_flow(self, name: str, version: Version | None = None) -> StoredFlow:
        return await self._database.read(lambda conn: _flow(conn, name, version))

    async def latest_flows(self) -> list[StoredFlow]:
        return await self._database.read(
            lambda conn: [flow_from_row(r) for r in conn.execute(_SELECT_LATEST_FLOWS)]
        )

    async def flow_versions(self, name: str) -> list[StoredFlow]:
        def operation(conn: Connection) -> list[StoredFlow]:
            rows = conn.execute(_SELECT_FLOW_VERSIONS, (name,)).fetchall()
            if not rows:
                raise FlowNotFoundError(f"no flow {name!r}")
            return [flow_from_row(row) for row in rows]

        return await self._database.read(operation)

    async def list_runs(
        self,
        *,
        flow: str | None = None,
        status: RunStatus | None = None,
        root_only: bool = True,
        before: RunId | None = None,
        limit: int = DEFAULT_PAGE,
    ) -> list[Run]:
        conditions: list[str] = []
        args: list[object] = []
        if flow is not None:
            conditions.append("flow = ?")
            args.append(flow)
        if status is not None:
            conditions.append("status = ?")
            args.append(status.value)
        if root_only:
            conditions.append("parent_id IS NULL")
        if before is not None:
            conditions.append(_STARTED_BEFORE)
            args.append(str(before))
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"{_LIST_RUNS}{where} ORDER BY position DESC LIMIT ?"

        def operation(conn: Connection) -> list[Run]:
            if before is not None:
                _run(conn, before)  # RunNotFoundError, else its position
            return [run_from_row(row) for row in conn.execute(query, [*args, limit])]

        return await self._database.read(operation)

    async def run_tasks(self, run_id: RunId) -> list[Task]:
        def operation(conn: Connection) -> list[Task]:
            _run(conn, run_id)
            return _tasks_of(conn, run_id)

        return await self._database.read(operation)

    async def sub_runs(self, run_id: RunId) -> list[Run]:
        def operation(conn: Connection) -> list[Run]:
            _run(conn, run_id)
            return _sub_runs_of(conn, run_id)

        return await self._database.read(operation)

    async def start_run(
        self,
        flow: str,
        version: Version,
        inputs: Mapping[str, JsonValue],
        *,
        parent_id: RunId | None = None,
        parent_address: Address | None = None,
    ) -> Run:
        if (parent_id is None) != (parent_address is None):
            raise ValueError("a sub-flow run names both its parent and its address")

        def operation(conn: Connection) -> Run:
            latest = _flow(conn, flow, None).version
            if version != latest:
                raise FlowVersionError(
                    f"{flow} {version} is not the latest version, {latest}"
                )
            if parent_id is None:
                run_id = uuid.uuid4()
                root_id = run_id
            else:
                assert parent_address is not None
                parent = _run(conn, parent_id)
                ensure_active(parent)
                run_id = sub_run_id_for(parent_id, parent_address)
                existing = conn.execute(_SELECT_RUN, (str(run_id),)).fetchone()
                if existing is not None:  # the same instance, started before
                    return run_from_row(existing)
                root_id = parent.root_id
            run = Run(
                id=run_id,
                flow=flow,
                version=version,
                inputs=dict(inputs),
                status=RunStatus.ACTIVE,
                root_id=root_id,
                parent_id=parent_id,
                parent_address=parent_address,
                created_at=_now(),
            )
            conn.execute(_INSERT_RUN, run_values(run))
            _append(conn, run_id, EventKind.RUN_STARTED)
            return run

        return await self._database.write(operation)

    async def get_run(self, run_id: RunId) -> Run:
        return await self._database.read(lambda conn: _run(conn, run_id))

    async def run_state(self, run_id: RunId) -> RunState:
        def operation(conn: Connection) -> RunState:
            run = _run(conn, run_id)
            return run_state_of(
                run, _tasks_of(conn, run_id), _sub_runs_of(conn, run_id)
            )

        return await self._database.read(operation)

    async def succeed_run(self, run_id: RunId, output: JsonValue) -> Run:
        def operation(conn: Connection) -> Run:
            ensure_active(_run(conn, run_id))
            row = conn.execute(
                _SUCCEED_RUN, (dumps_json(output), to_time(_now()), str(run_id))
            ).fetchone()
            _append(conn, run_id, EventKind.RUN_FINISHED)
            return run_from_row(row)

        return await self._database.write(operation)

    async def fail_run_tree(self, run_id: RunId, reason: str) -> None:
        await self._finish_tree(run_id, RunStatus.FAILED, reason)

    async def cancel_run_tree(self, run_id: RunId, reason: str) -> None:
        await self._finish_tree(run_id, RunStatus.CANCELLED, reason)

    async def publish_task(
        self,
        run_id: RunId,
        address: Address,
        *,
        queue: str,
        handler: str,
        params: Mapping[str, Reference],
        fixed_params: Mapping[str, JsonValue],
    ) -> Task:
        def operation(conn: Connection) -> Task:
            ensure_active(_run(conn, run_id))
            existing = conn.execute(
                _SELECT_TASK_AT, (str(run_id), address_text(address))
            ).fetchone()
            if existing is not None:  # published before: unchanged, its first id
                return task_from_row(existing)
            task = Task(
                id=uuid.uuid4(),
                run_id=run_id,
                address=address,
                queue=queue,
                handler=handler,
                params=dict(params),
                fixed_params=dict(fixed_params),
                created_at=_now(),
            )
            conn.execute(_INSERT_TASK, task_values(task))
            return task

        return await self._database.write(operation)

    async def receive_task(
        self, queue: str, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> Task | None:
        def operation(conn: Connection) -> Task | None:
            now = _now()
            expires = to_time(now + timedelta(seconds=lease_seconds))
            row = conn.execute(_RECEIVE_TASK, (expires, queue, to_time(now))).fetchone()
            return None if row is None else task_from_row(row)

        return await self._database.write(operation)

    async def claim_task(self, task_id: TaskId) -> Task:
        def operation(conn: Connection) -> Task | Run:
            task = _task(conn, task_id)
            ensure_transition(task.status, TaskStatus.RUNNING)
            run = _run(conn, task.run_id)
            now = to_time(_now())
            if run.status is not RunStatus.ACTIVE:
                # Failed and committed before the refusal is raised, so the
                # lease never hands the task out again.
                error = f"run {run.id} is {run.status.value}"
                conn.execute(
                    _FINISH_TASK,
                    (
                        TaskStatus.FAILED.value,
                        dumps_json(None),
                        error,
                        now,
                        str(task_id),
                    ),
                )
                return run
            row = conn.execute(_CLAIM_TASK, (now, str(task_id))).fetchone()
            return task_from_row(row)

        claimed = await self._database.write(operation)
        if isinstance(claimed, Run):
            ensure_active(claimed)
            raise AssertionError("unreachable: the run was not active")
        return claimed

    async def extend_task_lease(
        self, task_id: TaskId, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> datetime:
        def operation(conn: Connection) -> datetime:
            task = _task(conn, task_id)
            if task.status not in LEASED_STATUSES:
                raise TaskStateError(
                    f"a {task.status.value} task holds no lease to extend"
                )
            expires_at = _now() + timedelta(seconds=lease_seconds)
            conn.execute(_EXTEND_TASK_LEASE, (to_time(expires_at), str(task_id)))
            return expires_at

        return await self._database.write(operation)

    async def finish_task(
        self, task_id: TaskId, *, result: JsonValue = None, error: str | None = None
    ) -> Task:
        status = TaskStatus.FAILED if error is not None else TaskStatus.SUCCEEDED

        def operation(conn: Connection) -> Task:
            task = _task(conn, task_id)
            ensure_transition(task.status, status)
            row = conn.execute(
                _FINISH_TASK,
                (
                    status.value,
                    dumps_json(result if error is None else None),
                    storable_text(error) if error is not None else None,
                    to_time(_now()),
                    str(task_id),
                ),
            ).fetchone()
            _append(conn, task.run_id, EventKind.TASK_FINISHED)
            return task_from_row(row)

        return await self._database.write(operation)

    async def get_task(self, task_id: TaskId) -> Task:
        return await self._database.read(lambda conn: _task(conn, task_id))

    async def events_after(self, sequence: int, *, limit: int = 100) -> list[Event]:
        return await self._database.read(
            lambda conn: [
                event_from_row(row)
                for row in conn.execute(_SELECT_EVENTS, (sequence, limit))
            ]
        )

    async def last_sequence(self) -> int:
        def operation(conn: Connection) -> int:
            last: int = conn.execute(_LAST_SEQUENCE).fetchone()[0]
            return last

        return await self._database.read(operation)

    async def _finish_tree(self, run_id: RunId, status: RunStatus, reason: str) -> None:
        def operation(conn: Connection) -> None:
            _finish_tree(conn, str(_run(conn, run_id).root_id), status, reason)

        await self._database.write(operation)


def _now() -> datetime:
    """The store's clock, cut to what the tables keep."""
    moment = from_time(to_time(datetime.now(UTC)))
    assert moment is not None
    return moment


def _flow(conn: Connection, name: str, version: Version | None) -> StoredFlow:
    if version is None:
        row = conn.execute(_SELECT_LATEST_FLOW, (name,)).fetchone()
    else:
        row = conn.execute(
            _SELECT_FLOW, (name, version.major, version.minor, version.patch)
        ).fetchone()
    if row is None:
        if version is None:
            raise FlowNotFoundError(f"no flow {name!r}")
        raise FlowNotFoundError(f"no version {version} of flow {name!r}")
    return flow_from_row(row)


def _run(conn: Connection, run_id: RunId) -> Run:
    row = conn.execute(_SELECT_RUN, (str(run_id),)).fetchone()
    if row is None:
        raise RunNotFoundError(str(run_id))
    return run_from_row(row)


def _task(conn: Connection, task_id: TaskId) -> Task:
    row = conn.execute(_SELECT_TASK, (str(task_id),)).fetchone()
    if row is None:
        raise TaskNotFoundError(str(task_id))
    return task_from_row(row)


def _tasks_of(conn: Connection, run_id: RunId) -> list[Task]:
    return [task_from_row(r) for r in conn.execute(_TASKS_OF_RUN, (str(run_id),))]


def _sub_runs_of(conn: Connection, run_id: RunId) -> list[Run]:
    return [run_from_row(r) for r in conn.execute(_SUB_RUNS_OF_RUN, (str(run_id),))]


def _append(conn: Connection, run_id: RunId | str, kind: EventKind) -> None:
    conn.execute(_INSERT_EVENT, (str(run_id), kind.value))


def _finish_tree(
    conn: Connection, root_id: str, status: RunStatus, reason: str
) -> None:
    """End every active run of the tree, and record each one's "run finished"."""
    active = conn.execute(_ACTIVE_OF_TREE, (root_id,)).fetchall()
    conn.execute(
        _FINISH_TREE, (status.value, storable_text(reason), to_time(_now()), root_id)
    )
    for row in active:
        _append(conn, row["id"], EventKind.RUN_FINISHED)
