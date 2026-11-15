# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Assembling and running the manager process."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI

from neorc.auth._config import AuthConfig
from neorc.manager._app import DEFAULT_LONG_POLL_TIMEOUT, create_app
from neorc.manager._sign_in import SignIn
from neorc_core import Access, CredentialStore, Manager, Store, TaskNotifier
from neorc_core.local import MemoryTaskNotifier

# A manager serves workers on other hosts, so it binds every interface.
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8420

DATABASE_URL_ENV = "NEORC_DATABASE_URL"
"""Where the reference deployment keeps its flows, runs, tasks and events."""


def build_app(
    database_url: str | None = None,
    *,
    auth: bool,
    auth_config: AuthConfig | None = None,
    create_schema: bool = False,
    database_file: str | os.PathLike[str] | None = None,
    long_poll_timeout: float = DEFAULT_LONG_POLL_TIMEOUT,
    ui: bool = True,
) -> FastAPI:
    """Build the application from the environment: store, notifiers, then routes.

    Which adapter backs the store is a deployment choice, and nothing above
    this function names it. Postgres is the reference: ``database_url``, or
    ``NEORC_DATABASE_URL``. With ``database_file``, the store is that SQLite
    file instead, created with its tables if it is new.

    Two notification channels: one wakes workers when a task is published,
    the other wakes a scheduler when an event is recorded. Both are hints sent
    after the transaction commits; a waiter re-reads the store. On Postgres
    they go through the database, so a waiter on another manager process
    wakes too. On SQLite one process holds the file, and they stay in memory.

    With ``auth``, every request to the API needs an API token, checked
    against a credential store on the same database, which the lifespan opens
    beside the flow store, after the schema step, and closes with it; without
    ``create_schema``, the lifespan first checks the credential tables exist,
    and fails with ``RuntimeError`` naming the fix if not. The SQLite store
    keeps no credentials, so ``auth`` with ``database_file`` raises
    ``RuntimeError``. ``auth_config`` adds signing in to the UI with its
    providers, for the people its allow list names. Without ``auth``, anyone
    who reaches the service may do everything.

    With ``ui``, the web UI from the ``neorc-ui`` distribution is served at
    ``/ui/``; ``FileNotFoundError`` names the fix if that holds no built UI,
    as in a checkout that was never built.
    """
    if auth_config is not None and not auth:
        raise ValueError("sign-in needs authentication on")
    if database_file is not None and database_url is not None:
        raise ValueError("a manager keeps its data in one database: a file or a URL")
    if database_file is not None and auth:
        raise RuntimeError(SQLITE_HAS_NO_CREDENTIALS)
    dsn = None
    if database_file is None:
        dsn = database_url or os.environ.get(DATABASE_URL_ENV)
        if not dsn:
            raise RuntimeError(
                f"no database to serve: pass one or set {DATABASE_URL_ENV}"
            )
    static = None
    if ui:
        import neorc_ui

        static = neorc_ui.static_dir()

    if dsn is None:
        assert database_file is not None
        stores = _on_sqlite(database_file)
    else:
        stores = _on_postgres(dsn, auth=auth, create_schema=create_schema)

    access = None
    sign_in = None
    if stores.credentials is not None:
        if auth_config is None:
            access = Access(stores.credentials)
        else:
            access = Access(
                stores.credentials,
                allow=auth_config.allow,
                session_seconds=auth_config.session_seconds,
            )
            sign_in = SignIn.build(auth_config, access)
    app = create_app(
        Manager(stores.store, tasks=stores.tasks, events=stores.events),
        access=access,
        sign_in=sign_in,
        long_poll_timeout=long_poll_timeout,
        ui=static,
    )
    app.router.lifespan_context = stores.lifespan
    return app


SQLITE_HAS_NO_CREDENTIALS = (
    "a manager on a SQLite file cannot ask for API tokens yet, since the SQLite "
    "store keeps no credentials: start it with --no-auth on a machine nobody "
    "else reaches, or keep it on PostgreSQL with --database-url"
)


@dataclass(frozen=True)
class _Stores:
    """The adapters a manager runs on, and the lifespan that opens and closes them."""

    store: Store
    tasks: TaskNotifier
    events: TaskNotifier
    credentials: CredentialStore | None
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]]


def _on_postgres(dsn: str, *, auth: bool, create_schema: bool) -> _Stores:
    from neorc.postgres import (
        PostgresCredentialStore,
        PostgresStore,
        PostgresTaskNotifier,
    )
    from neorc.postgres import create_schema as create_postgres_schema
    from neorc.postgres._notifier import EVENTS_CHANNEL, TASKS_CHANNEL
    from neorc.postgres._schema import ensure_credential_tables

    store = PostgresStore(dsn)
    tasks = PostgresTaskNotifier(dsn, channel=TASKS_CHANNEL)
    events = PostgresTaskNotifier(dsn, channel=EVENTS_CHANNEL)
    credentials = PostgresCredentialStore(dsn) if auth else None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if create_schema:
            await create_postgres_schema(dsn)
        elif credentials is not None:
            await ensure_credential_tables(dsn)
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(store)
            await stack.enter_async_context(tasks)
            await stack.enter_async_context(events)
            if credentials is not None:
                await stack.enter_async_context(credentials)
            yield

    return _Stores(store, tasks, events, credentials, lifespan)


def _on_sqlite(path: str | os.PathLike[str]) -> _Stores:
    from neorc.sqlite import SqliteStore

    store = SqliteStore(path)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with store:
            yield

    return _Stores(store, MemoryTaskNotifier(), MemoryTaskNotifier(), None, lifespan)


def run(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    auth: bool,
    auth_config: AuthConfig | None = None,
    database_url: str | None = None,
    create_schema: bool = False,
    database_file: str | os.PathLike[str] | None = None,
    ui: bool = True,
    ssl_certfile: str | None = None,
    ssl_keyfile: str | None = None,
) -> None:
    """Serve the manager until interrupted. Blocks.

    With ``ssl_certfile`` and ``ssl_keyfile``, it serves HTTPS. Raises
    ``RuntimeError`` before serving when there is no database, when
    authentication is on and the credential tables are missing, and when
    ``database_file`` is a file of another schema version or application
    (``SchemaVersionError``); ``sqlite3.Error`` when SQLite cannot open it.
    """
    app = build_app(
        database_url,
        auth=auth,
        auth_config=auth_config,
        create_schema=create_schema,
        database_file=database_file,
        ui=ui,
    )
    # Checked again by the lifespan; here, so the command can say so and exit
    # rather than leave it to uvicorn's startup failure.
    if database_file is not None:
        from neorc.sqlite import check_database

        check_database(database_file)
    elif auth and not create_schema:
        from neorc.postgres._schema import ensure_credential_tables

        asyncio.run(
            ensure_credential_tables(database_url or os.environ[DATABASE_URL_ENV])
        )
    uvicorn.run(
        app,
        host=host,
        port=port,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
    )
