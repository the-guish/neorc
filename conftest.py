# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Fixtures shared by every package's tests.

They live at the root because pytest and mypy see the whole repository as one
run, and two directories cannot both hold a module called ``conftest``.

A manager is served as oneberth composes it: the ``neorc`` component, its
routes under ``/neorc/``, and oneberth's store of people and tokens. Tests
sign in and make tokens through ``oneberth.web.testing``.

The Postgres fixtures run against a real server. Point
``NEORC_TEST_DATABASE_URL`` at one to use your own; otherwise a private server
is started for the session with ``pgserver``, so a plain ``uv sync && uv run
pytest`` needs no Docker and no system Postgres. Where neither is possible,
those tests skip.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import sys
import uuid
from collections.abc import AsyncIterator, Iterable, Iterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from neorc_core import (
    InvalidValueError,
    Manager,
    ManagerClient,
    QueueKind,
    Role,
    TaskNotFoundError,
    check_token_queue,
)
from neorc_core.local import MemoryStore, MemoryTaskNotifier

if TYPE_CHECKING:
    import httpx
    from oneberth.identity import Identity, IdentityStore
    from oneberth.web import Composition

    from neorc.postgres import PostgresStore, PostgresTaskNotifier

# Before a test module imports the contract suites, so their asserts explain a
# failure the way asserts in test modules do.
pytest.register_assert_rewrite("neorc_core.testing.contracts")
pytest.register_assert_rewrite("neorc_core.testing.examples")

DATABASE_URL_ENV = "NEORC_TEST_DATABASE_URL"


@pytest.fixture
def manager() -> Manager:
    """A manager on the memory store, holding nothing."""
    return Manager(
        MemoryStore(
            queues=dict.fromkeys(
                (
                    "default",
                    "voice",
                    "other",
                    "scoring",
                    "elsewhere",
                ),
                QueueKind.WORKER,
            )
        ),
        tasks=MemoryTaskNotifier(),
        events=MemoryTaskNotifier(),
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
    """The database URL, its neorc tables created and emptied, and no oneberth store.

    A manager composed on it sets oneberth's store up afresh with
    ``create_schema``.
    """
    from neorc.postgres import create_schema
    from neorc.postgres._driver import connected
    from neorc.postgres._schema import TABLES

    await create_schema(database_url)
    async with connected(database_url) as conn:
        await conn.execute(f"TRUNCATE {', '.join(TABLES)}")
        await conn.execute("DROP SCHEMA IF EXISTS oneberth CASCADE")
    return database_url


async def declare_queues(
    database_url: str, *names: str, kind: QueueKind = QueueKind.WORKER
) -> None:
    """Declare queues on the database, as ``neorc queues declare`` does."""
    from neorc.postgres import PostgresStore

    async with PostgresStore(database_url, max_size=1) as store:
        for name in names:
            await store.declare_queue(name, kind)


@pytest.fixture
async def pg_store(pg_schema: str) -> AsyncIterator[PostgresStore]:
    """An open store for flows on empty flow tables."""
    from neorc.postgres import PostgresStore

    async with PostgresStore(pg_schema) as store:
        yield store


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


# A manager composed by oneberth, and the credentials its callers send.

PUBLIC_URL = "http://127.0.0.1:8000"
"""The public URL of a manager composed in process."""


async def identity_store() -> IdentityStore:
    """oneberth's store in memory, set up."""
    from oneberth.identity.memory import MemoryIdentityStore

    store = MemoryIdentityStore()
    await store.setup()
    return store


async def composed(
    manager: Manager,
    *,
    public_url: str = PUBLIC_URL,
    long_poll_timeout: float = 2,
    interface: Path | None = None,
) -> Composition:
    """``manager``'s component, composed alone on oneberth's store in memory.

    Start it with ``composition.lifespan()``, or ``oneberth.web.testing.running``.
    """
    from oneberth.web import compose_app
    from oneberth.web.testing import config_for

    from neorc.manager import new_manager_webapp

    return compose_app(
        [new_manager_webapp(manager=manager, long_poll_timeout=long_poll_timeout)],
        config=config_for("neorc", public_url=public_url),
        store=await identity_store(),
        interface=interface,
    )


async def person_token(
    composition: Composition, identity: Identity | None = None
) -> str:
    """A personal token of a person signed in, Ada by default, as a script sends it."""
    from oneberth.web.testing import ADA

    done = await composition.access.sign_in(identity or ADA)
    secret, _ = await composition.access.mint_personal_token(done.principal, "script")
    return secret


async def service_token(
    composition: Composition, role: Role, queue: str | None = None
) -> str:
    """A service token of ``role``, bound to ``queue``, as the operator makes one."""
    from oneberth.web.testing import service_token as made

    name = (
        f"{role.value.removeprefix('neorc/')}-{queue or 'any'}-{uuid.uuid4().hex[:6]}"
    )
    return await made(composition, role.value, queue, name=name)


async def kept_service_token(composition: Composition, role: Role, queue: str) -> str:
    """A service token of ``role``, bound to ``queue``, kept from before the queue
    changed.

    oneberth refuses a new token for a queue nobody declared, and a new worker
    token for an external queue. A token made earlier is still in its store:
    neorc's records were reset beside oneberth's, or a declaration changed
    while the token was made. Such a token meets the manager's own refusal.
    """
    from oneberth.identity import (
        CredentialKind,
        Grant,
        ServiceToken,
        new_secret,
        secret_hash,
    )

    secret = new_secret(CredentialKind.SERVICE_TOKEN)
    access = composition.access
    await access.store.add_service_token(
        ServiceToken(
            id=uuid.uuid4(),
            name=f"kept-{role.value.removeprefix('neorc/')}-{uuid.uuid4().hex[:6]}",
            grant=Grant(role.value, queue),
            secret_hash=secret_hash(secret),
            created_at=access.now(),
        )
    )
    return secret


@dataclass(frozen=True)
class Tokens:
    """A token for each process of a deployment, of its role, as roles.md has it."""

    ci: str
    """Uploads flows."""
    scheduler: str
    user: str
    """Starts runs and watches them: a person's own token, as their script sends."""
    workers: Mapping[str, str]
    """A worker token per queue, bound to it."""


async def role_tokens(
    composition: Composition, queues: Iterable[str], *, user: str | None = None
) -> Tokens:
    """A token of each role on a started composition, and a worker token per queue.

    ``user`` is the person's token, Ada's by default: in the local mode, the
    local user's, made on the account page.
    """
    ci = await service_token(composition, Role.CI)
    scheduler = await service_token(composition, Role.SCHEDULER)
    user = user or await person_token(composition)
    workers = {}
    for queue in queues:
        workers[queue] = await service_token(composition, Role.WORKER, queue)
    return Tokens(ci, scheduler, user, workers)


_WORKER_PATHS = re.compile(r"/(?:queues/(?P<queue>[^/]+)|tasks/(?P<task>[^/]+))/")


def role_tokens_transport(
    composition: Composition,
    manager: Manager | None = None,
    *,
    expect: str | None = None,
) -> httpx.AsyncBaseTransport:
    """A transport onto ``composition`` that sends each request with a token of a
    role that may make it; see ``RoleTokens``.

    ``manager`` says which queue a task named by id is on; without it, a
    worker's request about a task is sent with a token of ``default``. With
    it, a request on a queue that oneberth would refuse a new token for is sent
    with a token kept from before (``kept_service_token``), so that the request
    meets the manager's own refusal.
    """
    import httpx

    class RoleTokens(httpx.AsyncBaseTransport):
        """Sends each request on with a token of the role that may make it.

        A deployment gives each process a token of its own role; a test drives
        every request through one client, so here the role is picked per
        request, as the manager's routes say: a worker's bound to the queue in
        the path, or the queue of the task named. With ``expect``, every request
        must arrive carrying that one token, which is swapped.
        """

        def __init__(self) -> None:
            self._inner = httpx.ASGITransport(app=composition)
            self._tokens: dict[tuple[Role, str | None], str] = {}

        async def _token(self, role: Role, queue: str | None = None) -> str:
            if (role, queue) not in self._tokens:
                if role is Role.USER:
                    self._tokens[role, queue] = await person_token(composition)
                elif queue is not None and await self._refused(role, queue):
                    self._tokens[role, queue] = await kept_service_token(
                        composition, role, queue
                    )
                else:
                    self._tokens[role, queue] = await service_token(
                        composition, role, queue
                    )
            return self._tokens[role, queue]

        async def _refused(self, role: Role, queue: str) -> bool:
            if manager is None:
                return False
            try:
                await check_token_queue(role, queue, manager.queue_kind)
            except InvalidValueError:
                return True
            return False

        async def _role_token(self, request: httpx.Request) -> str:
            path, method = request.url.path.removeprefix("/neorc"), request.method
            if path.startswith("/queues/") and method == "PUT":
                return await self._token(Role.CI)
            worker = _WORKER_PATHS.match(path + "/")
            if worker and worker["queue"] and not path.endswith("/recent"):
                if path.endswith("/pending"):
                    return await self._token(Role.RESPONDER, worker["queue"])
                return await self._token(Role.WORKER, worker["queue"])
            if worker and worker["task"] and method == "POST":
                if path.endswith("/complete"):
                    return await self._token(Role.USER)
                queue = "default"
                with contextlib.suppress(ValueError, TaskNotFoundError):
                    if manager is not None:
                        task = await manager.get_task(uuid.UUID(worker["task"]))
                        queue = task.queue
                return await self._token(Role.WORKER, queue)
            if path == "/flows" and method == "POST":
                return await self._token(Role.CI)
            if (
                path.startswith("/runs/")
                and method == "POST"
                and not path.endswith("/cancel")
            ):
                return await self._token(Role.SCHEDULER)
            return await self._token(Role.USER)

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if expect is not None:
                assert request.headers.get("authorization") == f"Bearer {expect}"
            token = await self._role_token(request)
            request.headers["authorization"] = f"Bearer {token}"
            return await self._inner.handle_async_request(request)

    return RoleTokens()


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
