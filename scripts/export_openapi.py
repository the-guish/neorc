# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Write the manager's OpenAPI document to the snapshot the UI build reads.

    uv run python scripts/export_openapi.py

The snapshot, ``ts/neorc-ui/openapi.json``, is committed; a test in ``neorc``
fails when it differs from the application's schema, so the UI's generated
types never drift from the routes, and the UI build needs no Python.

The document is the one oneberth writes for the ``neorc`` component, served at
``/neorc/openapi.json``: its paths start with ``/neorc/``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
from oneberth.identity.memory import MemoryIdentityStore
from oneberth.web import compose_app
from oneberth.web.testing import config_for

from neorc.manager import new_manager_webapp
from neorc_core import Manager
from neorc_core.local import MemoryStore, MemoryTaskNotifier

SNAPSHOT = Path(__file__).resolve().parents[1] / "ts" / "neorc-ui" / "openapi.json"


def openapi_text() -> str:
    """The schema as the snapshot holds it: sorted keys, indented, one final newline."""
    manager = Manager(
        MemoryStore(), tasks=MemoryTaskNotifier(), events=MemoryTaskNotifier()
    )
    app = compose_app(
        [new_manager_webapp(manager=manager)],
        config=config_for("neorc"),
        store=MemoryIdentityStore(),
    )

    async def document() -> object:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1"
        ) as client:
            answer = await client.get("/neorc/openapi.json")
            answer.raise_for_status()
            return answer.json()

    return json.dumps(asyncio.run(document()), indent=2, sort_keys=True) + "\n"


def main() -> int:
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(openapi_text())
    print(f"wrote {SNAPSHOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
