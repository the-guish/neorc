# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The tables the SQLite store needs, and how to create them.

They follow the Postgres ones (``neorc.postgres._schema``), with SQLite's types:

- ids are ``text``, a UUID in its usual form;
- times are ``integer``, microseconds since the epoch in UTC, set by the store
  from this process's clock: every process on the file is on the same machine;
- values are ``text`` holding compact JSON, as in Postgres;
- ``position`` and ``sequence`` are the row id, allocated under the write lock,
  so both commit in order.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

FLOW_VERSIONS_TABLE = "neorc_flow_versions"
RUNS_TABLE = "neorc_runs"
FLOW_TASKS_TABLE = "neorc_flow_tasks"
EVENTS_TABLE = "neorc_events"

TABLES = (FLOW_VERSIONS_TABLE, RUNS_TABLE, FLOW_TASKS_TABLE, EVENTS_TABLE)
"""Every table ``create_schema`` creates."""

INDEXES = (
    f"{RUNS_TABLE}_root_idx",
    f"{RUNS_TABLE}_parent_idx",
    f"{RUNS_TABLE}_active_by_flow_idx",
    f"{RUNS_TABLE}_flow_position_idx",
    f"{FLOW_TASKS_TABLE}_run_address_idx",
    f"{FLOW_TASKS_TABLE}_receive_idx",
)
"""Every index ``create_schema`` creates, besides primary keys."""

CREATE_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {FLOW_VERSIONS_TABLE} (
    name             text NOT NULL,
    major            integer NOT NULL,
    minor            integer NOT NULL,
    patch            integer NOT NULL,
    content          text NOT NULL,
    PRIMARY KEY (name, major, minor, patch)
);

CREATE TABLE IF NOT EXISTS {RUNS_TABLE} (
    position         integer PRIMARY KEY AUTOINCREMENT,
    id               text NOT NULL UNIQUE,
    flow             text NOT NULL,
    version          text NOT NULL,
    inputs           text NOT NULL,
    status           text NOT NULL
                     CHECK (status IN ('active', 'succeeded', 'failed',
                                       'cancelled')),
    root_id          text NOT NULL,
    parent_id        text,
    parent_address   text,
    output           text NOT NULL DEFAULT 'null',
    reason           text,
    created_at       integer NOT NULL,
    finished_at      integer
);

CREATE INDEX IF NOT EXISTS {RUNS_TABLE}_root_idx ON {RUNS_TABLE} (root_id);
CREATE INDEX IF NOT EXISTS {RUNS_TABLE}_parent_idx ON {RUNS_TABLE} (parent_id);
CREATE INDEX IF NOT EXISTS {RUNS_TABLE}_active_by_flow_idx
    ON {RUNS_TABLE} (flow)
    WHERE status = 'active';
CREATE INDEX IF NOT EXISTS {RUNS_TABLE}_flow_position_idx
    ON {RUNS_TABLE} (flow, position);

CREATE TABLE IF NOT EXISTS {FLOW_TASKS_TABLE} (
    position         integer PRIMARY KEY AUTOINCREMENT,
    id               text NOT NULL UNIQUE,
    run_id           text NOT NULL,
    address          text NOT NULL,
    queue            text NOT NULL,
    handler          text NOT NULL,
    params           text NOT NULL,
    fixed_params     text NOT NULL,
    status           text NOT NULL
                     CHECK (status IN ('pending', 'received', 'running',
                                       'succeeded', 'failed')),
    attempts         integer NOT NULL DEFAULT 0,
    lease_expires_at integer,
    result           text NOT NULL DEFAULT 'null',
    error            text,
    created_at       integer NOT NULL,
    started_at       integer,
    finished_at      integer
);

CREATE UNIQUE INDEX IF NOT EXISTS {FLOW_TASKS_TABLE}_run_address_idx
    ON {FLOW_TASKS_TABLE} (run_id, address);
CREATE INDEX IF NOT EXISTS {FLOW_TASKS_TABLE}_receive_idx
    ON {FLOW_TASKS_TABLE} (queue, position)
    WHERE status IN ('pending', 'received', 'running');

CREATE TABLE IF NOT EXISTS {EVENTS_TABLE} (
    sequence         integer PRIMARY KEY AUTOINCREMENT,
    run_id           text NOT NULL,
    kind             text NOT NULL
                     CHECK (kind IN ('run_started', 'task_finished',
                                     'run_finished'))
);
"""


CREATE_SCHEMA_SCRIPT = f"BEGIN IMMEDIATE;\n{CREATE_SCHEMA}\nCOMMIT;"
"""The statements as one transaction, so two processes opening a new file at
once do not meet half a schema. ``executescript`` begins none of its own."""


def create_schema(path: Path | str) -> None:
    """Create the neorc tables and indexes in the file at ``path``, if absent.

    ``SqliteStore.open`` does this already; this is for a file prepared ahead.
    """
    connection = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    try:
        connection.executescript(CREATE_SCHEMA_SCRIPT)
    finally:
        connection.close()
