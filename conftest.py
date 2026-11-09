# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Fixtures shared by every package's tests.

They live at the root because pytest and mypy see the whole repository as one
run, and two directories cannot both hold a module called ``conftest``.

The Postgres fixtures run against a real server. Point
``NEORC_TEST_DATABASE_URL`` at one to use your own; otherwise a private server
is started for the session with ``pgserver``, so a plain ``uv sync && uv run
pytest`` needs no Docker and no system Postgres. Where neither is possible,
those tests skip.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import sys
import time
import uuid
from collections.abc import AsyncIterator, Iterable, Iterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote

import pytest

# At module level: FastAPI resolves the stand-in provider's annotations here.
from starlette.requests import Request

from neorc_core import Access, Manager, ManagerClient, Role
from neorc_core.local import MemoryStore, MemoryTaskNotifier

if TYPE_CHECKING:
    from neorc.postgres import (
        PostgresCredentialStore,
        PostgresStore,
        PostgresTaskNotifier,
    )

# Before a test module imports the contract suites, so their asserts explain a
# failure the way asserts in test modules do.
pytest.register_assert_rewrite("neorc_core.testing.contracts")
pytest.register_assert_rewrite("neorc_core.testing.examples")

DATABASE_URL_ENV = "NEORC_TEST_DATABASE_URL"


@pytest.fixture
def manager() -> Manager:
    """A manager on the memory store, holding nothing."""
    return Manager(
        MemoryStore(), tasks=MemoryTaskNotifier(), events=MemoryTaskNotifier()
    )


@pytest.fixture(scope="session")
def database_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A Postgres to test against, from the environment or started for us."""
    configured = os.environ.get(DATABASE_URL_ENV)
    if configured:
        yield configured
        return

    pgserver = pytest.importorskip(
        "pgserver", reason=f"no Postgres: set {DATABASE_URL_ENV} or install pgserver"
    )
    data_dir: Path = tmp_path_factory.mktemp("pgdata")
    server = pgserver.get_server(str(data_dir), cleanup_mode="stop")
    try:
        yield str(server.get_uri())
    finally:
        server.cleanup()


@pytest.fixture
async def pg_schema(database_url: str) -> str:
    """The database URL, its neorc tables created and emptied."""
    from neorc.postgres import create_schema
    from neorc.postgres._driver import connected
    from neorc.postgres._schema import TABLES

    await create_schema(database_url)
    async with connected(database_url) as conn:
        await conn.execute(f"TRUNCATE {', '.join(TABLES)}")
    return database_url


@pytest.fixture
async def pg_store(pg_schema: str) -> AsyncIterator[PostgresStore]:
    """An open store for flows on empty flow tables."""
    from neorc.postgres import PostgresStore

    async with PostgresStore(pg_schema) as store:
        yield store


@pytest.fixture
async def pg_credentials(pg_schema: str) -> AsyncIterator[PostgresCredentialStore]:
    """An open credential store on empty credential tables."""
    from neorc.postgres import PostgresCredentialStore

    async with PostgresCredentialStore(pg_schema) as credentials:
        yield credentials


@pytest.fixture
async def pg_notifier(database_url: str) -> AsyncIterator[PostgresTaskNotifier]:
    from neorc.postgres import PostgresTaskNotifier

    async with PostgresTaskNotifier(database_url) as notifier:
        yield notifier


# A manager on a real socket, and the examples deployed against it.

EXAMPLES = Path(__file__).parent / "examples"


def free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@asynccontextmanager
async def serve_app(app: Any, *, port: int | None = None) -> AsyncIterator[str]:
    """Serve an ASGI app on uvicorn, at ``port`` or a free one, for the block.

    Yields its address. A port chosen first lets an app be told its own
    address before it is served, as a public URL is.
    """
    import uvicorn

    port = free_port() if port is None else port
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"
    )
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(20):
            while not server.started:
                await asyncio.sleep(0.05)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(20):
                await serving


@pytest.fixture
def own_tasks_module(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Each example has a ``tasks`` module: import this one's, not another's."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "tasks", raising=False)
    yield
    sys.modules.pop("tasks", None)


@dataclass(frozen=True)
class Tokens:
    """A token for each process of a deployment, of its role, as roles.md has it."""

    ci: str
    """Uploads flows."""
    scheduler: str
    user: str
    """Starts runs and watches them, as a person's script would."""
    workers: Mapping[str, str]
    """A worker token per queue, bound to it."""


async def role_tokens(access: Access, queues: Iterable[str]) -> Tokens:
    """A token of each role on ``access``, and a worker token for each queue."""
    ci, _ = await access.create_token("ci", Role.CI)
    scheduler, _ = await access.create_token("scheduler", Role.SCHEDULER)
    user, _ = await access.create_token("user", Role.USER)
    workers = {}
    for queue in queues:
        workers[queue], _ = await access.create_token(
            f"worker-{queue}", Role.WORKER, queue=queue
        )
    return Tokens(ci, scheduler, user, workers)


@asynccontextmanager
async def deployed_example(
    manager_address: str,
    example: str,
    queues: list[str],
    *,
    scheduler: bool = True,
    tokens: Tokens | None = None,
) -> AsyncIterator[ManagerClient]:
    """A scheduler and a worker per queue on the HTTP clients, for the block.

    The client yielded reaches the same manager, to upload and start runs.
    Without ``scheduler``, only the workers: for more workers beside a block
    that already runs the deployment's one scheduler. With ``tokens``, each
    client sends the token of its role: the client yielded a user's, and a
    CI token for uploads.
    """
    from neorc.http import HttpManagerClient, HttpQueueClient
    from neorc_core import Scheduler, Worker

    def token(role: str, queue: str | None = None) -> str | None:
        if tokens is None:
            return None
        return tokens.workers[queue] if queue is not None else getattr(tokens, role)

    class Deploying(HttpManagerClient):
        """A person's client, uploading as CI/CD does, with CI's token."""

        def __init__(self, uploader: HttpManagerClient) -> None:
            super().__init__(manager_address, poll_timeout=2, token=token("user"))
            self._uploader = uploader

        async def upload_flows(self, contents: Any) -> list[bool]:
            return await self._uploader.upload_flows(contents)

    async with contextlib.AsyncExitStack() as stack:
        uploader = await stack.enter_async_context(
            HttpManagerClient(manager_address, poll_timeout=2, token=token("ci"))
        )
        client = await stack.enter_async_context(Deploying(uploader))
        schedulers = []
        if scheduler:
            schedulers.append(
                Scheduler(
                    await stack.enter_async_context(
                        HttpManagerClient(
                            manager_address, poll_timeout=2, token=token("scheduler")
                        )
                    ),
                    poll_timeout=2,
                )
            )
        workers = [
            Worker(
                await stack.enter_async_context(
                    HttpQueueClient(
                        manager_address, poll_timeout=2, token=token("worker", queue)
                    )
                ),
                queue=queue,
                code_location=EXAMPLES / example,
                poll_timeout=2,
                lease_seconds=5,
            )
            for queue in queues
        ]
        running = [asyncio.create_task(s.run()) for s in schedulers]
        running += [asyncio.create_task(worker.run()) for worker in workers]
        try:
            yield client
        finally:
            for s in schedulers:
                s.stop()
            for worker in workers:
                worker.stop()
            await asyncio.wait_for(
                asyncio.gather(*running, return_exceptions=True), timeout=30
            )


# A stand-in OpenID Connect provider, for signing in without a real one.


class StandInProvider:
    """An OpenID Connect provider in an ASGI app, issuing unsigned ID tokens.

    ``person`` is who signs in next: the claims the ID token carries, beside
    the ones the flow sets. ``tamper`` overrides any claim, to make an ID token
    the manager must refuse. What the provider was asked is recorded, so a
    test can check the manager sent what it should.
    """

    def __init__(
        self,
        issuer: str,
        *,
        client_id: str = "neorc-client",
        client_secret: str = "stand-in-secret",
    ) -> None:
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse, RedirectResponse

        self.issuer = issuer
        base = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.person: dict[str, Any] = {
            "sub": "248289761001",
            "name": "Ada Lovelace",
            "email": "ada@example.com",
            "email_verified": True,
        }
        self.tamper: dict[str, Any] = {}
        self.discovery: dict[str, Any] = {}
        self.unavailable = 0
        """How many discovery requests to fail before answering."""
        self.authorized: list[dict[str, str]] = []
        self.exchanged: list[dict[str, str]] = []
        self._codes: dict[str, dict[str, str]] = {}

        app = FastAPI()
        self.app = app

        @app.get("/.well-known/openid-configuration")
        async def discovery() -> JSONResponse:
            if self.unavailable > 0:
                self.unavailable -= 1
                return JSONResponse({"error": "down"}, status_code=503)
            return JSONResponse(
                {
                    "issuer": self.issuer,
                    "authorization_endpoint": f"{base}/authorize",
                    "token_endpoint": f"{base}/token",
                    "response_types_supported": ["code"],
                    **self.discovery,
                }
            )

        @app.get("/authorize")
        async def authorize(request: Request) -> RedirectResponse:
            asked = dict(request.query_params)
            self.authorized.append(asked)
            code = uuid.uuid4().hex
            self._codes[code] = asked
            return RedirectResponse(
                f"{asked['redirect_uri']}?code={code}&state={asked['state']}",
                status_code=303,
            )

        @app.post("/token")
        async def token(request: Request) -> JSONResponse:
            # Parsed here: Starlette's form parsing needs python-multipart.
            form = parse_qs((await request.body()).decode())
            asked = {key: values[0] for key, values in form.items()}
            asked["authorization"] = request.headers.get("authorization", "")
            self.exchanged.append(asked)
            authorized = self._codes.pop(asked.get("code", ""), None)
            if authorized is None:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            if not self._client_authenticated(asked):
                return JSONResponse({"error": "invalid_client"}, status_code=401)
            challenge = (
                base64.urlsafe_b64encode(
                    hashlib.sha256(asked.get("code_verifier", "").encode()).digest()
                )
                .rstrip(b"=")
                .decode()
            )
            if (
                challenge != authorized["code_challenge"]
                or asked.get("redirect_uri") != authorized["redirect_uri"]
            ):
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            now = int(time.time())
            claims = {
                "iss": self.issuer,
                "aud": self.client_id,
                "iat": now,
                "exp": now + 300,
                "nonce": authorized["nonce"],
                **self.person,
                **self.tamper,
            }
            claims = {key: value for key, value in claims.items() if value is not None}
            return JSONResponse(
                {
                    "access_token": "stand-in-access-token",
                    "token_type": "Bearer",
                    "id_token": unsigned_jwt(claims),
                }
            )

    def _client_authenticated(self, asked: Mapping[str, str]) -> bool:
        if asked["authorization"].startswith("Basic "):
            pair = base64.b64decode(asked["authorization"][6:]).decode()
            client_id, _, secret = pair.partition(":")
            return (unquote(client_id), unquote(secret)) == (
                self.client_id,
                self.client_secret,
            )
        return (asked.get("client_id"), asked.get("client_secret")) == (
            self.client_id,
            self.client_secret,
        )


def unsigned_jwt(claims: Mapping[str, Any]) -> str:
    """A JWT with ``claims`` and no signature, as the stand-in issues them."""

    def part(value: Mapping[str, Any]) -> str:
        return (
            base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()
        )

    return f"{part({'alg': 'none', 'typ': 'JWT'})}.{part(claims)}."
