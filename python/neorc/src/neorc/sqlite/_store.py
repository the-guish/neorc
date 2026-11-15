# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The SQLite store for flows, runs, their tasks and events, in one file.

One connection and one thread serve the store. Each operation is a plain
function that runs as one transaction on that thread, and the event loop waits
for it without blocking. The thread runs one operation at a time, so the
operations are atomic towards each other as they are under ``MemoryStore``'s
one lock; the transaction makes each one atomic on disk too. A writing
operation begins with ``BEGIN IMMEDIATE``: it takes the file's write lock
first, so a second process that holds the file makes it wait, up to the busy
timeout, and never fails it half way.

The store keeps the clock, as the memory store does: times are this process's
``datetime.now(UTC)``, read inside the operation that sets them.
"""

from __future__ import annotations

import asyncio
import functools
import os
import sqlite3
import uuid
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Self, TypeVar

from neorc.sqlite._rows import (
    EVENT_COLUMNS,
    FLOW_COLUMNS,
    RUN_COLUMNS,
    TASK_COLUMNS,
    address_to,
    event_from_row,
    flow_from_row,
    flow_to_row,
    placeholders,
    run_from_row,
    run_to_row,
    task_from_row,
    task_to_row,
    time_from,
    time_to,
)
from neorc.sqlite._schema import (
    EVENTS_TABLE,
    FLOW_TASKS_TABLE,
    FLOW_VERSIONS_TABLE,
    QUEUES_TABLE,
    RUNS_TABLE,
    connect,
    rollback,
)
from neorc_core import (
    LEASED_STATUSES,
    Event,
    EventKind,
    FlowNotFoundError,
    FlowVersionError,
    NeorcError,
    Queue,
    QueueKind,
    QueueTask,
    Run,
    RunId,
    RunNotFoundError,
    RunStarter,
    RunStatus,
    Store,
    StoredFlow,
    Task,
    TaskId,
    TaskNotFoundError,
    TaskStateError,
    TaskStatus,
    ensure_completable,
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

_T = TypeVar("_T")

_LATEST_ORDER = "ORDER BY major DESC, minor DESC, patch DESC"

_SELECT_ALL_FLOWS = f"SELECT {FLOW_COLUMNS} FROM {FLOW_VERSIONS_TABLE}"

_SELECT_LATEST_FLOW = f"""
SELECT {FLOW_COLUMNS} FROM {FLOW_VERSIONS_TABLE} WHERE name = ? {_LATEST_ORDER} LIMIT 1
"""

_SELECT_FLOW = f"""
SELECT {FLOW_COLUMNS} FROM {FLOW_VERSIONS_TABLE}
 WHERE name = ? AND major = ? AND minor = ? AND patch = ?
"""

# A version is the latest of its flow when no version of the flow is higher.
_SELECT_LATEST_FLOWS = f"""
SELECT {FLOW_COLUMNS} FROM {FLOW_VERSIONS_TABLE} AS f
 WHERE NOT EXISTS (
       SELECT 1 FROM {FLOW_VERSIONS_TABLE} AS g
        WHERE g.name = f.name
          AND (g.major, g.minor, g.patch) > (f.major, f.minor, f.patch))
 ORDER BY name
"""

_SELECT_FLOW_VERSIONS = f"""
SELECT {FLOW_COLUMNS} FROM {FLOW_VERSIONS_TABLE} WHERE name = ? {_LATEST_ORDER}
"""

_INSERT_FLOW = f"""
INSERT INTO {FLOW_VERSIONS_TABLE} ({FLOW_COLUMNS}) VALUES ({placeholders(FLOW_COLUMNS)})
"""

_SELECT_RUN = f"SELECT {RUN_COLUMNS} FROM {RUNS_TABLE} WHERE id = ?"

_ACTIVE_ROOTS_OF_FLOW = f"""
SELECT DISTINCT root_id FROM {RUNS_TABLE}
 WHERE flow = ? AND status = 'active'
 ORDER BY root_id
"""

_INSERT_RUN = f"""
INSERT INTO {RUNS_TABLE} ({RUN_COLUMNS}) VALUES ({placeholders(RUN_COLUMNS)})
"""

_SUCCEED_RUN = f"""
UPDATE {RUNS_TABLE} SET status = 'succeeded', output = ?, finished_at = ? WHERE id = ?
"""

_ACTIVE_IN_TREE = f"""
SELECT id FROM {RUNS_TABLE}
 WHERE root_id = ? AND status = 'active'
 ORDER BY position
"""

_FINISH_TREE = f"""
UPDATE {RUNS_TABLE}
   SET status = ?, reason = ?, finished_at = ?
 WHERE root_id = ? AND status = 'active'
"""

_LIST_RUNS = f"SELECT {RUN_COLUMNS} FROM {RUNS_TABLE}"

_STARTED_BEFORE = f"position < (SELECT position FROM {RUNS_TABLE} WHERE id = ?)"

_INSERT_EVENT = f"INSERT INTO {EVENTS_TABLE} (run_id, kind) VALUES (?, ?)"

_SELECT_EVENTS = f"""
SELECT {EVENT_COLUMNS} FROM {EVENTS_TABLE}
 WHERE sequence > ?
 ORDER BY sequence
 LIMIT ?
"""

_LAST_SEQUENCE = f"SELECT COALESCE(MAX(sequence), 0) FROM {EVENTS_TABLE}"

_SELECT_TASK = f"SELECT {TASK_COLUMNS} FROM {FLOW_TASKS_TABLE} WHERE id = ?"

_SELECT_TASK_AT = f"""
SELECT {TASK_COLUMNS} FROM {FLOW_TASKS_TABLE} WHERE run_id = ? AND address = ?
"""

_INSERT_TASK = f"""
INSERT INTO {FLOW_TASKS_TABLE} ({TASK_COLUMNS}) VALUES ({placeholders(TASK_COLUMNS)})
"""

# Ready is pending, or leased past the lease's end; the oldest goes first.
_READY_TASK = f"""
SELECT id FROM {FLOW_TASKS_TABLE}
 WHERE queue = :queue
   AND (status = 'pending'
        OR (status IN ('received', 'running') AND lease_expires_at <= :now))
 ORDER BY position
 LIMIT 1
"""

_RECEIVE_TASK = f"""
UPDATE {FLOW_TASKS_TABLE}
   SET status = 'received', attempts = attempts + 1, lease_expires_at = ?
 WHERE id = ?
"""

_CLAIM_TASK = f"""
UPDATE {FLOW_TASKS_TABLE}
   SET status = 'running', started_at = COALESCE(started_at, ?)
 WHERE id = ?
"""

_EXTEND_TASK_LEASE = f"UPDATE {FLOW_TASKS_TABLE} SET lease_expires_at = ? WHERE id = ?"

_FINISH_TASK = f"""
UPDATE {FLOW_TASKS_TABLE}
   SET status = ?, result = ?, error = ?, lease_expires_at = NULL, finished_at = ?
 WHERE id = ?
"""

_COMPLETE_TASK = f"""
UPDATE {FLOW_TASKS_TABLE}
   SET status = ?, result = ?, error = ?, finished_at = ?
 WHERE id = ?
"""

_TASK_COLUMNS_OF_T = ", ".join(f"t.{column}" for column in TASK_COLUMNS.split(", "))

# Reads only. The join leaves out the tasks of runs that ended.
_PENDING_TASKS = f"""
SELECT {_TASK_COLUMNS_OF_T}
  FROM {FLOW_TASKS_TABLE} AS t
  JOIN {RUNS_TABLE} AS r ON r.id = t.run_id
 WHERE t.queue = ? AND t.status = 'pending' AND r.status = 'active'
 ORDER BY t.position
 LIMIT ?
"""

# Reads only. A task's last change is the latest of its times; created_at is
# always set, so it stands in for the times a task does not have yet.
_QUEUE_TASKS = f"""
SELECT {_TASK_COLUMNS_OF_T}, r.flow AS run_flow, r.version AS run_version
  FROM {FLOW_TASKS_TABLE} AS t
  JOIN {RUNS_TABLE} AS r ON r.id = t.run_id
 WHERE t.queue = :queue
   AND MAX(t.created_at, COALESCE(t.started_at, t.created_at),
           COALESCE(t.finished_at, t.created_at)) >= :since
   AND (:status IS NULL OR t.status = :status)
   AND (:flow IS NULL OR r.flow = :flow)
   AND (t.finished_at IS NOT NULL OR r.status = 'active')
 ORDER BY t.position DESC
 LIMIT :limit
"""

_TASKS_OF_RUN = f"""
SELECT {TASK_COLUMNS} FROM {FLOW_TASKS_TABLE} WHERE run_id = ? ORDER BY position
"""

_SUB_RUNS_OF_RUN = f"""
SELECT {RUN_COLUMNS} FROM {RUNS_TABLE} WHERE parent_id = ? ORDER BY position
"""

_DECLARE_QUEUE = f"""
INSERT INTO {QUEUES_TABLE} (name, kind, created_at) VALUES (?, ?, ?)
ON CONFLICT (name) DO UPDATE SET kind = excluded.kind
"""

_SELECT_QUEUES = f"SELECT name, kind FROM {QUEUES_TABLE} ORDER BY name"

_SELECT_QUEUE_KIND = f"SELECT kind FROM {QUEUES_TABLE} WHERE name = ?"


class SqliteStore(Store):
    """Flows, runs, tasks and events in a SQLite file, at ``path``.

    Opening creates the file and its tables if they are not there, and refuses
    a file of another schema version with ``SchemaVersionError``. One process
    opens the file: the store does not tell another process's waiters about
    new work, which a ``MemoryTaskNotifier`` in this process does.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = path
        self._executor: ThreadPoolExecutor | None = None
        self._conn: sqlite3.Connection | None = None

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
        """Open the file, on the store's own thread."""
        if self._executor is not None:
            return
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="neorc-sqlite")
        try:
            loop = asyncio.get_running_loop()
            self._conn = await loop.run_in_executor(executor, connect, self._path)
        except BaseException:
            executor.shutdown(wait=False)
            raise
        self._executor = executor

    async def aclose(self) -> None:
        """Close the file, once the operations already begun have ended."""
        executor, conn = self._executor, self._conn
        if executor is None or conn is None:
            return
        self._executor = self._conn = None
        await asyncio.get_running_loop().run_in_executor(executor, conn.close)
        executor.shutdown(wait=False)

    async def _read(self, operation: Callable[..., _T], *args: Any) -> _T:
        return await self._run("BEGIN", operation, *args)

    async def _write(self, operation: Callable[..., _T], *args: Any) -> _T:
        return await self._run("BEGIN IMMEDIATE", operation, *args)

    async def _run(self, begin: str, operation: Callable[..., _T], *args: Any) -> _T:
        executor, conn = self._executor, self._conn
        if executor is None or conn is None:
            raise NeorcError("the store is not open")
        call = functools.partial(_transaction, conn, begin, operation, *args)
        return await asyncio.get_running_loop().run_in_executor(executor, call)

    # Flows.

    async def store_flows(self, uploads: Sequence[StoredFlow]) -> list[bool]:
        return await self._write(_store_flows, list(uploads))

    async def get_flow(self, name: str, version: Version | None = None) -> StoredFlow:
        return await self._read(_flow, name, version)

    async def latest_flows(self) -> list[StoredFlow]:
        return await self._read(_latest_flows)

    async def flow_versions(self, name: str) -> list[StoredFlow]:
        return await self._read(_flow_versions, name)

    # Runs.

    async def list_runs(
        self,
        *,
        flow: str | None = None,
        status: RunStatus | None = None,
        root_only: bool = True,
        before: RunId | None = None,
        limit: int = DEFAULT_PAGE,
    ) -> list[Run]:
        return await self._read(_list_runs, flow, status, root_only, before, limit)

    async def run_tasks(self, run_id: RunId) -> list[Task]:
        return await self._read(_run_tasks, run_id)

    async def sub_runs(self, run_id: RunId) -> list[Run]:
        return await self._read(_sub_runs, run_id)

    async def start_run(
        self,
        flow: str,
        version: Version,
        inputs: Mapping[str, JsonValue],
        *,
        parent_id: RunId | None = None,
        parent_address: Address | None = None,
        started_by: RunStarter | None = None,
    ) -> Run:
        if (parent_id is None) != (parent_address is None):
            raise ValueError("a sub-flow run names both its parent and its address")
        return await self._write(
            _start_run,
            flow,
            version,
            dict(inputs),
            parent_id,
            parent_address,
            started_by,
        )

    async def get_run(self, run_id: RunId) -> Run:
        return await self._read(_run, run_id)

    async def run_state(self, run_id: RunId) -> RunState:
        return await self._read(_run_state, run_id)

    async def succeed_run(self, run_id: RunId, output: JsonValue) -> Run:
        return await self._write(_succeed_run, run_id, output)

    async def fail_run_tree(self, run_id: RunId, reason: str) -> None:
        await self._write(_finish_run_tree, run_id, RunStatus.FAILED, reason)

    async def cancel_run_tree(self, run_id: RunId, reason: str) -> None:
        await self._write(_finish_run_tree, run_id, RunStatus.CANCELLED, reason)

    # Tasks.

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
        task = Task(
            id=uuid.uuid4(),
            run_id=run_id,
            address=address,
            queue=queue,
            handler=handler,
            params=dict(params),
            fixed_params=dict(fixed_params),
        )
        return await self._write(_publish_task, task)

    async def receive_task(
        self, queue: str, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> Task | None:
        return await self._write(_receive_task, queue, lease_seconds)

    async def claim_task(self, task_id: TaskId) -> Task:
        claimed, inactive = await self._write(_claim_task, task_id)
        if inactive is not None:
            # Raised once the task's failure is committed, so the lease never
            # hands it out again.
            ensure_active(inactive)
        assert claimed is not None
        return claimed

    async def extend_task_lease(
        self, task_id: TaskId, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> datetime:
        return await self._write(_extend_task_lease, task_id, lease_seconds)

    async def finish_task(
        self, task_id: TaskId, *, result: JsonValue = None, error: str | None = None
    ) -> Task:
        return await self._write(_finish_task, task_id, result, error)

    async def pending_tasks(
        self, queue: str, *, limit: int = DEFAULT_PAGE
    ) -> list[Task]:
        return await self._read(_pending_tasks, queue, limit)

    async def queue_tasks(
        self,
        queue: str,
        *,
        since: datetime,
        status: TaskStatus | None = None,
        flow: str | None = None,
        limit: int = DEFAULT_PAGE,
    ) -> list[QueueTask]:
        return await self._read(_queue_tasks, queue, since, status, flow, limit)

    async def complete_task(
        self, task_id: TaskId, *, result: JsonValue = None, error: str | None = None
    ) -> Task:
        return await self._write(_complete_task, task_id, result, error)

    async def get_task(self, task_id: TaskId) -> Task:
        return await self._read(_task, task_id)

    # Queues.

    async def declare_queue(self, name: str, kind: QueueKind) -> None:
        await self._write(_declare_queue, name, kind)

    async def queues(self) -> list[Queue]:
        return await self._read(_queues)

    async def queue_kind(self, name: str) -> QueueKind | None:
        return await self._read(_queue_kind, name)

    # Events.

    async def events_after(self, sequence: int, *, limit: int = 100) -> list[Event]:
        return await self._read(_events_after, sequence, limit)

    async def last_sequence(self) -> int:
        return await self._read(_last_sequence)


def _transaction(
    conn: sqlite3.Connection,
    begin: str,
    operation: Callable[..., _T],
    *args: Any,
) -> _T:
    """Run ``operation`` as one transaction: committed if it returns, else undone."""
    conn.execute(begin)
    try:
        result = operation(conn, *args)
    except BaseException:
        rollback(conn)
        raise
    conn.execute("COMMIT")
    return result


def _now() -> datetime:
    return datetime.now(UTC)


# The operations, each run inside a transaction on the store's thread.


def _store_flows(conn: sqlite3.Connection, uploads: list[StoredFlow]) -> list[bool]:
    stored: dict[str, list[StoredFlow]] = {}
    for row in conn.execute(_SELECT_ALL_FLOWS):
        flow = flow_from_row(row)
        stored.setdefault(flow.name, []).append(flow)
    results = check_uploads(stored, uploads)
    new = [u for u, is_new in zip(uploads, results, strict=True) if is_new]
    conn.executemany(_INSERT_FLOW, [flow_to_row(upload) for upload in new])
    for upload in new:
        reason = f"{upload.name} {upload.version} was uploaded"
        roots = [row[0] for row in conn.execute(_ACTIVE_ROOTS_OF_FLOW, (upload.name,))]
        for root_id in roots:
            _finish_tree(conn, root_id, RunStatus.CANCELLED, reason)
    return results


def _flow(conn: sqlite3.Connection, name: str, version: Version | None) -> StoredFlow:
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


def _latest_flows(conn: sqlite3.Connection) -> list[StoredFlow]:
    return [flow_from_row(row) for row in conn.execute(_SELECT_LATEST_FLOWS)]


def _flow_versions(conn: sqlite3.Connection, name: str) -> list[StoredFlow]:
    rows = conn.execute(_SELECT_FLOW_VERSIONS, (name,)).fetchall()
    if not rows:
        raise FlowNotFoundError(f"no flow {name!r}")
    return [flow_from_row(row) for row in rows]


def _list_runs(
    conn: sqlite3.Connection,
    flow: str | None,
    status: RunStatus | None,
    root_only: bool,
    before: RunId | None,
    limit: int,
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
        _run(conn, before)  # RunNotFoundError, else its position
        conditions.append(_STARTED_BEFORE)
        args.append(str(before))
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"{_LIST_RUNS}{where} ORDER BY position DESC LIMIT ?"
    return [run_from_row(row) for row in conn.execute(query, (*args, limit))]


def _run_tasks(conn: sqlite3.Connection, run_id: RunId) -> list[Task]:
    _run(conn, run_id)
    return [task_from_row(row) for row in conn.execute(_TASKS_OF_RUN, (str(run_id),))]


def _sub_runs(conn: sqlite3.Connection, run_id: RunId) -> list[Run]:
    _run(conn, run_id)
    rows = conn.execute(_SUB_RUNS_OF_RUN, (str(run_id),))
    return [run_from_row(row) for row in rows]


def _start_run(
    conn: sqlite3.Connection,
    flow: str,
    version: Version,
    inputs: dict[str, JsonValue],
    parent_id: RunId | None,
    parent_address: Address | None,
    started_by: RunStarter | None,
) -> Run:
    latest = _flow(conn, flow, None).version
    if version != latest:
        raise FlowVersionError(f"{flow} {version} is not the latest version, {latest}")
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
        inputs=inputs,
        status=RunStatus.ACTIVE,
        root_id=root_id,
        parent_id=parent_id,
        parent_address=parent_address,
        started_by=started_by,
    )
    conn.execute(_INSERT_RUN, run_to_row(run, _now()))
    _append(conn, run_id, EventKind.RUN_STARTED)
    return _run(conn, run_id)


def _run(conn: sqlite3.Connection, run_id: RunId) -> Run:
    row = conn.execute(_SELECT_RUN, (str(run_id),)).fetchone()
    if row is None:
        raise RunNotFoundError(str(run_id))
    return run_from_row(row)


def _run_state(conn: sqlite3.Connection, run_id: RunId) -> RunState:
    run = _run(conn, run_id)
    tasks = conn.execute(_TASKS_OF_RUN, (str(run_id),))
    sub_runs = conn.execute(_SUB_RUNS_OF_RUN, (str(run_id),))
    return run_state_of(
        run,
        [task_from_row(row) for row in tasks],
        [run_from_row(row) for row in sub_runs],
    )


def _succeed_run(conn: sqlite3.Connection, run_id: RunId, output: JsonValue) -> Run:
    ensure_active(_run(conn, run_id))
    conn.execute(_SUCCEED_RUN, (dumps_json(output), time_to(_now()), str(run_id)))
    _append(conn, run_id, EventKind.RUN_FINISHED)
    return _run(conn, run_id)


def _finish_run_tree(
    conn: sqlite3.Connection, run_id: RunId, status: RunStatus, reason: str
) -> None:
    _finish_tree(conn, str(_run(conn, run_id).root_id), status, reason)


def _finish_tree(
    conn: sqlite3.Connection, root_id: str, status: RunStatus, reason: str
) -> None:
    """End every active run of the tree of ``root_id``, each with its event."""
    ended = [row[0] for row in conn.execute(_ACTIVE_IN_TREE, (root_id,))]
    conn.execute(
        _FINISH_TREE, (status.value, storable_text(reason), time_to(_now()), root_id)
    )
    conn.executemany(
        _INSERT_EVENT, [(run_id, EventKind.RUN_FINISHED.value) for run_id in ended]
    )


def _append(conn: sqlite3.Connection, run_id: RunId, kind: EventKind) -> None:
    conn.execute(_INSERT_EVENT, (str(run_id), kind.value))


def _publish_task(conn: sqlite3.Connection, task: Task) -> Task:
    ensure_active(_run(conn, task.run_id))
    existing = conn.execute(
        _SELECT_TASK_AT, (str(task.run_id), address_to(task.address))
    ).fetchone()
    if existing is not None:  # published before: unchanged, with its first id
        return task_from_row(existing)
    conn.execute(_INSERT_TASK, task_to_row(task, _now()))
    return _task(conn, task.id)


def _task(conn: sqlite3.Connection, task_id: TaskId) -> Task:
    row = conn.execute(_SELECT_TASK, (str(task_id),)).fetchone()
    if row is None:
        raise TaskNotFoundError(str(task_id))
    return task_from_row(row)


def _lease_end(now: datetime, lease_seconds: float) -> int:
    return time_to(now + timedelta(seconds=lease_seconds))


def _receive_task(
    conn: sqlite3.Connection, queue: str, lease_seconds: float
) -> Task | None:
    # Only a declared worker queue hands tasks out.
    if _queue_kind(conn, queue) is not QueueKind.WORKER:
        return None
    now = _now()
    row = conn.execute(_READY_TASK, {"queue": queue, "now": time_to(now)}).fetchone()
    if row is None:
        return None
    conn.execute(_RECEIVE_TASK, (_lease_end(now, lease_seconds), row[0]))
    return _task(conn, uuid.UUID(row[0]))


def _claim_task(
    conn: sqlite3.Connection, task_id: TaskId
) -> tuple[Task | None, Run | None]:
    """The claimed task; or, when its run is no longer active, that run.

    The task of an inactive run fails here, and the caller raises after the
    commit.
    """
    task = _task(conn, task_id)
    ensure_transition(task.status, TaskStatus.RUNNING)
    run = _run(conn, task.run_id)
    now = time_to(_now())
    if run.status is not RunStatus.ACTIVE:
        conn.execute(
            _FINISH_TASK,
            (
                TaskStatus.FAILED.value,
                dumps_json(None),
                f"run {run.id} is {run.status.value}",
                now,
                str(task_id),
            ),
        )
        return None, run
    conn.execute(_CLAIM_TASK, (now, str(task_id)))
    return _task(conn, task_id), None


def _extend_task_lease(
    conn: sqlite3.Connection, task_id: TaskId, lease_seconds: float
) -> datetime:
    task = _task(conn, task_id)
    if task.status not in LEASED_STATUSES:
        raise TaskStateError(f"a {task.status.value} task holds no lease to extend")
    expires_at = _lease_end(_now(), lease_seconds)
    conn.execute(_EXTEND_TASK_LEASE, (expires_at, str(task_id)))
    moment = time_from(expires_at)
    assert moment is not None
    return moment


def _ended(result: JsonValue, error: str | None) -> tuple[TaskStatus, str, str | None]:
    """The status, the stored result and the stored error of an ending task."""
    if error is None:
        return TaskStatus.SUCCEEDED, dumps_json(result), None
    return TaskStatus.FAILED, dumps_json(None), storable_text(error)


def _finish_task(
    conn: sqlite3.Connection, task_id: TaskId, result: JsonValue, error: str | None
) -> Task:
    status, stored_result, stored_error = _ended(result, error)
    task = _task(conn, task_id)
    ensure_transition(task.status, status)
    conn.execute(
        _FINISH_TASK,
        (status.value, stored_result, stored_error, time_to(_now()), str(task_id)),
    )
    _append(conn, task.run_id, EventKind.TASK_FINISHED)
    return _task(conn, task_id)


def _pending_tasks(conn: sqlite3.Connection, queue: str, limit: int) -> list[Task]:
    return [task_from_row(row) for row in conn.execute(_PENDING_TASKS, (queue, limit))]


def _queue_tasks(
    conn: sqlite3.Connection,
    queue: str,
    since: datetime,
    status: TaskStatus | None,
    flow: str | None,
    limit: int,
) -> list[QueueTask]:
    rows = conn.execute(
        _QUEUE_TASKS,
        {
            "queue": queue,
            "since": time_to(since),
            "status": None if status is None else status.value,
            "flow": flow,
            "limit": limit,
        },
    )
    return [
        QueueTask(
            task_from_row(row), row["run_flow"], Version.parse(row["run_version"])
        )
        for row in rows
    ]


def _complete_task(
    conn: sqlite3.Connection, task_id: TaskId, result: JsonValue, error: str | None
) -> Task:
    status, stored_result, stored_error = _ended(result, error)
    task = _task(conn, task_id)
    ensure_completable(task.status)
    ensure_active(_run(conn, task.run_id))
    conn.execute(
        _COMPLETE_TASK,
        (status.value, stored_result, stored_error, time_to(_now()), str(task_id)),
    )
    _append(conn, task.run_id, EventKind.TASK_FINISHED)
    return _task(conn, task_id)


def _declare_queue(conn: sqlite3.Connection, name: str, kind: QueueKind) -> None:
    conn.execute(_DECLARE_QUEUE, (name, kind.value, time_to(_now())))


def _queues(conn: sqlite3.Connection) -> list[Queue]:
    return [Queue(row[0], QueueKind(row[1])) for row in conn.execute(_SELECT_QUEUES)]


def _queue_kind(conn: sqlite3.Connection, name: str) -> QueueKind | None:
    row = conn.execute(_SELECT_QUEUE_KIND, (name,)).fetchone()
    return None if row is None else QueueKind(row[0])


def _events_after(conn: sqlite3.Connection, sequence: int, limit: int) -> list[Event]:
    return [
        event_from_row(row) for row in conn.execute(_SELECT_EVENTS, (sequence, limit))
    ]


def _last_sequence(conn: sqlite3.Connection) -> int:
    last: int = conn.execute(_LAST_SEQUENCE).fetchone()[0]
    return last
