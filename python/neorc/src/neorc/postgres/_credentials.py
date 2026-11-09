# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The Postgres store for API tokens, sessions and sign-ins in progress.

Each operation is one statement, so one transaction, and none takes a lock
beyond the rows it writes: nothing here joins the flow store's lock order.
Expiry is compared with the server's ``now()``, which also sets every time,
so one clock decides.
"""

from __future__ import annotations

import uuid

from neorc.postgres._driver import Record, UniqueViolationError, affected
from neorc.postgres._pool import Pooled
from neorc.postgres._schema import PENDING_LOGINS_TABLE, SESSIONS_TABLE, TOKENS_TABLE
from neorc_core import ApiToken, CredentialStore, InvalidValueError
from neorc_core._access import PendingLogin, Principal, PrincipalKind, Role

_TOKEN_COLUMNS = "id, name, role, queue, created_at, expires_at"

_INSERT_TOKEN = f"""
INSERT INTO {TOKENS_TABLE} (id, name, secret_hash, role, queue, created_at,
                           expires_at)
VALUES ($1, $2, $3, $4, $5, now(),
        now() + make_interval(secs => $6::double precision))
RETURNING {_TOKEN_COLUMNS}
"""

_ALIVE = "(expires_at IS NULL OR now() < expires_at)"

_TOKEN_BY_HASH = f"""
SELECT {_TOKEN_COLUMNS} FROM {TOKENS_TABLE}
 WHERE secret_hash = $1 AND {_ALIVE}
"""

_TOKENS = f"SELECT {_TOKEN_COLUMNS} FROM {TOKENS_TABLE} ORDER BY name"

_DELETE_TOKEN = f"DELETE FROM {TOKENS_TABLE} WHERE name = $1"

_INSERT_SESSION = f"""
INSERT INTO {SESSIONS_TABLE} (secret_hash, kind, subject, name, role, queue,
                              provider, email, created_at, expires_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now(),
        now() + make_interval(secs => $9::double precision))
"""

_SESSION_BY_HASH = f"""
SELECT kind, subject, name, role, queue, provider, email FROM {SESSIONS_TABLE}
 WHERE secret_hash = $1 AND now() < expires_at
"""

_DELETE_SESSION = f"DELETE FROM {SESSIONS_TABLE} WHERE secret_hash = $1"

_DELETE_SESSIONS = f"DELETE FROM {SESSIONS_TABLE}"

# The count and the insert are one statement. Two at once may both count
# under the limit, so it can be passed by the number of concurrent sign-ins:
# a ceiling on growth, not an exact number, which is all it is for.
_INSERT_LOGIN = f"""
INSERT INTO {PENDING_LOGINS_TABLE} (state_hash, provider, nonce, verifier,
                                    created_at, expires_at)
SELECT $1, $2, $3, $4,
       now(), now() + make_interval(secs => $5::double precision)
 WHERE (SELECT count(*) FROM {PENDING_LOGINS_TABLE}
         WHERE expires_at > now()) < $6
"""

# Deleting and reading are one statement: of two takes at once, the second
# waits for the first's row lock and then finds the row gone.
_TAKE_LOGIN = f"""
DELETE FROM {PENDING_LOGINS_TABLE}
 WHERE state_hash = $1
RETURNING provider, nonce, verifier, now() < expires_at AS alive
"""

_DELETE_EXPIRED_SESSIONS = f"DELETE FROM {SESSIONS_TABLE} WHERE expires_at <= now()"
_DELETE_EXPIRED_LOGINS = f"DELETE FROM {PENDING_LOGINS_TABLE} WHERE expires_at <= now()"


class PostgresCredentialStore(Pooled, CredentialStore):
    """API tokens, sessions and pending logins in Postgres, over a small pool."""

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 4,
        schema: str | None = None,
    ) -> None:
        super().__init__(dsn, min_size=min_size, max_size=max_size, schema=schema)

    async def add_token(
        self,
        name: str,
        secret_hash: str,
        *,
        role: Role,
        queue: str | None = None,
        expires_seconds: float | None = None,
    ) -> ApiToken:
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    _INSERT_TOKEN,
                    uuid.uuid4(),
                    name,
                    secret_hash,
                    Role(role).value,
                    queue,
                    _seconds(expires_seconds),
                )
        except UniqueViolationError as exc:
            constraint = getattr(exc, "constraint_name", None)
            if constraint == f"{TOKENS_TABLE}_name_key":
                raise InvalidValueError(f"a token called {name!r} exists") from None
            raise
        assert row is not None
        return _token(row)

    async def token_by_hash(self, secret_hash: str) -> ApiToken | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(_TOKEN_BY_HASH, secret_hash)
        return None if row is None else _token(row)

    async def tokens(self) -> list[ApiToken]:
        async with self.pool.acquire() as conn:
            return [_token(row) for row in await conn.fetch(_TOKENS)]

    async def delete_token(self, name: str) -> bool:
        async with self.pool.acquire() as conn:
            return affected(await conn.execute(_DELETE_TOKEN, name)) > 0

    async def add_session(
        self, secret_hash: str, principal: Principal, *, seconds: float
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                _INSERT_SESSION,
                secret_hash,
                principal.kind.value,
                principal.subject,
                principal.name,
                principal.role.value,
                principal.queue,
                principal.provider,
                principal.email,
                _seconds(seconds),
            )

    async def session_by_hash(self, secret_hash: str) -> Principal | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(_SESSION_BY_HASH, secret_hash)
        if row is None:
            return None
        return Principal(
            kind=PrincipalKind(row["kind"]),
            subject=row["subject"],
            name=row["name"],
            role=Role(row["role"]),
            queue=row["queue"],
            provider=row["provider"],
            email=row["email"],
        )

    async def delete_session(self, secret_hash: str) -> bool:
        async with self.pool.acquire() as conn:
            return affected(await conn.execute(_DELETE_SESSION, secret_hash)) > 0

    async def delete_sessions(self) -> int:
        async with self.pool.acquire() as conn:
            return affected(await conn.execute(_DELETE_SESSIONS))

    async def add_login(
        self, state_hash: str, login: PendingLogin, *, seconds: float, limit: int
    ) -> bool:
        async with self.pool.acquire() as conn:
            status = await conn.execute(
                _INSERT_LOGIN,
                state_hash,
                login.provider,
                login.nonce,
                login.verifier,
                _seconds(seconds),
                limit,
            )
        return affected(status) > 0

    async def take_login(self, state_hash: str) -> PendingLogin | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(_TAKE_LOGIN, state_hash)
        if row is None or not row["alive"]:
            return None
        return PendingLogin(
            provider=row["provider"], nonce=row["nonce"], verifier=row["verifier"]
        )

    async def delete_expired(self) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(_DELETE_EXPIRED_SESSIONS)
            await conn.execute(_DELETE_EXPIRED_LOGINS)


def _seconds(seconds: float | None) -> float | None:
    """A duration to bind as ``double precision``, which asyncpg wants a float for."""
    return None if seconds is None else float(seconds)


def _token(row: Record) -> ApiToken:
    return ApiToken(
        id=row["id"],
        name=row["name"],
        role=Role(row["role"]),
        created_at=row["created_at"],
        queue=row["queue"],
        expires_at=row["expires_at"],
    )
