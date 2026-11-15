# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The manager service assembled on Postgres, two of them on one database.

What matters here is what a single process cannot show: a task published
through one manager wakes a worker waiting on another, and an event recorded
through one wakes a scheduler waiting on another, over the notification
channels rather than the poll timeout.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack

import httpx
import pytest
from conftest import PUBLIC_URL, declare_queues, role_tokens_transport
from oneberth.identity import SchemaVersionError
from oneberth.web.testing import config_for

from neorc.http import HttpManagerClient, HttpQueueClient
from neorc.manager import build_app
from neorc_core._values import JsonValue
from neorc_core.flows import Address

pytestmark = pytest.mark.postgres

FLOW: JsonValue = {
    "name": "f",
    "version": "1.0.0",
    "steps": {"work": {"handler": "tasks:work"}},
}


class _Process:
    """One manager process: its application and clients on an ASGI transport."""

    def __init__(self, manager: HttpManagerClient, queue: HttpQueueClient):
        self.manager = manager
        self.queue = queue


@pytest.fixture
async def processes(pg_schema: str) -> AsyncIterator[tuple[_Process, _Process]]:
    """Two manager processes on the same database, their lifespans running.

    Both share oneberth's store too, in the same database; each request carries
    a token of a role that may make it.
    """
    await declare_queues(pg_schema, "default")
    async with AsyncExitStack() as stack:
        built = []
        for _ in range(2):
            app = build_app(
                pg_schema,
                config=config_for("neorc"),
                create_schema=True,
                long_poll_timeout=5,
                ui=False,
            )
            await stack.enter_async_context(app.lifespan())
            transport = role_tokens_transport(app)
            manager = await stack.enter_async_context(
                HttpManagerClient(PUBLIC_URL, transport=transport)
            )
            queue = await stack.enter_async_context(
                HttpQueueClient(PUBLIC_URL, transport=transport)
            )
            built.append(_Process(manager, queue))
        yield built[0], built[1]


async def test_a_task_published_through_one_manager_wakes_a_worker_on_another(
    processes: tuple[_Process, _Process],
) -> None:
    one, other = processes
    await one.manager.upload_flows([FLOW])
    run = await one.manager.start_run("f", {})

    async def publish_shortly() -> None:
        await asyncio.sleep(0.2)
        await one.manager.publish_task(run.id, Address("work"))

    loop = asyncio.get_running_loop()
    begun = loop.time()
    async with asyncio.TaskGroup() as group:
        group.create_task(publish_shortly())
        delivery = await other.queue.receive_task("default", timeout=5)

    assert delivery is not None and delivery.task.run_id == run.id
    # Woken by the announcement, not by falling out of the poll timeout.
    assert loop.time() - begun < 3


async def test_an_event_recorded_through_one_manager_wakes_a_scheduler_on_another(
    processes: tuple[_Process, _Process],
) -> None:
    one, other = processes
    await one.manager.upload_flows([FLOW])

    async def start_shortly() -> None:
        await asyncio.sleep(0.2)
        await one.manager.start_run("f", {})

    loop = asyncio.get_running_loop()
    begun = loop.time()
    async with asyncio.TaskGroup() as group:
        group.create_task(start_shortly())
        events = await other.manager.wait_for_events(0, timeout=5)

    assert [e.kind.value for e in events] == ["run_started"]
    assert loop.time() - begun < 3


async def test_the_flow_tables_are_created_at_startup(pg_schema: str) -> None:
    from neorc.postgres import drop_schema

    await drop_schema(pg_schema)
    app = build_app(pg_schema, config=config_for("neorc"), create_schema=True, ui=False)

    async with app.lifespan():
        await declare_queues(pg_schema, "default")
        transport = role_tokens_transport(app)
        async with HttpManagerClient(PUBLIC_URL, transport=transport) as client:
            assert await client.upload_flows([FLOW]) == [True]


async def test_the_manager_needs_oneberths_store_set_up(pg_schema: str) -> None:
    """oneberth's store is the schema ``oneberth`` of the same database."""
    refused = build_app(pg_schema, config=config_for("neorc"), ui=False)
    created = build_app(
        pg_schema, config=config_for("neorc"), create_schema=True, ui=False
    )

    with pytest.raises(SchemaVersionError, match="not set up"):
        async with refused.lifespan():
            pass
    async with created.lifespan():
        pass
    async with refused.lifespan():
        transport = httpx.ASGITransport(app=refused)
        async with httpx.AsyncClient(transport=transport, base_url=PUBLIC_URL) as http:
            assert (await http.get("/neorc/runs")).status_code == 401
