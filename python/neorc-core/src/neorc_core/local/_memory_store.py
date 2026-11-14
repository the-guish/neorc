# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The store for flows, runs and tasks, in dictionaries."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from neorc_core._errors import (
    FlowNotFoundError,
    FlowVersionError,
    RunNotFoundError,
    TaskNotFoundError,
    TaskStateError,
)
from neorc_core._queues import Queue, QueueKind
from neorc_core._runs import (
    Event,
    EventKind,
    QueueTask,
    Run,
    RunId,
    RunStatus,
    StoredFlow,
    Task,
    check_uploads,
    ensure_active,
    last_change,
    run_state_of,
    storable_text,
    sub_run_id_for,
)
from neorc_core._task import (
    LEASED_STATUSES,
    TaskId,
    TaskStatus,
    ensure_completable,
    ensure_transition,
)
from neorc_core._values import JsonValue
from neorc_core.flows import Address, Reference, RunState, Version
from neorc_core.ports._clients import DEFAULT_LEASE_SECONDS
from neorc_core.ports._store import DEFAULT_PAGE, Store


class MemoryStore(Store):
    """Flows, runs and tasks in dictionaries: one lock per operation.

    Applies the same rules, transitions and lease expiry as a deployed store.

    ``queues`` are declared from the start, as a deployment's operator would
    have declared them before anything ran.
    """

    def __init__(self, *, queues: Mapping[str, QueueKind] | None = None) -> None:
        self._lock = asyncio.Lock()
        self._flows: dict[str, dict[Version, StoredFlow]] = {}
        self._runs: dict[RunId, Run] = {}
        self._tasks: dict[TaskId, Task] = {}
        self._task_ids: dict[tuple[RunId, Address], TaskId] = {}
        self._events: list[Event] = []
        self._queues: dict[str, QueueKind] = dict(queues or {})

    async def store_flows(self, uploads: Sequence[StoredFlow]) -> list[bool]:
        async with self._lock:
            stored = {
                name: list(versions.values()) for name, versions in self._flows.items()
            }
            results = check_uploads(stored, uploads)
            for upload, is_new in zip(uploads, results, strict=True):
                if not is_new:
                    continue
                self._flows.setdefault(upload.name, {})[upload.version] = upload
                reason = f"{upload.name} {upload.version} was uploaded"
                roots = {
                    run.root_id
                    for run in self._runs.values()
                    if run.flow == upload.name and run.status is RunStatus.ACTIVE
                }
                for root_id in sorted(roots):
                    self._finish_tree(root_id, RunStatus.CANCELLED, reason)
            return results

    async def get_flow(self, name: str, version: Version | None = None) -> StoredFlow:
        async with self._lock:
            return self._flow(name, version)

    async def latest_flows(self) -> list[StoredFlow]:
        async with self._lock:
            return [
                versions[max(versions)]
                for _, versions in sorted(self._flows.items())
                if versions
            ]

    async def flow_versions(self, name: str) -> list[StoredFlow]:
        async with self._lock:
            versions = self._flows.get(name)
            if not versions:
                raise FlowNotFoundError(f"no flow {name!r}")
            return [versions[v] for v in sorted(versions, reverse=True)]

    async def list_runs(
        self,
        *,
        flow: str | None = None,
        status: RunStatus | None = None,
        root_only: bool = True,
        before: RunId | None = None,
        limit: int = DEFAULT_PAGE,
    ) -> list[Run]:
        async with self._lock:
            # The dictionary keeps the order runs started in: the order to list
            # and page by, since the clock may give two runs the same time.
            started = list(self._runs.values())
            if before is not None:
                self._run(before)
                started = started[: [run.id for run in started].index(before)]
            started.reverse()
            runs = [
                run
                for run in started
                if (flow is None or run.flow == flow)
                and (status is None or run.status is status)
                and (not root_only or run.parent_id is None)
            ]
            return runs[:limit]

    async def run_tasks(self, run_id: RunId) -> list[Task]:
        async with self._lock:
            self._run(run_id)
            # Publishing order: the dictionary keeps it.
            return [task for task in self._tasks.values() if task.run_id == run_id]

    async def sub_runs(self, run_id: RunId) -> list[Run]:
        async with self._lock:
            self._run(run_id)
            # In the order they started: the dictionary keeps it.
            return [run for run in self._runs.values() if run.parent_id == run_id]

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
        async with self._lock:
            latest = self._flow(flow, None).version
            if version != latest:
                raise FlowVersionError(
                    f"{flow} {version} is not the latest version, {latest}"
                )
            if parent_id is None:
                run_id = uuid.uuid4()
                root_id = run_id
            else:
                assert parent_address is not None
                parent = self._run(parent_id)
                ensure_active(parent)
                run_id = sub_run_id_for(parent_id, parent_address)
                existing = self._runs.get(run_id)
                if existing is not None:
                    return existing
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
                created_at=datetime.now(UTC),
            )
            self._runs[run_id] = run
            self._append(run_id, EventKind.RUN_STARTED)
            return run

    async def get_run(self, run_id: RunId) -> Run:
        async with self._lock:
            return self._run(run_id)

    async def run_state(self, run_id: RunId) -> RunState:
        async with self._lock:
            run = self._run(run_id)
            tasks = [task for task in self._tasks.values() if task.run_id == run_id]
            sub_runs = [r for r in self._runs.values() if r.parent_id == run_id]
            return run_state_of(run, tasks, sub_runs)

    async def succeed_run(self, run_id: RunId, output: JsonValue) -> Run:
        async with self._lock:
            run = self._run(run_id)
            ensure_active(run)
            succeeded = replace(
                run,
                status=RunStatus.SUCCEEDED,
                output=output,
                finished_at=datetime.now(UTC),
            )
            self._runs[run_id] = succeeded
            self._append(run_id, EventKind.RUN_FINISHED)
            return succeeded

    async def fail_run_tree(self, run_id: RunId, reason: str) -> None:
        async with self._lock:
            self._finish_tree(self._run(run_id).root_id, RunStatus.FAILED, reason)

    async def cancel_run_tree(self, run_id: RunId, reason: str) -> None:
        async with self._lock:
            self._finish_tree(self._run(run_id).root_id, RunStatus.CANCELLED, reason)

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
        async with self._lock:
            ensure_active(self._run(run_id))
            existing_id = self._task_ids.get((run_id, address))
            if existing_id is not None:
                return self._tasks[existing_id]
            task_id = uuid.uuid4()
            task = Task(
                id=task_id,
                run_id=run_id,
                address=address,
                queue=queue,
                handler=handler,
                params=dict(params),
                fixed_params=dict(fixed_params),
                created_at=datetime.now(UTC),
            )
            self._tasks[task_id] = task
            self._task_ids[run_id, address] = task_id
            return task

    async def receive_task(
        self, queue: str, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> Task | None:
        now = datetime.now(UTC)
        async with self._lock:
            if self._queues.get(queue) is not QueueKind.WORKER:
                return None
            for task in self._tasks.values():  # in publishing order: oldest first
                if task.queue == queue and _is_receivable(task, now):
                    received = replace(
                        task,
                        status=TaskStatus.RECEIVED,
                        attempts=task.attempts + 1,
                        lease_expires_at=now + timedelta(seconds=lease_seconds),
                    )
                    self._tasks[task.id] = received
                    return received
            return None

    async def claim_task(self, task_id: TaskId) -> Task:
        async with self._lock:
            task = self._task(task_id)
            ensure_transition(task.status, TaskStatus.RUNNING)
            run = self._run(task.run_id)
            now = datetime.now(UTC)
            if run.status is not RunStatus.ACTIVE:
                self._tasks[task_id] = replace(
                    task,
                    status=TaskStatus.FAILED,
                    error=f"run {run.id} is {run.status.value}",
                    lease_expires_at=None,
                    finished_at=now,
                )
                ensure_active(run)
            claimed = replace(
                task, status=TaskStatus.RUNNING, started_at=task.started_at or now
            )
            self._tasks[task_id] = claimed
            return claimed

    async def extend_task_lease(
        self, task_id: TaskId, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> datetime:
        async with self._lock:
            task = self._task(task_id)
            if task.status not in LEASED_STATUSES:
                raise TaskStateError(
                    f"a {task.status.value} task holds no lease to extend"
                )
            expires_at = datetime.now(UTC) + timedelta(seconds=lease_seconds)
            self._tasks[task_id] = replace(task, lease_expires_at=expires_at)
            return expires_at

    async def finish_task(
        self, task_id: TaskId, *, result: JsonValue = None, error: str | None = None
    ) -> Task:
        status = TaskStatus.FAILED if error is not None else TaskStatus.SUCCEEDED
        async with self._lock:
            task = self._task(task_id)
            ensure_transition(task.status, status)
            finished = replace(
                task,
                status=status,
                result=result if error is None else None,
                error=storable_text(error) if error is not None else None,
                lease_expires_at=None,
                finished_at=datetime.now(UTC),
            )
            self._tasks[task_id] = finished
            self._append(task.run_id, EventKind.TASK_FINISHED)
            return finished

    async def pending_tasks(
        self, queue: str, *, limit: int = DEFAULT_PAGE
    ) -> list[Task]:
        async with self._lock:
            pending = (
                task
                for task in self._tasks.values()  # in publishing order
                if task.queue == queue
                and task.status is TaskStatus.PENDING
                and self._run(task.run_id).status is RunStatus.ACTIVE
            )
            return [task for task, _ in zip(pending, range(limit), strict=False)]

    async def queue_tasks(
        self,
        queue: str,
        *,
        since: datetime,
        status: TaskStatus | None = None,
        flow: str | None = None,
        limit: int = DEFAULT_PAGE,
    ) -> list[QueueTask]:
        async with self._lock:
            listed: list[QueueTask] = []
            for task in reversed(self._tasks.values()):  # published last, first
                if len(listed) == limit:
                    break
                changed = last_change(task)
                run = self._run(task.run_id)
                if (
                    task.queue == queue
                    and changed is not None
                    and changed >= since
                    and (status is None or task.status is status)
                    and (flow is None or run.flow == flow)
                    and (task.finished_at is not None or run.status is RunStatus.ACTIVE)
                ):
                    listed.append(QueueTask(task, run.flow, run.version))
            return listed

    async def complete_task(
        self, task_id: TaskId, *, result: JsonValue = None, error: str | None = None
    ) -> Task:
        status = TaskStatus.FAILED if error is not None else TaskStatus.SUCCEEDED
        async with self._lock:
            task = self._task(task_id)
            ensure_completable(task.status)
            ensure_active(self._run(task.run_id))
            completed = replace(
                task,
                status=status,
                result=result if error is None else None,
                error=storable_text(error) if error is not None else None,
                finished_at=datetime.now(UTC),
            )
            self._tasks[task_id] = completed
            self._append(task.run_id, EventKind.TASK_FINISHED)
            return completed

    async def get_task(self, task_id: TaskId) -> Task:
        async with self._lock:
            return self._task(task_id)

    async def declare_queue(self, name: str, kind: QueueKind) -> None:
        async with self._lock:
            self._queues[name] = kind

    async def queues(self) -> list[Queue]:
        async with self._lock:
            return [Queue(name, kind) for name, kind in sorted(self._queues.items())]

    async def queue_kind(self, name: str) -> QueueKind | None:
        async with self._lock:
            return self._queues.get(name)

    async def events_after(self, sequence: int, *, limit: int = 100) -> list[Event]:
        async with self._lock:
            # Sequences are 1, 2, 3...: the event after ``sequence`` is at its index.
            return self._events[max(sequence, 0) : max(sequence, 0) + limit]

    async def last_sequence(self) -> int:
        async with self._lock:
            return len(self._events)

    def _flow(self, name: str, version: Version | None) -> StoredFlow:
        versions = self._flows.get(name)
        if not versions:
            raise FlowNotFoundError(f"no flow {name!r}")
        stored = versions.get(max(versions) if version is None else version)
        if stored is None:
            raise FlowNotFoundError(f"no version {version} of flow {name!r}")
        return stored

    def _run(self, run_id: RunId) -> Run:
        run = self._runs.get(run_id)
        if run is None:
            raise RunNotFoundError(str(run_id))
        return run

    def _task(self, task_id: TaskId) -> Task:
        task = self._tasks.get(task_id)
        if task is None:
            raise TaskNotFoundError(str(task_id))
        return task

    def _append(self, run_id: RunId, kind: EventKind) -> None:
        self._events.append(Event(len(self._events) + 1, run_id, kind))

    def _finish_tree(self, root_id: RunId, status: RunStatus, reason: str) -> None:
        reason = storable_text(reason)
        now = datetime.now(UTC)
        for run in list(self._runs.values()):
            if run.root_id == root_id and run.status is RunStatus.ACTIVE:
                self._runs[run.id] = replace(
                    run, status=status, reason=reason, finished_at=now
                )
                self._append(run.id, EventKind.RUN_FINISHED)


def _is_receivable(task: Task, now: datetime) -> bool:
    if task.status is TaskStatus.PENDING:
        return True
    return (
        task.status in LEASED_STATUSES
        and task.lease_expires_at is not None
        and task.lease_expires_at <= now
    )
