# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The Postgres credential store, against a real server, held to the contract."""

from __future__ import annotations

import pytest
from asyncpg.exceptions import CheckViolationError  # type: ignore[import-untyped]

from neorc.postgres import PostgresCredentialStore
from neorc_core import CredentialStore, Principal, PrincipalKind, Role
from neorc_core._access import secret_hash
from neorc_core.testing.contracts import CredentialStoreContract

pytestmark = pytest.mark.postgres


class TestPostgresCredentialStore(CredentialStoreContract):
    @pytest.fixture
    def credentials(self, pg_credentials: PostgresCredentialStore) -> CredentialStore:
        return pg_credentials


@pytest.mark.parametrize(
    ("role", "queue"),
    [(Role.WORKER, None), (Role.CI, "default"), (Role.USER, "default")],
    ids=["worker-without-a-queue", "ci-with-a-queue", "user-with-a-queue"],
)
async def test_the_database_refuses_a_queue_only_a_worker_has(
    pg_credentials: PostgresCredentialStore, role: Role, queue: str | None
) -> None:
    """Beneath the checks ``Access`` makes, for whatever writes the table."""
    with pytest.raises(CheckViolationError, match="queue_check"):
        await pg_credentials.add_token("t", secret_hash("t"), role=role, queue=queue)
    person = Principal(
        kind=PrincipalKind.SESSION, subject="x", name="x", role=role, queue=queue
    )
    with pytest.raises(CheckViolationError, match="queue_check"):
        await pg_credentials.add_session(secret_hash("s"), person, seconds=60)
    assert await pg_credentials.tokens() == []
