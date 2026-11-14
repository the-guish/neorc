# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The Postgres store for flows, against a real server, held to the store contract.

Beyond the contract: pairs of transactions forced to interleave, one played by
hand on a connection of its own and held open while the store's operation
waits on the lock it holds. What each pair must come to is what one order or
the other of the two operations would have left.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any, TypeVar

import pytest

from neorc.postgres import PostgresStore
from neorc.postgres._driver import Connection
from neorc.postgres._schema import (
    EVENTS_TABLE,
    FLOW_TASKS_TABLE,
    FLOW_VERSIONS_TABLE,
    RUNS_TABLE,
)
from neorc.postgres._store import EVENT_LOCK, UPLOAD_LOCK
from neorc_core import (
    EventKind,
    FlowVersionError,
    QueueKind,
    Run,
    RunId,
    RunNotFoundError,
    RunStateError,
    RunStatus,
    Store,
    StoredFlow,
    Task,
    TaskStatus,
)
from neorc_core._runs import canonical_content, sub_run_id_for
from neorc_core._values import JsonValue
from neorc_core.flows import Address, Version, parse_flow
from neorc_core.testing.contracts import StoreContract

pytestmark = pytest.mark.postgres


class TestPostgresStore(StoreContract):
    @pytest.fixture
    def store(self, pg_store: PostgresStore) -> Store:
        return pg_store


async def test_transactions_run_in_read_committed_whatever_the_default(
    pg_schema: str,
) -> None:
    """The lock order is reasoned out for READ COMMITTED, so the store pins it."""
    separator = "&" if "?" in pg_schema else "?"
    options = "options=-c%20default_transaction_isolation%3Dserializable"
    serializable = f"{pg_schema}{separator}{options}"

    async with (
        PostgresStore(serializable) as store,
        store.pool.acquire() as conn,
    ):
        # The connection string's setting arrives: it is the one overridden.
        assert await conn.fetchval("SHOW default_transaction_isolation") == (
            "read committed"
        )
        async with conn.transaction():
            row = await conn.fetchrow("SHOW transaction_isolation")

    assert row is not None and row["transaction_isolation"] == "read committed"


def _flow(name: str, version: str) -> StoredFlow:
    content: JsonValue = {
        "name": name,
        "version": version,
        "steps": {"work": {"handler": "tasks:work"}},
    }
    return StoredFlow(parse_flow(content), content)


async def test_an_upload_spares_a_tree_whose_run_of_the_flow_just_succeeded(
    pg_store: PostgresStore,
) -> None:
    """Two transactions interleaved: a sub-run succeeds as its flow is uploaded.

    The upload reads the trees to cancel, then waits for the root lock the
    succeeding transaction holds. Once it has the lock, nothing in the tree runs
    the flow any more, so the tree must stay active, as one order or the other
    of the two operations would leave it.
    """
    store = pg_store
    await store.store_flows([_flow("a", "1.0.0"), _flow("b", "1.0.0")])
    root = await store.start_run("a", Version(1, 0, 0), {})
    sub_run = await store.start_run(
        "b", Version(1, 0, 0), {}, parent_id=root.id, parent_address=Address("call")
    )

    async with store.pool.acquire() as succeeding, succeeding.transaction():
        # succeed_run(sub_run), by hand, stopped short of its commit.
        await succeeding.execute(
            f"SELECT id FROM {RUNS_TABLE} WHERE id = $1 FOR UPDATE", root.id
        )
        await succeeding.execute(
            f"UPDATE {RUNS_TABLE} SET status = 'succeeded' WHERE id = $1",
            sub_run.id,
        )
        uploading = asyncio.create_task(store.store_flows([_flow("b", "2.0.0")]))
        await asyncio.sleep(0.2)
        assert not uploading.done()  # waiting on the root lock

    assert await uploading == [True]
    assert (await store.get_run(root.id)).status is RunStatus.ACTIVE
    assert (await store.get_run(sub_run.id)).status is RunStatus.SUCCEEDED


_T = TypeVar("_T")


@asynccontextmanager
async def held(store: PostgresStore) -> AsyncIterator[Connection]:
    """A transaction played by hand, open until the block ends, then committed."""
    async with store.pool.acquire() as conn, conn.transaction():
        yield conn


async def blocked(coroutine: Coroutine[Any, Any, _T]) -> asyncio.Task[_T]:
    """Start an operation and check it is waiting on a lock held elsewhere."""
    task = asyncio.create_task(coroutine)
    await asyncio.sleep(0.2)
    assert not task.done()
    return task


async def _lock_root(conn: Connection, root_id: RunId) -> None:
    await conn.execute(f"SELECT id FROM {RUNS_TABLE} WHERE id = $1 FOR UPDATE", root_id)


async def _cancel_by_hand(conn: Connection, run: Run) -> None:
    """cancel_run_tree, played by hand, stopped short of its commit."""
    await _lock_root(conn, run.root_id)
    await conn.execute(
        f"UPDATE {RUNS_TABLE} SET status = 'cancelled', reason = 'by hand'"
        " WHERE root_id = $1 AND status = 'active'",
        run.root_id,
    )


async def _append_event_by_hand(
    conn: Connection, run_id: RunId, kind: EventKind
) -> None:
    await conn.execute("SELECT pg_advisory_xact_lock($1::int, $2::int)", *EVENT_LOCK)
    await conn.execute(
        f"INSERT INTO {EVENTS_TABLE} (sequence, run_id, kind)"
        f" SELECT COALESCE(MAX(sequence), 0) + 1, $1, $2 FROM {EVENTS_TABLE}",
        run_id,
        kind.value,
    )


async def _published(store: PostgresStore, run: Run, name: str) -> Task:
    await store.declare_queue("default", QueueKind.WORKER)
    return await store.publish_task(
        run.id,
        Address(name),
        queue="default",
        handler="tasks:work",
        params={},
        fixed_params={},
    )


@pytest.fixture
async def run(pg_store: PostgresStore) -> Run:
    await pg_store.store_flows([_flow("a", "1.0.0"), _flow("b", "1.0.0")])
    return await pg_store.start_run("a", Version(1, 0, 0), {})


async def test_a_receive_skips_the_task_another_receive_holds(
    pg_store: PostgresStore, run: Run
) -> None:
    store = pg_store
    first = await _published(store, run, "one")
    second = await _published(store, run, "two")

    async with held(store) as receiving:
        await receiving.execute(
            f"SELECT id FROM {FLOW_TASKS_TABLE} WHERE id = $1 FOR UPDATE", first.id
        )
        meanwhile = await store.receive_task("default", lease_seconds=30)

    afterwards = await store.receive_task("default", lease_seconds=30)
    assert meanwhile is not None and meanwhile.id == second.id
    assert afterwards is not None and afterwards.id == first.id


async def test_a_task_claim_waits_for_a_cancellation_and_is_refused(
    pg_store: PostgresStore, run: Run
) -> None:
    store = pg_store
    task = await _published(store, run, "work")
    await store.receive_task("default", lease_seconds=30)

    async with held(store) as cancelling:
        await _cancel_by_hand(cancelling, run)
        claiming = await blocked(store.claim_task(task.id))

    with pytest.raises(RunStateError):
        await claiming
    assert (await store.get_task(task.id)).status is TaskStatus.FAILED
    assert await store.receive_task("default", lease_seconds=30) is None


async def test_a_sub_run_start_waits_for_a_cancellation_and_is_refused(
    pg_store: PostgresStore, run: Run
) -> None:
    store = pg_store
    call = Address("call")

    async with held(store) as cancelling:
        await _cancel_by_hand(cancelling, run)
        starting = await blocked(
            store.start_run(
                "b", Version(1, 0, 0), {}, parent_id=run.id, parent_address=call
            )
        )

    with pytest.raises(RunStateError):
        await starting
    with pytest.raises(RunNotFoundError):
        await store.get_run(sub_run_id_for(run.id, call))
    # No "run started" for the sub-run: the hand-played cancellation wrote no
    # event of its own, so the parent's start is the only one.
    assert [(e.run_id, e.kind) for e in await store.events_after(0)] == [
        (run.id, EventKind.RUN_STARTED)
    ]


async def test_a_run_start_waits_for_an_upload_and_is_refused_the_old_version(
    pg_store: PostgresStore, run: Run
) -> None:
    store = pg_store

    async with held(store) as uploading:
        # store_flows, by hand: the lock, then the new version, not committed.
        await uploading.execute(
            "SELECT pg_advisory_xact_lock($1::int, $2::int)", *UPLOAD_LOCK
        )
        await uploading.execute(
            f"INSERT INTO {FLOW_VERSIONS_TABLE} (name, major, minor, patch, content)"
            " VALUES ('a', 2, 0, 0, $1)",
            canonical_content(_flow("a", "2.0.0").content),
        )
        starting = await blocked(store.start_run("a", Version(1, 0, 0), {}))

    with pytest.raises(FlowVersionError, match="latest"):
        await starting
    assert (await store.get_flow("a")).version == Version(2, 0, 0)


async def test_events_appended_under_contention_follow_on_without_a_gap(
    pg_store: PostgresStore, run: Run
) -> None:
    store = pg_store

    async with held(store) as appending:
        await _append_event_by_hand(appending, run.id, EventKind.TASK_FINISHED)
        starting = await blocked(store.start_run("a", Version(1, 0, 0), {}))

    other = await starting
    events = await store.events_after(0)
    assert [e.sequence for e in events] == [1, 2, 3]
    assert [(e.run_id, e.kind) for e in events[1:]] == [
        (run.id, EventKind.TASK_FINISHED),
        (other.id, EventKind.RUN_STARTED),
    ]


async def test_runs_started_under_contention_list_in_commit_order(
    pg_store: PostgresStore, run: Run
) -> None:
    """A start that waits on the event lock is numbered after the one holding it."""
    store = pg_store
    by_hand = uuid.uuid4()

    async with held(store) as starting:
        # start_run, by hand: the row, its event under the event lock, its
        # number; stopped short of its commit.
        await starting.execute(
            f"INSERT INTO {RUNS_TABLE} (id, flow, version, inputs, status, root_id)"
            " VALUES ($1, 'a', '1.0.0', '{}', 'active', $1)",
            by_hand,
        )
        await _append_event_by_hand(starting, by_hand, EventKind.RUN_STARTED)
        await starting.execute(
            f"UPDATE {RUNS_TABLE} SET position = "
            f"(SELECT COALESCE(MAX(position), 0) + 1 FROM {RUNS_TABLE})"
            " WHERE id = $1",
            by_hand,
        )
        waiting = await blocked(store.start_run("a", Version(1, 0, 0), {}))

    late = await waiting
    listed = await store.list_runs()
    assert [r.id for r in listed] == [late.id, by_hand, run.id]
    assert [r.id for r in await store.list_runs(before=late.id)] == [by_hand, run.id]
    assert [r.id for r in await store.list_runs(before=by_hand)] == [run.id]


async def test_a_task_finishes_while_its_tree_is_being_cancelled(
    pg_store: PostgresStore, run: Run
) -> None:
    store = pg_store
    task = await _published(store, run, "work")
    await store.receive_task("default", lease_seconds=30)
    await store.claim_task(task.id)

    async with held(store) as cancelling:
        await _cancel_by_hand(cancelling, run)
        await _append_event_by_hand(cancelling, run.id, EventKind.RUN_FINISHED)
        finishing = await blocked(store.finish_task(task.id, result="done"))

    finished = await finishing
    assert finished.status is TaskStatus.SUCCEEDED
    assert (await store.get_run(run.id)).status is RunStatus.CANCELLED
    events = await store.events_after(0)
    assert [e.sequence for e in events] == [1, 2, 3]
    assert [e.kind for e in events] == [
        EventKind.RUN_STARTED,
        EventKind.RUN_FINISHED,
        EventKind.TASK_FINISHED,
    ]
