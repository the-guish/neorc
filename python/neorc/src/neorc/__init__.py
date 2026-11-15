# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""A next generation orchestration system.

Importing this package pulls in no optional dependency. The adapters live in
subpackages that match the extras that install them: ``neorc.http``,
``neorc.manager`` and ``neorc.postgres``. ``neorc.sqlite`` needs no extra.
"""

from importlib.metadata import PackageNotFoundError, version

from neorc_core import Scheduler, TaskId, TaskStatus, Worker

try:
    __version__ = version("neorc")
except PackageNotFoundError:  # a source checkout on sys.path, not installed
    __version__ = "0+unknown"

__all__ = [
    "Scheduler",
    "TaskId",
    "TaskStatus",
    "Worker",
    "__version__",
]
