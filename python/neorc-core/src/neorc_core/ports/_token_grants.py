# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The service tokens bound to queues, as the declarations of queues need them."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TokenGrant:
    """A service token's name, its role, and the queue its role is bound to."""

    name: str
    role: str
    """A role's name, such as ``neorc/worker``."""
    queue: str | None = None


class TokenGrants(ABC):
    """Where the service tokens are kept: oneberth's store, in a deployment."""

    @abstractmethod
    async def grants(self) -> list[TokenGrant]:
        """Every service token, expired ones included."""
