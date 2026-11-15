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

A token bound to a queue is checked against the queue's declaration, as
``QueueDeclarations`` says, before oneberth creates it.
"""

from __future__ import annotations

import argparse
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from oneberth.admin import Admin, ConnectError, StoreAdmin
from oneberth.identity import (
    DeviceSession,
    Grant,
    IdentityStore,
    SchemaVersionError,
    ServiceToken,
    User,
    check_schema,
)
from oneberth.identity import InvalidValueError as OneberthInvalidValueError

from neorc._roles import registry
from neorc_core import InvalidValueError, QueueDeclarations, Store
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


class NeorcAdmin:
    """oneberth's ``Admin`` on the store, with neorc's check of a token's queue."""

    def __init__(self, admin: Admin, declarations: QueueDeclarations) -> None:
        self._admin = admin
        self._declarations = declarations

    def now(self) -> datetime:
        return self._admin.now()

    async def init_store(self) -> int:
        return await self._admin.init_store()

    async def create_token(
        self,
        name: str,
        role: str,
        *,
        resource: str | None = None,
        expires_in: timedelta | None = None,
    ) -> tuple[str, ServiceToken]:
        # oneberth's own check first, so a malformed grant is said to be that.
        registry().check_grant(Grant(role, resource))
        try:
            await self._declarations.check_token(role, resource)
        except InvalidValueError as exc:
            raise OneberthInvalidValueError(str(exc)) from None
        return await self._admin.create_token(
            name, role, resource=resource, expires_in=expires_in
        )

    async def tokens(self) -> list[ServiceToken]:
        return await self._admin.tokens()

    async def revoke_token(self, name: str) -> ServiceToken:
        return await self._admin.revoke_token(name)

    async def clear_sessions(self) -> int:
        return await self._admin.clear_sessions()

    async def devices(self) -> list[tuple[User, DeviceSession]]:
        return await self._admin.devices()

    async def end_devices(
        self,
        *,
        email: str | None = None,
        device_id: uuid.UUID | None = None,
        every: bool = False,
    ) -> list[tuple[User | None, DeviceSession]]:
        return await self._admin.end_devices(
            email=email, device_id=device_id, every=every
        )


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
        identity: IdentityStore
        neorc_store: Store
        if args.database_file is not None:
            from oneberth.identity.sqlite import FILE_NAME, SqliteIdentityStore

            from neorc.sqlite import SqliteStore

            path: Path = args.database_file
            if not path.is_file() or not (path.parent / FILE_NAME).is_file():
                raise ConnectError(
                    f"no database file at {path}, or no {FILE_NAME} beside it: start "
                    "the manager on it first"
                )
            identity = await stack.enter_async_context(
                SqliteIdentityStore(path.parent / FILE_NAME)
            )
            neorc_store = await stack.enter_async_context(SqliteStore(path))
        else:
            url = args.database_url or os.environ.get(DATABASE_URL_ENV)
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
                neorc_store = await stack.enter_async_context(
                    PostgresStore(url, max_size=1)
                )
                if not initialising:
                    await ensure_tables(url, (QUEUES_TABLE,))
            except CONNECTION_ERRORS as exc:
                raise ConnectError(f"cannot reach the database: {exc}") from None
            except RuntimeError as exc:  # the manager's tables are missing
                raise ConnectError(str(exc)) from None
        if not initialising:
            try:
                await check_schema(identity)
            except SchemaVersionError as exc:
                raise ConnectError(
                    f"{exc}: start the manager once with --create-schema, or run "
                    "`neorc store init`"
                ) from None
        yield identity, neorc_store


class NeorcConnector:
    """Finds a neorc deployment from the command line, for oneberth's scripts."""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        add_database_options(parser)

    @asynccontextmanager
    async def open(self, args: argparse.Namespace) -> AsyncIterator[Admin]:
        async with opened_stores(args) as (identity, store):
            admin = StoreAdmin(identity, registry())
            yield NeorcAdmin(admin, QueueDeclarations(store, AdminTokenGrants(admin)))
