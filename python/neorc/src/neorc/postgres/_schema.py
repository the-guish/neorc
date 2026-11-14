# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The tables the store needs, and how to create them.

The statements are idempotent, so running them against an up-to-date database
does nothing. This is a create-if-absent step, not a migration tool: changing
the shape of an existing table is out of scope until there is a released
version to migrate from.

The choices behind the tables are in docs/working-notes/decisions-from-past-plans.md:

- Values (run inputs and outputs, task params, fixed params and results) are
  ``text`` holding their compact JSON, not ``jsonb``: ``jsonb`` keeps numbers as
  ``numeric`` and would hand ``1e16`` back as an integer and ``-0.0`` as
  ``0.0``, where the memory store keeps them as written.
- A flow version's content is the canonical JSON text of the upload's
  structure, never the text as it was sent.
- No foreign keys between the flow tables: an insert would take a ``KEY
  SHARE`` lock on the run it references, outside the store's lock order. The
  store's operations keep the references whole.
- The ``CHECK`` constraints mirror ``TaskStatus``, ``RunStatus``,
  ``EventKind``, ``PrincipalKind`` and ``Role``; a test keeps them in step
  with the enums.
- Runs and tasks are ordered by ``position``, not by their timestamps:
  ``now()`` can give two rows the same time. The timestamps are for display.
  A task's position is an identity column, allocated at insert; a run's is
  allocated by the store under its event lock, so it commits in order and
  paging by it never skips a run.

A few columns added early on are also added to existing tables, when a check
of ``information_schema`` finds them missing; the check comes first because
``ALTER TABLE`` locks the table exclusively before it looks. Later changes,
such as the unique task address and the roles on credentials, are made to the
statements alone: neorc is installed nowhere yet, so there is no database to
keep. The responder role is one of those: a database created before it keeps
``CHECK`` constraints that refuse a responder token.
"""

from __future__ import annotations

from neorc.postgres._driver import connected, quote_identifier, schema_settings

FLOW_VERSIONS_TABLE = "neorc_flow_versions"
RUNS_TABLE = "neorc_runs"
FLOW_TASKS_TABLE = "neorc_flow_tasks"
EVENTS_TABLE = "neorc_events"
QUEUES_TABLE = "neorc_queues"
TOKENS_TABLE = "neorc_api_tokens"
SESSIONS_TABLE = "neorc_sessions"
PENDING_LOGINS_TABLE = "neorc_pending_logins"

CREDENTIAL_TABLES = (TOKENS_TABLE, SESSIONS_TABLE, PENDING_LOGINS_TABLE)
"""The tables of the credential store, which a manager with authentication needs."""

TABLES = (
    FLOW_VERSIONS_TABLE,
    RUNS_TABLE,
    FLOW_TASKS_TABLE,
    EVENTS_TABLE,
    QUEUES_TABLE,
    *CREDENTIAL_TABLES,
)
"""Every table ``create_schema`` creates."""

RETIRED_TABLES = ("neorc_tasks",)
"""Tables of earlier versions, no longer created; ``drop_schema`` removes them too."""

INDEXES = (
    f"{RUNS_TABLE}_root_idx",
    f"{RUNS_TABLE}_parent_idx",
    f"{RUNS_TABLE}_active_by_flow_idx",
    f"{RUNS_TABLE}_position_idx",
    f"{RUNS_TABLE}_flow_position_idx",
    f"{FLOW_TASKS_TABLE}_run_address_idx",
    f"{FLOW_TASKS_TABLE}_receive_idx",
    f"{FLOW_TASKS_TABLE}_queue_idx",
    f"{SESSIONS_TABLE}_expires_idx",
    f"{PENDING_LOGINS_TABLE}_expires_idx",
)
"""Every index ``create_schema`` creates, besides primary keys."""

CREATE_SCHEMA = f"""
-- One row per flow version. The version is three integers so the latest is
-- ORDER BY major, minor, patch, not a text sort.
CREATE TABLE IF NOT EXISTS {FLOW_VERSIONS_TABLE} (
    name             text NOT NULL,
    major            integer NOT NULL,
    minor            integer NOT NULL,
    patch            integer NOT NULL,
    content          text NOT NULL,
    PRIMARY KEY (name, major, minor, patch)
);

CREATE TABLE IF NOT EXISTS {RUNS_TABLE} (
    id               uuid PRIMARY KEY,
    flow             text NOT NULL,
    version          text NOT NULL,
    inputs           text NOT NULL,
    status           text NOT NULL
                     CHECK (status IN ('active', 'succeeded', 'failed',
                                       'cancelled')),
    root_id          uuid NOT NULL,
    parent_id        uuid,
    parent_address   text,
    output           text NOT NULL DEFAULT 'null',
    reason           text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    finished_at      timestamptz,
    position         bigint NOT NULL DEFAULT 0
);

-- position orders runs by start, for listing and paging. The store sets it as
-- max + 1 under its event lock, in the transaction that starts the run, so
-- positions commit in order and a page never skips a run that committed late;
-- an identity column would be allocated at insert, before the wait for that
-- lock. It is 0 only between the insert and the numbering, inside that one
-- transaction.
--
-- Only when the columns are missing: ALTER TABLE takes an exclusive lock on
-- the table before it looks, even with IF NOT EXISTS, and this runs at every
-- manager start. IF NOT EXISTS still, for two managers starting at once.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = '{RUNS_TABLE}'
                      AND column_name = 'created_at') THEN
        ALTER TABLE {RUNS_TABLE}
            ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now(),
            ADD COLUMN IF NOT EXISTS finished_at timestamptz,
            ADD COLUMN IF NOT EXISTS position bigint NOT NULL DEFAULT 0;
        UPDATE {RUNS_TABLE} AS r
           SET position = numbered.n
          FROM (SELECT id, row_number() OVER () AS n FROM {RUNS_TABLE}) AS numbered
         WHERE r.id = numbered.id AND r.position = 0;
    END IF;
END $$;

-- A tree is finished by its root: every run of a root, and a run's sub-runs
-- for its state.
CREATE INDEX IF NOT EXISTS {RUNS_TABLE}_root_idx ON {RUNS_TABLE} (root_id);
CREATE INDEX IF NOT EXISTS {RUNS_TABLE}_parent_idx ON {RUNS_TABLE} (parent_id);

-- An upload cancels the active trees running its flow.
CREATE INDEX IF NOT EXISTS {RUNS_TABLE}_active_by_flow_idx
    ON {RUNS_TABLE} (flow)
    WHERE status = 'active';

-- Listing runs newest first, all of them or one flow's, a page at a time.
CREATE INDEX IF NOT EXISTS {RUNS_TABLE}_position_idx ON {RUNS_TABLE} (position);
CREATE INDEX IF NOT EXISTS {RUNS_TABLE}_flow_position_idx
    ON {RUNS_TABLE} (flow, position);

-- position orders tasks by publication: their ids are random and carry no
-- order. An insert that hits ON CONFLICT still consumes a value, so
-- positions have gaps; only their order means anything.
CREATE TABLE IF NOT EXISTS {FLOW_TASKS_TABLE} (
    id               uuid PRIMARY KEY,
    run_id           uuid NOT NULL,
    address          text NOT NULL,
    queue            text NOT NULL,
    handler          text NOT NULL,
    params           text NOT NULL,
    fixed_params     text NOT NULL,
    status           text NOT NULL
                     CHECK (status IN ('pending', 'received', 'running',
                                       'succeeded', 'failed')),
    attempts         integer NOT NULL DEFAULT 0,
    lease_expires_at timestamptz,
    result           text NOT NULL DEFAULT 'null',
    error            text,
    position         bigint GENERATED ALWAYS AS IDENTITY,
    created_at       timestamptz NOT NULL DEFAULT now(),
    started_at       timestamptz,
    finished_at      timestamptz
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = '{FLOW_TASKS_TABLE}'
                      AND column_name = 'created_at') THEN
        ALTER TABLE {FLOW_TASKS_TABLE}
            ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now(),
            ADD COLUMN IF NOT EXISTS started_at timestamptz,
            ADD COLUMN IF NOT EXISTS finished_at timestamptz;
    END IF;
END $$;

-- One task per address in a run: publishing it again finds the first. Serves
-- listing a run's tasks too.
CREATE UNIQUE INDEX IF NOT EXISTS {FLOW_TASKS_TABLE}_run_address_idx
    ON {FLOW_TASKS_TABLE} (run_id, address);

-- The receive query's index: the oldest unfinished task of a queue. Partial, so
-- finished tasks cost nothing to skip over.
CREATE INDEX IF NOT EXISTS {FLOW_TASKS_TABLE}_receive_idx
    ON {FLOW_TASKS_TABLE} (queue, position)
    WHERE status IN ('pending', 'received', 'running');

-- Listing a queue's tasks in any status, the task published last first.
CREATE INDEX IF NOT EXISTS {FLOW_TASKS_TABLE}_queue_idx
    ON {FLOW_TASKS_TABLE} (queue, position);

-- Sequences are allocated by the store, under its event lock, as max + 1: an
-- identity column would let a later transaction commit a lower sequence first.
CREATE TABLE IF NOT EXISTS {EVENTS_TABLE} (
    sequence         bigint PRIMARY KEY,
    run_id           uuid NOT NULL,
    kind             text NOT NULL
                     CHECK (kind IN ('run_started', 'task_finished',
                                     'run_finished'))
);

-- The declared queues; kind mirrors QueueKind. Every queue has a row before a
-- flow names it, a token is bound to it or a task is handed out from it.
CREATE TABLE IF NOT EXISTS {QUEUES_TABLE} (
    name             text PRIMARY KEY,
    kind             text NOT NULL CHECK (kind IN ('worker', 'external')),
    created_at       timestamptz NOT NULL DEFAULT now()
);

-- Credentials. Secrets are never stored, only their SHA-256 in hex, which
-- rows are found by. Times come from now(), so expiry is decided by one clock.
-- role mirrors Role; a worker or responder token alone is bound to a queue.
CREATE TABLE IF NOT EXISTS {TOKENS_TABLE} (
    id               uuid PRIMARY KEY,
    name             text NOT NULL UNIQUE,
    secret_hash      text NOT NULL UNIQUE,
    role             text NOT NULL
                     CHECK (role IN ('worker', 'responder', 'scheduler', 'ci',
                                     'user', 'external-trigger')),
    queue            text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz,
    CONSTRAINT {TOKENS_TABLE}_queue_check
        CHECK ((role IN ('worker', 'responder')) = (queue IS NOT NULL))
);

-- Who signed in, as their provider said; kind mirrors PrincipalKind, role Role.
CREATE TABLE IF NOT EXISTS {SESSIONS_TABLE} (
    secret_hash      text PRIMARY KEY,
    kind             text NOT NULL CHECK (kind IN ('token', 'session')),
    subject          text NOT NULL,
    name             text NOT NULL,
    role             text NOT NULL
                     CHECK (role IN ('worker', 'responder', 'scheduler', 'ci',
                                     'user', 'external-trigger')),
    queue            text,
    provider         text,
    email            text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz NOT NULL,
    CONSTRAINT {SESSIONS_TABLE}_queue_check
        CHECK ((role IN ('worker', 'responder')) = (queue IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS {PENDING_LOGINS_TABLE} (
    state_hash       text PRIMARY KEY,
    provider         text NOT NULL,
    nonce            text NOT NULL,
    verifier         text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz NOT NULL
);

-- The sweep of expired sessions and sign-ins deletes by expiry; anyone may
-- start a sign-in, so it must not scan the table.
CREATE INDEX IF NOT EXISTS {SESSIONS_TABLE}_expires_idx
    ON {SESSIONS_TABLE} (expires_at);
CREATE INDEX IF NOT EXISTS {PENDING_LOGINS_TABLE}_expires_idx
    ON {PENDING_LOGINS_TABLE} (expires_at);
"""

DROP_SCHEMA = f"DROP TABLE IF EXISTS {', '.join(TABLES + RETIRED_TABLES)}"


async def create_schema(dsn: str, *, schema: str | None = None) -> None:
    """Create the neorc tables and indexes if they do not exist.

    With ``schema``, in that PostgreSQL schema, which is created first if it
    does not exist; otherwise in the first schema of the connection's
    ``search_path``, ``public`` by default.
    """
    async with connected(dsn, server_settings=schema_settings(schema)) as conn:
        if schema is not None:
            await conn.execute(
                f"CREATE SCHEMA IF NOT EXISTS {quote_identifier(schema)}"
            )
        await conn.execute(CREATE_SCHEMA)


async def missing_tables(
    dsn: str, tables: tuple[str, ...], *, schema: str | None = None
) -> list[str]:
    """Which of ``tables`` the database does not have, in their order."""
    async with connected(dsn, server_settings=schema_settings(schema)) as conn:
        rows = await conn.fetch(
            "SELECT t.name FROM unnest($1::text[]) WITH ORDINALITY AS t (name, n)"
            " WHERE to_regclass(t.name) IS NULL ORDER BY t.n",
            list(tables),
        )
        return [row[0] for row in rows]


class MissingTablesError(RuntimeError):
    """The database lacks tables the manager needs; the message names the fix."""


async def ensure_credential_tables(dsn: str, *, schema: str | None = None) -> None:
    """Raise ``MissingTablesError`` naming the fix if the credential tables are missing.

    A manager with authentication on would otherwise answer every request with
    a 500, which workers and schedulers take for an outage and retry forever.
    """
    await ensure_tables(dsn, CREDENTIAL_TABLES, schema=schema)


async def ensure_tables(
    dsn: str, tables: tuple[str, ...], *, schema: str | None = None
) -> None:
    """Raise ``MissingTablesError`` naming the fix if any of ``tables`` is missing."""
    missing = await missing_tables(dsn, tables, schema=schema)
    if missing:
        raise MissingTablesError(
            f"the database has no {', '.join(missing)}: start the manager once "
            "with `neorc manager start --create-schema` to create them"
        )


async def drop_schema(dsn: str, *, schema: str | None = None) -> None:
    """Remove the neorc tables, from ``schema`` if one is named. For tests and teardown.

    The PostgreSQL schema itself is left: it may hold more than neorc's tables.
    """
    async with connected(dsn, server_settings=schema_settings(schema)) as conn:
        await conn.execute(DROP_SCHEMA)
