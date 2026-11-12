# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Who is asking, and whether a write may come from where it came.

One dependency guards every route of the router. Before the route reads its
body, or FastAPI reads its path and query:

- A write, any method but ``GET``, ``HEAD`` and ``OPTIONS``, is refused when a
  browser says a page on another site sent it (``Sec-Fetch-Site``), and when
  it is not sent as ``application/json``: a form, or a ``fetch`` a page may
  send without a preflight, cannot carry that type, and the manager answers no
  preflight. This holds with authentication off too, where no credential
  stands between a web page and the manager. It needs every route to read its
  body through ``read_body``, never through a declared body parameter, which
  FastAPI would read before any dependency runs.
- With authentication on, the request must carry an API token as
  ``Authorization: Bearer``, or, with sign-in configured, a session cookie;
  not both. A session's write must also come from a page on the public URL:
  its ``Origin``, or with none ``Sec-Fetch-Site: same-origin``, since a
  cookie goes with any request the browser sends. A token is not ambient, so
  a token's request is not checked so. The principal is the dependency's
  value.

Then each route asks, through ``permit``, whether the principal's role may do
the one permission the route needs, on the queue in its path if it names one.
Core decides; a route whose task is on another queue than a worker's is
refused by the manager itself, as not found.

Routes outside the router, ``/health`` and the UI, hold no data and are not
guarded.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from neorc_core import (
    QUEUE_PERMISSIONS,
    Access,
    AuthenticationError,
    CrossSiteRequestError,
    Permission,
    Principal,
    UnsupportedMediaTypeError,
    authorize,
)

if TYPE_CHECKING:  # the sign-in module uses this one's checks
    from neorc.manager._sign_in import SignIn

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

_CROSS_SITE = frozenset({"cross-site", "same-site"})

_bearer = HTTPBearer(
    auto_error=False,
    description="An API token, from `neorc tokens create`.",
)


async def guard(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal | None:
    """The principal behind the request, or ``None`` with authentication off.

    Raises ``CrossSiteRequestError`` and ``UnsupportedMediaTypeError`` for a
    write that may not be taken, and ``AuthenticationError`` for a request
    without an accepted token.
    """
    ensure_same_site_json(request)
    access: Access | None = request.app.state.access
    if access is None:
        return None
    sign_in: SignIn | None = request.app.state.sign_in
    session = request.cookies.get(sign_in.session_cookie) if sign_in else None
    if credentials is not None and session:
        raise AuthenticationError("send an API token or a session, not both")
    if credentials is not None:
        return await access.authenticate_token(credentials.credentials)
    if sign_in is not None and session:
        principal = await access.authenticate_session(session)
        sign_in.ensure_same_origin(request)
        return principal
    raise AuthenticationError(
        "this manager needs an API token, sent as 'Authorization: Bearer <token>'"
        + (", or a session: sign in at /ui/" if sign_in is not None else "")
    )


Guarded = Annotated[Principal | None, Depends(guard)]
"""The principal, injected; a module-level name, for FastAPI."""

PERMISSION_ATTRIBUTE = "neorc_permission"
"""Where a ``permit`` dependency says which permission it asks for."""


def permit(permission: Permission) -> Any:
    """A dependency on the guard's principal, refused unless it may do ``permission``.

    FastAPI runs the router's guard once per request, so this adds only the
    question. A queue permission is asked about the ``{queue}`` in the path,
    when the route has one. The value is the principal, or ``None`` with
    authentication off.
    """

    async def permitted(request: Request, principal: Guarded) -> Principal | None:
        queue = (
            request.path_params.get("queue")
            if permission in QUEUE_PERMISSIONS
            else None
        )
        authorize(principal, permission, queue=queue)
        return principal

    setattr(permitted, PERMISSION_ATTRIBUTE, permission)
    dependency: Callable[..., Awaitable[Principal | None]] = permitted
    return Depends(dependency)


def bound_queue(principal: Principal | None) -> str | None:
    """The queue a worker's or responder's request is confined to: its token's."""
    return None if principal is None else principal.queue


def ensure_same_site_json(request: Request) -> None:
    """Refuse a write from a page on another site, or not sent as JSON."""
    if request.method in SAFE_METHODS:
        return
    site = request.headers.get("sec-fetch-site", "").lower()
    if site in _CROSS_SITE:
        raise CrossSiteRequestError(
            f"{request.method} {request.url.path} came from a page on another site"
        )
    media_type = request.headers.get("content-type", "").partition(";")[0]
    if media_type.strip().lower() != "application/json":
        raise UnsupportedMediaTypeError(
            f"{request.method} {request.url.path} must be sent as application/json"
        )
