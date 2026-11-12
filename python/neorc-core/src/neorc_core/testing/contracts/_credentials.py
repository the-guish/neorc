# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""What every ``CredentialStore`` must do: tokens, sessions, sign-ins, expiry."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from neorc_core._access import (
    PendingLogin,
    Principal,
    PrincipalKind,
    Role,
    secret_hash,
)
from neorc_core._errors import InvalidValueError
from neorc_core.ports._credentials import CredentialStore

SHORT = 0.2
"""A life that ends within a test."""

PAST_SHORT = SHORT * 2
"""Long enough to wait for ``SHORT`` to have ended, on any store's clock."""

LONG = 3600.0

PERSON = Principal(
    kind=PrincipalKind.SESSION,
    subject="248289761001",
    name="Ada Lovelace",
    role=Role.USER,
    provider="google",
    email="ada@example.com",
)

LOGIN = PendingLogin(provider="google", nonce="n" * 43, verifier="v" * 64)


class CredentialStoreContract:
    """Subclass and provide a ``credentials`` fixture holding nothing."""

    @pytest.fixture
    def credentials(self) -> CredentialStore:
        raise NotImplementedError(
            "a CredentialStoreContract subclass provides `credentials`"
        )

    # API tokens.

    async def test_a_token_is_found_by_its_hash_alone(
        self, credentials: CredentialStore
    ) -> None:
        before = datetime.now(UTC) - timedelta(seconds=5)
        added = await credentials.add_token("worker-1", secret_hash("s1"), role=Role.CI)

        found = await credentials.token_by_hash(secret_hash("s1"))

        assert found == added
        assert (added.name, added.expires_at) == ("worker-1", None)
        assert before < added.created_at < before + timedelta(seconds=60)
        assert await credentials.token_by_hash(secret_hash("s2")) is None

    async def test_a_token_keeps_its_role_and_queue(
        self, credentials: CredentialStore
    ) -> None:
        worker = await credentials.add_token(
            "worker-1", secret_hash("w"), role=Role.WORKER, queue="python-gpu"
        )
        trigger = await credentials.add_token(
            "webhook", secret_hash("t"), role=Role.EXTERNAL_TRIGGER
        )

        responder = await credentials.add_token(
            "inbox", secret_hash("r"), role=Role.RESPONDER, queue="editors"
        )

        assert (worker.role, worker.queue) == (Role.WORKER, "python-gpu")
        assert (trigger.role, trigger.queue) == (Role.EXTERNAL_TRIGGER, None)
        assert (responder.role, responder.queue) == (Role.RESPONDER, "editors")
        assert await credentials.token_by_hash(secret_hash("w")) == worker
        assert await credentials.token_by_hash(secret_hash("r")) == responder
        assert await credentials.tokens() == [responder, trigger, worker]

    async def test_tokens_are_listed_by_name(
        self, credentials: CredentialStore
    ) -> None:
        await credentials.add_token("b", secret_hash("b"), role=Role.CI)
        await credentials.add_token("a", secret_hash("a"), role=Role.CI)
        await credentials.add_token("c", secret_hash("c"), role=Role.CI)

        assert [t.name for t in await credentials.tokens()] == ["a", "b", "c"]

    async def test_a_token_name_is_taken_once(
        self, credentials: CredentialStore
    ) -> None:
        await credentials.add_token("ci", secret_hash("one"), role=Role.CI)

        with pytest.raises(InvalidValueError, match="ci"):
            await credentials.add_token("ci", secret_hash("two"), role=Role.CI)
        assert await credentials.token_by_hash(secret_hash("two")) is None

    async def test_an_expired_token_is_not_found_but_still_listed(
        self, credentials: CredentialStore
    ) -> None:
        short = await credentials.add_token(
            "short", secret_hash("short"), role=Role.CI, expires_seconds=SHORT
        )
        long = await credentials.add_token(
            "long", secret_hash("long"), role=Role.CI, expires_seconds=LONG
        )
        assert short.expires_at is not None and long.expires_at is not None
        assert short.expires_at - short.created_at == timedelta(seconds=SHORT)

        await asyncio.sleep(PAST_SHORT)
        await credentials.delete_expired()

        assert await credentials.token_by_hash(secret_hash("short")) is None
        assert await credentials.token_by_hash(secret_hash("long")) == long
        assert [t.name for t in await credentials.tokens()] == ["long", "short"]

    async def test_a_deleted_token_is_gone(self, credentials: CredentialStore) -> None:
        await credentials.add_token("gone", secret_hash("g"), role=Role.CI)

        assert await credentials.delete_token("gone") is True
        assert await credentials.delete_token("gone") is False
        assert await credentials.token_by_hash(secret_hash("g")) is None
        assert await credentials.tokens() == []

    # Sessions.

    async def test_a_session_gives_back_its_principal(
        self, credentials: CredentialStore
    ) -> None:
        await credentials.add_session(secret_hash("s"), PERSON, seconds=LONG)

        assert await credentials.session_by_hash(secret_hash("s")) == PERSON
        assert await credentials.session_by_hash(secret_hash("other")) is None

    async def test_a_session_keeps_its_role(self, credentials: CredentialStore) -> None:
        worker = Principal(
            kind=PrincipalKind.SESSION,
            subject="x",
            name="x",
            role=Role.WORKER,
            queue="default",
        )
        await credentials.add_session(secret_hash("w"), worker, seconds=LONG)

        assert await credentials.session_by_hash(secret_hash("w")) == worker

    async def test_a_principal_with_nothing_optional_is_kept_as_it_is(
        self, credentials: CredentialStore
    ) -> None:
        bare = Principal(
            kind=PrincipalKind.SESSION,
            subject="x",
            name="x",
            role=Role.USER,
        )
        await credentials.add_session(secret_hash("bare"), bare, seconds=LONG)

        assert await credentials.session_by_hash(secret_hash("bare")) == bare

    async def test_an_expired_session_is_not_found(
        self, credentials: CredentialStore
    ) -> None:
        await credentials.add_session(secret_hash("short"), PERSON, seconds=SHORT)
        await credentials.add_session(secret_hash("long"), PERSON, seconds=LONG)

        await asyncio.sleep(PAST_SHORT)

        assert await credentials.session_by_hash(secret_hash("short")) is None
        assert await credentials.session_by_hash(secret_hash("long")) == PERSON
        await credentials.delete_expired()
        assert await credentials.delete_session(secret_hash("short")) is False
        assert await credentials.session_by_hash(secret_hash("long")) == PERSON

    async def test_sessions_are_ended_one_or_all(
        self, credentials: CredentialStore
    ) -> None:
        for key in ("a", "b", "c"):
            await credentials.add_session(secret_hash(key), PERSON, seconds=LONG)

        assert await credentials.delete_session(secret_hash("a")) is True
        assert await credentials.delete_session(secret_hash("a")) is False
        assert await credentials.session_by_hash(secret_hash("a")) is None
        assert await credentials.delete_sessions() == 2
        assert await credentials.session_by_hash(secret_hash("b")) is None
        assert await credentials.delete_sessions() == 0

    # Sign-ins in progress.

    async def test_a_login_is_taken_once(self, credentials: CredentialStore) -> None:
        assert await credentials.add_login(
            secret_hash("state"), LOGIN, seconds=LONG, limit=10
        )

        assert await credentials.take_login(secret_hash("state")) == LOGIN
        assert await credentials.take_login(secret_hash("state")) is None
        assert await credentials.take_login(secret_hash("never")) is None

    async def test_of_two_takes_at_once_one_gets_the_login(
        self, credentials: CredentialStore
    ) -> None:
        await credentials.add_login(secret_hash("state"), LOGIN, seconds=LONG, limit=10)

        taken = await asyncio.gather(
            *(credentials.take_login(secret_hash("state")) for _ in range(5))
        )

        assert taken.count(LOGIN) == 1
        assert taken.count(None) == 4

    async def test_an_expired_login_is_not_taken(
        self, credentials: CredentialStore
    ) -> None:
        await credentials.add_login(
            secret_hash("short"), LOGIN, seconds=SHORT, limit=10
        )
        await credentials.add_login(secret_hash("long"), LOGIN, seconds=LONG, limit=10)

        await asyncio.sleep(PAST_SHORT)
        await credentials.delete_expired()

        assert await credentials.take_login(secret_hash("short")) is None
        assert await credentials.take_login(secret_hash("long")) == LOGIN

    async def test_no_more_logins_than_the_limit_are_in_progress(
        self, credentials: CredentialStore
    ) -> None:
        added = [
            await credentials.add_login(
                secret_hash(f"s{n}"), LOGIN, seconds=LONG, limit=3
            )
            for n in range(4)
        ]
        await credentials.take_login(secret_hash("s0"))
        after_one_is_taken = await credentials.add_login(
            secret_hash("s4"), LOGIN, seconds=LONG, limit=3
        )

        assert added == [True, True, True, False]
        assert after_one_is_taken is True
        assert await credentials.take_login(secret_hash("s3")) is None

    async def test_expired_logins_do_not_count_toward_the_limit(
        self, credentials: CredentialStore
    ) -> None:
        for n in range(2):
            await credentials.add_login(
                secret_hash(f"short{n}"), LOGIN, seconds=SHORT, limit=2
            )

        await asyncio.sleep(PAST_SHORT)

        assert await credentials.add_login(
            secret_hash("fresh"), LOGIN, seconds=LONG, limit=2
        )
