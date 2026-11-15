# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The manager service on a SQLite file: one process, notified in memory.

A flow runs from start to finish on uvicorn, the HTTP clients and the file,
with a scheduler and a worker per queue, as it does on Postgres. oneberth's
store is ``oneberth.db`` beside the file, opened by the same process: the
manager started again finds what the first one left, the tokens included.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from pathlib import Path

import httpx
import pytest
from conftest import (
    EXAMPLES,
    PUBLIC_URL,
    deployed_example,
    free_port,
    role_tokens,
    role_tokens_transport,
    serve_app,
)
from oneberth.web.testing import config_for

from neorc.http import HttpManagerClient, HttpQueueClient
from neorc.manager import build_app
from neorc.sqlite import SqliteStore
from neorc_core import QueueKind
from neorc_core._values import JsonValue
from neorc_core.flows import Address
from neorc_core.testing import examples

FLOW: JsonValue = {
    "name": "f",
    "version": "1.0.0",
    "steps": {"work": {"handler": "tasks:work"}},
}


async def _declare(path: Path, *queues: str) -> None:
    """Declare worker queues on the file, as ``neorc queues declare`` does."""
    async with SqliteStore(path) as store:
        for queue in queues:
            await store.declare_queue(queue, QueueKind.WORKER)


@pytest.fixture
def stand_in_ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A built UI to serve: the real one needs Node.js, which Python tests never do."""
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<!doctype html><title>stand-in</title>")
    monkeypatch.setattr("neorc_ui.STATIC", ui)


@pytest.mark.usefixtures("own_tasks_module", "stand_in_ui")
async def test_a_flow_runs_to_its_end_on_a_sqlite_file(tmp_path: Path) -> None:
    path = tmp_path / "neorc.db"
    await _declare(path, "default", "scoring")
    port = free_port()
    config = config_for("neorc", public_url=f"http://127.0.0.1:{port}")
    app = build_app(database_file=path, config=config, long_poll_timeout=2)

    async with serve_app(app, port=port) as address:
        tokens = await role_tokens(app, ["default", "scoring"])
        async with deployed_example(
            address, "wordplay", ["default", "scoring"], tokens=tokens
        ) as client:
            await examples.word_picker_rounds(client, EXAMPLES)
        async with httpx.AsyncClient(
            base_url=address, headers={"authorization": f"Bearer {tokens.user}"}
        ) as http:
            page = await http.get("/ui/")
            (finished,) = (await http.get("/neorc/runs")).json()["runs"]

    assert page.status_code == 200
    assert "stand-in" in page.text
    assert finished["status"] == "succeeded"
    assert (tmp_path / "oneberth.db").is_file()

    again = build_app(database_file=path, config=config, ui=False)
    async with again.lifespan():
        transport = httpx.ASGITransport(app=again)
        async with httpx.AsyncClient(
            transport=transport,
            base_url=f"{PUBLIC_URL}/neorc",
            headers={"authorization": f"Bearer {tokens.scheduler}"},
        ) as http:
            runs = (await http.get("/runs")).json()["runs"]
            flows = (await http.get("/flows")).json()["flows"]

    assert runs == [finished]
    assert sorted(flow["name"] for flow in flows) == [
        "word_picker",
        "word_picker_rounds",
    ]


@pytest.fixture
async def clients(
    tmp_path: Path,
) -> AsyncIterator[tuple[HttpManagerClient, HttpQueueClient]]:
    path = tmp_path / "neorc.db"
    await _declare(path, "default")
    app = build_app(
        database_file=path, config=config_for("neorc"), long_poll_timeout=5, ui=False
    )
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(app.lifespan())
        transport = role_tokens_transport(app)
        manager = await stack.enter_async_context(
            HttpManagerClient(PUBLIC_URL, transport=transport)
        )
        queue = await stack.enter_async_context(
            HttpQueueClient(PUBLIC_URL, transport=transport)
        )
        yield manager, queue


async def test_a_published_task_wakes_a_waiting_worker_in_memory(
    clients: tuple[HttpManagerClient, HttpQueueClient],
) -> None:
    manager, queue = clients
    await manager.upload_flows([FLOW])
    run = await manager.start_run("f", {})

    async def publish_shortly() -> None:
        await asyncio.sleep(0.2)
        await manager.publish_task(run.id, Address("work"))

    loop = asyncio.get_running_loop()
    begun = loop.time()
    async with asyncio.TaskGroup() as group:
        group.create_task(publish_shortly())
        delivery = await queue.receive_task("default", timeout=5)

    assert delivery is not None
    assert delivery.task.run_id == run.id
    # Woken by the announcement, not by falling out of the poll timeout.
    assert loop.time() - begun < 3


async def test_a_manager_keeps_its_data_in_one_database(tmp_path: Path) -> None:
    path = tmp_path / "neorc.db"

    with pytest.raises(ValueError, match="a file or a URL"):
        build_app(
            "postgresql:///neorc",
            database_file=path,
            config=config_for("neorc"),
            ui=False,
        )
    assert not path.exists()
