# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The whole thing: a manager on a socket, Postgres underneath, workers over HTTP.

Nothing here is stubbed. The example scenarios of ``neorc_core.testing.examples``
run here as they run in memory, with a scheduler and one worker per queue on
the HTTP clients: if this passes, the pieces fit. The manager needs a token, as
a deployment's does, created as ``neorc tokens create`` creates one: one of each
role, and a worker token per queue.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from conftest import EXAMPLES, Tokens, deployed_example, role_tokens, serve_app

from neorc.manager import build_app
from neorc_core import Access, InvalidValueError, QueueKind, Role, RunStatus
from neorc_core._values import JsonValue
from neorc_core.flows import read_flows
from neorc_core.testing import examples

pytestmark = pytest.mark.postgres


@pytest.fixture
async def tokens(pg_schema: str) -> Tokens:
    """Tokens the manager accepts, one for each process's role.

    Their queues are declared first, as an operator declares them: a worker
    token is bound only to a declared worker queue.
    """
    from neorc.postgres import PostgresCredentialStore, PostgresStore, create_schema

    await create_schema(pg_schema)
    async with (
        PostgresCredentialStore(pg_schema) as credentials,
        PostgresStore(pg_schema) as store,
    ):
        access = Access(credentials, queues=store)
        for queue in ("default", "scoring"):
            await access.declare_queue(queue, QueueKind.WORKER)
        return await role_tokens(access, ["default", "scoring"])


@pytest.fixture
async def manager_address(
    pg_schema: str, tokens: Tokens, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[str]:
    """A manager service listening on a real port, backed by a real database.

    It serves a stand-in UI: the built one needs Node.js, which Python tests
    never do.
    """
    (tmp_path / "index.html").write_text("<!doctype html><title>stand-in</title>")
    monkeypatch.setattr("neorc_ui.STATIC", tmp_path)
    app = build_app(pg_schema, auth=True, create_schema=True, long_poll_timeout=2)
    async with serve_app(app) as address:
        yield address


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
        anonymous = await http.get("/flows")
        headers = {"authorization": f"Bearer {tokens.user}"}
        flows = await http.get("/flows", headers=headers)
        runs = await http.get("/runs", headers=headers)

    assert page.status_code == 200 and "stand-in" in page.text
    assert "content-security-policy" in page.headers
    assert anonymous.status_code == 401
    assert flows.status_code == 200 and flows.json() == {"flows": []}
    assert runs.status_code == 200 and runs.json() == {"runs": []}


@pytest.mark.usefixtures("own_tasks_module")
async def test_an_external_trigger_starts_runs_and_reads_nothing(
    pg_schema: str, manager_address: str, tokens: Tokens
) -> None:
    from neorc.postgres import PostgresCredentialStore

    async with PostgresCredentialStore(pg_schema) as credentials:
        trigger, _ = await Access(credentials).create_token(
            "webhook", Role.EXTERNAL_TRIGGER
        )
    headers = {"authorization": f"Bearer {trigger}"}

    async with (
        deployed_example(
            manager_address, "hello", ["default"], tokens=tokens
        ) as client,
        httpx.AsyncClient(base_url=manager_address, headers=headers) as webhook,
    ):
        await client.upload_flows(read_flows(EXAMPLES / "hello" / "flows"))
        started = await webhook.post("/flows/a/runs", json={"inputs": {}})
        run_id = started.json()["id"]
        read = await webhook.get(f"/runs/{run_id}")
        cancel = await webhook.post(f"/runs/{run_id}/cancel", json={})
        run = await examples.wait_for_run(client, uuid.UUID(run_id))

    assert started.status_code == 201
    assert run.status is RunStatus.SUCCEEDED
    assert (read.status_code, read.json()["error"]) == (403, "PermissionDeniedError")
    assert "external-trigger token may not runs:read" in read.json()["detail"]
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
    worker's token, not a person's, and not a second completion.
    """
    from neorc.postgres import PostgresCredentialStore, PostgresStore

    async with (
        PostgresCredentialStore(pg_schema) as credentials,
        PostgresStore(pg_schema) as store,
    ):
        access = Access(credentials, queues=store)
        await access.declare_queue("editors", QueueKind.EXTERNAL)
        responder, _ = await access.create_token(
            "inbox", Role.RESPONDER, queue="editors"
        )
        with pytest.raises(InvalidValueError, match="queue 'editors' is external"):
            await access.create_token("w", Role.WORKER, queue="editors")

    def bearer(secret: str) -> dict[str, str]:
        return {"authorization": f"Bearer {secret}", "content-type": "application/json"}

    async with (
        deployed_example(
            manager_address, "hello", ["default"], tokens=tokens
        ) as client,
        httpx.AsyncClient(base_url=manager_address) as http,
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
