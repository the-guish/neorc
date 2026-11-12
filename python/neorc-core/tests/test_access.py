# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Tokens, the allow list, sign-ins and sessions, on the memory credential store."""

from __future__ import annotations

import asyncio

import pytest

from neorc_core import (
    Access,
    Allow,
    AuthenticationError,
    Identity,
    InvalidValueError,
    Matcher,
    Permission,
    PermissionDeniedError,
    Principal,
    PrincipalKind,
    Role,
    SignInRefusedError,
    authorize,
)
from neorc_core._access import (
    MAX_SECONDS,
    QUEUE_ROLES,
    TOKEN_PREFIX,
    allowed,
    secret_hash,
)
from neorc_core.local import MemoryCredentialStore

ADA = Identity(
    provider="google",
    subject="248289761001",
    name="Ada Lovelace",
    email="Ada@Example.com",
    email_verified=True,
    hosted_domain="example.com",
    groups=frozenset({"neorc-users"}),
)

EVERYONE = Allow("google", Matcher.EVERYONE)


@pytest.fixture
def credentials() -> MemoryCredentialStore:
    return MemoryCredentialStore()


@pytest.fixture
def access(credentials: MemoryCredentialStore) -> Access:
    return Access(
        credentials,
        allow=[
            Allow("google", Matcher.HOSTED_DOMAIN, "example.com"),
            Allow("okta", Matcher.GROUP, "neorc-users"),
        ],
    )


# The allow list.


@pytest.mark.parametrize(
    ("matcher", "value", "matches"),
    [
        (Matcher.EVERYONE, None, True),
        (Matcher.SUBJECT, "248289761001", True),
        (Matcher.SUBJECT, "24828976100", False),
        (Matcher.EMAIL, "ada@example.com", True),
        (Matcher.EMAIL, "ADA@EXAMPLE.COM", True),
        (Matcher.EMAIL, "ada@example.org", False),
        (Matcher.EMAIL_DOMAIN, "EXAMPLE.com", True),
        (Matcher.EMAIL_DOMAIN, "ample.com", False),
        (Matcher.HOSTED_DOMAIN, "example.com", True),
        (Matcher.HOSTED_DOMAIN, "example.org", False),
        (Matcher.GROUP, "neorc-users", True),
        (Matcher.GROUP, "Neorc-Users", False),
    ],
)
def test_an_entry_matches_what_its_matcher_names(
    matcher: Matcher, value: str | None, matches: bool
) -> None:
    assert Allow("google", matcher, value).matches(ADA) is matches


def test_an_entry_matches_only_its_own_providers_people() -> None:
    assert not Allow("okta", Matcher.EVERYONE).matches(ADA)


def test_an_unverified_email_matches_no_email_entry() -> None:
    unverified = Identity("google", "1", email="ada@example.com")
    no_at = Identity("google", "1", email="example.com", email_verified=True)

    for identity in (unverified, no_at):
        assert not Allow("google", Matcher.EMAIL, "ada@example.com").matches(identity)
        assert not Allow("google", Matcher.EMAIL_DOMAIN, "example.com").matches(
            identity
        )


def test_an_identity_without_a_hosted_domain_matches_no_hosted_domain_entry() -> None:
    personal = Identity("google", "1", email="ada@example.com", email_verified=True)

    assert not Allow("google", Matcher.HOSTED_DOMAIN, "example.com").matches(personal)
    assert Allow("google", Matcher.EMAIL_DOMAIN, "example.com").matches(personal)


def test_one_matching_entry_is_enough() -> None:
    entries = [
        Allow("google", Matcher.GROUP, "nobody"),
        Allow("google", Matcher.SUBJECT, ADA.subject),
    ]

    assert allowed(ADA, entries)
    assert not allowed(ADA, entries[:1])
    assert not allowed(ADA, [])


@pytest.mark.parametrize(
    ("provider", "matcher", "value", "problem"),
    [
        ("", Matcher.EVERYONE, None, "provider"),
        ("google", Matcher.EVERYONE, "x", "compares with nothing"),
        ("google", Matcher.EMAIL, None, "needs a value"),
        ("google", Matcher.GROUP, "", "needs a value"),
    ],
)
def test_an_entry_that_cannot_mean_anything_is_refused(
    provider: str, matcher: Matcher, value: str | None, problem: str
) -> None:
    with pytest.raises(InvalidValueError, match=problem):
        Allow(provider, matcher, value)


# API tokens.


async def test_a_token_authenticates_as_its_name_and_role(access: Access) -> None:
    secret, token = await access.create_token("ci", Role.CI)

    principal = await access.authenticate_token(secret)

    assert secret.startswith(TOKEN_PREFIX) and len(secret) > 40
    assert principal == Principal(PrincipalKind.TOKEN, str(token.id), "ci", Role.CI)


async def test_a_worker_token_is_bound_to_its_queue(access: Access) -> None:
    secret, token = await access.create_token(
        "gpu-worker-1", Role.WORKER, queue="python-gpu"
    )

    principal = await access.authenticate_token(secret)

    assert (token.role, token.queue) == (Role.WORKER, "python-gpu")
    assert (principal.role, principal.queue) == (Role.WORKER, "python-gpu")


@pytest.mark.parametrize(
    ("role", "queue", "problem"),
    [
        (Role.WORKER, None, "bound to a queue: name one"),
        (Role.WORKER, "", "not a queue name"),
        (Role.WORKER, "python.gpu", "not a queue name"),
        (Role.WORKER, "a/b", "not a queue name"),
        (Role.RESPONDER, None, "a responder token is bound to a queue"),
        (Role.RESPONDER, "a/b", "not a queue name"),
        (Role.CI, "default", "only a worker or responder token"),
        (Role.USER, "default", "only a worker or responder token"),
        ("admin", None, "'admin' is not a role: one of worker, responder"),
    ],
)
async def test_a_token_with_a_role_it_cannot_have_is_refused(
    access: Access, role: Role, queue: str | None, problem: str
) -> None:
    with pytest.raises(InvalidValueError, match=problem):
        await access.create_token("t", role, queue=queue)
    assert await access.tokens() == []


async def test_a_token_secret_is_not_kept(
    access: Access, credentials: MemoryCredentialStore
) -> None:
    secret, _ = await access.create_token("ci", Role.CI)

    assert secret not in repr(vars(credentials))
    assert secret_hash(secret) in repr(vars(credentials))


async def test_every_new_token_has_a_secret_of_its_own(access: Access) -> None:
    first, _ = await access.create_token("one", Role.CI)
    second, _ = await access.create_token("two", Role.CI)

    assert first != second
    assert (await access.authenticate_token(second)).name == "two"


@pytest.mark.parametrize(
    "secret",
    ["", "neorc_", "not-ours", "neorc_" + "x" * 43, "neorc_" + "x" * 300],
)
async def test_a_secret_that_is_no_token_is_refused_without_repeating_it(
    access: Access, secret: str
) -> None:
    await access.create_token("ci", Role.CI)

    with pytest.raises(AuthenticationError) as refused:
        await access.authenticate_token(secret)
    assert "API token" in str(refused.value)
    if secret:
        assert secret not in str(refused.value)


async def test_a_revoked_token_is_refused(access: Access) -> None:
    secret, _ = await access.create_token("ci", Role.CI)

    assert await access.revoke_token("ci") is True
    assert await access.revoke_token("ci") is False
    with pytest.raises(AuthenticationError, match="revoked"):
        await access.authenticate_token(secret)
    assert await access.tokens() == []


async def test_an_expired_token_is_refused_and_still_listed(access: Access) -> None:
    secret, token = await access.create_token("brief", Role.CI, expires_seconds=0.1)
    assert token.expires_at is not None

    await asyncio.sleep(0.2)

    with pytest.raises(AuthenticationError, match="expired"):
        await access.authenticate_token(secret)
    assert [t.name for t in await access.tokens()] == ["brief"]


@pytest.mark.parametrize(
    ("name", "expires", "problem"),
    [
        ("", None, "not a token name"),
        ("-ci", None, "not a token name"),
        ("ci/1", None, "not a token name"),
        ("x" * 101, None, "not a token name"),
        ("ci", 0, "token's life"),
        ("ci", float("inf"), "token's life"),
        ("ci", MAX_SECONDS * 2, "token's life"),
    ],
)
async def test_a_token_that_cannot_be_made_is_refused(
    access: Access, name: str, expires: float | None, problem: str
) -> None:
    with pytest.raises(InvalidValueError, match=problem):
        await access.create_token(name, Role.CI, expires_seconds=expires)
    assert await access.tokens() == []


async def test_a_token_name_is_used_once(access: Access) -> None:
    await access.create_token("ci.deploy-1", Role.CI)

    with pytest.raises(InvalidValueError, match="exists"):
        await access.create_token("ci.deploy-1", Role.CI)


# Signing in.


async def test_a_sign_in_opens_a_session(access: Access) -> None:
    state, login = await access.begin_login("google")

    taken = await access.take_login("google", state)
    secret, principal = await access.open_session(ADA)

    assert taken == login
    assert login.nonce != login.verifier and 43 <= len(login.verifier) <= 128
    assert principal == Principal(
        kind=PrincipalKind.SESSION,
        subject=ADA.subject,
        name="Ada Lovelace",
        role=Role.USER,
        provider="google",
        email="Ada@Example.com",
    )
    assert await access.authenticate_session(secret) == principal


async def test_a_sign_in_is_taken_once_and_by_its_own_provider(
    access: Access,
) -> None:
    state, _ = await access.begin_login("google")
    other, _ = await access.begin_login("google")

    with pytest.raises(AuthenticationError, match="another provider"):
        await access.take_login("okta", state)
    with pytest.raises(AuthenticationError, match="already used"):
        await access.take_login("google", state)
    with pytest.raises(AuthenticationError, match="already used"):
        await access.take_login("google", "a state never given")
    with pytest.raises(AuthenticationError, match="not one"):
        await access.take_login("google", "")
    assert (await access.take_login("google", other)).provider == "google"


async def test_an_expired_sign_in_is_refused(
    credentials: MemoryCredentialStore,
) -> None:
    access = Access(credentials, login_seconds=0.1)
    state, _ = await access.begin_login("google")

    await asyncio.sleep(0.2)

    with pytest.raises(AuthenticationError, match="expired"):
        await access.take_login("google", state)


async def test_a_person_no_entry_matches_is_not_let_in(access: Access) -> None:
    stranger = Identity("google", "7", email="eve@example.org", email_verified=True)

    with pytest.raises(SignInRefusedError, match=r"eve@example\.org"):
        await access.open_session(stranger)


async def test_with_no_allow_list_nobody_signs_in(
    credentials: MemoryCredentialStore,
) -> None:
    with pytest.raises(SignInRefusedError):
        await Access(credentials).open_session(ADA)


async def test_a_name_falls_back_to_the_verified_email_then_the_subject(
    credentials: MemoryCredentialStore,
) -> None:
    access = Access(credentials, allow=[EVERYONE])

    _, by_email = await access.open_session(
        Identity("google", "1", email="ada@example.com", email_verified=True)
    )
    _, by_subject = await access.open_session(Identity("google", "1"))

    assert (by_email.name, by_email.email) == ("ada@example.com", "ada@example.com")
    assert by_subject.name == "1"


async def test_an_unverified_email_is_neither_kept_nor_shown(
    credentials: MemoryCredentialStore,
) -> None:
    access = Access(credentials, allow=[EVERYONE])

    _, principal = await access.open_session(
        Identity("google", "1", email="ceo@example.com")
    )

    assert (principal.name, principal.email) == ("1", None)


# Sessions.


async def test_a_signed_out_session_is_refused(access: Access) -> None:
    secret, _ = await access.open_session(ADA)
    kept, _ = await access.open_session(ADA)

    await access.end_session(secret)
    await access.end_session(secret)
    await access.end_session("")

    with pytest.raises(AuthenticationError, match="ended"):
        await access.authenticate_session(secret)
    assert (await access.authenticate_session(kept)).subject == ADA.subject
    assert await access.end_sessions() == 1
    with pytest.raises(AuthenticationError):
        await access.authenticate_session(kept)


async def test_an_expired_session_is_refused(
    credentials: MemoryCredentialStore,
) -> None:
    access = Access(credentials, allow=[EVERYONE], session_seconds=0.1)
    secret, _ = await access.open_session(ADA)

    await asyncio.sleep(0.2)

    with pytest.raises(AuthenticationError, match="expired"):
        await access.authenticate_session(secret)


@pytest.mark.parametrize("secret", ["", "x" * 300, "never opened"])
async def test_a_secret_that_is_no_session_is_refused(
    access: Access, secret: str
) -> None:
    with pytest.raises(AuthenticationError, match="session"):
        await access.authenticate_session(secret)


async def test_beginning_a_sign_in_sweeps_away_what_has_expired(
    credentials: MemoryCredentialStore,
) -> None:
    access = Access(
        credentials,
        allow=[EVERYONE],
        session_seconds=0.1,
        login_seconds=0.1,
        sweep_seconds=0,
    )
    first, _ = await access.begin_login("google")
    session, _ = await access.open_session(ADA)
    await asyncio.sleep(0.2)

    await access.begin_login("google")

    assert await credentials.take_login(secret_hash(first)) is None
    assert await credentials.delete_session(secret_hash(session)) is False
    assert await credentials.delete_sessions() == 0


async def test_expired_rows_are_swept_at_most_once_an_interval(
    credentials: MemoryCredentialStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    sweeps: list[None] = []
    original = credentials.delete_expired

    async def counted() -> None:
        sweeps.append(None)
        await original()

    monkeypatch.setattr(credentials, "delete_expired", counted)
    access = Access(credentials, sweep_seconds=3600)

    for _ in range(5):
        await access.begin_login("google")

    assert len(sweeps) == 1


async def test_a_sign_in_past_the_limit_is_refused_until_one_is_taken(
    credentials: MemoryCredentialStore,
) -> None:
    access = Access(credentials, max_pending_logins=2)
    first, _ = await access.begin_login("google")
    await access.begin_login("google")

    with pytest.raises(AuthenticationError, match="too many sign-ins"):
        await access.begin_login("google")
    await access.take_login("google", first)
    await access.begin_login("google")


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), MAX_SECONDS + 1])
def test_lives_out_of_bounds_are_refused(
    credentials: MemoryCredentialStore, seconds: float
) -> None:
    with pytest.raises(InvalidValueError, match="session's life"):
        Access(credentials, session_seconds=seconds)
    with pytest.raises(InvalidValueError, match="sign-in's life"):
        Access(credentials, login_seconds=seconds)


# Permissions.

ALLOWED = {
    Role.WORKER: {
        "queue:definitions",
        "queue:receive",
        "tasks:claim",
        "tasks:heartbeat",
        "tasks:finish",
    },
    Role.RESPONDER: {"queue:pending", "tasks:complete"},
    Role.SCHEDULER: {"flows:read", "runs:read", "runs:schedule", "events:read"},
    Role.CI: {"flows:upload"},
    Role.USER: {
        "flows:read",
        "runs:start",
        "runs:read",
        "runs:cancel",
        "events:read",
    },
    Role.EXTERNAL_TRIGGER: {"runs:start"},
}
"""roles.md, written out here rather than read from ``PERMISSIONS``."""


def token(role: Role, queue: str | None = None) -> Principal:
    return Principal(PrincipalKind.TOKEN, "id", "t", role, queue)


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("permission", list(Permission))
def test_a_role_may_do_what_roles_md_lists_and_nothing_else(
    role: Role, permission: Permission
) -> None:
    principal = token(role, "default" if role in QUEUE_ROLES else None)

    if permission.value in ALLOWED[role]:
        authorize(principal, permission)
    else:
        with pytest.raises(PermissionDeniedError, match=permission.value):
            authorize(principal, permission)


def test_every_permission_is_some_roles() -> None:
    assert set().union(*ALLOWED.values()) == {p.value for p in Permission}


def test_with_authentication_off_everything_is_allowed() -> None:
    for permission in Permission:
        authorize(None, permission, queue="any")


def test_a_worker_may_act_on_its_own_queue_only() -> None:
    worker = token(Role.WORKER, "default")

    authorize(worker, Permission.QUEUE_RECEIVE, queue="default")
    authorize(worker, Permission.TASKS_CLAIM)
    with pytest.raises(PermissionDeniedError, match="bound to queue 'default'"):
        authorize(worker, Permission.QUEUE_RECEIVE, queue="python-gpu")
    with pytest.raises(PermissionDeniedError, match="on queue 'python-gpu'"):
        authorize(worker, Permission.QUEUE_DEFINITIONS, queue="python-gpu")


def test_a_responder_may_act_on_its_own_queue_only() -> None:
    responder = token(Role.RESPONDER, "editors")

    authorize(responder, Permission.QUEUE_PENDING, queue="editors")
    authorize(responder, Permission.TASKS_COMPLETE)
    with pytest.raises(PermissionDeniedError, match="bound to queue 'editors'"):
        authorize(responder, Permission.QUEUE_PENDING, queue="default")


def test_a_refusal_says_who_was_refused_what() -> None:
    person = Principal(PrincipalKind.SESSION, "sub", "Ada", Role.USER)

    with pytest.raises(PermissionDeniedError) as refused:
        authorize(person, Permission.FLOWS_UPLOAD)

    assert str(refused.value) == "a user session may not flows:upload"
