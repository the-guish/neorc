# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""neorc's role table, given to oneberth.

``neorc_core`` names neorc's permissions and roles as plain data. Here they
become oneberth's ``RoleTable``, which the manager's component and the admin
scripts register. oneberth then decides whether a principal holds a
permission, on the queue a worker's or a responder's grant is bound to.

``service_token_check`` gives oneberth neorc's rule for a new service token:
a worker's or a responder's token names a declared queue of the right kind.
oneberth runs it for every service token it creates, through the interface,
a launcher or the admin scripts.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from oneberth.identity import ONEBERTH_ROLES, Registry, RoleTable, ServiceTokenCheck
from oneberth.identity import Role as OneberthRole

from neorc_core import (
    DEFAULT_ROLE,
    PEOPLE_ROLES,
    PERMISSIONS,
    QUEUE_RESOURCE,
    QUEUE_ROLES,
    InvalidValueError,
    Permission,
    QueueKind,
    Role,
    check_token_queue,
)

NEORC_ROLES = RoleTable(
    app="neorc",
    permissions=frozenset(permission.value for permission in Permission),
    roles=tuple(
        OneberthRole(
            role.value,
            frozenset(permission.value for permission in PERMISSIONS[role]),
            resource=QUEUE_RESOURCE if role in QUEUE_ROLES else None,
            people=role in PEOPLE_ROLES,
        )
        for role in Role
    ),
    default=DEFAULT_ROLE.value,
)
"""neorc's permissions and roles, as oneberth registers them."""


def registry() -> Registry:
    """oneberth's roles and neorc's: what a neorc deployment knows."""
    return Registry([ONEBERTH_ROLES, NEORC_ROLES])


def service_token_check(
    queue_kind: Callable[[str], Awaitable[QueueKind | None]],
) -> ServiceTokenCheck:
    """neorc's check of a new service token, for oneberth.

    ``queue_kind`` gives a queue's kind, or ``None`` for a queue not declared.
    The check answers why a token is refused, or ``None`` for a token neorc
    allows.
    """

    async def check(role: str, resource: str | None) -> str | None:
        try:
            await check_token_queue(role, resource, queue_kind)
        except InvalidValueError as exc:
            return str(exc)
        return None

    return check
