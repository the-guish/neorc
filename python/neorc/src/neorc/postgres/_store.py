# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The Postgres store for flows, runs, their tasks and events.

Every operation is one transaction in ``READ COMMITTED``, and takes only the
locks it needs, always in this order, so the rules ``MemoryStore`` keeps under
one lock hold here too, and no two transactions wait on each other in a cycle:

1. The upload lock, a transaction-level advisory lock: exclusive in
   ``store_flows``, shared in ``start_run``. Two uploads cannot pass the set
   check against the same stored flows, and a run cannot start on a version an
   upload is replacing.
2. Root run rows, ``FOR UPDATE``, in id order, in every operation that adds to,
   finishes or reads the status of a run tree. A tree update runs after its root
   is locked, as a statement of its own, so it sees every child committed before
   the lock was granted; and a child cannot be added without the same lock. A
   cancellation therefore never misses a sub-run or task, and nothing is added
   to a tree after it stopped.
3. Other rows: runs, tasks.
4. The event lock, a second advisory lock, taken last, by a statement of its
   own. A later statement appends every event of the transaction, with
   sequences allocated as ``max(sequence) + 1``: its snapshot is taken after
   the lock is granted, so it sees the events of the transaction that held the
   lock before. Nothing is locked after it, so events commit in sequence order
   and a scheduler reading after a sequence misses none. A run's ``position``
   is allocated the same way, right after its "run started" event, so runs
   list and page in the order they committed.

There are no foreign keys between the tables, so an insert takes no lock on
another row behind the order's back; the operations keep the references whole.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime

from neorc.postgres._driver import Connection
from neorc.postgres._pool import Pooled
from neorc.postgres._rows import (
    EVENT_COLUMNS,
    FLOW_COLUMNS,
    RUN_COLUMNS,
    TASK_COLUMNS,
    event_from_row,
    flow_from_row,
    flow_to_row,
    placeholders,
    run_from_row,
    run_to_row,
    task_from_row,
    task_to_row,
    values,
)
from neorc.postgres._schema import (
    EVENTS_TABLE,
    FLOW_TASKS_TABLE,
    FLOW_VERSIONS_TABLE,
    QUEUES_TABLE,
    RUNS_TABLE,
)
from neorc_core import (
    LEASED_STATUSES,
    Event,
    EventKind,
    FlowNotFoundError,
    FlowVersionError,
    Queue,
    QueueKind,
    QueueTask,
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

_LOCK_SPACE = 0x6E656F72  # "neor": keeps clear of other advisory locks in the database
UPLOAD_LOCK = (_LOCK_SPACE, 1)
"""The advisory lock key uploads take exclusively and run starts share."""
EVENT_LOCK = (_LOCK_SPACE, 2)
"""The advisory lock key every transaction appending events takes, last."""

_LATEST_ORDER = "ORDER BY major DESC, minor DESC, patch DESC"

_SELECT_ALL_FLOWS = f"SELECT {FLOW_COLUMNS} FROM {FLOW_VERSIONS_TABLE}"

_SELECT_LATEST_FLOW = f"""
SELECT {FLOW_COLUMNS} FROM {FLOW_VERSIONS_TABLE}
 WHERE name = $1
 {_LATEST_ORDER} LIMIT 1
"""

_SELECT_FLOW = f"""
SELECT {FLOW_COLUMNS} FROM {FLOW_VERSIONS_TABLE}
 WHERE name = $1 AND major = $2 AND minor = $3 AND patch = $4
"""

_SELECT_LATEST_FLOWS = f"""
SELECT DISTINCT ON (name) {FLOW_COLUMNS} FROM {FLOW_VERSIONS_TABLE}
 ORDER BY name, major DESC, minor DESC, patch DESC
"""

_INSERT_FLOW = f"""
INSERT INTO {FLOW_VERSIONS_TABLE} ({FLOW_COLUMNS})
VALUES ({placeholders(FLOW_COLUMNS)})
"""

_SELECT_RUN = f"SELECT {RUN_COLUMNS} FROM {RUNS_TABLE} WHERE id = $1"

_ACTIVE_ROOTS_OF_FLOW = f"""
SELECT DISTINCT root_id FROM {RUNS_TABLE}
 WHERE flow = $1 AND status = 'active'
"""

_ACTIVE_ROOTS_OF_FLOW_AMONG = f"""
SELECT DISTINCT root_id FROM {RUNS_TABLE}
 WHERE flow = $1 AND status = 'active' AND root_id = ANY($2)
"""

_LOCK_ROOTS = f"""
SELECT id FROM {RUNS_TABLE} WHERE id = ANY($1) ORDER BY id FOR UPDATE
"""

_SELECT_FLOW_VERSIONS = f"""
SELECT {FLOW_COLUMNS} FROM {FLOW_VERSIONS_TABLE}
 WHERE name = $1
 {_LATEST_ORDER}
"""

# The database sets created_at, so every row's clock is the server's, and hands
# the row back to be what the caller returns.
_INSERT_RUN_COLUMNS = (
    "id, flow, version, inputs, status, root_id, parent_id, parent_address, "
    "output, reason"
)
_INSERT_RUN = f"""
INSERT INTO {RUNS_TABLE} ({_INSERT_RUN_COLUMNS}, created_at)
VALUES ({placeholders(_INSERT_RUN_COLUMNS)}, now())
ON CONFLICT (id) DO NOTHING
RETURNING {RUN_COLUMNS}
"""

_SUCCEED_RUN = f"""
UPDATE {RUNS_TABLE}
   SET status = 'succeeded', output = $2, finished_at = now()
 WHERE id = $1 AND status = 'active'
RETURNING {RUN_COLUMNS}
"""

_FINISH_TREE = f"""
UPDATE {RUNS_TABLE}
   SET status = $1, reason = $2, finished_at = now()
 WHERE root_id = ANY($3) AND status = 'active'
RETURNING id
"""

# Under the event lock, after the run's event: the statement's snapshot sees
# every position committed before, as it does every sequence, so positions
# commit in order and a page never skips a run that committed late.
_NUMBER_RUN = f"""
UPDATE {RUNS_TABLE}
   SET position = (SELECT COALESCE(MAX(position), 0) + 1 FROM {RUNS_TABLE})
 WHERE id = $1
"""

_LIST_RUNS = f"SELECT {RUN_COLUMNS} FROM {RUNS_TABLE}"

_STARTED_BEFORE = f"position < (SELECT position FROM {RUNS_TABLE} WHERE id = {{}})"
"""Runs started before another, whose id is bound to the placeholder put in."""

_NEWEST_FIRST = "ORDER BY position DESC LIMIT {}"

# Sequences follow on from the highest committed, which the statement sees
# because the event lock was granted first. WITH ORDINALITY keeps the events of
# one transaction in the order they happened.
_INSERT_EVENTS = f"""
INSERT INTO {EVENTS_TABLE} ({EVENT_COLUMNS})
SELECT (SELECT COALESCE(MAX(sequence), 0) FROM {EVENTS_TABLE}) + e.ordinality,
       e.run_id, e.kind
  FROM unnest($1::uuid[], $2::text[])
       WITH ORDINALITY AS e (run_id, kind, ordinality)
"""

_SELECT_EVENTS = f"""
SELECT {EVENT_COLUMNS} FROM {EVENTS_TABLE}
 WHERE sequence > $1
 ORDER BY sequence
 LIMIT $2
"""

_LAST_SEQUENCE = f"SELECT COALESCE(MAX(sequence), 0) AS last FROM {EVENTS_TABLE}"

_SELECT_TASK = f"SELECT {TASK_COLUMNS} FROM {FLOW_TASKS_TABLE} WHERE id = $1"

_LOCK_TASK = f"{_SELECT_TASK} FOR UPDATE"

_SELECT_TASK_AT = f"""
SELECT {TASK_COLUMNS} FROM {FLOW_TASKS_TABLE}
 WHERE run_id = $1 AND address = $2
"""

_INSERT_TASK_COLUMNS = (
    "id, run_id, address, queue, handler, params, fixed_params, status, "
    "attempts, lease_expires_at, result, error"
)
_INSERT_TASK = f"""
INSERT INTO {FLOW_TASKS_TABLE} ({_INSERT_TASK_COLUMNS}, created_at)
VALUES ({placeholders(_INSERT_TASK_COLUMNS)}, now())
ON CONFLICT (run_id, address) DO NOTHING
RETURNING {TASK_COLUMNS}
"""

# One statement, so the lease and everything that goes with it are atomic. The
# inner SELECT takes the oldest ready task of the queue and locks it, skipping
# rows another receiver already holds; ready is pending, or leased past the
# lease's end. Only a declared worker queue has ready tasks: no worker receives
# from an external queue, or from one nobody declared.
_RECEIVE_TASK = f"""
UPDATE {FLOW_TASKS_TABLE} AS t
   SET status = 'received',
       attempts = t.attempts + 1,
       lease_expires_at = now() + make_interval(secs => $1)
 WHERE t.id = (
       SELECT c.id
         FROM {FLOW_TASKS_TABLE} AS c
        WHERE c.queue = $2
          AND (c.status = 'pending'
               OR (c.status IN ('received', 'running')
                   AND c.lease_expires_at <= now()))
          AND EXISTS (SELECT 1 FROM {QUEUES_TABLE} AS q
                       WHERE q.name = $2 AND q.kind = 'worker')
        ORDER BY c.position
          FOR UPDATE OF c SKIP LOCKED
        LIMIT 1)
RETURNING {TASK_COLUMNS}
"""

# Reads only. The join leaves out the tasks of runs that ended: nothing will
# be published after them, so they are no longer anyone's to complete.
_PENDING_TASKS = f"""
SELECT {", ".join(f"t.{column}" for column in TASK_COLUMNS.split(", "))}
  FROM {FLOW_TASKS_TABLE} AS t
  JOIN {RUNS_TABLE} AS r ON r.id = t.run_id
 WHERE t.queue = $1 AND t.status = 'pending' AND r.status = 'active'
 ORDER BY t.position
 LIMIT $2
"""

# Reads only. GREATEST skips the times a task does not have yet, so this is
# the last of published, started and finished. A task that has not finished
# is left out once its run ended, as it is from the pending tasks.
_QUEUE_TASKS = f"""
SELECT {", ".join(f"t.{column}" for column in TASK_COLUMNS.split(", "))},
       r.flow AS run_flow, r.version AS run_version
  FROM {FLOW_TASKS_TABLE} AS t
  JOIN {RUNS_TABLE} AS r ON r.id = t.run_id
 WHERE t.queue = $1
   AND GREATEST(t.created_at, t.started_at, t.finished_at) >= $2
   AND ($3::text IS NULL OR t.status = $3)
   AND ($4::text IS NULL OR r.flow = $4)
   AND (t.finished_at IS NOT NULL OR r.status = 'active')
 ORDER BY t.position DESC
 LIMIT $5
"""

_COMPLETE_TASK = f"""
UPDATE {FLOW_TASKS_TABLE}
   SET status = $1, result = $2, error = $3, finished_at = now()
 WHERE id = $4
RETURNING {TASK_COLUMNS}
"""

_DECLARE_QUEUE = f"""
INSERT INTO {QUEUES_TABLE} (name, kind) VALUES ($1, $2)
ON CONFLICT (name) DO UPDATE SET kind = EXCLUDED.kind
"""

_SELECT_QUEUES = f"SELECT name, kind FROM {QUEUES_TABLE} ORDER BY name"

_SELECT_QUEUE_KIND = f"SELECT kind FROM {QUEUES_TABLE} WHERE name = $1"

_CLAIM_TASK = f"""
UPDATE {FLOW_TASKS_TABLE}
   SET status = 'running', started_at = COALESCE(started_at, now())
 WHERE id = $1
RETURNING {TASK_COLUMNS}
"""

_EXTEND_TASK_LEASE = f"""
UPDATE {FLOW_TASKS_TABLE}
   SET lease_expires_at = now() + make_interval(secs => $1)
 WHERE id = $2
RETURNING lease_expires_at
"""

_FINISH_TASK = f"""
UPDATE {FLOW_TASKS_TABLE}
   SET status = $1, result = $2, error = $3,
       lease_expires_at = NULL, finished_at = now()
 WHERE id = $4
RETURNING {TASK_COLUMNS}
"""

_TASKS_OF_RUN = f"""
SELECT {TASK_COLUMNS} FROM {FLOW_TASKS_TABLE} WHERE run_id = $1 ORDER BY position
"""

_SUB_RUNS_OF_RUN = f"""
SELECT {RUN_COLUMNS} FROM {RUNS_TABLE} WHERE parent_id = $1 ORDER BY position
"""

Events = list[tuple[RunId, EventKind]]


class PostgresStore(Pooled, Store):
    """Flows, runs, tasks and events in Postgres, over a connection pool."""

    async def store_flows(self, uploads: Sequence[StoredFlow]) -> list[bool]:
        async with self.pool.acquire() as conn, conn.transaction():
            await _lock(conn, UPLOAD_LOCK, shared=False)
            stored: dict[str, list[StoredFlow]] = {}
            for row in await conn.fetch(_SELECT_ALL_FLOWS):
                flow = flow_from_row(row)
                stored.setdefault(flow.name, []).append(flow)
            results = check_uploads(stored, uploads)
            new = [u for u, is_new in zip(uploads, results, strict=True) if is_new]
            if not new:
                return results
            await conn.executemany(
                _INSERT_FLOW, [values(flow_to_row(u), FLOW_COLUMNS) for u in new]
            )

            # The trees to cancel are read before their roots are locked. No
            # run of the flow can start meanwhile, because a start takes the
            # upload lock too; but one can finish, under its root lock alone,
            # so once the roots are held the trees are read again among them:
            # a tree with no run of the flow left active is not cancelled.
            roots: dict[str, list[RunId]] = {}
            for upload in new:
                rows = await conn.fetch(_ACTIVE_ROOTS_OF_FLOW, upload.name)
                roots[upload.name] = [row["root_id"] for row in rows]
            await _lock_roots(conn, [r for ids in roots.values() for r in ids])
            events: Events = []
            for upload in new:
                if not roots[upload.name]:
                    continue
                rows = await conn.fetch(
                    _ACTIVE_ROOTS_OF_FLOW_AMONG, upload.name, roots[upload.name]
                )
                still = [row["root_id"] for row in rows]
                if still:
                    reason = f"{upload.name} {upload.version} was uploaded"
                    events += await _finish_trees(
                        conn, still, RunStatus.CANCELLED, reason
                    )
            await _append_events(conn, events)
            return results

    async def get_flow(self, name: str, version: Version | None = None) -> StoredFlow:
        async with self.pool.acquire() as conn:
            return await _flow(conn, name, version)

    async def latest_flows(self) -> list[StoredFlow]:
        async with self.pool.acquire() as conn:
            return [
                flow_from_row(row) for row in await conn.fetch(_SELECT_LATEST_FLOWS)
            ]

    async def flow_versions(self, name: str) -> list[StoredFlow]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(_SELECT_FLOW_VERSIONS, name)
        if not rows:
            raise FlowNotFoundError(f"no flow {name!r}")
        return [flow_from_row(row) for row in rows]

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

        def bind(value: object) -> str:
            args.append(value)
            return f"${len(args)}"

        if flow is not None:
            conditions.append(f"flow = {bind(flow)}")
        if status is not None:
            conditions.append(f"status = {bind(status.value)}")
        if root_only:
            conditions.append("parent_id IS NULL")
        if before is not None:
            conditions.append(_STARTED_BEFORE.format(bind(before)))
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"{_LIST_RUNS}{where} {_NEWEST_FIRST.format(bind(limit))}"
        async with self.pool.acquire() as conn, conn.transaction():
            if before is not None:
                await _run(conn, before)  # RunNotFoundError, else its position
            return [run_from_row(row) for row in await conn.fetch(query, *args)]

    async def run_tasks(self, run_id: RunId) -> list[Task]:
        async with self.pool.acquire() as conn, conn.transaction():
            await _run(conn, run_id)
            rows = await conn.fetch(_TASKS_OF_RUN, run_id)
            return [task_from_row(row) for row in rows]

    async def sub_runs(self, run_id: RunId) -> list[Run]:
        async with self.pool.acquire() as conn, conn.transaction():
            await _run(conn, run_id)
            rows = await conn.fetch(_SUB_RUNS_OF_RUN, run_id)
            return [run_from_row(row) for row in rows]

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
        async with self.pool.acquire() as conn, conn.transaction():
            await _lock(conn, UPLOAD_LOCK, shared=True)
            latest = (await _flow(conn, flow, None)).version
            if version != latest:
                raise FlowVersionError(
                    f"{flow} {version} is not the latest version, {latest}"
                )
            if parent_id is None:
                run_id = uuid.uuid4()
                root_id = run_id
            else:
                assert parent_address is not None
                parent = await _run(conn, parent_id)
                await _lock_roots(conn, [parent.root_id])
                # Under the root lock, the parent's status is settled.
                ensure_active(await _run(conn, parent_id))
                run_id = sub_run_id_for(parent_id, parent_address)
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
            )
            row = await conn.fetchrow(
                _INSERT_RUN, *values(run_to_row(run), _INSERT_RUN_COLUMNS)
            )
            if row is None:  # the same instance, started before
                return await _run(conn, run_id)
            await _append_events(conn, [(run_id, EventKind.RUN_STARTED)])
            await conn.execute(_NUMBER_RUN, run_id)
            return run_from_row(row)

    async def get_run(self, run_id: RunId) -> Run:
        async with self.pool.acquire() as conn:
            return await _run(conn, run_id)

    async def run_state(self, run_id: RunId) -> RunState:
        # The run, its tasks and its sub-runs from one snapshot, so the state is
        # one a serial history could have reached.
        async with (
            self.pool.acquire() as conn,
            conn.transaction(isolation="repeatable_read"),
        ):
            run = await _run(conn, run_id)
            tasks = await conn.fetch(_TASKS_OF_RUN, run_id)
            sub_runs = await conn.fetch(_SUB_RUNS_OF_RUN, run_id)
            return run_state_of(
                run,
                [task_from_row(row) for row in tasks],
                [run_from_row(row) for row in sub_runs],
            )

    async def succeed_run(self, run_id: RunId, output: JsonValue) -> Run:
        async with self.pool.acquire() as conn, conn.transaction():
            run = await _run(conn, run_id)
            await _lock_roots(conn, [run.root_id])
            row = await conn.fetchrow(_SUCCEED_RUN, run_id, dumps_json(output))
            if row is None:  # not active any more: say what it is instead
                ensure_active(await _run(conn, run_id))
                raise AssertionError("unreachable: the run was active and locked")
            await _append_events(conn, [(run_id, EventKind.RUN_FINISHED)])
            return run_from_row(row)

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
        async with self.pool.acquire() as conn, conn.transaction():
            run = await _run(conn, run_id)
            await _lock_roots(conn, [run.root_id])
            # Under the root lock, the run's status is settled.
            ensure_active(await _run(conn, run_id))
            task = Task(
                id=uuid.uuid4(),
                run_id=run_id,
                address=address,
                queue=queue,
                handler=handler,
                params=dict(params),
                fixed_params=dict(fixed_params),
            )
            stored = task_to_row(task)
            row = await conn.fetchrow(
                _INSERT_TASK, *values(stored, _INSERT_TASK_COLUMNS)
            )
            if row is None:  # published before: unchanged, with its first id
                row = await conn.fetchrow(
                    _SELECT_TASK_AT, stored["run_id"], stored["address"]
                )
                if row is None:  # unreachable: the run's root is locked
                    raise TaskNotFoundError(f"{run_id} {address}")
            return task_from_row(row)

    async def receive_task(
        self, queue: str, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> Task | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(_RECEIVE_TASK, float(lease_seconds), queue)
        return None if row is None else task_from_row(row)

    async def claim_task(self, task_id: TaskId) -> Task:
        inactive: Run | None = None
        row = None
        async with self.pool.acquire() as conn, conn.transaction():
            run = await _run(conn, (await _task(conn, task_id)).run_id)
            await _lock_roots(conn, [run.root_id])
            task = await _task(conn, task_id, lock=True)
            ensure_transition(task.status, TaskStatus.RUNNING)
            run = await _run(conn, run.id)
            if run.status is not RunStatus.ACTIVE:
                # Failed and committed before the refusal is raised, so the
                # lease never hands the task out again.
                await conn.execute(
                    _FINISH_TASK,
                    TaskStatus.FAILED.value,
                    dumps_json(None),
                    f"run {run.id} is {run.status.value}",
                    task_id,
                )
                inactive = run
            else:
                row = await conn.fetchrow(_CLAIM_TASK, task_id)
        if inactive is not None:
            ensure_active(inactive)
        if row is None:  # unreachable: the row was locked just above
            raise TaskNotFoundError(str(task_id))
        return task_from_row(row)

    async def extend_task_lease(
        self, task_id: TaskId, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> datetime:
        async with self.pool.acquire() as conn, conn.transaction():
            task = await _task(conn, task_id, lock=True)
            if task.status not in LEASED_STATUSES:
                raise TaskStateError(
                    f"a {task.status.value} task holds no lease to extend"
                )
            row = await conn.fetchrow(_EXTEND_TASK_LEASE, float(lease_seconds), task_id)
        if row is None:  # unreachable: the row was locked just above
            raise TaskNotFoundError(str(task_id))
        expires_at: datetime = row["lease_expires_at"]
        return expires_at

    async def finish_task(
        self, task_id: TaskId, *, result: JsonValue = None, error: str | None = None
    ) -> Task:
        status = TaskStatus.FAILED if error is not None else TaskStatus.SUCCEEDED
        async with self.pool.acquire() as conn, conn.transaction():
            task = await _task(conn, task_id, lock=True)
            ensure_transition(task.status, status)
            row = await conn.fetchrow(
                _FINISH_TASK,
                status.value,
                dumps_json(result if error is None else None),
                storable_text(error) if error is not None else None,
                task_id,
            )
            if row is None:  # unreachable: the row was locked just above
                raise TaskNotFoundError(str(task_id))
            await _append_events(conn, [(task.run_id, EventKind.TASK_FINISHED)])
            return task_from_row(row)

    async def pending_tasks(
        self, queue: str, *, limit: int = DEFAULT_PAGE
    ) -> list[Task]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(_PENDING_TASKS, queue, limit)
        return [task_from_row(row) for row in rows]

    async def queue_tasks(
        self,
        queue: str,
        *,
        since: datetime,
        status: TaskStatus | None = None,
        flow: str | None = None,
        limit: int = DEFAULT_PAGE,
    ) -> list[QueueTask]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                _QUEUE_TASKS,
                queue,
                since,
                None if status is None else status.value,
                flow,
                limit,
            )
        return [
            QueueTask(
                task_from_row(row), row["run_flow"], Version.parse(row["run_version"])
            )
            for row in rows
        ]

    async def complete_task(
        self, task_id: TaskId, *, result: JsonValue = None, error: str | None = None
    ) -> Task:
        status = TaskStatus.FAILED if error is not None else TaskStatus.SUCCEEDED
        async with self.pool.acquire() as conn, conn.transaction():
            run = await _run(conn, (await _task(conn, task_id)).run_id)
            await _lock_roots(conn, [run.root_id])
            task = await _task(conn, task_id, lock=True)
            ensure_completable(task.status)
            # Under the root lock, the run's status is settled.
            ensure_active(await _run(conn, run.id))
            row = await conn.fetchrow(
                _COMPLETE_TASK,
                status.value,
                dumps_json(result if error is None else None),
                storable_text(error) if error is not None else None,
                task_id,
            )
            if row is None:  # unreachable: the row was locked just above
                raise TaskNotFoundError(str(task_id))
            await _append_events(conn, [(task.run_id, EventKind.TASK_FINISHED)])
            return task_from_row(row)

    async def get_task(self, task_id: TaskId) -> Task:
        async with self.pool.acquire() as conn:
            return await _task(conn, task_id)

    async def declare_queue(self, name: str, kind: QueueKind) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(_DECLARE_QUEUE, name, kind.value)

    async def queues(self) -> list[Queue]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(_SELECT_QUEUES)
        return [Queue(row["name"], QueueKind(row["kind"])) for row in rows]

    async def queue_kind(self, name: str) -> QueueKind | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(_SELECT_QUEUE_KIND, name)
        return None if row is None else QueueKind(row["kind"])

    async def events_after(self, sequence: int, *, limit: int = 100) -> list[Event]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(_SELECT_EVENTS, sequence, limit)
        return [event_from_row(row) for row in rows]

    async def last_sequence(self) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(_LAST_SEQUENCE)
        assert row is not None
        last: int = row["last"]
        return last

    async def _finish_tree(self, run_id: RunId, status: RunStatus, reason: str) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            root_id = (await _run(conn, run_id)).root_id
            await _lock_roots(conn, [root_id])
            events = await _finish_trees(conn, [root_id], status, reason)
            await _append_events(conn, events)


async def _lock(conn: Connection, key: tuple[int, int], *, shared: bool) -> None:
    function = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
    await conn.execute(f"SELECT {function}($1::integer, $2::integer)", *key)


async def _lock_roots(conn: Connection, root_ids: Sequence[RunId]) -> None:
    """Lock root run rows, in id order, before anything in their trees changes."""
    if root_ids:
        await conn.execute(_LOCK_ROOTS, sorted(set(root_ids)))


async def _flow(conn: Connection, name: str, version: Version | None) -> StoredFlow:
    if version is None:
        row = await conn.fetchrow(_SELECT_LATEST_FLOW, name)
    else:
        row = await conn.fetchrow(
            _SELECT_FLOW, name, version.major, version.minor, version.patch
        )
    if row is None:
        if version is None:
            raise FlowNotFoundError(f"no flow {name!r}")
        raise FlowNotFoundError(f"no version {version} of flow {name!r}")
    return flow_from_row(row)


async def _run(conn: Connection, run_id: RunId) -> Run:
    row = await conn.fetchrow(_SELECT_RUN, run_id)
    if row is None:
        raise RunNotFoundError(str(run_id))
    return run_from_row(row)


async def _task(conn: Connection, task_id: TaskId, *, lock: bool = False) -> Task:
    """A task; with ``lock``, its row locked so the caller can decide and write."""
    row = await conn.fetchrow(_LOCK_TASK if lock else _SELECT_TASK, task_id)
    if row is None:
        raise TaskNotFoundError(str(task_id))
    return task_from_row(row)


async def _finish_trees(
    conn: Connection, root_ids: Sequence[RunId], status: RunStatus, reason: str
) -> Events:
    """End every active run of the trees, their roots already locked; the events."""
    rows = await conn.fetch(
        _FINISH_TREE, status.value, storable_text(reason), list(root_ids)
    )
    return [(row["id"], EventKind.RUN_FINISHED) for row in rows]


async def _append_events(conn: Connection, events: Events) -> None:
    """Record the transaction's events, last thing, under the event lock."""
    if not events:
        return
    await _lock(conn, EVENT_LOCK, shared=False)
    await conn.execute(
        _INSERT_EVENTS,
        [run_id for run_id, _ in events],
        [kind.value for _, kind in events],
    )
