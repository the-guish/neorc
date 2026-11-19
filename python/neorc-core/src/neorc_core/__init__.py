# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Core primitives for the neorc orchestration system."""

from importlib.metadata import PackageNotFoundError, version

from neorc_core._declarations import QueueDeclarations, check_token_queue
from neorc_core._errors import (
    AccessError,
    AuthenticationError,
    CrossSiteRequestError,
    FlowDefinitionError,
    FlowNotFoundError,
    FlowVersionError,
    InvalidValueError,
    ManagerUnavailableError,
    NeorcError,
    PayloadTooLargeError,
    PermissionDeniedError,
    QueueKindError,
    ResolutionError,
    RunNotFoundError,
    RunStateError,
    TaskNotFoundError,
    TaskStateError,
    UnsupportedMediaTypeError,
)
from neorc_core._manager import Manager
from neorc_core._queues import Queue, QueueKind
from neorc_core._roles import (
    DEFAULT_ROLE,
    PEOPLE_ROLES,
    PERMISSIONS,
    QUEUE_PERMISSIONS,
    QUEUE_RESOURCE,
    QUEUE_ROLES,
    Permission,
    Role,
)
from neorc_core._runs import (
    Event,
    EventKind,
    QueueTask,
    Run,
    RunId,
    RunStarter,
    RunStatus,
    StarterKind,
    StoredFlow,
    Task,
    TaskDelivery,
)
from neorc_core._scheduler import Scheduler
from neorc_core._task import (
    LEASED_STATUSES,
    TERMINAL_STATUSES,
    TaskId,
    TaskStatus,
    ensure_completable,
    ensure_transition,
)
from neorc_core._worker import HandlerError, Worker
from neorc_core.ports import (
    ManagerClient,
    QueueClient,
    Store,
    Subscription,
    TaskNotifier,
    TokenGrant,
    TokenGrants,
)

try:
    __version__ = version("neorc-core")
except PackageNotFoundError:  # a source checkout on sys.path, not installed
    __version__ = "0+unknown"

__all__ = [
    "DEFAULT_ROLE",
    "LEASED_STATUSES",
    "PEOPLE_ROLES",
    "PERMISSIONS",
    "QUEUE_PERMISSIONS",
    "QUEUE_RESOURCE",
    "QUEUE_ROLES",
    "TERMINAL_STATUSES",
    "AccessError",
    "AuthenticationError",
    "CrossSiteRequestError",
    "Event",
    "EventKind",
    "FlowDefinitionError",
    "FlowNotFoundError",
    "FlowVersionError",
    "HandlerError",
    "InvalidValueError",
    "Manager",
    "ManagerClient",
    "ManagerUnavailableError",
    "NeorcError",
    "PayloadTooLargeError",
    "Permission",
    "PermissionDeniedError",
    "Queue",
    "QueueClient",
    "QueueDeclarations",
    "QueueKind",
    "QueueKindError",
    "QueueTask",
    "ResolutionError",
    "Role",
    "Run",
    "RunId",
    "RunNotFoundError",
    "RunStarter",
    "RunStateError",
    "RunStatus",
    "Scheduler",
    "StarterKind",
    "Store",
    "StoredFlow",
    "Subscription",
    "Task",
    "TaskDelivery",
    "TaskId",
    "TaskNotFoundError",
    "TaskNotifier",
    "TaskStateError",
    "TaskStatus",
    "TokenGrant",
    "TokenGrants",
    "UnsupportedMediaTypeError",
    "Worker",
    "__version__",
    "check_token_queue",
    "ensure_completable",
    "ensure_transition",
]
