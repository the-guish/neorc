# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The whole thing: a manager on a socket, Postgres underneath, workers over HTTP.

Nothing here is stubbed. The example scenarios of ``neorc_core.testing.examples``
run here as they run in memory, with a scheduler and one worker per queue on
the HTTP clients: if this passes, the pieces fit. The manager is standalone
neorc, composed by oneberth, and needs a token, as a deployment's does: a
service token of each role, made as ``neorc tokens create`` makes one, a worker
token per queue, and a person's own token for the client that starts runs.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from conftest import (
    EXAMPLES,
    Tokens,
    declare_queues,
    deployed_example,
    free_port,
    role_tokens,
    serve_app,
    service_token,
)
from oneberth.identity import InvalidValueError as TokenRefusedError
from oneberth.web import Composition
from oneberth.web.testing import config_for

from neorc.manager import build_app
from neorc_core import QueueKind, Role, RunStatus
from neorc_core._values import JsonValue
from neorc_core.flows import read_flows
from neorc_core.testing import examples

pytestmark = pytest.mark.postgres


@pytest.fixture
async def served(
    pg_schema: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[str, Composition, Tokens]]:
    """A manager service listening on a real port, backed by a real database.

    Its queues are declared first, as an operator declares them. It serves a
    stand-in UI: the built one needs Node.js, which Python tests never do.
    """
    (tmp_path / "index.html").write_text("<!doctype html><title>stand-in</title>")
    monkeypatch.setattr("neorc_ui.STATIC", tmp_path)
    await declare_queues(pg_schema, "default", "scoring")
    port = free_port()
    app = build_app(
        pg_schema,
        config=config_for("neorc", public_url=f"http://127.0.0.1:{port}"),
        create_schema=True,
        long_poll_timeout=2,
    )
    async with serve_app(app, port=port) as address:
        yield address, app, await role_tokens(app, ["default", "scoring"])


@pytest.fixture
def manager_address(served: tuple[str, Composition, Tokens]) -> str:
    return served[0]


@pytest.fixture
def tokens(served: tuple[str, Composition, Tokens]) -> Tokens:
    """Tokens the manager accepts, one for each process's role."""
    return served[2]


# The examples, deployed.


@pytest.mark.usefixtures("own_tasks_module")
@pytest.mark.parametrize("flow", ["a", "b"])
async def test_hello_runs_deployed(
    manager_address: str, tokens: Tokens, flow: str, capfd: pytest.CaptureFixture[str]
) -> None:
    async with deployed_example(
        manager_address, "hello", ["default"], tokens=tokens
    ) as client:
        await examples.hello(client, EXAMPLES, flow)

    assert capfd.readouterr().out == f"{flow}\n"


@pytest.mark.usefixtures("own_tasks_module")
async def test_word_picker_runs_deployed(manager_address: str, tokens: Tokens) -> None:
    async with deployed_example(
        manager_address, "wordplay", ["default", "scoring"], tokens=tokens
    ) as client:
        await examples.word_picker(client, EXAMPLES)


@pytest.mark.usefixtures("own_tasks_module")
async def test_word_picker_rounds_runs_deployed(
    manager_address: str, tokens: Tokens
) -> None:
    async with deployed_example(
        manager_address, "wordplay", ["default", "scoring"], tokens=tokens
    ) as client:
        await examples.word_picker_rounds(client, EXAMPLES)


async def test_the_manager_serves_the_ui_and_the_listings(
    manager_address: str, tokens: Tokens
) -> None:
    async with httpx.AsyncClient(base_url=manager_address) as http:
        page = await http.get("/ui/")
        anonymous = await http.get("/neorc/flows")
        headers = {"authorization": f"Bearer {tokens.user}"}
        flows = await http.get("/neorc/flows", headers=headers)
        runs = await http.get("/neorc/runs", headers=headers)

    assert page.status_code == 200 and "stand-in" in page.text
    assert "content-security-policy" in page.headers
    assert anonymous.status_code == 401
    assert flows.status_code == 200 and flows.json() == {"flows": []}
    assert runs.status_code == 200 and runs.json() == {"runs": []}


@pytest.mark.usefixtures("own_tasks_module")
async def test_an_external_trigger_starts_runs_and_reads_nothing(
    served: tuple[str, Composition, Tokens], manager_address: str, tokens: Tokens
) -> None:
    trigger = await service_token(served[1], Role.EXTERNAL_TRIGGER)
    headers = {"authorization": f"Bearer {trigger}"}

    async with (
        deployed_example(
            manager_address, "hello", ["default"], tokens=tokens
        ) as client,
        httpx.AsyncClient(
            base_url=f"{manager_address}/neorc", headers=headers
        ) as webhook,
    ):
        await client.upload_flows(read_flows(EXAMPLES / "hello" / "flows"))
        started = await webhook.post("/flows/a/runs", json={"inputs": {}})
        run_id = started.json()["id"]
        read = await webhook.get(f"/runs/{run_id}")
        cancel = await webhook.post(f"/runs/{run_id}/cancel", json={})
        run = await examples.wait_for_run(client, uuid.UUID(run_id))

    assert started.status_code == 201
    assert run.status is RunStatus.SUCCEEDED
    assert (read.status_code, read.json()["error"]) == (403, "Forbidden")
    assert "may not neorc/runs:read" in read.json()["detail"]
    assert cancel.status_code == 403


# Task completion, deployed: docs/specs/task-completion.md.

APPROVAL: JsonValue = {
    "name": "approval",
    "version": "1.0.0",
    "output": "tasks.approve",
    "steps": {
        # hello's handler, on a worker queue; it prints, and returns nothing.
        "a": {"queue": "default", "handler": "tasks:a"},
        "approve": {
            "queue": "editors",
            "handler": "approve-draft",
            "params": {"written": "tasks.a", "id": "neorc.task_id"},
            "fixed_params": {"form": "approval"},
        },
    },
}


@pytest.mark.usefixtures("own_tasks_module")
async def test_a_run_waits_for_a_responder_deployed(
    pg_schema: str, manager_address: str, tokens: Tokens
) -> None:
    """A worker runs one step, a responder completes the next, over HTTP.

    The queue is declared and the tokens created as the commands do it, on the
    database. Nothing takes the external task but the responder: not the
    worker's token, not the scheduler's, and not a second completion.
    """
    from argparse import Namespace

    from neorc._admin import NeorcConnector
    from neorc_core import QueueDeclarations
    from neorc_core.local import MemoryTokenGrants

    args = Namespace(database_url=pg_schema, database_file=None)
    async with NeorcConnector().open(args) as admin:
        from neorc.postgres import PostgresStore

        async with PostgresStore(pg_schema, max_size=1) as store:
            await QueueDeclarations(store, MemoryTokenGrants()).declare(
                "editors", QueueKind.EXTERNAL
            )
        responder, _ = await admin.create_token(
            "inbox", Role.RESPONDER, resource="editors"
        )
        with pytest.raises(TokenRefusedError, match="queue 'editors' is external"):
            await admin.create_token("w", Role.WORKER, resource="editors")

    def bearer(secret: str) -> dict[str, str]:
        return {"authorization": f"Bearer {secret}", "content-type": "application/json"}

    async with (
        deployed_example(
            manager_address, "hello", ["default"], tokens=tokens
        ) as client,
        httpx.AsyncClient(base_url=f"{manager_address}/neorc") as http,
    ):
        await client.upload_flows([APPROVAL])
        run = await client.start_run("approval", {})

        async with asyncio.timeout(30):
            while True:
                listed = await http.get(
                    "/queues/editors/tasks/pending", headers=bearer(responder)
                )
                assert listed.status_code == 200
                if listed.json()["tasks"]:
                    break
                await asyncio.sleep(0.05)
        (delivery,) = listed.json()["tasks"]
        task_id = delivery["task"]["id"]
        complete = f"/tasks/{task_id}/complete"
        answer = {"result": {"approved": True, "by": "ada"}}

        by_a_worker = await http.post(
            "/queues/editors/tasks/receive",
            params={"timeout": 0},
            headers=bearer(tokens.workers["default"]),
        )
        by_the_scheduler = await http.post(
            complete, json=answer, headers=bearer(tokens.scheduler)
        )
        waiting = await client.get_run(run.id)
        completed = await http.post(complete, json=answer, headers=bearer(responder))
        again = await http.post(complete, json=answer, headers=bearer(responder))
        finished = await examples.wait_for_run(client, run.id)
        after = await http.get(
            "/queues/editors/tasks/pending", headers=bearer(responder)
        )

    assert delivery["task"]["handler"] == "approve-draft"
    assert delivery["inputs"] == {"form": "approval", "written": None, "id": task_id}
    assert by_a_worker.status_code == 403
    assert by_the_scheduler.status_code == 403
    assert waiting.status is RunStatus.ACTIVE
    assert completed.status_code == 204
    assert (again.status_code, again.json()["error"]) == (409, "TaskStateError")
    assert finished.status is RunStatus.SUCCEEDED
    assert finished.output == {"approved": True, "by": "ada"}
    assert after.json() == {"tasks": []}
