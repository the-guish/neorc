# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""neorc's roles and permissions, as docs/specs/roles.md lists them."""

from __future__ import annotations

import pytest

from neorc_core import (
    DEFAULT_ROLE,
    PEOPLE_ROLES,
    PERMISSIONS,
    QUEUE_PERMISSIONS,
    QUEUE_ROLES,
    Permission,
    Role,
)

ALLOWED = {
    "neorc/worker": {
        "queue:definitions",
        "queue:receive",
        "tasks:claim",
        "tasks:heartbeat",
        "tasks:finish",
    },
    "neorc/responder": {"queue:pending", "tasks:complete"},
    "neorc/scheduler": {"flows:read", "runs:read", "runs:schedule", "events:read"},
    "neorc/ci": {"flows:upload", "queues:declare"},
    "neorc/user": {
        "flows:read",
        "runs:start",
        "runs:read",
        "runs:cancel",
        "events:read",
        "queues:read",
        "tasks:complete",
    },
    "neorc/external-trigger": {"runs:start"},
}
"""roles.md, written out here rather than read from ``PERMISSIONS``."""


@pytest.mark.parametrize("role", list(Role))
def test_a_role_may_do_what_roles_md_lists_and_nothing_else(role: Role) -> None:
    assert {p.removeprefix("neorc/") for p in PERMISSIONS[role]} == ALLOWED[role]


def test_every_role_and_permission_is_named_under_neorc() -> None:
    assert set(ALLOWED) == {role.value for role in Role}
    assert all(p.value.startswith("neorc/") for p in Permission)


def test_every_permission_is_some_roles() -> None:
    assert set().union(*ALLOWED.values()) == {
        p.value.removeprefix("neorc/") for p in Permission
    }


def test_a_worker_and_a_responder_are_bound_to_a_queue() -> None:
    assert {Role.WORKER, Role.RESPONDER} == QUEUE_ROLES
    assert PERMISSIONS[Role.WORKER] | PERMISSIONS[Role.RESPONDER] == QUEUE_PERMISSIONS


def test_a_person_holds_the_user_role_alone() -> None:
    assert {Role.USER} == PEOPLE_ROLES
    assert DEFAULT_ROLE is Role.USER
