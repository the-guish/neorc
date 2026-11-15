# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The file the SQLite store keeps, how it is opened and how its version is checked.

A new file gets its tables, neorc's application id and the schema version in
one transaction. A file that holds another application's tables, or another
version of neorc's, is refused before anything in it changes.

The choices that differ from the Postgres tables:

- Ids are UUIDs in their text form; SQLite has no UUID type.
- Times are integers, microseconds since the Unix epoch in UTC. They compare
  as numbers, and they come back as written, to the microsecond.
- Runs, tasks and events are ordered by an ``AUTOINCREMENT`` key. One
  connection writes, one transaction at a time, so the order of the keys is
  the order of the commits.
- The ``CHECK`` constraints are written from the enums, so they cannot drift.
"""

from __future__ import annotations

import os
import sqlite3

from neorc_core import EventKind, QueueKind, RunStatus, StarterKind, TaskStatus

SCHEMA_VERSION = 2
"""The version of the tables this neorc reads and writes, kept in ``user_version``."""

APPLICATION_ID = 0x6E656F72
"""``application_id`` of a neorc file: "neor", as the Postgres advisory locks use."""

BUSY_TIMEOUT_SECONDS = 5.0
"""How long a statement waits for a lock another connection holds, before it fails."""

FLOW_VERSIONS_TABLE = "neorc_flow_versions"
RUNS_TABLE = "neorc_runs"
FLOW_TASKS_TABLE = "neorc_flow_tasks"
EVENTS_TABLE = "neorc_events"
QUEUES_TABLE = "neorc_queues"


def _one_of(
    values: type[TaskStatus | RunStatus | EventKind | QueueKind | StarterKind],
) -> str:
    return ", ".join(f"'{member.value}'" for member in values)


CREATE_SCHEMA = f"""
CREATE TABLE {FLOW_VERSIONS_TABLE} (
    name             TEXT NOT NULL,
    major            INTEGER NOT NULL,
    minor            INTEGER NOT NULL,
    patch            INTEGER NOT NULL,
    content          TEXT NOT NULL,
    PRIMARY KEY (name, major, minor, patch)
);

CREATE TABLE {RUNS_TABLE} (
    position         INTEGER PRIMARY KEY AUTOINCREMENT,
    id               TEXT NOT NULL UNIQUE,
    flow             TEXT NOT NULL,
    version          TEXT NOT NULL,
    inputs           TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ({_one_of(RunStatus)})),
    root_id          TEXT NOT NULL,
    parent_id        TEXT,
    parent_address   TEXT,
    output           TEXT NOT NULL DEFAULT 'null',
    reason           TEXT,
    created_at       INTEGER NOT NULL,
    finished_at      INTEGER,
    starter_kind     TEXT CHECK (starter_kind IN ({_one_of(StarterKind)})),
    starter_id       TEXT,
    starter_name     TEXT,
    CHECK ((starter_kind IS NULL) = (starter_id IS NULL))
);
CREATE INDEX {RUNS_TABLE}_root_idx ON {RUNS_TABLE} (root_id);
CREATE INDEX {RUNS_TABLE}_parent_idx ON {RUNS_TABLE} (parent_id);
CREATE INDEX {RUNS_TABLE}_flow_idx ON {RUNS_TABLE} (flow, position);

CREATE TABLE {FLOW_TASKS_TABLE} (
    position         INTEGER PRIMARY KEY AUTOINCREMENT,
    id               TEXT NOT NULL UNIQUE,
    run_id           TEXT NOT NULL,
    address          TEXT NOT NULL,
    queue            TEXT NOT NULL,
    handler          TEXT NOT NULL,
    params           TEXT NOT NULL,
    fixed_params     TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ({_one_of(TaskStatus)})),
    attempts         INTEGER NOT NULL DEFAULT 0,
    lease_expires_at INTEGER,
    result           TEXT NOT NULL DEFAULT 'null',
    error            TEXT,
    created_at       INTEGER NOT NULL,
    started_at       INTEGER,
    finished_at      INTEGER,
    UNIQUE (run_id, address)
);
CREATE INDEX {FLOW_TASKS_TABLE}_queue_idx ON {FLOW_TASKS_TABLE} (queue, position);

CREATE TABLE {EVENTS_TABLE} (
    sequence         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT NOT NULL,
    kind             TEXT NOT NULL CHECK (kind IN ({_one_of(EventKind)}))
);

CREATE TABLE {QUEUES_TABLE} (
    name             TEXT PRIMARY KEY,
    kind             TEXT NOT NULL CHECK (kind IN ({_one_of(QueueKind)})),
    created_at       INTEGER NOT NULL
);

PRAGMA application_id = {APPLICATION_ID};
PRAGMA user_version = {SCHEMA_VERSION};
"""


class SchemaVersionError(RuntimeError):
    """The file is not a neorc database of the version this neorc reads."""


def connect(path: str | os.PathLike[str]) -> sqlite3.Connection:
    """Open the file at ``path``, creating it and its tables if it is new.

    The connection is in autocommit mode: the caller begins and ends each
    transaction. Raises ``SchemaVersionError`` for a file of another version
    or of another application, and ``sqlite3.Error`` for one SQLite cannot
    open.
    """
    conn = sqlite3.connect(
        path,
        timeout=BUSY_TIMEOUT_SECONDS,
        isolation_level=None,
        check_same_thread=False,
    )
    try:
        conn.row_factory = sqlite3.Row
        # The write-ahead log: a commit is one append, a crash mid-write leaves
        # the last commit whole, and a reader in another process does not block
        # the writer. A full sync on every commit: a finished task that was
        # answered for stays finished after a power cut.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        _check_or_create(conn, os.fspath(path))
    except BaseException:
        conn.close()
        raise
    return conn


def check_database(path: str | os.PathLike[str]) -> None:
    """Open the file at ``path`` as the store would, then close it.

    For a command to refuse a file before it starts serving.
    """
    connect(path).close()


def _check_or_create(conn: sqlite3.Connection, path: str) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        application_id = conn.execute("PRAGMA application_id").fetchone()[0]
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        objects = conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
        if application_id == 0 and version == 0 and objects == 0:
            for statement in _statements(CREATE_SCHEMA):
                conn.execute(statement)
        elif application_id != APPLICATION_ID:
            raise SchemaVersionError(f"{path} is not a neorc database")
        elif version != SCHEMA_VERSION:
            raise SchemaVersionError(
                f"{path} holds version {version} of neorc's tables, and this "
                f"neorc reads version {SCHEMA_VERSION}"
            )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _statements(script: str) -> list[str]:
    """The statements of ``script``, one by one.

    ``executescript`` commits first, and the tables are created inside the
    transaction that checked the file was empty.
    """
    return [s.strip() for s in script.split(";") if s.strip()]
