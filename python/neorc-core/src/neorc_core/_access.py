# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Who is asking a manager, and the credentials that say so.

A ``Principal`` is the identity behind a request. It comes from an API token,
which a machine sends, or from a session, which a person opens by signing in
with an identity provider: the provider says who they are, as an ``Identity``,
and the deployment's allow list says whether they may sign in.

Every principal has a ``Role``: a token the one it was created with, a person
signed in ``USER``. A worker's token is bound to the one queue it serves, and
a responder's to the one queue whose tasks it completes.

A queue is declared with its kind (see ``neorc_core._queues``). A token is
bound to a declared queue, and a worker token to a worker queue. ``Access``
holds those rules where tokens are created and queues declared, when it is
given the store that keeps the declarations.

Tokens, sessions and sign-ins in progress are secrets handed out once. The
credential store keeps only their SHA-256: each has 256 random bits and is
never chosen by a person, so a plain hash is enough to make a stolen table
useless, and a lookup by hash needs no comparison in constant time.
"""

from __future__ import annotations

import hashlib
import math
import re
import secrets
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

from neorc_core._errors import (
    AuthenticationError,
    InvalidValueError,
    PermissionDeniedError,
    SignInRefusedError,
)
from neorc_core._queues import Queue, QueueKind
from neorc_core.flows._definition import is_queue_name

if TYPE_CHECKING:  # the ports name this module's records
    from neorc_core.ports._credentials import CredentialStore
    from neorc_core.ports._store import Store


class PrincipalKind(StrEnum):
    """What a principal authenticated with."""

    TOKEN = "token"
    SESSION = "session"


class Role(StrEnum):
    """What a principal is for; each role is a fixed set of permissions."""

    WORKER = "worker"
    """Takes and runs the tasks of the one queue its token is bound to."""
    RESPONDER = "responder"
    """Lists and completes the pending tasks of the one queue its token is bound to."""
    SCHEDULER = "scheduler"
    CI = "ci"
    """Uploads flows."""
    USER = "user"
    """A person: signed in, or with a token for their scripts."""
    EXTERNAL_TRIGGER = "external-trigger"
    """Starts runs, and nothing else."""


class Permission(StrEnum):
    """One operation on one kind of resource; every guarded route needs one."""

    FLOWS_UPLOAD = "flows:upload"
    FLOWS_READ = "flows:read"
    RUNS_START = "runs:start"
    RUNS_READ = "runs:read"
    """Runs, their tasks, sub-runs and state, and a task by id."""
    RUNS_CANCEL = "runs:cancel"
    RUNS_SCHEDULE = "runs:schedule"
    """The scheduler's requests: publish tasks, start sub-runs, finish runs."""
    EVENTS_READ = "events:read"
    QUEUE_DEFINITIONS = "queue:definitions"
    QUEUE_RECEIVE = "queue:receive"
    TASKS_CLAIM = "tasks:claim"
    TASKS_HEARTBEAT = "tasks:heartbeat"
    TASKS_FINISH = "tasks:finish"
    QUEUE_PENDING = "queue:pending"
    """List a queue's pending tasks, taking no lease."""
    TASKS_COMPLETE = "tasks:complete"
    """End a pending task by its id, with no lease before."""


WORKER_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.QUEUE_DEFINITIONS,
        Permission.QUEUE_RECEIVE,
        Permission.TASKS_CLAIM,
        Permission.TASKS_HEARTBEAT,
        Permission.TASKS_FINISH,
    }
)
"""What a worker may do, on the queue its token is bound to."""

RESPONDER_PERMISSIONS: frozenset[Permission] = frozenset(
    {Permission.QUEUE_PENDING, Permission.TASKS_COMPLETE}
)
"""What a responder may do, on the queue its token is bound to."""

QUEUE_PERMISSIONS: frozenset[Permission] = WORKER_PERMISSIONS | RESPONDER_PERMISSIONS
"""The permissions that apply to one queue: the one a token is bound to."""

QUEUE_ROLES: frozenset[Role] = frozenset({Role.WORKER, Role.RESPONDER})
"""The roles whose tokens are bound to one queue."""

PERMISSIONS: Mapping[Role, frozenset[Permission]] = MappingProxyType(
    {
        Role.WORKER: WORKER_PERMISSIONS,
        Role.RESPONDER: RESPONDER_PERMISSIONS,
        Role.SCHEDULER: frozenset(
            {
                Permission.FLOWS_READ,
                Permission.RUNS_READ,
                Permission.RUNS_SCHEDULE,
                Permission.EVENTS_READ,
            }
        ),
        Role.CI: frozenset({Permission.FLOWS_UPLOAD}),
        Role.USER: frozenset(
            {
                Permission.FLOWS_READ,
                Permission.RUNS_START,
                Permission.RUNS_READ,
                Permission.RUNS_CANCEL,
                Permission.EVENTS_READ,
            }
        ),
        Role.EXTERNAL_TRIGGER: frozenset({Permission.RUNS_START}),
    }
)
"""What each role may do; nothing else is allowed. Fixed: roles are not edited."""


def authorize(
    principal: Principal | None, permission: Permission, *, queue: str | None = None
) -> None:
    """Raise ``PermissionDeniedError`` unless ``principal`` may do ``permission``.

    ``None`` is no principal at all, with authentication off: everything is
    allowed. ``queue`` names the queue a request is about, when it names one; a
    queue permission then holds only for the queue the principal is bound to.
    A request about a task names no queue here: the manager compares the
    task's own queue with the principal's, and answers as if a task on another
    queue did not exist.
    """
    if principal is None:
        return
    if permission not in PERMISSIONS[principal.role]:
        raise PermissionDeniedError(
            f"a {principal.role.value} {principal.kind.value} may not "
            f"{permission.value}"
        )
    if (
        queue is not None
        and permission in QUEUE_PERMISSIONS
        and queue != principal.queue
    ):
        raise PermissionDeniedError(
            f"a {principal.role.value} {principal.kind.value} bound to queue "
            f"{principal.queue!r} may not {permission.value} on queue {queue!r}"
        )


def _role(value: str) -> Role:
    """``value`` as a ``Role``; ``InvalidValueError`` naming the roles otherwise."""
    try:
        return Role(value)
    except ValueError:
        roles = ", ".join(role.value for role in Role)
        raise InvalidValueError(f"{value!r} is not a role: one of {roles}") from None


def check_role(role: Role, queue: str | None) -> None:
    """Raise ``InvalidValueError`` unless a credential may have this role and queue.

    A worker or a responder is bound to one queue, named as queues are; no
    other role is.
    """
    if role in QUEUE_ROLES:
        if queue is None:
            raise InvalidValueError(
                f"a {role.value} token is bound to a queue: name one"
            )
        if not is_queue_name(queue):
            raise InvalidValueError(
                f"{queue!r} is not a queue name: letters, digits, _ and -"
            )
    elif queue is not None:
        raise InvalidValueError(
            "only a worker or responder token is bound to a queue, "
            f"not a {role.value} token"
        )


@dataclass(frozen=True, slots=True)
class Principal:
    """Who a request comes from."""

    kind: PrincipalKind
    subject: str
    """A token's id, or the provider's identifier for a person."""
    name: str
    """A token's name, or a person's name as their provider gave it."""
    role: Role
    queue: str | None = None
    """The queue a worker or responder is bound to; ``None`` for every other role."""
    provider: str | None = None
    """The identity provider a session was opened with; ``None`` for a token."""
    email: str | None = None
    """A session's address, only if its provider verified it."""


@dataclass(frozen=True, slots=True)
class Identity:
    """A person, as an identity provider described them at sign-in."""

    provider: str
    """The provider's id in the deployment's configuration."""
    subject: str
    """The provider's stable identifier for the person, never reassigned."""
    name: str | None = None
    email: str | None = None
    email_verified: bool = False
    """Whether the provider checked the person controls ``email``."""
    hosted_domain: str | None = None
    """Google's ``hd``: the Workspace domain the account belongs to."""
    groups: frozenset[str] = frozenset()


class Matcher(StrEnum):
    """What an allow entry looks at in an identity."""

    EVERYONE = "everyone"
    SUBJECT = "subject"
    EMAIL = "email"
    """A verified email address, compared without regard to case."""
    EMAIL_DOMAIN = "email_domain"
    """The domain of a verified email address, compared without regard to case.

    Whoever the provider verified an address at the domain for matches, which
    for Google includes personal accounts registered with a work address:
    ``HOSTED_DOMAIN`` is the one that says an account belongs to a Workspace.
    """
    HOSTED_DOMAIN = "hosted_domain"
    GROUP = "group"
    """A value of the provider's groups claim, compared exactly."""


@dataclass(frozen=True, slots=True)
class Allow:
    """The people of one provider that one matcher accepts, who may sign in."""

    provider: str
    matcher: Matcher
    value: str | None = None
    """What the matcher compares with; ``None`` for ``EVERYONE`` alone."""

    def __post_init__(self) -> None:
        if not self.provider:
            raise InvalidValueError("an allow entry names its provider")
        if self.matcher is Matcher.EVERYONE:
            if self.value is not None:
                raise InvalidValueError("allowing everyone compares with nothing")
        elif not self.value:
            raise InvalidValueError(
                f"an allow entry by {self.matcher.value} needs a value"
            )

    def matches(self, identity: Identity) -> bool:
        """Whether this entry lets ``identity`` sign in."""
        if identity.provider != self.provider:
            return False
        value = self.value or ""
        match self.matcher:
            case Matcher.EVERYONE:
                return True
            case Matcher.SUBJECT:
                return identity.subject == value
            case Matcher.EMAIL:
                email = _verified_email(identity)
                return email is not None and email == value.lower()
            case Matcher.EMAIL_DOMAIN:
                email = _verified_email(identity)
                return email is not None and email.rpartition("@")[2] == value.lower()
            case Matcher.HOSTED_DOMAIN:
                domain = identity.hosted_domain
                return domain is not None and domain.lower() == value.lower()
            case Matcher.GROUP:
                return value in identity.groups


def _verified_email(identity: Identity) -> str | None:
    """The identity's email, lower-cased, if the provider verified it."""
    email = identity.email
    if not identity.email_verified or not email or "@" not in email:
        return None
    return email.lower()


def allowed(identity: Identity, allow: Iterable[Allow]) -> bool:
    """Whether any entry of the allow list lets ``identity`` sign in."""
    return any(entry.matches(identity) for entry in allow)


@dataclass(frozen=True, slots=True)
class ApiToken:
    """What is kept of an API token: never the secret."""

    id: uuid.UUID
    name: str
    role: Role
    created_at: datetime
    queue: str | None = None
    """The queue a worker or responder token is bound to; ``None`` otherwise."""
    expires_at: datetime | None = None
    """When the token stops being accepted; ``None`` if never."""


@dataclass(frozen=True, slots=True)
class PendingLogin:
    """A sign-in started and not finished: what its callback needs."""

    provider: str
    nonce: str
    """Sent to the provider, and expected back in its ID token."""
    verifier: str
    """The PKCE code verifier; the provider was sent its challenge."""


TOKEN_PREFIX = "neorc_"
"""Every API token starts with it, so a leaked one is recognisable as ours."""

DEFAULT_SESSION_SECONDS = 12 * 60 * 60.0
DEFAULT_LOGIN_SECONDS = 10 * 60.0

MAX_PENDING_LOGINS = 10_000
"""How many sign-ins may be in progress at once.

Anyone may begin one, so this is what keeps a flood of them from growing the
table without end: past it, a sign-in is refused until some expire.
"""

SWEEP_SECONDS = 60.0
"""How often, at most, expired sessions and sign-ins are deleted."""

MAX_SECONDS = 5 * 366 * 24 * 60 * 60.0
"""The longest life a token, session or sign-in may be given."""

_SECRET_BYTES = 32
_MAX_SECRET_LENGTH = 256
"""Longer than any secret this module makes; a longer one is not looked up."""

_TOKEN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")


def secret_hash(secret: str) -> str:
    """What the credential store keeps of a secret, and finds it by."""
    return hashlib.sha256(secret.encode("utf-8", "surrogatepass")).hexdigest()


def check_seconds(seconds: float, what: str) -> None:
    """Raise ``InvalidValueError`` unless ``seconds`` is a life to give ``what``."""
    if (
        isinstance(seconds, bool)
        or not isinstance(seconds, int | float)
        or not math.isfinite(seconds)
        or not 0 < seconds <= MAX_SECONDS
    ):
        raise InvalidValueError(
            f"{what} must be over 0 and at most {MAX_SECONDS} seconds, not {seconds!r}"
        )


def is_token_name(text: str) -> bool:
    """Whether ``text`` may name a token: letters, digits, ``_``, ``.`` and ``-``."""
    return _TOKEN_NAME.fullmatch(text) is not None


class Access:
    """Tokens, sign-ins and sessions, on a credential store, under an allow list.

    Every refusal to authenticate is ``AuthenticationError``, saying what kind
    of credential failed and never repeating it.

    ``queues`` is the store that keeps the queue declarations. With it, a
    token is refused a queue nobody declared, a worker token is refused an
    external queue, and a queue is refused the external kind while a worker
    token is bound to it. Tokens and declarations
    are in two stores, so the two checks are not one step with what they
    guard: the manager refusing workers an external queue is what holds.
    """

    def __init__(
        self,
        credentials: CredentialStore,
        *,
        queues: Store | None = None,
        allow: Sequence[Allow] = (),
        session_seconds: float = DEFAULT_SESSION_SECONDS,
        login_seconds: float = DEFAULT_LOGIN_SECONDS,
        max_pending_logins: int = MAX_PENDING_LOGINS,
        sweep_seconds: float = SWEEP_SECONDS,
    ) -> None:
        check_seconds(session_seconds, "a session's life")
        check_seconds(login_seconds, "a sign-in's life")
        if max_pending_logins < 1:
            raise InvalidValueError("at least one sign-in must be allowed at once")
        self._credentials = credentials
        self._queues = queues
        self._allow = tuple(allow)
        self._session_seconds = session_seconds
        self._login_seconds = login_seconds
        self._max_pending_logins = max_pending_logins
        self._sweep_seconds = sweep_seconds
        self._swept_at: float | None = None

    @property
    def allow(self) -> tuple[Allow, ...]:
        return self._allow

    @property
    def session_seconds(self) -> float:
        """How long a session opened now lasts."""
        return self._session_seconds

    @property
    def login_seconds(self) -> float:
        """How long a sign-in begun now may take."""
        return self._login_seconds

    # API tokens.

    async def create_token(
        self,
        name: str,
        role: Role,
        *,
        queue: str | None = None,
        expires_seconds: float | None = None,
    ) -> tuple[str, ApiToken]:
        """A new token and its secret, which is not kept and cannot be shown again.

        Raises ``InvalidValueError`` for a name that is not one or is taken, a
        worker or responder token without a queue or another role's with one,
        a queue nobody declared, a worker token for an external queue, or a
        life out of bounds.
        """
        if not is_token_name(name):
            raise InvalidValueError(
                f"{name!r} is not a token name: up to 100 letters, digits, _, . "
                "and -, starting with a letter or digit"
            )
        role = _role(role)
        check_role(role, queue)
        if expires_seconds is not None:
            check_seconds(expires_seconds, "a token's life")
        if queue is not None and self._queues is not None:
            kind = await self._queues.queue_kind(queue)
            if kind is None:
                raise InvalidValueError(
                    f"queue {queue!r} is not declared: declare it before a token "
                    "is bound to it"
                )
            if role is Role.WORKER and kind is QueueKind.EXTERNAL:
                raise InvalidValueError(
                    f"queue {queue!r} is external: a worker token is bound to a "
                    "worker queue"
                )
        secret = TOKEN_PREFIX + secrets.token_urlsafe(_SECRET_BYTES)
        token = await self._credentials.add_token(
            name,
            secret_hash(secret),
            role=role,
            queue=queue,
            expires_seconds=expires_seconds,
        )
        return secret, token

    async def tokens(self) -> list[ApiToken]:
        """Every token, expired ones included, by name."""
        return await self._credentials.tokens()

    async def revoke_token(self, name: str) -> bool:
        """Delete the token called ``name``; whether there was one."""
        return await self._credentials.delete_token(name)

    # Queues.

    async def declare_queue(self, name: str, kind: QueueKind) -> None:
        """Declare ``name`` a queue of ``kind``, whatever it was declared before.

        Raises ``InvalidValueError`` for a name that is not a queue's, and for
        the external kind while a worker token is bound to the queue, expired
        or not: revoke those tokens first.
        """
        if not is_queue_name(name):
            raise InvalidValueError(
                f"{name!r} is not a queue name: letters, digits, _ and -"
            )
        if kind is QueueKind.EXTERNAL:
            bound = [
                token.name
                for token in await self._credentials.tokens()
                if token.role is Role.WORKER and token.queue == name
            ]
            if bound:
                raise InvalidValueError(
                    f"queue {name!r} has worker tokens bound to it, and workers "
                    f"serve only worker queues: revoke {', '.join(bound)} first"
                )
        await self._declarations().declare_queue(name, kind)

    async def queues(self) -> list[Queue]:
        """Every declared queue, by name."""
        return await self._declarations().queues()

    def _declarations(self) -> Store:
        if self._queues is None:
            raise RuntimeError("this Access was given no store of queue declarations")
        return self._queues

    async def authenticate_token(self, secret: str) -> Principal:
        """The principal a token secret stands for."""
        if not secret.startswith(TOKEN_PREFIX) or len(secret) > _MAX_SECRET_LENGTH:
            raise AuthenticationError("the API token is not one a manager issues")
        token = await self._credentials.token_by_hash(secret_hash(secret))
        if token is None:
            raise AuthenticationError("the API token is unknown, revoked or expired")
        return Principal(
            kind=PrincipalKind.TOKEN,
            subject=str(token.id),
            name=token.name,
            role=token.role,
            queue=token.queue,
        )

    # Signing in.

    async def begin_login(self, provider: str) -> tuple[str, PendingLogin]:
        """Start a sign-in with ``provider``: its ``state``, and what is kept for it.

        The ``state`` goes to the provider and comes back to the callback,
        which hands it to ``take_login``. Raises ``AuthenticationError`` when
        too many sign-ins are in progress already.
        """
        # Expired rows go as people sign in, at most once a sweep interval:
        # the tables need no job of their own, and a flood of sign-ins does
        # not become a flood of deletes.
        now = time.monotonic()
        if self._swept_at is None or now - self._swept_at >= self._sweep_seconds:
            self._swept_at = now
            await self._credentials.delete_expired()
        state = secrets.token_urlsafe(_SECRET_BYTES)
        login = PendingLogin(
            provider=provider,
            nonce=secrets.token_urlsafe(_SECRET_BYTES),
            # 64 characters, within the 43 to 128 PKCE allows.
            verifier=secrets.token_urlsafe(48),
        )
        stored = await self._credentials.add_login(
            secret_hash(state),
            login,
            seconds=self._login_seconds,
            limit=self._max_pending_logins,
        )
        if not stored:
            raise AuthenticationError(
                "too many sign-ins are in progress; try again in a few minutes"
            )
        return state, login

    async def take_login(self, provider: str, state: str) -> PendingLogin:
        """The sign-in ``state`` began, once: a second take of it is refused."""
        if not state or len(state) > _MAX_SECRET_LENGTH:
            raise AuthenticationError("the sign-in is not one this manager started")
        login = await self._credentials.take_login(secret_hash(state))
        if login is None:
            raise AuthenticationError("the sign-in is unknown, already used or expired")
        if login.provider != provider:
            raise AuthenticationError(
                f"the sign-in was started with another provider than {provider!r}"
            )
        return login

    async def open_session(self, identity: Identity) -> tuple[str, Principal]:
        """A session for ``identity``, if the allow list lets them sign in.

        Raises ``SignInRefusedError`` when no entry matches: the person is who
        they say, and not let in.
        """
        if not allowed(identity, self._allow):
            who = identity.email or identity.subject
            raise SignInRefusedError(
                f"no allow entry lets {who!r} of {identity.provider!r} sign in"
            )
        # An address the provider did not verify is not the person's: it is
        # neither kept nor shown as their name.
        email = identity.email if identity.email_verified else None
        principal = Principal(
            kind=PrincipalKind.SESSION,
            subject=identity.subject,
            name=identity.name or email or identity.subject,
            role=Role.USER,
            provider=identity.provider,
            email=email,
        )
        secret = secrets.token_urlsafe(_SECRET_BYTES)
        await self._credentials.add_session(
            secret_hash(secret), principal, seconds=self._session_seconds
        )
        return secret, principal

    async def authenticate_session(self, secret: str) -> Principal:
        """The principal a session secret stands for."""
        if not secret or len(secret) > _MAX_SECRET_LENGTH:
            raise AuthenticationError("the session is not one this manager opened")
        principal = await self._credentials.session_by_hash(secret_hash(secret))
        if principal is None:
            raise AuthenticationError("the session is unknown, ended or expired")
        return principal

    async def end_session(self, secret: str) -> None:
        """Sign out: the session is no longer accepted. Nothing if it was not."""
        if secret and len(secret) <= _MAX_SECRET_LENGTH:
            await self._credentials.delete_session(secret_hash(secret))

    async def end_sessions(self) -> int:
        """Sign everyone out; how many sessions there were."""
        return await self._credentials.delete_sessions()
