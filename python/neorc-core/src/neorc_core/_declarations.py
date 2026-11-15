# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Declaring queues, and the rules between a queue's kind and its tokens.

A queue is declared with its kind (see ``neorc_core._queues``). A token is
bound to a declared queue, and a worker token to a worker queue. A queue is
refused the external kind while a worker token is bound to it.

oneberth keeps the tokens. ``TokenGrants`` reads them, as plain records, so
the rules stay here with the store that keeps the declarations. Tokens and
declarations are in two stores, so the checks are not one step with what they
guard: the manager refusing workers an external queue is what holds.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from neorc_core._errors import InvalidValueError
from neorc_core._queues import Queue, QueueKind
from neorc_core._roles import QUEUE_ROLES, Role
from neorc_core.flows._definition import is_queue_name
from neorc_core.ports._store import Store
from neorc_core.ports._token_grants import TokenGrants


class QueueDeclarations:
    """The declared queues, on a store, checked against the tokens bound to them."""

    def __init__(self, store: Store, tokens: TokenGrants) -> None:
        self._store = store
        self._tokens = tokens

    async def declare(self, name: str, kind: QueueKind) -> None:
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
            bound = sorted(
                grant.name
                for grant in await self._tokens.grants()
                if grant.role == Role.WORKER and grant.queue == name
            )
            if bound:
                raise InvalidValueError(
                    f"queue {name!r} has worker tokens bound to it, and workers "
                    f"serve only worker queues: revoke {', '.join(bound)} first"
                )
        await self._store.declare_queue(name, kind)

    async def queues(self) -> list[Queue]:
        """Every declared queue, by name."""
        return await self._store.queues()

    async def check_token(self, role: str, queue: str | None) -> None:
        """Raise ``InvalidValueError`` unless ``role`` may be bound to ``queue``.

        ``check_token_queue`` says the rule.
        """
        await check_token_queue(role, queue, self._store.queue_kind)


async def check_token_queue(
    role: str,
    queue: str | None,
    queue_kind: Callable[[str], Awaitable[QueueKind | None]],
) -> None:
    """Raise ``InvalidValueError`` unless ``role`` may be bound to ``queue``.

    A worker or responder token is bound to a declared queue, and a worker
    token to a worker queue. ``queue_kind`` gives a queue's kind, or ``None``
    for a queue not declared. Any other role is bound to none, which oneberth
    checks.
    """
    if queue is None or role not in QUEUE_ROLES:
        return
    kind = await queue_kind(queue)
    if kind is None:
        raise InvalidValueError(
            f"queue {queue!r} is not declared: declare it before a token is bound to it"
        )
    if role == Role.WORKER and kind is QueueKind.EXTERNAL:
        raise InvalidValueError(
            f"queue {queue!r} is external: a worker token is bound to a worker queue"
        )
