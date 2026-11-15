# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The manager as a oneberth component, named ``neorc``.

oneberth composes a backend from components (oneberth's
``docs/specs/composition.md``). This one holds the manager's routes, neorc's
roles, the check of a new service token's queue, and what the manager starts
and stops: its stores, its notifiers and, if asked, a scheduler. A backend
serves it under ``/neorc/``, alone in standalone neorc or beside another
application's component, as Robinauts composes it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from typing import Any

from oneberth.web import Component, Context, Refusal

from neorc._errors import STATUS_OF, error_body
from neorc._roles import NEORC_ROLES, service_token_check
from neorc.manager import _routes
from neorc_core import Manager, NeorcError, QueueKind, Scheduler
from neorc_core.local import DirectManagerClient, MemoryTaskNotifier

NAME = "neorc"
"""The component's name, and the first segment of its addresses."""

DEFAULT_LONG_POLL_TIMEOUT = 25.0
"""How long a receive waits before answering "nothing yet".

Must stay under the idle timeout of any proxy in front of the service; a worker
that hits it simply asks again.
"""

SCHEDULER_POLL_SECONDS = 30.0
"""How long the scheduler a component runs waits for events before it asks again."""

_log = logging.getLogger("neorc.manager")

ManagerSource = Callable[[], AbstractAsyncContextManager[Manager]]
"""What opens a manager on its stores for the life of the backend."""


def new_manager_webapp(
    *,
    database_url: str | None = None,
    database_file: str | os.PathLike[str] | None = None,
    manager: Manager | None = None,
    create_schema: bool = False,
    scheduler: bool = False,
    long_poll_timeout: float = DEFAULT_LONG_POLL_TIMEOUT,
) -> Component:
    """The manager's component, for ``oneberth.web.compose_app``.

    Name one place for the manager's records: ``database_url``, a PostgreSQL
    database; ``database_file``, a SQLite file, created with its tables if it
    is new; or ``manager``, a manager on stores the caller opens and closes,
    as a test's memory store. With ``create_schema``, the tables of a
    PostgreSQL database are created at the start.

    With ``scheduler``, the component runs a scheduler in the backend's
    process too, as a backend that composes neorc does. A deployment runs one
    scheduler.

    Raises ``ValueError`` unless exactly one place is named.
    """
    named = [database_url is not None, database_file is not None, manager is not None]
    if named.count(True) != 1:
        raise ValueError(
            "a manager keeps its records in one place: a database URL, a SQLite "
            "file, or a manager given"
        )
    source: ManagerSource
    if database_url is not None:
        source = _on_postgres(database_url, create_schema=create_schema)
    elif database_file is not None:
        source = _on_sqlite(database_file)
    else:
        assert manager is not None
        source = _given(manager)
    return manager_component(
        source, scheduler=scheduler, long_poll_timeout=long_poll_timeout
    )


def manager_component(
    source: ManagerSource,
    *,
    scheduler: bool = False,
    long_poll_timeout: float = DEFAULT_LONG_POLL_TIMEOUT,
) -> Component:
    """The component around the manager ``source`` opens when the backend starts."""
    running: list[Manager] = []
    stops: list[asyncio.Event] = []

    def current() -> Manager:
        if not running:
            raise Refusal(503, "Unavailable", "the manager is starting or stopping")
        return running[0]

    def stopping() -> asyncio.Event:
        if not stops:
            raise Refusal(503, "Unavailable", "the manager is starting or stopping")
        return stops[0]

    router = _routes.routes_for(
        _routes.ManagerState(current, long_poll_timeout, stopping)
    )

    @asynccontextmanager
    async def lifespan(context: Context) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            manager = await stack.enter_async_context(source())
            if scheduler:
                await stack.enter_async_context(_scheduling(manager))
            running.append(manager)
            stops.append(asyncio.Event())
            try:
                yield
            finally:
                stops[0].set()
                running.clear()
                stops.clear()

    @asynccontextmanager
    async def serving(context: Context) -> AsyncIterator[None]:
        # The backend stops: the long polls end while the listeners still
        # serve, so the server does not wait them out.
        try:
            yield
        finally:
            if stops:
                stops[0].set()

    async def health() -> bool:  # NOSONAR: oneberth awaits a component's health
        return bool(running)

    async def queue_kind(name: str) -> QueueKind | None:
        return await current().queue_kind(name)

    return Component(
        name=NAME,
        roles=NEORC_ROLES,
        router=router,
        lifespan=lifespan,
        serving=serving,
        health=health,
        errors=dict[type[Exception], int](STATUS_OF.items()),
        error_body=_error_body,
        title="neorc manager",
        check_service_token=service_token_check(queue_kind),
    )


def _error_body(error: Exception) -> Mapping[str, Any]:
    assert isinstance(error, NeorcError)
    return error_body(error)


@asynccontextmanager
async def _scheduling(manager: Manager) -> AsyncIterator[None]:
    """A scheduler on ``manager``, in this process, for the block."""
    moving = Scheduler(
        DirectManagerClient(manager), poll_timeout=SCHEDULER_POLL_SECONDS
    )
    task = asyncio.create_task(moving.run(), name="neorc-scheduler")
    try:
        yield
    finally:
        moving.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _given(manager: Manager) -> ManagerSource:
    @asynccontextmanager
    async def opened() -> AsyncIterator[Manager]:
        yield manager

    return opened


def _on_postgres(dsn: str, *, create_schema: bool) -> ManagerSource:
    """The manager on PostgreSQL, its notifications through the database.

    A waiter on another manager process wakes too.
    """
    from neorc.postgres import PostgresStore, PostgresTaskNotifier
    from neorc.postgres import create_schema as create_postgres_schema
    from neorc.postgres._notifier import EVENTS_CHANNEL, TASKS_CHANNEL

    @asynccontextmanager
    async def opened() -> AsyncIterator[Manager]:
        if create_schema:
            await create_postgres_schema(dsn)
        async with (
            PostgresStore(dsn) as store,
            PostgresTaskNotifier(dsn, channel=TASKS_CHANNEL) as tasks,
            PostgresTaskNotifier(dsn, channel=EVENTS_CHANNEL) as events,
        ):
            yield Manager(store, tasks=tasks, events=events)

    return opened


def _on_sqlite(path: str | os.PathLike[str]) -> ManagerSource:
    """The manager on a SQLite file. One process holds the file, so it notifies
    in memory."""
    from neorc.sqlite import SqliteStore

    @asynccontextmanager
    async def opened() -> AsyncIterator[Manager]:
        async with SqliteStore(path) as store:
            yield Manager(
                store, tasks=MemoryTaskNotifier(), events=MemoryTaskNotifier()
            )

    return opened
