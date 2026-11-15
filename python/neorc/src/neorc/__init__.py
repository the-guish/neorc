# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""A next generation orchestration system.

Importing this package pulls in no optional dependency. The adapters live in
subpackages that match the extras that install them: ``neorc.http``,
``neorc.manager`` and ``neorc.postgres``. ``neorc.sqlite`` needs no extra.
``new_manager_webapp``, the manager as a oneberth component, is imported from
``neorc.manager`` when it is first asked for. ``NEORC_ROLES`` is neorc's role
table as oneberth registers it, and ``registry()`` holds oneberth's roles and
neorc's: a host reads both without building a manager.
"""

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

from neorc._roles import NEORC_ROLES, registry
from neorc_core import Scheduler, TaskId, TaskStatus, Worker

try:
    __version__ = version("neorc")
except PackageNotFoundError:  # a source checkout on sys.path, not installed
    __version__ = "0+unknown"

if TYPE_CHECKING:
    from neorc.manager import new_manager_webapp


def __getattr__(name: str) -> Any:
    if name == "new_manager_webapp":
        from neorc.manager import new_manager_webapp

        return new_manager_webapp
    raise AttributeError(f"module 'neorc' has no attribute {name!r}")


__all__ = [
    "NEORC_ROLES",
    "Scheduler",
    "TaskId",
    "TaskStatus",
    "Worker",
    "__version__",
    "new_manager_webapp",
    "registry",
]
