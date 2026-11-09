# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""A connection pool the Postgres stores share the handling of."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from neorc.postgres._driver import Pool, create_pool, schema_settings
from neorc_core import NeorcError

_READ_COMMITTED = {"default_transaction_isolation": "read committed"}
"""The stores' locking is reasoned out for READ COMMITTED: a statement after a
lock sees what was committed before the lock was granted. Sent with every
connection's startup, after the connection string's own settings and so over
them, so a server, role or DSN with another default does not change that. A
startup setting is also what the pool's reset on release goes back to."""


class Pooled:
    """Owns a pool of connections.

    Every connection runs its transactions in ``READ COMMITTED``, whatever the
    server's default. With ``schema``, its ``search_path`` is that schema
    alone, so the neorc tables are found there; ``create_schema`` with the same
    ``schema`` creates them in it.
    """

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
        schema: str | None = None,
    ) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._settings = {**schema_settings(schema), **_READ_COMMITTED}
        self._pool: Pool | None = None

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def open(self) -> None:
        """Open the connection pool."""
        if self._pool is not None:
            return
        self._pool = await create_pool(
            self._dsn,
            min_size=self._min_size,
            max_size=self._max_size,
            server_settings=self._settings,
        )

    async def aclose(self) -> None:
        """Close the connection pool."""
        if self._pool is None:
            return
        await self._pool.close()
        self._pool = None

    @property
    def pool(self) -> Pool:
        """The open pool, for callers that share this store's connections."""
        if self._pool is None:
            raise NeorcError("the store is not open")
        return self._pool
