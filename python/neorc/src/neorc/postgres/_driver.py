# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The one module that imports asyncpg: typed views of the part the adapters use.

asyncpg ships no type information, so its connections and pools are described
here as protocols, and every other module talks to those. This is also where a
connection string becomes asyncpg's arguments: asyncpg reads URLs only, where
libpq also reads ``key=value`` connection strings, which neorc goes on
accepting.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol, cast
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

import asyncpg  # type: ignore[import-untyped]
import asyncpg.exceptions  # type: ignore[import-untyped]


class Record(Protocol):
    """A row, read by column name or position."""

    def __getitem__(self, key: str | int, /) -> Any: ...


class Connection(Protocol):
    """An ``asyncpg.Connection``, as far as the adapters use one."""

    async def execute(
        self, query: str, *args: object, timeout: float | None = None
    ) -> str: ...

    async def executemany(
        self,
        command: str,
        args: Iterable[Sequence[object]],
        *,
        timeout: float | None = None,
    ) -> None: ...

    async def fetch(
        self, query: str, *args: object, timeout: float | None = None
    ) -> list[Record]: ...

    async def fetchrow(
        self, query: str, *args: object, timeout: float | None = None
    ) -> Record | None: ...

    async def fetchval(
        self,
        query: str,
        *args: object,
        column: int = 0,
        timeout: float | None = None,
    ) -> Any: ...

    def transaction(
        self,
        *,
        isolation: str | None = None,
        readonly: bool = False,
        deferrable: bool = False,
    ) -> AbstractAsyncContextManager[object]: ...

    def is_closed(self) -> bool: ...

    async def close(self, *, timeout: float | None = None) -> None: ...

    def terminate(self) -> None: ...

    async def add_listener(
        self, channel: str, callback: Callable[[Any, int, str, str], object]
    ) -> None: ...

    def add_termination_listener(self, callback: Callable[[Any], object]) -> None: ...


class Pool(Protocol):
    """An ``asyncpg.Pool``, as far as the adapters use one."""

    def acquire(
        self, *, timeout: float | None = None
    ) -> AbstractAsyncContextManager[Connection]: ...

    async def close(self) -> None: ...


PostgresError: type[Exception] = asyncpg.PostgresError
"""Any error the server reported."""

UniqueViolationError: type[Exception] = asyncpg.exceptions.UniqueViolationError

DRIVER_ERRORS: tuple[type[BaseException], ...] = (
    asyncpg.PostgresError,
    asyncpg.InterfaceError,
    OSError,
)
"""Whatever talking to the server may raise: its errors, the driver's, the link's.

``TimeoutError`` is an ``OSError``.
"""

CONNECTION_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    asyncpg.exceptions.PostgresConnectionError,
    asyncpg.exceptions.InvalidAuthorizationSpecificationError,
    asyncpg.exceptions.InvalidCatalogNameError,
    asyncpg.exceptions.CannotConnectNowError,
    asyncpg.exceptions.TooManyConnectionsError,
)
"""The database could not be reached or would not let us in.

No route or refused, a password or role refused, no such database, a server
starting up or shutting down, or out of connection slots: what libpq reports
as an operational error when connecting.
"""

_LIBPQ_ONLY = frozenset(
    {
        "keepalives",
        "keepalives_idle",
        "keepalives_interval",
        "keepalives_count",
        "tcp_user_timeout",
        "fallback_application_name",
    }
)
"""libpq parameters asyncpg has no use for; dropped, rather than sent to the
server as settings, which it would refuse."""


def connect_arguments(dsn: str) -> dict[str, Any]:
    """asyncpg's ``dsn`` and keyword arguments for a URL or a ``key=value`` string.

    ``connect_timeout`` becomes asyncpg's ``timeout``; libpq's keepalive
    parameters are dropped. Everything else is left to asyncpg, which sends
    what it does not know, ``options`` among them, to the server as settings.
    """
    if dsn.startswith(("postgresql://", "postgres://")):
        parts = urlsplit(dsn)
        query = parse_qsl(parts.query, keep_blank_values=True)
    elif dsn.strip():
        parts = urlsplit("postgresql://")
        query = list(_parse_conninfo(dsn).items())
    else:
        return {}  # asyncpg reads the PG* environment variables, as libpq does
    arguments: dict[str, Any] = {}
    kept: list[tuple[str, str]] = []
    for key, value in query:
        if key == "connect_timeout":
            if value and float(value) > 0:
                arguments["timeout"] = float(value)
        elif key not in _LIBPQ_ONLY:
            kept.append((key, value))
    query_text = f"?{urlencode(kept, quote_via=quote)}" if kept else ""
    # Not urlunsplit: it drops the "//" of a URL with no host, which asyncpg needs.
    arguments["dsn"] = f"{parts.scheme}://{parts.netloc}{parts.path}{query_text}"
    return arguments


def _parse_conninfo(text: str) -> dict[str, str]:
    """A libpq ``key=value`` connection string, quoting and escapes included."""
    params: dict[str, str] = {}
    i, n = 0, len(text)
    while True:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            return params
        start = i
        while i < n and text[i] != "=" and not text[i].isspace():
            i += 1
        key = text[start:i]
        while i < n and text[i].isspace():
            i += 1
        if not key or i >= n or text[i] != "=":
            raise ValueError(f"invalid connection string: missing '=' after {key!r}")
        i += 1
        while i < n and text[i].isspace():
            i += 1
        value: list[str] = []
        if i < n and text[i] == "'":
            i += 1
            while True:
                if i >= n:
                    raise ValueError("invalid connection string: unterminated quote")
                if text[i] == "\\" and i + 1 < n:
                    value.append(text[i + 1])
                    i += 2
                elif text[i] == "'":
                    i += 1
                    break
                else:
                    value.append(text[i])
                    i += 1
        else:
            while i < n and not text[i].isspace():
                if text[i] == "\\" and i + 1 < n:
                    value.append(text[i + 1])
                    i += 2
                else:
                    value.append(text[i])
                    i += 1
        params[key] = "".join(value)


def quote_identifier(name: str) -> str:
    """``name`` as a quoted SQL identifier."""
    return '"' + name.replace('"', '""') + '"'


def schema_settings(schema: str | None) -> dict[str, str]:
    """The server settings that put a connection in ``schema``, if one is named."""
    if schema is None:
        return {}
    if not schema:
        raise ValueError("a schema name cannot be empty")
    return {"search_path": quote_identifier(schema)}


async def connect(
    dsn: str,
    *,
    timeout: float | None = None,
    server_settings: dict[str, str] | None = None,
) -> Connection:
    """One connection, autocommitting, as asyncpg's are.

    ``timeout`` bounds opening it, unless the connection string sets
    ``connect_timeout``.
    """
    arguments = connect_arguments(dsn)
    if timeout is not None:
        arguments.setdefault("timeout", timeout)
    if server_settings:
        arguments["server_settings"] = server_settings
    return cast(Connection, await asyncpg.connect(**arguments))


@asynccontextmanager
async def connected(
    dsn: str, *, server_settings: dict[str, str] | None = None
) -> AsyncIterator[Connection]:
    """A connection for the block, closed after it."""
    conn = await connect(dsn, server_settings=server_settings)
    try:
        yield conn
    finally:
        await conn.close()


async def create_pool(
    dsn: str,
    *,
    min_size: int,
    max_size: int,
    server_settings: dict[str, str],
) -> Pool:
    """An open pool, its first ``min_size`` connections made."""
    pool = await asyncpg.create_pool(
        min_size=min_size,
        max_size=max_size,
        server_settings=server_settings,
        **connect_arguments(dsn),
    )
    return cast(Pool, pool)


def affected(status: str) -> int:
    """How many rows a command touched, from its status, such as ``DELETE 3``."""
    count = status.rpartition(" ")[2]
    return int(count) if count.isdigit() else 0
