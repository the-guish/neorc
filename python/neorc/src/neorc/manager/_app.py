# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The manager's HTTP surface, on ``Manager``.

Each route is a thin translation between JSON and one manager call. The routes
that receive a task and that claim it are separate on purpose: receiving may
move to another queue backend later, while claiming stays with the store.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from neorc._errors import error_body, status_of
from neorc.manager._access import guard
from neorc.manager._auth_routes import auth_router
from neorc.manager._routes import router
from neorc.manager._sign_in import SignIn
from neorc.manager._ui import mount_ui
from neorc_core import (
    Access,
    AuthenticationError,
    InvalidValueError,
    Manager,
    NeorcError,
)

DEFAULT_LONG_POLL_TIMEOUT = 25.0
"""How long a receive waits before answering "nothing yet".

Must stay under the idle timeout of any proxy in front of the service; a worker
that hits it simply asks again.
"""


def create_app(
    manager: Manager,
    *,
    access: Access | None,
    sign_in: SignIn | None = None,
    long_poll_timeout: float = DEFAULT_LONG_POLL_TIMEOUT,
    ui: Path | None = None,
    frame_ancestors: Iterable[str] = (),
) -> FastAPI:
    """Build the ASGI application serving ``manager``.

    ``access`` authenticates every request to the API; ``None`` serves it to
    anyone, and must be said. ``sign_in``, on the same ``access``, lets people
    sign in to the UI with its providers, and accepts their sessions. Whoever
    built ``access`` opens and closes its credential store. With
    authentication on, the Swagger and ReDoc pages are not served: they load
    scripts from a CDN, outside the UI's Content-Security-Policy.
    ``/openapi.json`` is served either way.

    With ``ui``, a directory holding the built web UI, the app serves it at
    ``/ui/`` and sends ``/`` there; without, ``/`` is not found. No page may
    frame the UI but those of the origins in ``frame_ancestors``, an
    application that embeds it; ``ValueError`` for one that is not an origin.
    """
    pages = access is None
    app = FastAPI(
        title="neorc manager",
        version="0",
        docs_url="/docs" if pages else None,
        redoc_url="/redoc" if pages else None,
    )
    app.state.manager = manager
    if sign_in is not None and (access is None or sign_in.access is not access):
        raise ValueError("sign-in opens sessions on the app's own Access")
    app.state.access = access
    app.state.sign_in = sign_in
    app.state.long_poll_timeout = long_poll_timeout

    @app.exception_handler(NeorcError)
    async def _neorc_error(request: Request, exc: NeorcError) -> JSONResponse:
        headers = (
            {"www-authenticate": "Bearer"}
            if isinstance(exc, AuthenticationError)
            else None
        )
        return JSONResponse(
            error_body(exc), status_code=status_of(exc), headers=headers
        )

    @app.exception_handler(RequestValidationError)
    async def _bad_request(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # A path or query parameter FastAPI could not read, in the same form
        # as every other refusal, so a client raises one kind of error for it.
        error = InvalidValueError(f"request: {exc}")
        return JSONResponse(error_body(error), status_code=status_of(error))

    app.include_router(router, dependencies=[Depends(guard)])
    app.include_router(auth_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness, for load balancers and deployment scripts."""
        return {"status": "ok"}

    if ui is not None:
        # After the routes: the mount takes a whole prefix.
        mount_ui(app, ui, framed_by=frame_ancestors)
    return app
