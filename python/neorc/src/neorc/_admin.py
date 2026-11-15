# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""oneberth's admin scripts under ``neorc``, with neorc's roles and queues.

    neorc tokens create worker-1 --role neorc/worker --resource default
    neorc tokens list
    neorc sessions clear

oneberth holds the scripts (``oneberth.admin``). neorc shows them in its own
command line, with its roles registered beside oneberth's, so a token can be
given ``neorc/worker``. ``NeorcConnector`` finds the deployment as the manager
does: a PostgreSQL database, whose schema ``oneberth`` is oneberth's store, or
a SQLite file, beside which ``oneberth.db`` is.

oneberth runs neorc's check of a new service token, ``service_token_check``,
so a token bound to a queue names a declared queue of the right kind.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from oneberth.admin import Admin, ConnectError, StoreAdmin
from oneberth.identity import IdentityStore, SchemaVersionError, check_schema

from neorc._roles import NEORC_ROLES, registry, service_token_check
from neorc_core import Store
from neorc_core.ports import TokenGrant, TokenGrants

DATABASE_URL_ENV = "NEORC_DATABASE_URL"


class AdminTokenGrants(TokenGrants):
    """The service tokens oneberth keeps, read through an ``Admin``."""

    def __init__(self, admin: Admin) -> None:
        self._admin = admin

    async def grants(self) -> list[TokenGrant]:
        return [
            TokenGrant(token.name, token.grant.role, token.grant.resource)
            for token in await self._admin.tokens()
        ]


def add_database_options(parser: argparse.ArgumentParser) -> None:
    """The manager's database: PostgreSQL by URL, or a SQLite file."""
    database = parser.add_mutually_exclusive_group()
    database.add_argument(
        "--database-url",
        default=None,
        help=f"the manager's PostgreSQL database; defaults to ${DATABASE_URL_ENV}",
    )
    database.add_argument(
        "--database-file",
        type=Path,
        default=None,
        help="the manager's SQLite file instead; oneberth.db is beside it",
    )


@asynccontextmanager
async def opened_stores(
    args: argparse.Namespace,
) -> AsyncIterator[tuple[IdentityStore, Store]]:
    """oneberth's store and the manager's, as the command line names them.

    Raises ``ConnectError`` when it names none, a database it cannot reach, or
    one nobody set up: the scripts create no database, so a mistyped one is not
    given a set of empty tables. ``neorc store init`` alone runs on a store not
    set up yet.
    """
    initialising = getattr(args, "store_command", None) == "init"
    async with AsyncExitStack() as stack:
        if args.database_file is not None:
            identity, neorc_store = await _open_sqlite(stack, args.database_file)
        else:
            identity, neorc_store = await _open_postgres(
                stack, args.database_url, initialising=initialising
            )
        if not initialising:
            try:
                await check_schema(identity)
            except SchemaVersionError as exc:
                raise ConnectError(
                    f"{exc}: start the manager once with --create-schema, or run "
                    "`neorc store init`"
                ) from None
        yield identity, neorc_store


async def _open_sqlite(
    stack: AsyncExitStack, path: Path
) -> tuple[IdentityStore, Store]:
    from oneberth.identity.sqlite import FILE_NAME, SqliteIdentityStore

    from neorc.sqlite import SqliteStore

    if not path.is_file() or not (path.parent / FILE_NAME).is_file():
        raise ConnectError(
            f"no database file at {path}, or no {FILE_NAME} beside it: start "
            "the manager on it first"
        )
    identity = await stack.enter_async_context(
        SqliteIdentityStore(path.parent / FILE_NAME)
    )
    return identity, await stack.enter_async_context(SqliteStore(path))


async def _open_postgres(
    stack: AsyncExitStack, url: str | None, *, initialising: bool
) -> tuple[IdentityStore, Store]:
    url = url or os.environ.get(DATABASE_URL_ENV)
    if not url:
        raise ConnectError(
            f"no database: pass --database-url or set ${DATABASE_URL_ENV}"
        )
    try:
        from oneberth.identity.postgres import PostgresIdentityStore

        from neorc.postgres import PostgresStore
        from neorc.postgres._driver import CONNECTION_ERRORS
        from neorc.postgres._schema import QUEUES_TABLE, ensure_tables
    except ImportError:
        raise ConnectError(
            "the PostgreSQL store needs the postgres extra: neorc[postgres]"
        ) from None
    try:
        identity = await stack.enter_async_context(
            PostgresIdentityStore(url, max_size=1)
        )
        neorc_store = await stack.enter_async_context(PostgresStore(url, max_size=1))
        if not initialising:
            await ensure_tables(url, (QUEUES_TABLE,))
    except CONNECTION_ERRORS as exc:
        raise ConnectError(f"cannot reach the database: {exc}") from None
    except RuntimeError as exc:  # the manager's tables are missing
        raise ConnectError(str(exc)) from None
    return identity, neorc_store


class NeorcConnector:
    """Finds a neorc deployment from the command line, for oneberth's scripts."""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        add_database_options(parser)

    @asynccontextmanager
    async def open(self, args: argparse.Namespace) -> AsyncIterator[Admin]:
        async with opened_stores(args) as (identity, store):
            yield StoreAdmin(
                identity,
                registry(),
                service_token_checks={
                    NEORC_ROLES.app: service_token_check(store.queue_kind)
                },
            )
