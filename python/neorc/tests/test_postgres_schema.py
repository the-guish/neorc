# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The flow tables, against a real server.

What the store will build on: the tables and indexes are there after
``create_schema``, twice over; the ``CHECK`` constraints say what the enums
say; and a row written from a dataclass reads back as the same dataclass, with
numbers as JSON wrote them.
"""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import StrEnum

import pytest

from neorc.postgres import (
    PostgresCredentialStore,
    PostgresStore,
    create_schema,
    drop_schema,
)
from neorc.postgres._driver import Connection, UniqueViolationError, connected
from neorc.postgres._rows import (
    EVENT_COLUMNS,
    FLOW_COLUMNS,
    RUN_COLUMNS,
    TASK_COLUMNS,
    event_from_row,
    event_to_row,
    flow_from_row,
    flow_to_row,
    placeholders,
    run_from_row,
    run_to_row,
    task_from_row,
    task_to_row,
    values,
)
from neorc.postgres._schema import (
    CREDENTIAL_TABLES,
    EVENTS_TABLE,
    FLOW_TASKS_TABLE,
    FLOW_VERSIONS_TABLE,
    INDEXES,
    QUEUES_TABLE,
    RETIRED_TABLES,
    RUNS_TABLE,
    SESSIONS_TABLE,
    TABLES,
    TOKENS_TABLE,
    MissingTablesError,
    ensure_credential_tables,
    missing_tables,
)
from neorc_core import (
    Event,
    EventKind,
    PrincipalKind,
    QueueKind,
    Role,
    Run,
    RunStatus,
    StoredFlow,
    Task,
    TaskStatus,
)
from neorc_core._runs import canonical_content
from neorc_core._values import JsonValue
from neorc_core.flows import Address, Reference, Version, parse_flow

pytestmark = pytest.mark.postgres


@pytest.fixture
async def conn(pg_schema: str) -> AsyncIterator[Connection]:
    async with connected(pg_schema) as connection:
        yield connection


async def _names(conn: Connection, query: str, *args: object) -> set[str]:
    return {row["name"] for row in await conn.fetch(query, *args)}


_TABLES = "SELECT tablename AS name FROM pg_tables WHERE tablename LIKE 'neorc_%'"
_INDEXES = "SELECT indexname AS name FROM pg_indexes WHERE tablename LIKE 'neorc_%'"


async def test_create_schema_is_idempotent_and_creates_everything(
    database_url: str,
) -> None:
    await drop_schema(database_url)
    # A table of an earlier version, as a reused database may hold.
    async with connected(database_url) as conn:
        await conn.execute("CREATE TABLE neorc_tasks (id uuid PRIMARY KEY)")
    await create_schema(database_url)
    await create_schema(database_url)

    async with connected(database_url) as conn:
        assert await _names(conn, _TABLES) == set(TABLES) | set(RETIRED_TABLES)
        assert set(INDEXES) <= await _names(conn, _INDEXES)

    await drop_schema(database_url)
    async with connected(database_url) as conn:
        assert await _names(conn, _TABLES) == set()


_COLUMNS = """
SELECT column_name AS name FROM information_schema.columns
 WHERE table_schema = current_schema() AND table_name = $1
"""

_OLD_RUNS = f"""
CREATE TABLE {RUNS_TABLE} (
    id uuid PRIMARY KEY, flow text NOT NULL, version text NOT NULL,
    inputs text NOT NULL, status text NOT NULL, root_id uuid NOT NULL,
    parent_id uuid, parent_address text, output text NOT NULL DEFAULT 'null',
    reason text
)
"""

_OLD_TASKS = f"""
CREATE TABLE {FLOW_TASKS_TABLE} (
    id uuid PRIMARY KEY, run_id uuid NOT NULL, address text NOT NULL,
    queue text NOT NULL, handler text NOT NULL, params text NOT NULL,
    fixed_params text NOT NULL, status text NOT NULL,
    attempts integer NOT NULL DEFAULT 0, lease_expires_at timestamptz,
    result text NOT NULL DEFAULT 'null', error text,
    position bigint GENERATED ALWAYS AS IDENTITY
)
"""


async def test_create_schema_adds_the_columns_tables_from_before_lack(
    database_url: str,
) -> None:
    """Tables from a checkout before the timestamps get them, with rows kept."""
    await drop_schema(database_url)
    try:
        async with connected(database_url) as conn:
            await conn.execute(_OLD_RUNS)
            await conn.execute(_OLD_TASKS)
            run_id = uuid.uuid4()
            await conn.execute(
                f"INSERT INTO {RUNS_TABLE} (id, flow, version, inputs, status, root_id)"
                " VALUES ($1, 'a', '1.0.0', '{}', 'active', $1)",
                run_id,
            )

            await create_schema(database_url)

            runs = await _names(conn, _COLUMNS, RUNS_TABLE)
            tasks = await _names(conn, _COLUMNS, FLOW_TASKS_TABLE)
            (row,) = await conn.fetch(
                f"SELECT {RUN_COLUMNS}, position FROM {RUNS_TABLE}"
            )

        assert {"created_at", "finished_at", "position"} <= runs
        assert {"created_at", "started_at", "finished_at"} <= tasks
        assert run_from_row(row).id == run_id
        assert row["created_at"] is not None and row["position"] == 1  # backfilled
    finally:  # the tables lack their constraints: leave nothing for the next test
        await drop_schema(database_url)


_CONSTRAINT = """
SELECT pg_get_constraintdef(c.oid) AS definition
  FROM pg_constraint AS c
  JOIN pg_class AS t ON t.oid = c.conrelid
 WHERE c.contype = 'c' AND t.relname = $1
   AND pg_get_constraintdef(c.oid) LIKE $2
"""


@pytest.mark.parametrize(
    ("table", "column", "enum"),
    [
        (FLOW_TASKS_TABLE, "status", TaskStatus),
        (RUNS_TABLE, "status", RunStatus),
        (EVENTS_TABLE, "kind", EventKind),
        (SESSIONS_TABLE, "kind", PrincipalKind),
        (SESSIONS_TABLE, "role", Role),
        (TOKENS_TABLE, "role", Role),
        (QUEUES_TABLE, "kind", QueueKind),
    ],
    ids=[
        "tasks",
        "runs",
        "events",
        "sessions",
        "session-roles",
        "token-roles",
        "queue-kinds",
    ],
)
async def test_check_constraints_are_in_step_with_the_enums(
    conn: Connection,
    table: str,
    column: str,
    enum: type[StrEnum],
) -> None:
    (row,) = await conn.fetch(_CONSTRAINT, table, f"CHECK (({column} = ANY%")

    literals = re.findall(r"'([^']*)'", row["definition"])
    assert sorted(literals) == sorted(member.value for member in enum)


def _flow(content: JsonValue) -> StoredFlow:
    return StoredFlow(parse_flow(content), content)


async def test_a_flow_version_is_stored_as_canonical_json(
    conn: Connection,
) -> None:
    content: JsonValue = {
        "version": "1.2.3",
        "steps": {"work": {"handler": "tasks:work", "fixed_params": {"n": 1.0}}},
        "name": "a",
    }
    flow = _flow(content)

    await conn.execute(
        f"INSERT INTO {FLOW_VERSIONS_TABLE} ({FLOW_COLUMNS}) VALUES "
        f"({placeholders(FLOW_COLUMNS)})",
        *values(flow_to_row(flow), FLOW_COLUMNS),
    )
    (row,) = await conn.fetch(f"SELECT {FLOW_COLUMNS} FROM {FLOW_VERSIONS_TABLE}")

    assert row["content"] == canonical_content(content)
    assert row["content"].startswith('{"name":"a","steps":')
    assert (row["major"], row["minor"], row["patch"]) == (1, 2, 3)
    read = flow_from_row(row)
    assert read == flow
    assert read.version == Version(1, 2, 3)


WHEN = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)
NUMBERS: list[JsonValue] = [1e16, -0.0, 10**20, 1.0, 1]


def _same_numbers(read: JsonValue) -> None:
    """Each number came back as JSON wrote it: type, sign and all."""
    assert isinstance(read, list)
    big, negative_zero, huge, whole, integer = read
    assert isinstance(big, float) and big == 1e16
    assert isinstance(negative_zero, float) and math.copysign(1, negative_zero) < 0
    assert isinstance(huge, int) and huge == 10**20
    assert isinstance(whole, float)
    assert isinstance(integer, int) and not isinstance(integer, bool)


async def _insert_run(conn: Connection, run: Run) -> Run:
    await conn.execute(
        f"INSERT INTO {RUNS_TABLE} ({RUN_COLUMNS}) VALUES "
        f"({placeholders(RUN_COLUMNS)})",
        *values(run_to_row(run), RUN_COLUMNS),
    )
    (row,) = await conn.fetch(
        f"SELECT {RUN_COLUMNS} FROM {RUNS_TABLE} WHERE id = $1", run.id
    )
    return run_from_row(row)


async def test_runs_round_trip(conn: Connection) -> None:
    root_id = uuid.uuid4()
    root = Run(
        id=root_id,
        flow="a",
        version=Version(1, 0, 0),
        inputs={"numbers": NUMBERS, "when": {"$datetime": WHEN.isoformat()}},
        status=RunStatus.ACTIVE,
        root_id=root_id,
        created_at=WHEN,
    )
    sub_run = Run(
        id=uuid.uuid4(),
        flow="b",
        version=Version(2, 10, 0),
        inputs={},
        status=RunStatus.FAILED,
        root_id=root_id,
        parent_id=root_id,
        parent_address=Address("call", (("rounds", 2), ("picking", 3))),
        output={"words": ["red"]},
        reason="a task failed",
        created_at=WHEN,
        finished_at=WHEN,
    )

    read_root = await _insert_run(conn, root)
    read_sub_run = await _insert_run(conn, sub_run)

    assert read_root == root
    assert read_sub_run == sub_run
    assert isinstance(read_root.inputs, dict)
    _same_numbers(read_root.inputs["numbers"])


async def test_tasks_round_trip_and_are_ordered_by_publication(
    conn: Connection,
) -> None:
    run_id = uuid.uuid4()
    pending = Task(
        id=uuid.uuid4(),
        run_id=run_id,
        address=Address("work", (("rounds", 1),)),
        queue="default",
        handler="tasks:work",
        params={
            "x": Reference.parse("inputs.x"),
            "n": Reference.parse("neorc.attempts"),
        },
        fixed_params={"k": {"$datetime": WHEN.isoformat()}, "numbers": NUMBERS},
        created_at=WHEN,
    )
    finished = Task(
        id=uuid.uuid4(),
        run_id=run_id,
        address=Address("other"),
        queue="voice",
        handler="tasks:say",
        status=TaskStatus.SUCCEEDED,
        attempts=2,
        lease_expires_at=WHEN,
        result=NUMBERS,
        error=None,
        created_at=WHEN,
        started_at=WHEN,
        finished_at=WHEN,
    )
    for task in (pending, finished):
        await conn.execute(
            f"INSERT INTO {FLOW_TASKS_TABLE} ({TASK_COLUMNS}) VALUES "
            f"({placeholders(TASK_COLUMNS)})",
            *values(task_to_row(task), TASK_COLUMNS),
        )

    rows = await conn.fetch(
        f"SELECT {TASK_COLUMNS} FROM {FLOW_TASKS_TABLE} ORDER BY position"
    )

    read_pending, read_finished = (task_from_row(row) for row in rows)
    assert read_pending == pending
    assert read_finished == finished
    assert isinstance(read_pending.fixed_params["numbers"], list)
    _same_numbers(read_pending.fixed_params["numbers"])
    _same_numbers(read_finished.result)


async def test_events_round_trip(conn: Connection) -> None:
    event = Event(7, uuid.uuid4(), EventKind.TASK_FINISHED)
    insert = (
        f"INSERT INTO {EVENTS_TABLE} ({EVENT_COLUMNS}) "
        f"VALUES ({placeholders(EVENT_COLUMNS)})"
    )

    row_values = values(event_to_row(event), EVENT_COLUMNS)

    await conn.execute(insert, *row_values)
    (row,) = await conn.fetch(f"SELECT {EVENT_COLUMNS} FROM {EVENTS_TABLE}")

    assert event_from_row(row) == event
    with pytest.raises(UniqueViolationError):
        await conn.execute(insert, *row_values)


_SCHEMA_TABLES = "SELECT tablename AS name FROM pg_tables WHERE schemaname = $1"


async def test_a_schema_of_its_own_holds_every_table_and_is_used(
    database_url: str,
) -> None:
    """With ``schema``, the tables are made in it, and the stores work in it."""
    schema = f"neorc test {uuid.uuid4().hex[:8]}"  # quoted, as any name may need
    try:
        assert await missing_tables(
            database_url, CREDENTIAL_TABLES, schema=schema
        ) == list(CREDENTIAL_TABLES)
        with pytest.raises(MissingTablesError):
            await ensure_credential_tables(database_url, schema=schema)

        await create_schema(database_url, schema=schema)
        await create_schema(database_url, schema=schema)
        await ensure_credential_tables(database_url, schema=schema)

        async with connected(database_url) as conn:
            assert await _names(conn, _SCHEMA_TABLES, schema) == set(TABLES)
        async with PostgresStore(database_url, schema=schema) as store:
            content: JsonValue = {
                "name": "a",
                "version": "1.0.0",
                "steps": {"work": {"handler": "tasks:work"}},
            }
            await store.store_flows([_flow(content)])
            assert [f.name for f in await store.latest_flows()] == ["a"]
        async with PostgresCredentialStore(database_url, schema=schema) as store:
            assert await store.tokens() == []
        async with connected(database_url) as conn:
            count = await conn.fetchval(
                f'SELECT count(*) FROM "{schema}".{FLOW_VERSIONS_TABLE}'
            )
        assert count == 1

        await drop_schema(database_url, schema=schema)
        async with connected(database_url) as conn:
            assert await _names(conn, _SCHEMA_TABLES, schema) == set()
    finally:
        async with connected(database_url) as conn:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
