# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""A task's identity and lifecycle, the same in every store."""

from __future__ import annotations

import uuid
from enum import StrEnum

from neorc_core._errors import TaskStateError

TaskId = uuid.UUID


class TaskStatus(StrEnum):
    """Where a task is in its lifecycle.

    A task is published ``PENDING``, becomes ``RECEIVED`` when a worker
    receives it and takes its lease, ``RUNNING`` once that worker claims it,
    and ends ``SUCCEEDED`` or ``FAILED``. A lease that lapses makes a
    ``RECEIVED`` or ``RUNNING`` task receivable again without passing through a
    terminal status.
    """

    PENDING = "pending"
    RECEIVED = "received"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


TERMINAL_STATUSES = frozenset({TaskStatus.SUCCEEDED, TaskStatus.FAILED})
"""Statuses a task never leaves."""

LEASED_STATUSES = frozenset({TaskStatus.RECEIVED, TaskStatus.RUNNING})
"""Statuses in which a worker holds the task and must keep heartbeating."""

_ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.RECEIVED}),
    TaskStatus.RECEIVED: frozenset(
        {TaskStatus.RUNNING, TaskStatus.SUCCEEDED, TaskStatus.FAILED}
    ),
    # Claiming again is allowed: delivery is at-least-once, so a worker that
    # retried the call must not be told off for it.
    TaskStatus.RUNNING: frozenset(
        {TaskStatus.RUNNING, TaskStatus.SUCCEEDED, TaskStatus.FAILED}
    ),
    TaskStatus.SUCCEEDED: frozenset(),
    TaskStatus.FAILED: frozenset(),
}


def ensure_transition(current: TaskStatus, target: TaskStatus) -> None:
    """Raise ``TaskStateError`` unless ``current`` may become ``target``.

    Every store enforces the same rules, so an adapter cannot invent its own
    lifecycle.
    """
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise TaskStateError(
            f"a {current.value} task cannot become {target.value}",
        )


def ensure_completable(current: TaskStatus) -> None:
    """Raise ``TaskStateError`` unless a task that is ``current`` may be completed.

    A pending task is the only one: once a worker received a task, the task
    is the worker's to finish, and a task already ended stays as it ended.
    """
    if current is not TaskStatus.PENDING:
        raise TaskStateError(
            f"a {current.value} task cannot be completed: only a pending one can",
        )
