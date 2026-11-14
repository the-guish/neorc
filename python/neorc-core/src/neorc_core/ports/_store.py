# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The single port a manager keeps flows, runs, tasks and events behind.

Each method is one atomic operation: a transaction in a database, one lock in
memory. Core never composes several of them into something that has to be
atomic. The decisions an operation makes inside, such as the upload rules and a
run's state, come from ``neorc_core._runs``, so every store makes them alike.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from datetime import datetime

from neorc_core._queues import Queue, QueueKind
from neorc_core._runs import (
    Event,
    QueueTask,
    Run,
    RunId,
    RunStatus,
    StoredFlow,
    Task,
)
from neorc_core._task import TaskId, TaskStatus
from neorc_core._values import JsonValue
from neorc_core.flows import Address, Reference, RunState, Version
from neorc_core.ports._clients import DEFAULT_LEASE_SECONDS

DEFAULT_PAGE = 50
"""How many runs ``list_runs`` returns unless told otherwise."""


class Store(ABC):
    """Durable storage for flow versions, runs, their tasks and their events.

    The store keeps the clock: it sets ``created_at``, ``started_at`` and
    ``finished_at`` on runs and tasks as it creates, starts and finishes them,
    so the times agree with the rows they describe.
    """

    @abstractmethod
    async def store_flows(self, uploads: Sequence[StoredFlow]) -> list[bool]:
        """Store flow versions deployed together, and cancel the run trees they replace.

        One operation: ``check_uploads`` decides against every stored version,
        and raises before anything changes if an upload breaks a version rule
        or leaves the latest flows invalid as a set. Returns, per upload,
        whether a new version was stored; ``False`` for an identical one. Each
        new version cancels every active run tree containing a run of its flow.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_flow(self, name: str, version: Version | None = None) -> StoredFlow:
        """A flow version, the latest when ``version`` is ``None``.

        Raises ``FlowNotFoundError`` if there is no such flow or version.
        """
        raise NotImplementedError

    @abstractmethod
    async def latest_flows(self) -> list[StoredFlow]:
        """The latest version of every stored flow, by name."""
        raise NotImplementedError

    @abstractmethod
    async def flow_versions(self, name: str) -> list[StoredFlow]:
        """Every stored version of a flow, newest first.

        Raises ``FlowNotFoundError`` if there is no such flow.
        """
        raise NotImplementedError

    @abstractmethod
    async def list_runs(
        self,
        *,
        flow: str | None = None,
        status: RunStatus | None = None,
        root_only: bool = True,
        before: RunId | None = None,
        limit: int = DEFAULT_PAGE,
    ) -> list[Run]:
        """Up to ``limit`` runs, newest first: the reverse of the order they started.

        The store keeps that order itself, since a clock can give two runs the
        same ``created_at``; the time is for display. ``flow`` and ``status``
        keep only the runs of that flow or in that status; ``root_only``
        leaves sub-flow runs out. ``before`` is the id of the last run of the
        previous page: the next page holds the runs started before it, so pages
        never overlap or skip. Raises ``RunNotFoundError`` if there is no run
        ``before``.
        """
        raise NotImplementedError

    @abstractmethod
    async def run_tasks(self, run_id: RunId) -> list[Task]:
        """A run's tasks, in the order they were published.

        Raises ``RunNotFoundError`` if there is no such run.
        """
        raise NotImplementedError

    @abstractmethod
    async def sub_runs(self, run_id: RunId) -> list[Run]:
        """A run's direct sub-flow runs, in the order they started.

        Raises ``RunNotFoundError`` if there is no such run.
        """
        raise NotImplementedError

    @abstractmethod
    async def start_run(
        self,
        flow: str,
        version: Version,
        inputs: Mapping[str, JsonValue],
        *,
        parent_id: RunId | None = None,
        parent_address: Address | None = None,
    ) -> Run:
        """Start an active run of ``version``, and record a "run started" event.

        ``version`` must still be the flow's latest, or this raises
        ``FlowVersionError``; ``FlowNotFoundError`` if the flow is unknown. A
        sub-flow run names its parent and the step instance it is: the parent
        must be active (``RunStateError``), and starting the same instance again
        returns the run already started, with no new event.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_run(self, run_id: RunId) -> Run:
        """A run; ``RunNotFoundError`` if there is none."""
        raise NotImplementedError

    @abstractmethod
    async def run_state(self, run_id: RunId) -> RunState:
        """The run's state, built by ``run_state_of`` from its tasks and sub-runs."""
        raise NotImplementedError

    @abstractmethod
    async def succeed_run(self, run_id: RunId, output: JsonValue) -> Run:
        """Mark an active run succeeded with ``output``; record "run finished".

        Raises ``RunStateError`` if the run is not active.
        """
        raise NotImplementedError

    @abstractmethod
    async def fail_run_tree(self, run_id: RunId, reason: str) -> None:
        """Fail every active run in ``run_id``'s tree, recording "run finished"."""
        raise NotImplementedError

    @abstractmethod
    async def cancel_run_tree(self, run_id: RunId, reason: str) -> None:
        """Cancel every active run in ``run_id``'s tree, recording "run finished"."""
        raise NotImplementedError

    @abstractmethod
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
        """Publish the pending task at ``address`` in an active run.

        Its id is a random UUID, which a worker cannot guess: a worker holds the
        id of a task only by receiving it. Publishing an address again returns
        the task already there, unchanged, with its first id. Raises
        ``RunStateError`` if the run is not active.
        """
        raise NotImplementedError

    @abstractmethod
    async def receive_task(
        self, queue: str, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> Task | None:
        """Lease the oldest ready task on ``queue``, or return ``None``.

        Ready means pending, or holding a lapsed lease. Taking the lease,
        changing the status and counting the attempt are one step, and no two
        callers are given the same task. Only a declared worker queue hands
        tasks out: an external queue's end only by ``complete_task``, and a
        queue nobody declared is no queue.
        """
        raise NotImplementedError

    @abstractmethod
    async def claim_task(self, task_id: TaskId) -> Task:
        """Record that a worker claimed a received task and is executing it.

        If the task's run is no longer active, the task fails instead, so it is
        never handed out again, and this raises ``RunStateError``: the worker
        drops it without running it.
        """
        raise NotImplementedError

    @abstractmethod
    async def extend_task_lease(
        self, task_id: TaskId, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> datetime:
        """Push a leased task's lease out from now, and return its new expiry."""
        raise NotImplementedError

    @abstractmethod
    async def finish_task(
        self, task_id: TaskId, *, result: JsonValue = None, error: str | None = None
    ) -> Task:
        """Record a task's result, or its failure when ``error`` is set.

        Records "task finished" whether or not the run is still active: a task
        already in flight may finish after its run was cancelled.
        """
        raise NotImplementedError

    @abstractmethod
    async def pending_tasks(
        self, queue: str, *, limit: int = DEFAULT_PAGE
    ) -> list[Task]:
        """Up to ``limit`` pending tasks of ``queue`` in active runs, oldest first.

        Reads only: no lease is taken, and no task changes.
        """
        raise NotImplementedError

    @abstractmethod
    async def queue_tasks(
        self,
        queue: str,
        *,
        since: datetime,
        status: TaskStatus | None = None,
        flow: str | None = None,
        limit: int = DEFAULT_PAGE,
    ) -> list[QueueTask]:
        """Up to ``limit`` tasks of ``queue`` that changed at or after ``since``.

        In any status, the task published last first. A task that has not
        finished is listed only while its run is active: once the run ended,
        nothing will finish it. A task changes when it is published, started
        or finished (``last_change``). ``status`` keeps only the tasks in that
        status, and ``flow`` only those of that flow's runs. Reads only.
        """
        raise NotImplementedError

    @abstractmethod
    async def complete_task(
        self, task_id: TaskId, *, result: JsonValue = None, error: str | None = None
    ) -> Task:
        """End a pending task with its result, or its failure when ``error`` is set.

        One step, with no lease before it: ``ensure_completable`` decides, so a
        task a worker received, or one already ended, raises ``TaskStateError``,
        and of two completions of a task one is refused. Raises
        ``RunStateError``, leaving the task as it is, if its run is no longer
        active. Records "task finished".
        """
        raise NotImplementedError

    @abstractmethod
    async def get_task(self, task_id: TaskId) -> Task:
        """A task; ``TaskNotFoundError`` if there is none."""
        raise NotImplementedError

    @abstractmethod
    async def declare_queue(self, name: str, kind: QueueKind) -> None:
        """Declare ``name`` a queue of ``kind``, whatever it was declared before."""
        raise NotImplementedError

    @abstractmethod
    async def queues(self) -> list[Queue]:
        """Every declared queue, by name."""
        raise NotImplementedError

    @abstractmethod
    async def queue_kind(self, name: str) -> QueueKind | None:
        """The kind of the queue ``name``, or ``None`` if nobody declared it."""
        raise NotImplementedError

    @abstractmethod
    async def events_after(self, sequence: int, *, limit: int = 100) -> list[Event]:
        """Up to ``limit`` events with a sequence above ``sequence``, oldest first."""
        raise NotImplementedError

    @abstractmethod
    async def last_sequence(self) -> int:
        """The sequence of the latest event, or 0 with none: where a reader starts.

        A reader that wants only what happens from now on begins after it,
        instead of reading the whole log to find its end.
        """
        raise NotImplementedError
