# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The SQLite store, on the standard library's ``sqlite3``. It needs no extra.

A manager on SQLite runs in one process, which alone opens the file, and tells
its waiters about new work with ``neorc_core.local.MemoryTaskNotifier``.
"""

from neorc.sqlite._schema import SCHEMA_VERSION, SchemaVersionError, check_database
from neorc.sqlite._store import SqliteStore

__all__ = [
    "SCHEMA_VERSION",
    "SchemaVersionError",
    "SqliteStore",
    "check_database",
]
