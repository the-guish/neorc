# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The SQLite store on a real file, held to the store contract.

Beyond the contract: what the file keeps across a restart, how it is set up
for a crash, and the files it refuses.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from neorc.sqlite import SCHEMA_VERSION, SchemaVersionError, SqliteStore
from neorc.sqlite._schema import APPLICATION_ID
from neorc_core import EventKind, QueueKind, Store, StoredFlow
from neorc_core._values import JsonValue
from neorc_core.flows import Address, Reference, Version, parse_flow
from neorc_core.testing.contracts import StoreContract


@pytest.fixture
async def sqlite_store(tmp_path: Path) -> AsyncIterator[SqliteStore]:
    async with SqliteStore(tmp_path / "neorc.db") as store:
        yield store


class TestSqliteStore(StoreContract):
    @pytest.fixture
    def store(self, sqlite_store: SqliteStore) -> Store:
        return sqlite_store


def _flow(name: str, version: str) -> StoredFlow:
    content: JsonValue = {
        "name": name,
        "version": version,
        "steps": {"work": {"handler": "tasks:work"}},
    }
    return StoredFlow(parse_flow(content), content)


async def test_the_file_keeps_everything_across_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "neorc.db"
    async with SqliteStore(path) as store:
        await store.declare_queue("default", QueueKind.WORKER)
        await store.store_flows([_flow("a", "1.0.0")])
        run = await store.start_run("a", Version(1, 0, 0), {"x": 1})
        task = await store.publish_task(
            run.id,
            Address("work"),
            queue="default",
            handler="tasks:work",
            params={"x": Reference.parse("inputs.x")},
            fixed_params={},
        )

    async with SqliteStore(path) as store:
        received = await store.receive_task("default", lease_seconds=30)
        second = await store.start_run("a", Version(1, 0, 0), {})
        runs = await store.list_runs()
        events = await store.events_after(0)

    assert received is not None and received.id == task.id
    assert received.params == {"x": Reference.parse("inputs.x")}
    assert [r.id for r in runs] == [second.id, run.id]
    assert runs[1].created_at == run.created_at
    assert [(e.sequence, e.kind) for e in events] == [
        (1, EventKind.RUN_STARTED),
        (2, EventKind.RUN_STARTED),
    ]


async def test_the_file_is_set_up_for_a_crash_and_holds_its_version(
    tmp_path: Path,
) -> None:
    path = tmp_path / "neorc.db"
    async with SqliteStore(path):
        pass

    with sqlite3.connect(path) as conn:
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        application = conn.execute("PRAGMA application_id").fetchone()[0]
    conn.close()

    assert (journal, version, application) == ("wal", SCHEMA_VERSION, APPLICATION_ID)


async def test_a_file_of_another_version_is_refused_and_left_as_it_is(
    tmp_path: Path,
) -> None:
    path = tmp_path / "neorc.db"
    async with SqliteStore(path) as store:
        await store.store_flows([_flow("a", "1.0.0")])
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.close()

    with pytest.raises(SchemaVersionError, match=rf"version {SCHEMA_VERSION + 1}"):
        await SqliteStore(path).open()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION + 1
        assert conn.execute("SELECT count(*) FROM neorc_flow_versions").fetchone()[0]
    conn.close()


async def test_a_file_of_another_application_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "other.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE notes (body TEXT)")
    conn.close()

    with pytest.raises(SchemaVersionError, match="not a neorc database"):
        await SqliteStore(path).open()

    with sqlite3.connect(path) as conn:
        tables = conn.execute("SELECT name FROM sqlite_master").fetchall()
    conn.close()
    assert tables == [("notes",)]
