# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""neorc's permissions and roles, as plain data.

A permission is one operation of neorc, such as ``neorc/runs:start``. A role is
a fixed set of permissions, such as ``neorc/worker``. Every name starts with
``neorc/``, as oneberth names an application's permissions and roles
(oneberth's ``docs/specs/identity.md``, "Roles").

This module is data alone. oneberth signs people in, keeps tokens and decides
whether a principal holds a permission. The ``neorc`` application hands this
table to oneberth when its backend starts. The core imports nothing of
oneberth: what it needs of who is asking, such as the queue a worker is bound
to, it is given as a plain value.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

APP = "neorc"
"""The application's name: the first part of every permission and role."""

QUEUE_RESOURCE = "queue"
"""What a worker's or a responder's grant is bound to."""


class Role(StrEnum):
    """What a principal is for; each role is a fixed set of permissions."""

    WORKER = "neorc/worker"
    """Takes and runs the tasks of the one queue its token is bound to."""
    RESPONDER = "neorc/responder"
    """Lists and completes the pending tasks of the one queue its token is bound to."""
    SCHEDULER = "neorc/scheduler"
    CI = "neorc/ci"
    """Uploads flows, and declares the queues they name."""
    USER = "neorc/user"
    """A person, signed in or with a personal token for their scripts.

    Bound to no queue: a person sees every queue and completes the pending
    tasks of any.
    """
    EXTERNAL_TRIGGER = "neorc/external-trigger"
    """Starts runs, and nothing else."""


class Permission(StrEnum):
    """One operation on one kind of resource; every route needs one."""

    FLOWS_UPLOAD = "neorc/flows:upload"
    FLOWS_READ = "neorc/flows:read"
    RUNS_START = "neorc/runs:start"
    RUNS_READ = "neorc/runs:read"
    """Runs, their tasks, sub-runs and state, and a task by id."""
    RUNS_CANCEL = "neorc/runs:cancel"
    RUNS_SCHEDULE = "neorc/runs:schedule"
    """The scheduler's requests: publish tasks, start sub-runs, finish runs."""
    EVENTS_READ = "neorc/events:read"
    QUEUE_DEFINITIONS = "neorc/queue:definitions"
    QUEUE_RECEIVE = "neorc/queue:receive"
    TASKS_CLAIM = "neorc/tasks:claim"
    TASKS_HEARTBEAT = "neorc/tasks:heartbeat"
    TASKS_FINISH = "neorc/tasks:finish"
    QUEUE_PENDING = "neorc/queue:pending"
    """List a queue's pending tasks, taking no lease."""
    TASKS_COMPLETE = "neorc/tasks:complete"
    """End a pending task by its id, with no lease before."""
    QUEUES_READ = "neorc/queues:read"
    """The declared queues, and the tasks of any of them in any status."""
    QUEUES_DECLARE = "neorc/queues:declare"
    """Declare a new queue with its kind, or one again with the kind it has."""


WORKER_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.QUEUE_DEFINITIONS,
        Permission.QUEUE_RECEIVE,
        Permission.TASKS_CLAIM,
        Permission.TASKS_HEARTBEAT,
        Permission.TASKS_FINISH,
    }
)
"""What a worker may do, on the queue its token is bound to."""

RESPONDER_PERMISSIONS: frozenset[Permission] = frozenset(
    {Permission.QUEUE_PENDING, Permission.TASKS_COMPLETE}
)
"""What a responder may do, on the queue its token is bound to."""

QUEUE_PERMISSIONS: frozenset[Permission] = WORKER_PERMISSIONS | RESPONDER_PERMISSIONS
"""The permissions that apply to one queue: the one a token is bound to."""

QUEUE_ROLES: frozenset[Role] = frozenset({Role.WORKER, Role.RESPONDER})
"""The roles whose grants are bound to one queue."""

PEOPLE_ROLES: frozenset[Role] = frozenset({Role.USER})
"""The roles a person may hold. Every other role is a service token's."""

DEFAULT_ROLE = Role.USER
"""The role every person who may sign in gets."""

PERMISSIONS: Mapping[Role, frozenset[Permission]] = MappingProxyType(
    {
        Role.WORKER: WORKER_PERMISSIONS,
        Role.RESPONDER: RESPONDER_PERMISSIONS,
        Role.SCHEDULER: frozenset(
            {
                Permission.FLOWS_READ,
                Permission.RUNS_READ,
                Permission.RUNS_SCHEDULE,
                Permission.EVENTS_READ,
            }
        ),
        Role.CI: frozenset({Permission.FLOWS_UPLOAD, Permission.QUEUES_DECLARE}),
        Role.USER: frozenset(
            {
                Permission.FLOWS_READ,
                Permission.RUNS_START,
                Permission.RUNS_READ,
                Permission.RUNS_CANCEL,
                Permission.EVENTS_READ,
                Permission.QUEUES_READ,
                Permission.TASKS_COMPLETE,
            }
        ),
        Role.EXTERNAL_TRIGGER: frozenset({Permission.RUNS_START}),
    }
)
"""What each role may do; nothing else is allowed. Fixed: roles are not edited."""
