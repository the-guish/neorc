# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The SQLite adapters: one file, for one machine. They need no extra.

``SqliteStore`` keeps flows, runs, tasks and events in a file that survives
the process, for a manager embedded in an application. ``SqliteTaskNotifier``
is for when workers in other processes open the same file with managers of
their own; a manager that is alone on its file uses ``MemoryTaskNotifier``.
"""

from neorc.sqlite._notifier import SqliteTaskNotifier
from neorc.sqlite._schema import create_schema
from neorc.sqlite._store import SqliteStore

__all__ = [
    "SqliteStore",
    "SqliteTaskNotifier",
    "create_schema",
]
