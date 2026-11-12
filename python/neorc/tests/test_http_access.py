# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The guard on the manager's routes: API tokens, and writes from other sites.

Over an ASGI transport, on the memory stores. What a token is, and when it is
refused, is settled in core; here, only what HTTP adds: where the token is
read from, the statuses, which routes are guarded, and before what.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncIterator, Iterable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.routing import APIRoute
from starlette.routing import Route

from neorc.manager import create_app
from neorc.manager._access import PERMISSION_ATTRIBUTE
from neorc_core import Access, Manager, Role
from neorc_core.local import MemoryCredentialStore

FLOW = {"name": "f", "version": "1.0.0", "steps": {"work": {"handler": "m:work"}}}
JSON = {"content-type": "application/json"}


@pytest.fixture
def access() -> Access:
    return Access(MemoryCredentialStore())


@pytest.fixture
def static(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text("<!doctype html><title>ui</title>")
    return tmp_path


@pytest.fixture
async def guarded(
    manager: Manager, access: Access, static: Path
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(manager, access=access, long_poll_timeout=1, ui=static)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://manager.test"
    ) as client:
        yield client


@pytest.fixture
async def open_app(manager: Manager) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(manager, access=None, long_poll_timeout=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://manager.test"
    ) as client:
        yield client


def bearer(secret: str) -> dict[str, str]:
    return {"authorization": f"Bearer {secret}", **JSON}


def _error(response: httpx.Response) -> str:
    error: str = response.json()["error"]
    return error


# Tokens.


async def test_a_request_without_a_token_is_refused_and_told_how(
    guarded: httpx.AsyncClient,
) -> None:
    response = await guarded.get("/runs")

    assert (response.status_code, _error(response)) == (401, "AuthenticationError")
    assert response.headers["www-authenticate"] == "Bearer"
    assert "Authorization: Bearer" in response.json()["detail"]


@pytest.mark.parametrize(
    "authorization",
    [
        "Bearer neorc_not-a-token-this-manager-issued",
        "Bearer not-even-shaped-like-one",
        "Basic dXNlcjpwYXNz",
        "neorc_without-a-scheme",
    ],
)
async def test_a_request_with_a_token_not_accepted_is_refused(
    guarded: httpx.AsyncClient, access: Access, authorization: str
) -> None:
    await access.create_token("ci", Role.CI)

    response = await guarded.get("/runs", headers={"authorization": authorization})

    assert (response.status_code, _error(response)) == (401, "AuthenticationError")
    secret = authorization.partition(" ")[2]
    assert not secret or secret not in response.text


async def test_a_token_is_accepted_on_the_routes_its_role_may_use(
    guarded: httpx.AsyncClient, access: Access
) -> None:
    ci, _ = await access.create_token("ci", Role.CI)
    user, _ = await access.create_token("person", Role.USER)
    worker, _ = await access.create_token("worker-1", Role.WORKER, queue="default")

    uploaded = await guarded.post("/flows", json={"flows": [FLOW]}, headers=bearer(ci))
    run = await guarded.post("/flows/f/runs", json={"inputs": {}}, headers=bearer(user))
    listed = await guarded.get("/runs", headers=bearer(user))
    received = await guarded.post(
        "/queues/default/tasks/receive", params={"timeout": 0}, headers=bearer(worker)
    )
    events = await guarded.get("/events/latest", headers=bearer(user))

    assert uploaded.status_code == 200
    assert run.status_code == 201
    assert listed.json()["runs"][0]["id"] == run.json()["id"]
    assert received.status_code == 204
    assert events.status_code == 200


async def test_a_revoked_or_expired_token_is_refused(
    guarded: httpx.AsyncClient, access: Access
) -> None:
    revoked, _ = await access.create_token("revoked", Role.CI)
    brief, _ = await access.create_token("brief", Role.CI, expires_seconds=0.1)
    await access.revoke_token("revoked")
    await asyncio.sleep(0.2)

    for secret in (revoked, brief):
        response = await guarded.get("/runs", headers=bearer(secret))
        assert response.status_code == 401


async def test_the_token_is_checked_before_the_request_is_read(
    guarded: httpx.AsyncClient,
) -> None:
    malformed = await guarded.post("/flows", content=b"{not json", headers=JSON)
    bad_id = await guarded.get("/runs/not-a-uuid")
    bad_query = await guarded.get("/runs", params={"limit": "many"})

    for response in (malformed, bad_id, bad_query):
        assert (response.status_code, _error(response)) == (401, "AuthenticationError")


async def test_what_holds_no_data_is_open_without_a_token(
    guarded: httpx.AsyncClient,
) -> None:
    health = await guarded.get("/health")
    root = await guarded.get("/")
    page = await guarded.get("/ui/")
    schema = await guarded.get("/openapi.json")

    assert health.status_code == 200
    assert root.status_code == 307
    assert page.status_code == 200
    assert schema.status_code == 200
    assert "HTTPBearer" in schema.json()["components"]["securitySchemes"]


async def test_the_documentation_pages_are_served_only_without_authentication(
    guarded: httpx.AsyncClient, open_app: httpx.AsyncClient
) -> None:
    for path in ("/docs", "/redoc"):
        assert (await guarded.get(path)).status_code == 404
        assert (await open_app.get(path)).status_code == 200


PUBLIC = {
    "/health",
    "/openapi.json",
    "/auth/session",
    "/auth/login/{provider}",
    "/auth/callback/{provider}",
    "/auth/logout",
}
"""What a person with no session reaches: to sign in, and out."""


def every_route(routes: Iterable[Any], prefix: str = "") -> Iterator[tuple[str, str]]:
    """Every method and path the app routes, whether the schema shows it or not.

    FastAPI keeps an included router whole in the app's routes, from 0.141,
    rather than copying its routes in; both shapes are walked.
    """
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from every_route(
                included.routes, prefix + route.include_context.prefix
            )
        elif isinstance(route, Route):
            for method in sorted(route.methods or ()):
                yield method, prefix + route.path


async def test_every_route_but_the_public_ones_needs_a_token(
    manager: Manager, access: Access, guarded: httpx.AsyncClient
) -> None:
    """Asked of the app's routes, not its schema, which leaves some out."""
    app = create_app(manager, access=access)
    samples = {"run_id": str(uuid.uuid4()), "task_id": str(uuid.uuid4())}
    operations = sorted(set(every_route(app.routes)) - {("HEAD", "/openapi.json")})
    paths = {path for _, path in operations}
    assert len(operations) > 20
    assert {"/auth/login/{provider}", "/auth/callback/{provider}"} <= paths

    for method, path in operations:
        url = re.sub(
            r"{(\w+)}", lambda m: samples.get(m.group(1), "sample"), path
        ).replace("/versions/sample", "/versions/1.0.0")
        response = await guarded.request(method, url, headers=JSON)
        if path in PUBLIC:
            assert response.status_code != 401, f"{method} {path}"
        else:
            assert response.status_code == 401, f"{method} {path}"


# Permissions.

ROUTE_PERMISSIONS = {
    ("POST", "/flows"): "flows:upload",
    ("GET", "/flows"): "flows:read",
    ("GET", "/flows/{name}"): "flows:read",
    ("GET", "/flows/{name}/versions"): "flows:read",
    ("GET", "/flows/{name}/versions/{version}"): "flows:read",
    ("POST", "/flows/{name}/runs"): "runs:start",
    ("GET", "/runs"): "runs:read",
    ("GET", "/runs/{run_id}"): "runs:read",
    ("GET", "/runs/{run_id}/tasks"): "runs:read",
    ("GET", "/runs/{run_id}/sub-runs"): "runs:read",
    ("GET", "/runs/{run_id}/state"): "runs:read",
    ("GET", "/tasks/{task_id}"): "runs:read",
    ("POST", "/runs/{run_id}/cancel"): "runs:cancel",
    ("POST", "/runs/{run_id}/tasks"): "runs:schedule",
    ("POST", "/runs/{run_id}/sub-runs"): "runs:schedule",
    ("POST", "/runs/{run_id}/succeed"): "runs:schedule",
    ("POST", "/runs/{run_id}/fail"): "runs:schedule",
    ("GET", "/events"): "events:read",
    ("GET", "/events/latest"): "events:read",
    ("GET", "/queues/{queue}/tasks"): "queue:definitions",
    ("POST", "/queues/{queue}/tasks/receive"): "queue:receive",
    ("POST", "/tasks/{task_id}/claim"): "tasks:claim",
    ("POST", "/tasks/{task_id}/heartbeat"): "tasks:heartbeat",
    ("POST", "/tasks/{task_id}/finished"): "tasks:finish",
    ("GET", "/queues/{queue}/tasks/pending"): "queue:pending",
    ("POST", "/tasks/{task_id}/complete"): "tasks:complete",
}
"""The plan's table of routes, written out rather than read from the routes."""

ALLOWED = {
    Role.WORKER: {
        "queue:definitions",
        "queue:receive",
        "tasks:claim",
        "tasks:heartbeat",
        "tasks:finish",
    },
    Role.RESPONDER: {"queue:pending", "tasks:complete"},
    Role.SCHEDULER: {"flows:read", "runs:read", "runs:schedule", "events:read"},
    Role.CI: {"flows:upload"},
    Role.USER: {"flows:read", "runs:start", "runs:read", "runs:cancel", "events:read"},
    Role.EXTERNAL_TRIGGER: {"runs:start"},
}


def permissions_asked(dependant: Any) -> list[str]:
    """Every permission a route's dependencies ask for, at any depth."""
    asked = []
    for dependency in dependant.dependencies:
        permission = getattr(dependency.call, PERMISSION_ATTRIBUTE, None)
        if permission is not None:
            asked.append(permission.value)
        asked.extend(permissions_asked(dependency))
    return asked


def guarded_routes(routes: Iterable[Any]) -> Iterator[tuple[str, str, Any]]:
    """Method, path and route of every route in the guarded router."""
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from guarded_routes(included.routes)
        elif isinstance(route, APIRoute) and route.path not in PUBLIC:
            for method in sorted(route.methods or ()):
                yield method, route.path, route


def test_every_route_asks_for_exactly_the_permission_it_needs(
    manager: Manager, access: Access
) -> None:
    app = create_app(manager, access=access)

    asked = {
        (method, path): permissions_asked(route.dependant)
        for method, path, route in guarded_routes(app.routes)
    }

    assert asked == {key: [value] for key, value in ROUTE_PERMISSIONS.items()}


def sample_url(path: str, queue: str = "default") -> str:
    samples = {
        "run_id": str(uuid.uuid4()),
        "task_id": str(uuid.uuid4()),
        "queue": queue,
        "name": "f",
        "version": "1.0.0",
    }
    return re.sub(r"{(\w+)}", lambda m: samples[m.group(1)], path)


@pytest.mark.parametrize("role", list(Role))
async def test_a_role_reaches_its_routes_and_is_refused_the_rest(
    guarded: httpx.AsyncClient, access: Access, role: Role
) -> None:
    queue = "default" if role in (Role.WORKER, Role.RESPONDER) else None
    secret, _ = await access.create_token("t", role, queue=queue)

    for (method, path), permission in ROUTE_PERMISSIONS.items():
        response = await guarded.request(
            method, sample_url(path), params={"timeout": 0}, headers=bearer(secret)
        )
        where = f"{role.value}: {method} {path} answered {response.status_code}"
        if permission in ALLOWED[role]:
            assert response.status_code not in (401, 403), where
        else:
            assert response.status_code == 403, where
            assert _error(response) == "PermissionDeniedError", where
            assert permission in response.json()["detail"], where


async def test_a_worker_is_refused_another_queue_and_does_not_see_its_tasks(
    guarded: httpx.AsyncClient, access: Access
) -> None:
    ci, _ = await access.create_token("ci", Role.CI)
    user, _ = await access.create_token("person", Role.USER)
    scheduler, _ = await access.create_token("scheduler", Role.SCHEDULER)
    default, _ = await access.create_token("w1", Role.WORKER, queue="default")
    other, _ = await access.create_token("w2", Role.WORKER, queue="other")
    flow = {"name": "f", "version": "1.0.0", "steps": {"work": {"handler": "m:w"}}}
    await guarded.post("/flows", json={"flows": [flow]}, headers=bearer(ci))
    run = await guarded.post("/flows/f/runs", json={"inputs": {}}, headers=bearer(user))
    published = await guarded.post(
        f"/runs/{run.json()['id']}/tasks",
        json={"address": {"step": "work", "scope": []}},
        headers=bearer(scheduler),
    )
    task_id = published.json()["id"]

    for path in ("/queues/default/tasks", "/queues/default/tasks/receive"):
        method = "GET" if path.endswith("/tasks") else "POST"
        refused = await guarded.request(method, path, headers=bearer(other))
        assert (refused.status_code, _error(refused)) == (403, "PermissionDeniedError")
        assert "bound to queue 'other'" in refused.json()["detail"]
    received = await guarded.post(
        "/queues/default/tasks/receive", params={"timeout": 0}, headers=bearer(default)
    )
    assert received.json()["task"]["id"] == task_id
    for action, body in (
        ("claim", None),
        ("heartbeat", {"lease_seconds": 30}),
        ("finished", {"result": "done"}),
    ):
        hidden = await guarded.post(
            f"/tasks/{task_id}/{action}", json=body, headers=bearer(other)
        )
        assert (hidden.status_code, _error(hidden)) == (404, "TaskNotFoundError")
    for action, body in (
        ("claim", None),
        ("heartbeat", {"lease_seconds": 30}),
        ("finished", {"result": "done"}),
    ):
        done = await guarded.post(
            f"/tasks/{task_id}/{action}", json=body, headers=bearer(default)
        )
        assert done.status_code in (200, 204), action
    task = await guarded.get(f"/tasks/{task_id}", headers=bearer(user))
    assert task.json()["status"] == "succeeded"


async def test_a_responder_is_confined_to_its_queue_and_a_worker_cannot_complete(
    guarded: httpx.AsyncClient, access: Access
) -> None:
    ci, _ = await access.create_token("ci", Role.CI)
    user, _ = await access.create_token("person", Role.USER)
    scheduler, _ = await access.create_token("scheduler", Role.SCHEDULER)
    worker, _ = await access.create_token("w", Role.WORKER, queue="default")
    responder, _ = await access.create_token("r1", Role.RESPONDER, queue="default")
    other, _ = await access.create_token("r2", Role.RESPONDER, queue="other")
    flow = {"name": "f", "version": "1.0.0", "steps": {"work": {"handler": "m:w"}}}
    await guarded.post("/flows", json={"flows": [flow]}, headers=bearer(ci))
    run = await guarded.post("/flows/f/runs", json={"inputs": {}}, headers=bearer(user))
    published = await guarded.post(
        f"/runs/{run.json()['id']}/tasks",
        json={"address": {"step": "work", "scope": []}},
        headers=bearer(scheduler),
    )
    task_id = published.json()["id"]
    done = {"result": "done"}

    elsewhere = await guarded.get(
        "/queues/default/tasks/pending", headers=bearer(other)
    )
    assert (elsewhere.status_code, _error(elsewhere)) == (403, "PermissionDeniedError")
    assert "bound to queue 'other'" in elsewhere.json()["detail"]
    hidden = await guarded.post(
        f"/tasks/{task_id}/complete", json=done, headers=bearer(other)
    )
    assert (hidden.status_code, _error(hidden)) == (404, "TaskNotFoundError")
    for secret, role in ((worker, "worker"), (user, "user"), (scheduler, "scheduler")):
        refused = await guarded.post(
            f"/tasks/{task_id}/complete", json=done, headers=bearer(secret)
        )
        assert (refused.status_code, _error(refused)) == (403, "PermissionDeniedError")
        assert f"a {role} token may not tasks:complete" in refused.json()["detail"]
    for path in ("/queues/default/tasks", "/queues/default/tasks/receive"):
        method = "GET" if path.endswith("/tasks") else "POST"
        refused = await guarded.request(method, path, headers=bearer(responder))
        assert (refused.status_code, _error(refused)) == (403, "PermissionDeniedError")

    listed = await guarded.get(
        "/queues/default/tasks/pending", headers=bearer(responder)
    )
    assert [d["task"]["id"] for d in listed.json()["tasks"]] == [task_id]
    completed = await guarded.post(
        f"/tasks/{task_id}/complete", json=done, headers=bearer(responder)
    )
    assert completed.status_code == 204
    task = await guarded.get(f"/tasks/{task_id}", headers=bearer(user))
    assert (task.json()["status"], task.json()["result"]) == ("succeeded", "done")


async def test_with_authentication_off_every_route_is_open(
    open_app: httpx.AsyncClient,
) -> None:
    for method, path in ROUTE_PERMISSIONS:
        response = await open_app.request(
            method, sample_url(path), params={"timeout": 0}, headers=JSON
        )
        assert response.status_code not in (401, 403), f"{method} {path}"


# Writes from other sites, with authentication off.


@pytest.mark.parametrize("site", ["cross-site", "same-site", "Cross-Site"])
async def test_a_write_a_page_on_another_site_sent_is_refused(
    open_app: httpx.AsyncClient, site: str
) -> None:
    await open_app.post("/flows", json={"flows": [FLOW]})
    run = await open_app.post("/flows/f/runs", json={"inputs": {}})

    cancelled = await open_app.post(
        f"/runs/{run.json()['id']}/cancel",
        headers={"sec-fetch-site": site, **JSON},
    )

    assert (cancelled.status_code, _error(cancelled)) == (403, "CrossSiteRequestError")
    status = (await open_app.get(f"/runs/{run.json()['id']}")).json()["status"]
    assert status == "active"


@pytest.mark.parametrize("site", ["same-origin", "none"])
async def test_a_write_from_the_page_itself_or_typed_in_is_taken(
    open_app: httpx.AsyncClient, site: str
) -> None:
    response = await open_app.post(
        "/flows", json={"flows": [FLOW]}, headers={"sec-fetch-site": site}
    )

    assert response.status_code == 200


async def test_a_read_is_not_refused_for_where_it_came_from(
    open_app: httpx.AsyncClient,
) -> None:
    response = await open_app.get("/runs", headers={"sec-fetch-site": "cross-site"})

    assert response.status_code == 200


@pytest.mark.parametrize(
    "content_type",
    [None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data"],
)
async def test_a_write_a_form_could_send_is_refused_with_no_body_or_fetch_metadata(
    open_app: httpx.AsyncClient, content_type: str | None
) -> None:
    """What a browser that sends no Sec-Fetch-Site lets any page send."""
    await open_app.post("/flows", json={"flows": [FLOW]})
    run = await open_app.post("/flows/f/runs", json={"inputs": {}})
    headers = {} if content_type is None else {"content-type": content_type}

    cancelled = await open_app.post(f"/runs/{run.json()['id']}/cancel", headers=headers)
    received = await open_app.post(
        "/queues/default/tasks/receive",
        params={"timeout": 0, "lease_seconds": 1e9},
        headers=headers,
    )
    claimed = await open_app.post(f"/tasks/{uuid.uuid4()}/claim", headers=headers)
    uploaded = await open_app.post("/flows", content=b'{"flows": []}', headers=headers)

    for response in (cancelled, received, claimed, uploaded):
        assert (response.status_code, _error(response)) == (
            415,
            "UnsupportedMediaTypeError",
        )
    status = (await open_app.get(f"/runs/{run.json()['id']}")).json()["status"]
    assert status == "active"


async def test_json_with_parameters_is_json(open_app: httpx.AsyncClient) -> None:
    response = await open_app.post(
        "/flows",
        content=b'{"flows": []}',
        headers={"content-type": "Application/JSON; charset=utf-8"},
    )

    assert response.status_code == 200


async def test_a_cross_site_write_is_refused_before_its_token_is_looked_at(
    guarded: httpx.AsyncClient,
) -> None:
    response = await guarded.post(
        "/flows", json={"flows": []}, headers={"sec-fetch-site": "cross-site"}
    )

    assert (response.status_code, _error(response)) == (403, "CrossSiteRequestError")
