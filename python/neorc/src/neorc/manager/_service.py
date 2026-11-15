# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Standalone neorc: the manager's component, composed alone and served.

oneberth composes the backend: the manager's routes under ``/neorc/``, sign-in,
sessions and tokens under ``/auth``, the web UI under ``/ui/`` and
``/health``. oneberth's server serves it.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

from oneberth.config import Config
from oneberth.identity import IdentityStore
from oneberth.stores import opened_store
from oneberth.web import Composition, Listen, compose_app
from oneberth.web import run as serve_composition

from neorc.manager._component import DEFAULT_LONG_POLL_TIMEOUT, new_manager_webapp

# A manager serves workers on other hosts, so it binds every interface.
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8420

DATABASE_URL_ENV = "NEORC_DATABASE_URL"
"""Where the reference deployment keeps its flows, runs, tasks and events."""


def build_app(
    database_url: str | None = None,
    *,
    config: Config,
    create_schema: bool = False,
    database_file: str | os.PathLike[str] | None = None,
    long_poll_timeout: float = DEFAULT_LONG_POLL_TIMEOUT,
    ui: bool | Path = True,
    local: bool = False,
) -> Composition:
    """Compose standalone neorc: the manager's component, alone.

    ``config`` is the deployment's sign-in configuration, oneberth's TOML file
    read; it names the manager's public URL under ``[apps.neorc]``. With
    ``local``, the backend is in oneberth's local mode: one person, the one at
    the machine, signs in with a ticket, and ``config`` is
    ``oneberth.config.local_config("neorc", port=...)``.

    The manager's records are in PostgreSQL, ``database_url`` or
    ``NEORC_DATABASE_URL``, and oneberth's store is the schema ``oneberth`` of
    the same database. With ``create_schema``, both sets of tables are created
    at the start. With ``database_file``, the records are in that SQLite file,
    and oneberth's store is ``oneberth.db`` in the same directory; both are
    created if they are new.

    With ``ui``, the web UI from the ``neorc-ui`` distribution is served at
    ``/ui/``, or the built UI in the directory ``ui`` names;
    ``FileNotFoundError`` names the fix if there is none.
    """
    if database_file is not None and database_url is not None:
        raise ValueError("a manager keeps its data in one database: a file or a URL")
    store: Callable[[], AbstractAsyncContextManager[IdentityStore]]
    if database_file is not None:
        folder = Path(database_file).parent
        component = new_manager_webapp(
            database_file=database_file, long_poll_timeout=long_poll_timeout
        )

        def store() -> AbstractAsyncContextManager[IdentityStore]:
            return opened_store(data_dir=folder, setup=True)

    else:
        dsn = database_url or os.environ.get(DATABASE_URL_ENV)
        if not dsn:
            raise RuntimeError(
                f"no database to serve: pass one or set {DATABASE_URL_ENV}"
            )
        component = new_manager_webapp(
            database_url=dsn,
            create_schema=create_schema,
            long_poll_timeout=long_poll_timeout,
        )
        store = _postgres_store(dsn, setup=create_schema)
    interface = None
    if isinstance(ui, Path):
        interface = ui
    elif ui:
        import neorc_ui

        interface = neorc_ui.static_dir()
    return compose_app(
        [component], config=config, store=store, interface=interface, local=local
    )


def _postgres_store(
    dsn: str, *, setup: bool
) -> Callable[[], AbstractAsyncContextManager[IdentityStore]]:
    @asynccontextmanager
    async def opened() -> AsyncIterator[IdentityStore]:
        async with opened_store(database_url=dsn, setup=setup) as store:
            yield store

    return opened


def run(app: Composition, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Serve ``app`` until interrupted, on oneberth's server. Blocks.

    Prints ``ready`` once it serves. Exits 1 when the backend does not start,
    as when a store is of another version or not set up. In the local mode, a
    launcher writes the launch secret on standard input; a start by hand has
    none there, and prints ``open <url>``, the address that signs the person
    in.
    """
    serve_composition(app, Listen(host, port))
