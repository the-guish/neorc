# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""``neorc-mcp``: the server over streamable HTTP at ``/mcp``, behind a key.

Every request carries ``Authorization: Bearer <NEORC_MCP_KEY>``, the key a
host such as Robinauts sends from its tool server's ``secret_env``. The server
refuses to start without one. docs/specs/mcp.md, "Running it".
"""

from __future__ import annotations

import argparse
import hmac
import os
import sys
from collections.abc import Sequence
from typing import Any

from neorc_mcp._client import ManagerApi
from neorc_mcp._server import build_server

KEY_ENV = "NEORC_MCP_KEY"
HOST_ENV = "NEORC_MCP_HOST"
PORT_ENV = "NEORC_MCP_PORT"
MANAGER_ADDRESS_ENV = "NEORC_MANAGER_ADDRESS"
API_TOKEN_ENV = "NEORC_API_TOKEN"
CI_TOKEN_ENV = "NEORC_CI_TOKEN"
ALLOW_INSECURE_HTTP_ENV = "NEORC_ALLOW_INSECURE_HTTP"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8938

ASGIApp = Any


class RequireKey:
    """An ASGI app that lets an HTTP request through only with the key.

    Lifespan events pass, so the app behind it starts and stops as usual.
    """

    def __init__(self, app: ASGIApp, key: str) -> None:
        self._app = app
        self._expected = f"Bearer {key}".encode()

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            sent = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(sent, self._expected):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"www-authenticate", b"Bearer"),
                        ],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b'{"error": "a key is needed: Authorization: Bearer"}',
                    }
                )
                return
        await self._app(scope, receive, send)


def app_from_environment(
    environ: dict[str, str], host: str, port: int
) -> tuple[ASGIApp, ManagerApi]:
    """The server's ASGI app, and its client of the manager, as the variables say."""
    key = environ.get(KEY_ENV)
    if not key:
        raise SystemExit(
            f"{KEY_ENV} is not set: it is the key a host sends to this server"
        )
    address = environ.get(MANAGER_ADDRESS_ENV)
    if not address:
        raise SystemExit(f"{MANAGER_ADDRESS_ENV} is not set: it names the manager")
    try:
        api = ManagerApi(
            address,
            person_token=environ.get(API_TOKEN_ENV) or None,
            ci_token=environ.get(CI_TOKEN_ENV) or None,
            allow_insecure=environ.get(ALLOW_INSECURE_HTTP_ENV) == "1",
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from None

    def view_html() -> str:
        import neorc_ui

        return neorc_ui.mcp_view_html()

    server = build_server(api, view_html, host=host, port=port)
    return RequireKey(server.streamable_http_app(), key), api


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="neorc-mcp",
        description="neorc's manager as an MCP server, over streamable HTTP at /mcp.",
    )
    parser.add_argument("--host", default=os.environ.get(HOST_ENV, DEFAULT_HOST))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get(PORT_ENV, DEFAULT_PORT))
    )
    arguments = parser.parse_args(argv)
    app, api = app_from_environment(dict(os.environ), arguments.host, arguments.port)

    import neorc_ui

    try:
        neorc_ui.mcp_view_html()
    except FileNotFoundError as exc:
        print(f"neorc-mcp: the panel has no view: {exc}", file=sys.stderr)

    import uvicorn

    print(
        f"neorc-mcp at http://{arguments.host}:{arguments.port}/mcp, "
        f"for the manager at {api.base_url}",
        file=sys.stderr,
        flush=True,
    )
    uvicorn.run(app, host=arguments.host, port=arguments.port, log_level="warning")
    return 0
