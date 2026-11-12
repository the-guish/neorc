# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The SQLite store for flows, on a file, held to the store contract.

Beyond the contract, what a file adds to the memory store: what was stored is
there when the file is opened again, and several stores, in this process or in
others, share one file under its one write lock.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from enum import StrEnum
from pathlib import Path

import pytest

from neorc.sqlite import SqliteStore, create_schema
from neorc.sqlite._db import Database
from neorc.sqlite._schema import (
    EVENTS_TABLE,
    FLOW_TASKS_TABLE,
    INDEXES,
    RUNS_TABLE,
    TABLES,
)
from neorc_core import (
    EventKind,
    ManagerUnavailableError,
    NeorcError,
    Run,
    RunStatus,
    Store,
    StoredFlow,
    TaskStatus,
)
from neorc_core._values import JsonValue
from neorc_core.flows import Address, parse_flow
from neorc_core.testing.contracts import StoreContract

CONTENT: JsonValue = {
    "name": "f",
    "version": "1.0.0",
    "steps": {"work": {"handler": "tasks:work"}},
}


class TestSqliteStore(StoreContract):
    @pytest.fixture
    async def store(self, tmp_path: Path) -> AsyncIterator[Store]:
        async with SqliteStore(tmp_path / "neorc.db") as store:
            yield store


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "neorc.db"


async def _started(store: Store) -> Run:
    await store.store_flows([StoredFlow(parse_flow(CONTENT), CONTENT)])
    return await store.start_run("f", parse_flow(CONTENT).version, {"x": 1.5})


async def _publish(store: Store, run: Run, count: int) -> list[str]:
    tasks = [
        await store.publish_task(
            run.id,
            Address(f"t{index}"),
            queue="default",
            handler="tasks:work",
            params={},
            fixed_params={},
        )
        for index in range(count)
    ]
    return [str(task.id) for task in tasks]


async def test_what_was_stored_is_there_when_the_file_is_opened_again(
    path: Path,
) -> None:
    async with SqliteStore(path) as store:
        run = await _started(store)
        (task_id,) = await _publish(store, run, 1)
        received = await store.receive_task("default", lease_seconds=30)
        assert received is not None
        await store.claim_task(received.id)
        finished = await store.finish_task(received.id, result={"n": 1e16})

    async with SqliteStore(path) as store:
        assert await store.get_run(run.id) == run
        assert await store.get_task(finished.id) == finished
        assert str(finished.id) == task_id
        assert (await store.get_flow("f")).content == CONTENT
        events = await store.events_after(0)
        assert [event.kind for event in events] == [
            EventKind.RUN_STARTED,
            EventKind.TASK_FINISHED,
        ]
        # Sequences go on from where they were, as a scheduler reading after
        # the last one it saw needs.
        await store.succeed_run(run.id, None)
        assert await store.last_sequence() == 3


async def test_a_lease_held_when_the_file_was_closed_lapses_all_the_same(
    path: Path,
) -> None:
    """A worker that went with its process: the task goes to the next one."""
    async with SqliteStore(path) as store:
        run = await _started(store)
        await _publish(store, run, 1)
        first = await store.receive_task("default", lease_seconds=0.05)
        assert first is not None
        await store.claim_task(first.id)

    await asyncio.sleep(0.06)
    async with SqliteStore(path) as store:
        again = await store.receive_task("default", lease_seconds=30)

    assert again is not None and again.id == first.id
    assert again.attempts == 2


async def test_two_stores_on_one_file_see_each_others_writes(path: Path) -> None:
    async with SqliteStore(path) as one, SqliteStore(path) as other:
        run = await _started(one)
        ids = await _publish(one, run, 6)

        received = await asyncio.gather(
            *(store.receive_task("default") for store in (one, other) * 5)
        )

        leased = [str(task.id) for task in received if task is not None]
        assert sorted(leased) == sorted(ids)
        assert (await other.get_run(run.id)).status is RunStatus.ACTIVE
        await other.cancel_run_tree(run.id, "stopped")
        assert (await one.get_run(run.id)).status is RunStatus.CANCELLED


_RECEIVE_ALL = """
import asyncio, json, sys
from neorc.sqlite import SqliteStore

async def main() -> None:
    leased = []
    async with SqliteStore(sys.argv[1]) as store:
        while (task := await store.receive_task("default")) is not None:
            leased.append(str(task.id))
    print(json.dumps(leased))

asyncio.run(main())
"""


async def test_processes_on_one_file_never_share_a_task(path: Path) -> None:
    async with SqliteStore(path) as store:
        run = await _started(store)
        ids = await _publish(store, run, 60)

        def receive_all() -> list[str]:
            done = subprocess.run(
                [sys.executable, "-c", _RECEIVE_ALL, str(path)],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            leased: list[str] = json.loads(done.stdout)
            return leased

        per_process = await asyncio.gather(
            *(asyncio.to_thread(receive_all) for _ in range(3))
        )

        leased = [task_id for process in per_process for task_id in process]
        assert sorted(leased) == sorted(ids)
        assert await store.receive_task("default") is None


async def test_a_store_that_is_not_open_says_so(path: Path) -> None:
    store = SqliteStore(path)

    with pytest.raises(NeorcError, match="not open"):
        await store.last_sequence()

    await store.open()
    await store.open()  # safe to repeat
    await store.aclose()
    await store.aclose()
    with pytest.raises(NeorcError, match="not open"):
        await store.last_sequence()


async def test_a_sqlite_too_old_for_the_statements_is_refused_at_open(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 34, 1))

    with pytest.raises(NeorcError, match=r"too old: neorc needs 3\.35\.0"):
        await SqliteStore(path).open()


async def test_a_file_locked_past_the_wait_is_the_manager_unavailable(
    path: Path,
) -> None:
    """Another process writing for longer than the wait: said as a manager that
    did not answer is, which a worker and a scheduler try again after."""
    async with SqliteStore(path, busy_seconds=0.05) as store:
        run = await _started(store)
        writing = sqlite3.connect(path, isolation_level=None)
        try:
            writing.execute("BEGIN IMMEDIATE")

            with pytest.raises(ManagerUnavailableError, match="database is locked"):
                await _publish(store, run, 1)
            # Readers never wait for a writer.
            assert await store.get_run(run.id) == run

            writing.execute("ROLLBACK")
            assert len(await _publish(store, run, 1)) == 1
        finally:
            writing.close()


async def test_a_commit_sqlite_refuses_leaves_no_transaction_open(path: Path) -> None:
    """A transaction left open would have every later one refused."""
    async with Database(path) as database:
        await database.script(
            "PRAGMA foreign_keys = ON;"
            "CREATE TABLE parent (id integer PRIMARY KEY);"
            "CREATE TABLE child (parent integer REFERENCES parent"
            " DEFERRABLE INITIALLY DEFERRED);"
        )

        def orphan(connection: sqlite3.Connection) -> None:
            connection.execute("INSERT INTO child VALUES (1)")  # refused at COMMIT

        with pytest.raises(ManagerUnavailableError, match="FOREIGN KEY"):
            await database.write(orphan)

        def count(connection: sqlite3.Connection) -> int:
            connection.execute("INSERT INTO parent VALUES (1)")
            rows: int = connection.execute("SELECT count(*) FROM child").fetchone()[0]
            return rows

        assert await database.write(count) == 0


@pytest.mark.parametrize("target", [":memory:", ""], ids=["memory", "temporary"])
async def test_a_database_that_cannot_keep_a_write_ahead_log_is_refused(
    target: str,
) -> None:
    """SQLite answers the pragma with the journal it kept, and raises nothing."""
    store = SqliteStore(target)

    with pytest.raises(NeorcError, match="cannot keep a write-ahead log"):
        await store.open()

    with pytest.raises(NeorcError, match="not open"):
        await store.last_sequence()


_OPEN_EACH = """
import asyncio, sys, time
from neorc.sqlite import SqliteStore

async def main() -> None:
    begin, step = float(sys.argv[1]), float(sys.argv[2])
    for index, path in enumerate(sys.argv[3:]):
        await asyncio.sleep(max(0.0, begin + index * step - time.time()))
        async with SqliteStore(path) as store:
            await store.last_sequence()

asyncio.run(main())
"""


async def test_processes_opening_a_new_file_at_once_all_open_it(tmp_path: Path) -> None:
    """SQLite does not wait for the lock when the journal changes: the store does.

    Each process opens the same new files at the same moments, one file after
    another, so that some of them meet in the change of a file's journal.
    """
    paths = [str(tmp_path / f"new-{index}.db") for index in range(40)]
    begin = str(time.time() + 1.5)  # once every interpreter has started

    def open_each() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", _OPEN_EACH, begin, "0.02", *paths],
            capture_output=True,
            text=True,
            timeout=120,
        )

    opened = await asyncio.gather(*(asyncio.to_thread(open_each) for _ in range(6)))

    assert [done.stderr for done in opened] == [""] * 6


def _names(path: Path, kind: str) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = ? AND name LIKE 'neorc_%'",
            (kind,),
        ).fetchall()
    finally:
        connection.close()
    return {row[0] for row in rows}


def test_create_schema_is_idempotent_and_creates_everything(path: Path) -> None:
    create_schema(path)
    create_schema(path)

    assert _names(path, "table") == set(TABLES)
    assert set(INDEXES) <= _names(path, "index")


@pytest.mark.parametrize(
    ("table", "column", "enum"),
    [
        (FLOW_TASKS_TABLE, "status", TaskStatus),
        (RUNS_TABLE, "status", RunStatus),
        (EVENTS_TABLE, "kind", EventKind),
    ],
    ids=["tasks", "runs", "events"],
)
def test_check_constraints_are_in_step_with_the_enums(
    path: Path, table: str, column: str, enum: type[StrEnum]
) -> None:
    create_schema(path)
    connection = sqlite3.connect(path)
    try:
        (sql,) = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
    finally:
        connection.close()

    (check,) = re.findall(rf"CHECK \({column} IN \(([^)]*)\)\)", sql)
    literals = re.findall(r"'([^']*)'", check)
    assert sorted(literals) == sorted(member.value for member in enum)
