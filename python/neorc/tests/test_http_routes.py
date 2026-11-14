# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The routes over an ASGI transport, on the memory store.

Routing, status codes and request validation only: what a flow means is
settled in core, and the HTTP clients are held to the client contracts.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from neorc.manager import create_app
from neorc.manager._routes import MAX_BODY_BYTES
from neorc_core import Manager, QueueKind
from neorc_core._values import MAX_JSON_DEPTH, JsonValue

MAIN: JsonValue = {
    "name": "main",
    "version": "1.0.0",
    "inputs": {"word": "string"},
    "output": "tasks.work",
    "steps": {"work": {"handler": "tasks:work", "params": {"word": "inputs.word"}}},
}


@pytest.fixture
async def http(manager: Manager) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(manager, access=None, long_poll_timeout=2)
    transport = httpx.ASGITransport(app=app)
    # Every write is sent as JSON, as the clients send it, body or not.
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://manager.test",
        headers={"content-type": "application/json"},
    ) as client:
        yield client


async def _started(http: httpx.AsyncClient) -> str:
    await http.post("/flows", json={"flows": [MAIN]})
    response = await http.post("/flows/main/runs", json={"inputs": {"word": "hi"}})
    assert response.status_code == 201
    run_id: str = response.json()["id"]
    return run_id


def _error(response: httpx.Response) -> str:
    error: str = response.json()["error"]
    return error


async def test_flows_are_uploaded_under_the_version_rules(
    http: httpx.AsyncClient,
) -> None:
    assert isinstance(MAIN, dict)
    uploaded = await http.post("/flows", json={"flows": [MAIN]})
    again = await http.post("/flows", json={"flows": [MAIN]})
    changed = await http.post(
        "/flows", json={"flows": [{**MAIN, "inputs": {"word": "number"}}]}
    )
    invalid = await http.post("/flows", json={"flows": [{"name": "broken"}]})

    assert (uploaded.status_code, uploaded.json()) == (200, {"stored": [True]})
    assert again.json() == {"stored": [False]}
    assert (changed.status_code, _error(changed)) == (409, "FlowVersionError")
    assert (invalid.status_code, _error(invalid)) == (422, "FlowDefinitionError")
    assert any("missing field" in p for p in invalid.json()["problems"])


async def test_a_flow_reads_back_by_name_and_by_version(
    http: httpx.AsyncClient,
) -> None:
    await http.post("/flows", json={"flows": [MAIN]})

    latest = await http.get("/flows/main")
    version = await http.get("/flows/main/versions/1.0.0")
    missing = await http.get("/flows/main/versions/2.0.0")
    unknown = await http.get("/flows/nothing")
    malformed = await http.get("/flows/main/versions/latest")

    assert latest.status_code == 200
    assert latest.json() == {"name": "main", "version": "1.0.0", "content": MAIN}
    assert version.json() == latest.json()
    assert (missing.status_code, _error(missing)) == (404, "FlowNotFoundError")
    assert (unknown.status_code, _error(unknown)) == (404, "FlowNotFoundError")
    assert (malformed.status_code, _error(malformed)) == (422, "InvalidValueError")


async def test_a_run_starts_reads_back_and_is_cancelled(
    http: httpx.AsyncClient,
) -> None:
    run_id = await _started(http)

    run = await http.get(f"/runs/{run_id}")
    state = await http.get(f"/runs/{run_id}/state")
    cancelled = await http.post(f"/runs/{run_id}/cancel")
    again = await http.post(f"/runs/{run_id}/cancel")

    assert run.status_code == 200
    assert run.json()["flow"] == "main"
    assert run.json()["inputs"] == {"word": "hi"}
    assert run.json()["status"] == "active"
    assert state.json() == {"inputs": {"word": "hi"}, "steps": []}
    assert cancelled.status_code == 204
    assert (again.status_code, _error(again)) == (409, "RunStateError")
    assert (await http.get(f"/runs/{run_id}")).json()["status"] == "cancelled"


async def test_requests_the_manager_refuses_carry_the_error_they_hit(
    http: httpx.AsyncClient,
) -> None:
    await http.post("/flows", json={"flows": [MAIN]})
    missing = uuid.uuid4()

    bad_inputs = await http.post("/flows/main/runs", json={"inputs": {"word": 1}})
    no_flow = await http.post("/flows/nothing/runs", json={"inputs": {}})
    no_run = await http.get(f"/runs/{missing}")
    no_state = await http.get(f"/runs/{missing}/state")
    no_cancel = await http.post(f"/runs/{missing}/cancel")
    not_an_id = await http.get("/runs/not-a-uuid")
    nul_name = await http.get("/flows/main%00")

    assert (bad_inputs.status_code, _error(bad_inputs)) == (422, "InvalidValueError")
    assert (nul_name.status_code, _error(nul_name)) == (422, "InvalidValueError")
    assert "is not a string" in bad_inputs.json()["detail"]
    assert (no_flow.status_code, _error(no_flow)) == (404, "FlowNotFoundError")
    for response in (no_run, no_state, no_cancel):
        assert (response.status_code, _error(response)) == (404, "RunNotFoundError")
    assert (not_an_id.status_code, _error(not_an_id)) == (422, "InvalidValueError")


@pytest.mark.parametrize(
    ("content", "error"),
    [
        (
            b"[" * (MAX_JSON_DEPTH + 1) + b"]" * (MAX_JSON_DEPTH + 1),
            "InvalidValueError",
        ),
        (b"[" * 100_000, "InvalidValueError"),
        (b"{not json", "InvalidValueError"),
        (b'{"flows": "\xff"}', "InvalidValueError"),
        (b'{"flows": "not a list"}', "InvalidValueError"),
        (b'{"inputs": {}}', "InvalidValueError"),
        (b'{"flows": [], "flows": []}', "InvalidValueError"),
        (b'{"flows": [NaN]}', "InvalidValueError"),
        (b'{"flows": [-Infinity]}', "InvalidValueError"),
        (b'{"flows": [' + b" " * MAX_BODY_BYTES + b"]}", "PayloadTooLargeError"),
    ],
    ids=[
        "one-too-deep",
        "stack-deep",
        "not-json",
        "not-utf-8",
        "wrong-shape",
        "missing-field",
        "repeated-key",
        "nan",
        "infinity",
        "over-the-body-limit",
    ],
)
async def test_bodies_are_checked_before_anything_parses_them(
    http: httpx.AsyncClient, content: bytes, error: str
) -> None:
    response = await http.post(
        "/flows", content=content, headers={"content-type": "application/json"}
    )

    assert response.status_code == (413 if error == "PayloadTooLargeError" else 422)
    assert _error(response) == error


async def test_values_are_checked_by_core_not_by_the_envelope(
    http: httpx.AsyncClient,
) -> None:
    """A value of the wrong JSON type reaches core, which says what is wrong with it."""
    await http.post("/flows", json={"flows": [MAIN]})
    body: dict[str, Any] = {"inputs": {"word": [1, {"$datetime": "x"}]}}

    response = await http.post("/flows/main/runs", content=json.dumps(body).encode())

    assert (response.status_code, _error(response)) == (422, "InvalidValueError")
    assert "input 'word' is not a string" in response.json()["detail"]


# The scheduler's and the workers' routes.

WORK = {"step": "work", "scope": []}


async def test_a_run_is_driven_to_its_end_over_http(http: httpx.AsyncClient) -> None:
    run_id = await _started(http)

    published = await http.post(f"/runs/{run_id}/tasks", json={"address": WORK})
    definitions = await http.get("/queues/default/tasks")
    received = await http.post("/queues/default/tasks/receive", params={"timeout": 1})
    task_id = received.json()["task"]["id"]
    claimed = await http.post(f"/tasks/{task_id}/claim")
    beat = await http.post(f"/tasks/{task_id}/heartbeat", json={"lease_seconds": 30})
    finished = await http.post(f"/tasks/{task_id}/finished", json={"result": "hi!"})
    succeeded = await http.post(
        f"/runs/{run_id}/succeed", json={"output": "tasks.work"}
    )
    events = await http.get("/events", params={"after": 0, "timeout": 0})
    latest = await http.get("/events/latest")

    assert published.status_code == 201
    assert published.json()["address"] == WORK
    assert [t["name"] for t in definitions.json()["tasks"]] == ["work"]
    assert received.status_code == 200
    assert received.json()["inputs"] == {"word": "hi"}
    assert claimed.status_code == 204
    assert beat.status_code == 200 and "lease_expires_at" in beat.json()
    assert finished.status_code == 204
    assert succeeded.status_code == 200
    assert succeeded.json()["status"] == "succeeded"
    assert succeeded.json()["output"] == "hi!"
    assert [e["kind"] for e in events.json()["events"]] == [
        "run_started",
        "task_finished",
        "run_finished",
    ]
    assert latest.json() == {"sequence": events.json()["events"][-1]["sequence"]}


async def test_a_sub_run_starts_and_a_run_fails_over_http(
    http: httpx.AsyncClient,
) -> None:
    caller: JsonValue = {
        "name": "caller",
        "version": "1.0.0",
        "steps": {"call": {"flow": "main", "fixed_params": {"word": "x"}}},
    }
    await http.post("/flows", json={"flows": [MAIN, caller]})
    run_id = (await http.post("/flows/caller/runs", json={"inputs": {}})).json()["id"]

    sub_run = await http.post(
        f"/runs/{run_id}/sub-runs", json={"address": {"step": "call", "scope": []}}
    )
    failed = await http.post(f"/runs/{run_id}/fail", json={"reason": "boom"})

    assert sub_run.status_code == 201
    assert sub_run.json()["parent_id"] == run_id
    assert sub_run.json()["flow"] == "main"
    assert failed.status_code == 204
    sub_run_id = sub_run.json()["id"]
    assert (await http.get(f"/runs/{sub_run_id}")).json()["status"] == "failed"
    assert (await http.get(f"/runs/{run_id}")).json()["reason"] == "boom"


async def test_a_claim_refused_for_an_inactive_run_is_a_409(
    http: httpx.AsyncClient,
) -> None:
    run_id = await _started(http)
    await http.post(f"/runs/{run_id}/tasks", json={"address": WORK})
    received = await http.post("/queues/default/tasks/receive", params={"timeout": 1})
    task_id = received.json()["task"]["id"]
    await http.post(f"/runs/{run_id}/cancel")

    claimed = await http.post(f"/tasks/{task_id}/claim")

    assert (claimed.status_code, _error(claimed)) == (409, "RunStateError")


@pytest.mark.parametrize(
    "path", ["/events", "/queues/default/tasks/receive"], ids=["events", "tasks"]
)
async def test_the_long_poll_deadline_caps_a_longer_request(
    http: httpx.AsyncClient, path: str
) -> None:
    """The manager's own deadline answers, at two seconds here, not the hour asked."""
    loop = asyncio.get_running_loop()
    begun = loop.time()

    method = "GET" if path == "/events" else "POST"
    response = await http.request(method, path, params={"timeout": 3600})

    assert response.status_code == (200 if path == "/events" else 204)
    assert loop.time() - begun < 10


async def test_scheduler_and_worker_requests_are_validated(
    http: httpx.AsyncClient,
) -> None:
    run_id = await _started(http)
    await http.post(f"/runs/{run_id}/tasks", json={"address": WORK})
    received = await http.post("/queues/default/tasks/receive", params={"timeout": 1})
    task_id = received.json()["task"]["id"]

    not_an_address = await http.post(
        f"/runs/{run_id}/tasks", json={"address": {"step": 1, "scope": "x"}}
    )
    bool_in_scope = await http.post(
        f"/runs/{run_id}/tasks",
        json={"address": {"step": "work", "scope": [["rounds", True]]}},
    )
    not_a_task = await http.post(
        f"/runs/{run_id}/tasks", json={"address": {"step": "nothing", "scope": []}}
    )
    not_a_reference = await http.post(f"/runs/{run_id}/succeed", json={"output": "x"})
    no_such_step = await http.post(
        f"/runs/{run_id}/succeed", json={"output": "tasks.nothing"}
    )
    no_reason = await http.post(f"/runs/{run_id}/fail", json={})
    bad_lease = await http.post(
        f"/tasks/{task_id}/heartbeat", json={"lease_seconds": 0}
    )
    huge_lease = await http.post(
        f"/tasks/{task_id}/heartbeat",
        content=b'{"lease_seconds": 1e400}',
        headers={"content-type": "application/json"},
    )
    infinite_lease = await http.post(
        "/queues/default/tasks/receive", params={"timeout": 0, "lease_seconds": "inf"}
    )
    bad_after = await http.get("/events", params={"after": -1})
    unknown_task = await http.post(f"/tasks/{uuid.uuid4()}/claim")

    for response in (not_an_address, bool_in_scope, not_a_task, not_a_reference):
        assert (response.status_code, _error(response)) == (422, "InvalidValueError")
    for response in (no_such_step, no_reason, bad_lease, huge_lease, infinite_lease):
        assert (response.status_code, _error(response)) == (422, "InvalidValueError")
    assert (bad_after.status_code, _error(bad_after)) == (422, "InvalidValueError")
    assert (unknown_task.status_code, _error(unknown_task)) == (
        404,
        "TaskNotFoundError",
    )


async def test_a_result_the_manager_refuses_fails_the_task(
    http: httpx.AsyncClient,
) -> None:
    run_id = await _started(http)
    await http.post(f"/runs/{run_id}/tasks", json={"address": WORK})
    received = await http.post("/queues/default/tasks/receive", params={"timeout": 1})
    task_id = received.json()["task"]["id"]

    finished = await http.post(
        f"/tasks/{task_id}/finished", json={"result": {"$datetime": "no"}}
    )
    state = await http.get(f"/runs/{run_id}/state")

    assert finished.status_code == 204
    (step,) = state.json()["steps"]
    assert step["outcome"] == "failed"


# The listings a status page reads.


async def test_flows_versions_runs_tasks_and_sub_runs_are_listed(
    http: httpx.AsyncClient,
) -> None:
    caller: JsonValue = {
        "name": "caller",
        "version": "1.0.0",
        "steps": {"call": {"flow": "main", "fixed_params": {"word": "x"}}},
    }
    await http.post("/flows", json={"flows": [MAIN, caller]})
    assert isinstance(MAIN, dict)
    await http.post("/flows", json={"flows": [{**MAIN, "version": "1.1.0"}]})
    first = (await http.post("/flows/caller/runs", json={"inputs": {}})).json()["id"]
    second = await _started(http)
    await http.post(f"/runs/{second}/tasks", json={"address": WORK})
    sub_run = await http.post(
        f"/runs/{first}/sub-runs", json={"address": {"step": "call", "scope": []}}
    )
    await http.post(f"/runs/{second}/cancel")

    flows = await http.get("/flows")
    versions = await http.get("/flows/main/versions")
    runs = await http.get("/runs")
    page = await http.get("/runs", params={"limit": 1})
    rest = await http.get("/runs", params={"limit": 1, "before": second})
    cancelled = await http.get("/runs", params={"status": "cancelled"})
    everything = await http.get("/runs", params={"root_only": "false", "flow": "main"})
    tasks = await http.get(f"/runs/{second}/tasks")
    children = await http.get(f"/runs/{first}/sub-runs")
    task_id = tasks.json()["tasks"][0]["id"]
    task = await http.get(f"/tasks/{task_id}")

    assert flows.status_code == 200
    assert [(f["name"], f["version"]) for f in flows.json()["flows"]] == [
        ("caller", "1.0.0"),
        ("main", "1.1.0"),
    ]
    assert [v["version"] for v in versions.json()["versions"]] == ["1.1.0", "1.0.0"]
    assert [r["id"] for r in runs.json()["runs"]] == [second, first]
    assert [r["id"] for r in page.json()["runs"]] == [second]
    assert [r["id"] for r in rest.json()["runs"]] == [first]
    assert [r["id"] for r in cancelled.json()["runs"]] == [second]
    assert {r["id"] for r in everything.json()["runs"]} == {
        second,
        sub_run.json()["id"],
    }
    assert [t["address"] for t in tasks.json()["tasks"]] == [WORK]
    assert tasks.json()["tasks"][0]["created_at"] is not None
    assert [r["id"] for r in children.json()["runs"]] == [sub_run.json()["id"]]
    assert task.status_code == 200 and task.json()["id"] == task_id


async def test_listing_requests_are_validated_and_misses_are_404(
    http: httpx.AsyncClient,
) -> None:
    await http.post("/flows", json={"flows": [MAIN]})
    missing = uuid.uuid4()

    no_versions = await http.get("/flows/nothing/versions")
    no_tasks = await http.get(f"/runs/{missing}/tasks")
    no_children = await http.get(f"/runs/{missing}/sub-runs")
    no_task = await http.get(f"/tasks/{missing}")
    no_before = await http.get("/runs", params={"before": str(missing)})
    bad_before = await http.get("/runs", params={"before": "yesterday"})
    bad_status = await http.get("/runs", params={"status": "done"})
    bad_limit = await http.get("/runs", params={"limit": 0})
    huge_limit = await http.get("/runs", params={"limit": 10_000})
    bad_flow = await http.get("/runs", params={"flow": "a/b"})

    assert (no_versions.status_code, _error(no_versions)) == (404, "FlowNotFoundError")
    for response in (no_tasks, no_children, no_before):
        assert (response.status_code, _error(response)) == (404, "RunNotFoundError")
    assert (no_task.status_code, _error(no_task)) == (404, "TaskNotFoundError")
    for response in (bad_before, bad_status, bad_limit, huge_limit, bad_flow):
        assert (response.status_code, _error(response)) == (422, "InvalidValueError")


# The responders' routes: docs/specs/task-completion.md.


async def test_a_pending_task_is_listed_and_completed_over_http(
    http: httpx.AsyncClient,
) -> None:
    run_id = await _started(http)
    published = await http.post(f"/runs/{run_id}/tasks", json={"address": WORK})
    task_id = published.json()["id"]

    listed = await http.get("/queues/default/tasks/pending")
    empty = await http.get("/queues/elsewhere/tasks/pending")
    completed = await http.post(f"/tasks/{task_id}/complete", json={"result": "hi!"})
    again = await http.post(f"/tasks/{task_id}/complete", json={"result": "twice"})
    after = await http.get("/queues/default/tasks/pending")
    task = await http.get(f"/tasks/{task_id}")
    succeeded = await http.post(
        f"/runs/{run_id}/succeed", json={"output": "tasks.work"}
    )

    assert listed.status_code == 200
    (delivery,) = listed.json()["tasks"]
    assert delivery["task"]["id"] == task_id
    assert delivery["task"]["status"] == "pending"
    assert delivery["inputs"] == {"word": "hi"}
    assert (empty.status_code, empty.json()) == (200, {"tasks": []})
    assert (completed.status_code, completed.content) == (204, b"")
    assert (again.status_code, again.json()["error"]) == (409, "TaskStateError")
    assert after.json() == {"tasks": []}
    assert task.json()["status"] == "succeeded"
    assert task.json()["attempts"] == 0
    assert succeeded.json()["output"] == "hi!"


async def test_completions_the_manager_refuses_carry_the_error_they_hit(
    http: httpx.AsyncClient,
) -> None:
    run_id = await _started(http)
    published = await http.post(f"/runs/{run_id}/tasks", json={"address": WORK})
    task_id = published.json()["id"]

    unknown = await http.post(f"/tasks/{uuid.uuid4()}/complete", json={"result": 1})
    not_an_id = await http.post("/tasks/nope/complete", json={"result": 1})
    reserved = await http.post(
        f"/tasks/{task_id}/complete", json={"result": {"$oops": 1}}
    )
    no_body = await http.post(f"/tasks/{task_id}/complete")
    bad_limit = await http.get("/queues/default/tasks/pending", params={"limit": 0})
    bad_queue = await http.get("/queues/a.b/tasks/pending")
    still = await http.get(f"/tasks/{task_id}")
    await http.post(f"/runs/{run_id}/cancel")
    inactive = await http.post(f"/tasks/{task_id}/complete", json={"result": 1})

    assert (unknown.status_code, unknown.json()["error"]) == (404, "TaskNotFoundError")
    assert not_an_id.status_code == 422
    for invalid in (reserved, no_body, bad_queue):
        assert (invalid.status_code, invalid.json()["error"]) == (
            422,
            "InvalidValueError",
        )
    assert bad_limit.status_code == 422
    assert still.json()["status"] == "pending"
    assert (inactive.status_code, inactive.json()["error"]) == (409, "RunStateError")


async def test_a_completion_with_an_error_fails_the_task_over_http(
    http: httpx.AsyncClient,
) -> None:
    run_id = await _started(http)
    published = await http.post(f"/runs/{run_id}/tasks", json={"address": WORK})
    task_id = published.json()["id"]

    failed = await http.post(f"/tasks/{task_id}/complete", json={"error": "refused"})
    task = await http.get(f"/tasks/{task_id}")

    assert failed.status_code == 204
    assert (task.json()["status"], task.json()["error"]) == ("failed", "refused")


async def test_an_external_queue_is_refused_to_workers_over_http(
    manager: Manager, http: httpx.AsyncClient
) -> None:
    await manager.declare_queue("editors", QueueKind.EXTERNAL)

    definitions = await http.get("/queues/editors/tasks")
    received = await http.post("/queues/editors/tasks/receive", params={"timeout": 0})
    pending = await http.get("/queues/editors/tasks/pending")

    for refused in (definitions, received):
        assert (refused.status_code, refused.json()["error"]) == (
            403,
            "PermissionDeniedError",
        )
        assert "queue 'editors' is external" in refused.json()["detail"]
    assert (pending.status_code, pending.json()) == (200, {"tasks": []})


async def test_a_flow_naming_a_queue_nobody_declared_is_refused_over_http(
    manager: Manager, http: httpx.AsyncClient
) -> None:
    on_gpu = {
        "name": "render",
        "version": "1.0.0",
        "steps": {"draw": {"queue": "gpu", "handler": "tasks:draw"}},
    }

    refused = await http.post("/flows", json={"flows": [on_gpu]})
    definitions = await http.get("/queues/gpu/tasks")
    await manager.declare_queue("gpu", QueueKind.WORKER)
    stored = await http.post("/flows", json={"flows": [on_gpu]})
    served = await http.get("/queues/gpu/tasks")

    assert (refused.status_code, refused.json()["error"]) == (
        422,
        "FlowDefinitionError",
    )
    assert refused.json()["problems"] == [
        "render: draw is on queue 'gpu', which is not declared"
    ]
    assert (definitions.status_code, definitions.json()["error"]) == (
        403,
        "PermissionDeniedError",
    )
    assert "queue 'gpu' is not declared" in definitions.json()["detail"]
    assert stored.json() == {"stored": [True]}
    assert [task["name"] for task in served.json()["tasks"]] == ["draw"]


# The routes a page for people reads: the queues, and a queue's tasks.


async def test_the_queues_are_listed_with_their_kinds(
    manager: Manager, http: httpx.AsyncClient
) -> None:
    await manager.declare_queue("editors", QueueKind.EXTERNAL)

    listed = await http.get("/queues")

    assert listed.status_code == 200
    queues = listed.json()["queues"]
    assert [queue["name"] for queue in queues] == sorted(q["name"] for q in queues)
    assert {"name": "editors", "kind": "external"} in queues
    assert {"name": "default", "kind": "worker"} in queues


async def test_a_queues_tasks_are_listed_in_any_status_with_their_flow(
    http: httpx.AsyncClient,
) -> None:
    run_id = await _started(http)
    published = await http.post(f"/runs/{run_id}/tasks", json={"address": WORK})
    task_id = published.json()["id"]
    recent = "/queues/default/tasks/recent"
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    last_year = (datetime.now(UTC) - timedelta(days=365)).isoformat()

    waiting = await http.get(recent)
    await http.post(f"/tasks/{task_id}/complete", json={"result": "hi!"})
    done = await http.get(recent, params={"status": "succeeded", "flow": "main"})
    still_pending = await http.get(recent, params={"status": "pending"})
    of_another_flow = await http.get(recent, params={"flow": "other"})
    since_tomorrow = await http.get(recent, params={"since": tomorrow})
    since_last_year = await http.get(recent, params={"since": last_year, "limit": 1})
    elsewhere = await http.get("/queues/elsewhere/tasks/recent")

    (listed,) = waiting.json()["tasks"]
    assert (listed["flow"], listed["version"]) == ("main", "1.0.0")
    assert (listed["task"]["id"], listed["task"]["status"]) == (task_id, "pending")
    (finished,) = done.json()["tasks"]
    assert (finished["task"]["status"], finished["task"]["result"]) == (
        "succeeded",
        "hi!",
    )
    assert [t["task"]["id"] for t in since_last_year.json()["tasks"]] == [task_id]
    for empty in (still_pending, of_another_flow, since_tomorrow, elsewhere):
        assert (empty.status_code, empty.json()) == (200, {"tasks": []})


async def test_a_listing_of_a_queues_tasks_is_validated(
    http: httpx.AsyncClient,
) -> None:
    recent = "/queues/default/tasks/recent"

    refused = [
        await http.get(recent, params={"status": "done"}),
        await http.get(recent, params={"limit": 0}),
        await http.get(recent, params={"limit": 10_000}),
        await http.get(recent, params={"flow": "a/b"}),
        await http.get(recent, params={"since": "yesterday"}),
        await http.get(recent, params={"since": "2026-11-14T10:00:00"}),
        await http.get("/queues/a.b/tasks/recent"),
    ]

    for response in refused:
        assert (response.status_code, _error(response)) == (422, "InvalidValueError")


async def test_a_task_is_read_with_its_inputs_filled_in(
    http: httpx.AsyncClient,
) -> None:
    run_id = await _started(http)
    published = await http.post(f"/runs/{run_id}/tasks", json={"address": WORK})
    task_id = published.json()["id"]

    read = await http.get(f"/tasks/{task_id}/inputs")
    await http.post(f"/tasks/{task_id}/complete", json={"result": "hi!"})
    after = await http.get(f"/tasks/{task_id}/inputs")
    missing = await http.get(f"/tasks/{uuid.uuid4()}/inputs")

    assert read.status_code == 200
    assert read.json()["task"]["id"] == task_id
    assert read.json()["inputs"] == {"word": "hi"}
    assert (after.json()["task"]["status"], after.json()["inputs"]) == (
        "succeeded",
        {"word": "hi"},
    )
    assert (missing.status_code, _error(missing)) == (404, "TaskNotFoundError")
