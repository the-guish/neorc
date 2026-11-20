# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""neorc-mcp spoken to as a host speaks to it, against a real manager.

The manager is composed by oneberth, on the memory store, and served on a
socket, with a scheduler and a worker over HTTP running the approval example.
neorc-mcp is started from its variables, as its command starts it, with a
person's own token and CI's, and asked over streamable HTTP with its key.
Nothing is stubbed but the view's HTML, which a Python test never builds.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import (
    EXAMPLES,
    Tokens,
    composed,
    deployed_example,
    free_port,
    role_tokens,
    serve_app,
)

import neorc_ui
from neorc_core import Manager, QueueKind
from neorc_core.flows import read_flows
from neorc_core.local import MemoryStore, MemoryTaskNotifier
from neorc_mcp import PANEL_TOOL, VIEW_MIME_TYPE, VIEW_URI, app_from_environment

KEY = "a-key-the-host-sends"
VIEW = "<!doctype html><title>the panel</title>"
FLOW_A = (EXAMPLES / "hello" / "flows" / "a.yaml").read_text(encoding="utf-8")
"""A flow file as it is written, in YAML: one task on `default`."""

READS = {
    "list_flows",
    "get_flow",
    "list_flow_versions",
    "get_flow_version",
    "list_runs",
    "get_run",
    "list_run_tasks",
    "list_sub_runs",
    "get_run_state",
    "list_queues",
    "list_queue_tasks",
    "get_task",
    "get_task_inputs",
}
WRITES = {"start_run", "cancel_run", "complete_task"}
CI_TOOLS = {"upload_flows", "declare_queue"}
PANEL_TOOLS = {PANEL_TOOL, "show_flow", "show_run"}

Call = Callable[..., Awaitable[dict[str, Any]]]


async def rpc(
    address: str,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    key: str | None = KEY,
) -> httpx.Response:
    headers = {"accept": "application/json, text/event-stream"}
    if key is not None:
        headers["authorization"] = f"Bearer {key}"
    async with httpx.AsyncClient(timeout=30) as http:
        return await http.post(
            f"{address}/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
            headers=headers,
        )


@pytest.fixture
async def manager_served() -> AsyncIterator[tuple[str, Tokens]]:
    """A manager on a socket, and a token of each role: the person's is Ada's own."""
    manager = Manager(
        MemoryStore(queues={"default": QueueKind.WORKER}),
        tasks=MemoryTaskNotifier(),
        events=MemoryTaskNotifier(),
    )
    port = free_port()
    composition = await composed(manager, public_url=f"http://127.0.0.1:{port}")
    async with serve_app(composition, port=port) as address:
        yield address, await role_tokens(composition, ["default"])


def start_mcp(environ: dict[str, str]) -> Any:
    port = free_port()
    app, _ = app_from_environment(environ, "127.0.0.1", port)
    return serve_app(app, port=port)


@pytest.fixture
def view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    page = tmp_path / "mcp-view.html"
    page.write_text(VIEW, encoding="utf-8")
    monkeypatch.setattr(neorc_ui, "MCP_VIEW", page)
    return VIEW


@pytest.fixture
async def mcp(
    manager_served: tuple[str, Tokens], view: str
) -> AsyncIterator[tuple[str, str, Call]]:
    """neorc-mcp with both tokens: its address, the manager's, and a tool caller."""
    address, tokens = manager_served
    environ = {
        "NEORC_MCP_KEY": KEY,
        "NEORC_MANAGER_ADDRESS": address,
        "NEORC_API_TOKEN": tokens.user,
        "NEORC_CI_TOKEN": tokens.ci,
    }
    async with start_mcp(environ) as mcp_address:

        async def call(tool: str, /, **arguments: Any) -> dict[str, Any]:
            response = await rpc(
                mcp_address, "tools/call", {"name": tool, "arguments": arguments}
            )
            assert response.status_code == 200, response.text
            result: dict[str, Any] = response.json()["result"]
            return result

        yield mcp_address, address, call


def ok(result: dict[str, Any]) -> Any:
    """The structured content of a tool's answer that did not fail."""
    assert not result.get("isError"), result["content"]
    return result["structuredContent"]


def refused(result: dict[str, Any]) -> str:
    assert result.get("isError"), result
    text: str = result["content"][0]["text"]
    return text


# What a host is offered.


async def test_every_route_a_person_or_ci_may_call_is_a_tool_with_the_panel(
    mcp: tuple[str, str, Call],
) -> None:
    address, _, _ = mcp
    tools = {
        tool["name"]: tool
        for tool in (await rpc(address, "tools/list")).json()["result"]["tools"]
    }

    assert set(tools) == READS | WRITES | CI_TOOLS | PANEL_TOOLS
    for name in READS | {PANEL_TOOL}:
        assert tools[name]["annotations"]["readOnlyHint"] is True, name
    for name in {"cancel_run", "complete_task", "upload_flows"}:
        assert tools[name]["annotations"]["destructiveHint"] is True, name
    assert tools[PANEL_TOOL]["_meta"]["ui"] == {
        "resourceUri": VIEW_URI,
        "visibility": ["model", "app"],
    }
    for name in {"show_flow", "show_run"}:
        assert tools[name]["_meta"]["ui"] == {"visibility": ["model", "app"]}
    for name in READS | WRITES | CI_TOOLS:
        assert tools[name]["_meta"]["ui"] == {"visibility": ["model"]}, name
    assert tools[PANEL_TOOL]["inputSchema"].get("required", []) == []


async def test_the_view_is_read_as_an_mcp_app(mcp: tuple[str, str, Call]) -> None:
    address, _, _ = mcp
    contents = (await rpc(address, "resources/read", {"uri": VIEW_URI})).json()[
        "result"
    ]["contents"]

    assert [(c["mimeType"], c["text"]) for c in contents] == [(VIEW_MIME_TYPE, VIEW)]


async def test_without_ci_token_there_is_no_upload_and_no_declaration(
    manager_served: tuple[str, Tokens], view: str
) -> None:
    address, tokens = manager_served
    environ = {
        "NEORC_MCP_KEY": KEY,
        "NEORC_MANAGER_ADDRESS": address,
        "NEORC_API_TOKEN": tokens.user,
    }
    async with start_mcp(environ) as mcp_address:
        tools = {
            t["name"]
            for t in (await rpc(mcp_address, "tools/list")).json()["result"]["tools"]
        }

    assert tools == READS | WRITES | PANEL_TOOLS


@pytest.mark.parametrize("key", [None, "another-key"])
async def test_a_request_without_the_key_is_refused(
    mcp: tuple[str, str, Call], key: str | None
) -> None:
    address, _, _ = mcp
    response = await rpc(address, "tools/list", key=key)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_it_does_not_start_without_a_key_or_a_manager() -> None:
    with pytest.raises(SystemExit, match="NEORC_MCP_KEY"):
        app_from_environment({"NEORC_MANAGER_ADDRESS": "127.0.0.1:1"}, "127.0.0.1", 1)
    with pytest.raises(SystemExit, match="NEORC_MANAGER_ADDRESS"):
        app_from_environment({"NEORC_MCP_KEY": KEY}, "127.0.0.1", 1)


def test_a_token_is_not_sent_over_plain_http_to_another_machine() -> None:
    environ = {
        "NEORC_MCP_KEY": KEY,
        "NEORC_MANAGER_ADDRESS": "http://control:8420",
        "NEORC_API_TOKEN": "t",
    }

    with pytest.raises(SystemExit, match="NEORC_ALLOW_INSECURE_HTTP=1"):
        app_from_environment(environ, "127.0.0.1", 1)
    app_from_environment({**environ, "NEORC_ALLOW_INSECURE_HTTP": "1"}, "127.0.0.1", 1)


# The journey: deploy, run, troubleshoot, show.


async def until(read: Callable[[], Awaitable[Any]], done: Callable[[Any], bool]) -> Any:
    async with asyncio.timeout(30):
        while True:
            value = await read()
            if done(value):
                return value
            await asyncio.sleep(0.05)


@pytest.mark.usefixtures("own_tasks_module")
async def test_an_agent_deploys_runs_troubleshoots_and_shows_a_flow(
    mcp: tuple[str, str, Call], manager_served: tuple[str, Tokens]
) -> None:
    _, manager_address, call = mcp
    _, tokens = manager_served
    # The approval example as an upload holds it: its form inline, as JSON text.
    (approval,) = read_flows(EXAMPLES / "approval" / "flows")

    declared = ok(await call("declare_queue", queue="editors", kind="external"))
    stored = ok(await call("upload_flows", flows=[json.dumps(approval)]))
    again = ok(await call("upload_flows", flows=[json.dumps(approval)]))
    bad = refused(
        await call("upload_flows", flows=["name: broken\nversion: 1.0.0\nsteps: 3\n"])
    )

    assert declared == {"name": "editors", "kind": "external"}
    assert stored == {"stored": [True]}
    assert again == {"stored": [False]}
    assert "invalid flow definition" in bad

    # A scheduler, and a worker on `default`, each with its own token.
    async with deployed_example(
        manager_address, "approval", ["default"], tokens=tokens
    ):
        run = ok(await call("start_run", flow="approval", inputs={"topic": "tea"}))
        assert run["status"] == "active"
        assert run["started_by"]["kind"] == "user"

        pending = await until(
            lambda: call("list_queue_tasks", queue="editors", status="pending"),
            lambda result: len(ok(result)["tasks"]) == 2,
        )
        review = next(
            t["task"]
            for t in ok(pending)["tasks"]
            if t["task"]["address"]["step"] == "review_draft"
        )
        inputs = ok(await call("get_task_inputs", task_id=review["id"]))
        assert inputs["inputs"]["draft"] == "A first draft about tea."

        # A person's answer fails the task, and the run with it.
        ok(
            await call(
                "complete_task", task_id=review["id"], error="the draft is off-topic"
            )
        )
        twice = refused(
            await call(
                "complete_task",
                task_id=review["id"],
                result={"decision": "approve", "comment": "fine"},
            )
        )
        assert "TaskStateError" in twice

        failed = await until(
            lambda: call("get_run", run_id=run["id"]),
            lambda result: ok(result)["status"] == "failed",
        )

    assert ok(failed)["reason"]
    listed = ok(await call("list_runs", status="failed"))["runs"]
    assert [r["id"] for r in listed] == [run["id"]]
    tasks = ok(await call("list_run_tasks", run_id=run["id"]))["tasks"]
    by_step = {t["address"]["step"]: t for t in tasks}
    assert by_step["review_draft"]["status"] == "failed"
    assert by_step["review_draft"]["error"] == "the draft is off-topic"
    assert by_step["write_draft"]["status"] == "succeeded"
    assert (
        ok(await call("get_task", task_id=review["id"]))["error"]
        == "the draft is off-topic"
    )
    state = ok(await call("get_run_state", run_id=run["id"]))
    assert state["inputs"] == {"topic": "tea"}
    assert ok(await call("list_sub_runs", run_id=run["id"])) == {"runs": []}

    # The panel: a run, drawn with what failed; then a flow.
    shown = await call("show_run", run_id=run["id"])
    panel = ok(shown)
    assert panel["shows"] == "run"
    assert panel["run"]["id"] == run["id"]
    assert panel["flow"]["name"] == "approval"
    assert {t["address"]["step"] for t in panel["tasks"]} >= {
        "write_draft",
        "review_draft",
    }
    assert "review_draft: failed" in shown["content"][0]["text"]
    assert "error: the draft is off-topic" in shown["content"][0]["text"]
    assert ok(await call(PANEL_TOOL)) == panel

    flow = ok(await call("show_flow", name="approval"))
    assert (flow["shows"], flow["flow"]["version"], flow["versions"]) == (
        "flow",
        "1.0.0",
        ["1.0.0"],
    )
    assert ok(await call(PANEL_TOOL))["shows"] == "flow"


async def test_reads_and_a_cancellation(mcp: tuple[str, str, Call]) -> None:
    _, _, call = mcp
    ok(await call("upload_flows", flows=[FLOW_A]))

    flows = ok(await call("list_flows"))["flows"]
    flow = ok(await call("get_flow", name="a"))
    versions = ok(await call("list_flow_versions", name="a"))["versions"]
    version = ok(await call("get_flow_version", name="a", version=flow["version"]))
    queues = ok(await call("list_queues"))["queues"]
    # No scheduler runs here: the run stays active until it is cancelled.
    run = ok(await call("start_run", flow="a", inputs={}))
    ok(await call("cancel_run", run_id=run["id"]))

    assert [f["name"] for f in flows] == ["a"]
    assert [v["version"] for v in versions] == [flow["version"]]
    assert version == flow
    assert {"name": "default", "kind": "worker"} in queues
    assert ok(await call("get_run", run_id=run["id"]))["status"] == "cancelled"


async def test_the_manager_s_refusals_reach_the_model_in_its_words(
    mcp: tuple[str, str, Call],
) -> None:
    _, _, call = mcp
    unknown = "00000000-0000-4000-8000-000000000000"

    assert "RunNotFoundError" in refused(await call("get_run", run_id=unknown))
    assert "FlowNotFoundError" in refused(await call("start_run", flow="nowhere"))
    assert ok(await call("show_run", run_id=unknown))["shows"] == "missing"
    nothing = ok(await call("show_flow", name="nowhere"))
    assert nothing["shows"] == "missing"


async def test_the_panel_lists_the_flows_before_anything_is_shown(
    mcp: tuple[str, str, Call],
) -> None:
    _, _, call = mcp
    ok(await call("upload_flows", flows=[FLOW_A]))

    shown = await call(PANEL_TOOL)

    assert ok(shown) == {"shows": "nothing", "flows": ["a"]}
    assert shown["content"][0]["text"] == "The panel shows nothing yet. Flows: a."


async def test_the_sdk_s_own_client_reaches_it(mcp: tuple[str, str, Call]) -> None:
    """A host's engine connects with an MCP client such as the SDK's: the
    handshake, the listing, and a call."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    address, _, _ = mcp
    async with (
        httpx.AsyncClient(headers={"authorization": f"Bearer {KEY}"}) as http,
        streamable_http_client(f"{address}/mcp", http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        initialized = await session.initialize()
        listed = await session.list_tools()
        result = await session.call_tool("list_queues", {})

    assert initialized.serverInfo.name == "neorc"
    assert len(listed.tools) == len(READS | WRITES | CI_TOOLS | PANEL_TOOLS)
    assert result.structuredContent == {
        "queues": [{"name": "default", "kind": "worker"}]
    }
