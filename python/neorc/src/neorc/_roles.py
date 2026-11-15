# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""neorc's role table, given to oneberth.

``neorc_core`` names neorc's permissions and roles as plain data. Here they
become oneberth's ``RoleTable``, which the manager's component and the admin
scripts register. oneberth then decides whether a principal holds a
permission, on the queue a worker's or a responder's grant is bound to.
"""

from __future__ import annotations

from oneberth.identity import ONEBERTH_ROLES, Registry, RoleTable
from oneberth.identity import Role as OneberthRole

from neorc_core import (
    DEFAULT_ROLE,
    PEOPLE_ROLES,
    PERMISSIONS,
    QUEUE_RESOURCE,
    QUEUE_ROLES,
    Permission,
    Role,
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
