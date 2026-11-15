# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Service tokens kept in memory, for ``QueueDeclarations`` in one process."""

from __future__ import annotations

from collections.abc import Iterable

from neorc_core.ports._token_grants import TokenGrant, TokenGrants


class MemoryTokenGrants(TokenGrants):
    """A list of token grants, which a test or a script changes in place."""

    def __init__(self, grants: Iterable[TokenGrant] = ()) -> None:
        self.items: list[TokenGrant] = list(grants)

    async def grants(self) -> list[TokenGrant]:
        return list(self.items)
