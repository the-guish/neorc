# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Serving the built web UI next to the API.

The assets come from the ``neorc-ui`` distribution and are plain files under
one path prefix; the app uses hash routing, so ``index.html`` is the only page
and no fallback route is needed. Every response under the prefix carries a
Content-Security-Policy that keeps scripts, styles and connections to the
page's own origin: a compromised bundled package can then neither load more
code nor send what it reads to another host. Inline styles are allowed, since
React and its libraries set ``style`` attributes; inline scripts are not, and
the built ``index.html`` has none.

The UI refuses to be framed (``frame-ancestors 'none'``), unless the manager
is started with the origins that may frame it: an application that embeds the
UI in its own pages, on an origin of its own, names its origin and nothing
else is let in. ``frame_ancestors`` checks each is an origin and nothing more.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

UI_PATH = "/ui"

_HOST = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*")
"""A host name, or an IPv4 address, as a policy may name one: no wildcard."""


def frame_ancestors(origins: Iterable[str]) -> tuple[str, ...]:
    """The origins that may frame the UI, each checked and lowercased.

    An origin is a scheme, ``http`` or ``https``, a host and an optional
    port: ``https://app.example.com`` or ``http://127.0.0.1:8000``. A path,
    a query, a fragment, a user, a wildcard, a quote or a space would each
    change what the policy says, so ``ValueError`` names the first that is
    not an origin.
    """
    checked: list[str] = []
    for origin in origins:
        lowered = origin.lower()
        try:
            parts = urlsplit(lowered)
            port = parts.port
        except ValueError:
            raise ValueError(f"{origin!r} is not an origin") from None
        host = parts.hostname or ""
        is_ipv6 = parts.netloc.startswith("[")
        if (
            parts.scheme not in ("http", "https")
            or lowered != f"{parts.scheme}://{parts.netloc}"
            or "@" in parts.netloc
            or parts.netloc.endswith(":")
            or not host
            or (not is_ipv6 and not _HOST.fullmatch(host))
            or (is_ipv6 and not re.fullmatch(r"[0-9a-f:.]+", host))
            or (port is not None and not 0 < port < 65536)
        ):
            raise ValueError(
                f"{origin!r} is not an origin: a scheme, http or https, a host "
                "and an optional port, such as https://app.example.com"
            )
        if lowered not in checked:
            checked.append(lowered)
    return tuple(checked)


def content_security_policy(ancestors: tuple[str, ...] = ()) -> str:
    """The UI's policy, framed by ``ancestors``, checked by ``frame_ancestors``,
    or by nothing."""
    framed = " ".join(ancestors) or "'none'"
    return "; ".join(
        [
            "default-src 'none'",
            "script-src 'self'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data:",
            "font-src 'self'",
            "connect-src 'self'",
            "base-uri 'none'",
            "form-action 'self'",
            f"frame-ancestors {framed}",
        ]
    )


CONTENT_SECURITY_POLICY = content_security_policy()
"""The policy of a UI that no page may frame: the default."""

_IMMUTABLE = b"public, max-age=31536000, immutable"
"""Hashed assets never change under their name, so a browser may keep them."""

_NO_CACHE = b"no-cache"
"""``index.html`` names the current hashes; a browser must ask each time."""


def mount_ui(
    app: FastAPI,
    static: Path,
    *,
    path: str = UI_PATH,
    framed_by: Iterable[str] = (),
) -> None:
    """Serve the built UI in ``static`` under ``path``, and send ``/`` there.

    ``static`` must hold an ``index.html``, as ``neorc_ui.static_dir()``
    checks; the manager's tests hand in a stand-in directory. ``framed_by``
    are the origins that may frame it; ``ValueError`` for one that is not.
    """
    policy = content_security_policy(frame_ancestors(framed_by))
    if not (static / "index.html").is_file():
        raise FileNotFoundError(f"{static} holds no index.html to serve")
    index = f"{path}/"

    @app.get("/", include_in_schema=False)
    async def to_ui() -> RedirectResponse:
        return RedirectResponse(index)

    app.mount(
        path, _Headed(StaticFiles(directory=static, html=True), policy), name="ui"
    )


class _Headed:
    """The static files, with the cache and security headers on every response."""

    def __init__(self, files: ASGIApp, policy: str = CONTENT_SECURITY_POLICY) -> None:
        self._files = files
        self._policy = policy.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._files(scope, receive, send)
            return
        # The path below the mount: the scope keeps the whole one, and the
        # mount's own prefix in root_path.
        below = scope["path"].removeprefix(scope.get("root_path", ""))
        hashed = below.startswith("/assets/")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # Only a hashed asset that is there is immutable: a 404 kept
                # for a year would outlive the deploy that adds the file.
                keep = hashed and message["status"] in (200, 304)
                headers.append((b"cache-control", _IMMUTABLE if keep else _NO_CACHE))
                headers.append((b"content-security-policy", self._policy))
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"referrer-policy", b"same-origin"))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self._files(scope, receive, send_with_headers)
        except HTTPException as exc:
            # A missing file: answered here, so it carries the headers too,
            # rather than by the application's handler outside them.
            response = PlainTextResponse(
                str(exc.detail), status_code=exc.status_code, headers=exc.headers
            )
            await response(scope, receive, send_with_headers)
