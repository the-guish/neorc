# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""How a core error crosses HTTP, for the manager's routes and the clients alike.

Every ``NeorcError`` the manager raises answers with the status of its class
and a body naming the class::

    {"error": "RunStateError", "detail": "run ... is cancelled, no longer active"}

A ``FlowDefinitionError`` carries its ``problems`` too. Clients raise the
exception the ``error`` field names: several classes share a status, and
callers branch on the type. A worker drops a task on ``RunStateError`` alone.

oneberth refuses a request before it reaches the manager, in the same shape
with a name of its own, such as ``Unauthorized``. ``ONEBERTH_REFUSALS`` gives
each the core exception a client raises for it.
"""

from __future__ import annotations

from typing import Any

import neorc_core
from neorc_core import (
    AccessError,
    AuthenticationError,
    CrossSiteRequestError,
    FlowDefinitionError,
    FlowNotFoundError,
    FlowVersionError,
    HandlerError,
    InvalidValueError,
    ManagerUnavailableError,
    NeorcError,
    PayloadTooLargeError,
    PermissionDeniedError,
    QueueKindError,
    ResolutionError,
    RunNotFoundError,
    RunStateError,
    TaskNotFoundError,
    TaskStateError,
    UnsupportedMediaTypeError,
)

STATUS_OF: dict[type[NeorcError], int] = {
    NeorcError: 500,
    # What was sent cannot be taken as it is.
    FlowDefinitionError: 422,
    InvalidValueError: 422,
    ResolutionError: 422,
    HandlerError: 422,
    PayloadTooLargeError: 413,
    UnsupportedMediaTypeError: 415,
    # What was asked does not fit where things are.
    FlowVersionError: 409,
    QueueKindError: 409,
    RunStateError: 409,
    TaskStateError: 409,
    # Who is asking is not known, or not let in.
    AccessError: 403,
    AuthenticationError: 401,
    PermissionDeniedError: 403,
    CrossSiteRequestError: 403,
    # What was named is not there.
    FlowNotFoundError: 404,
    RunNotFoundError: 404,
    TaskNotFoundError: 404,
    # Raised by clients, never sent; listed so every error has a status.
    ManagerUnavailableError: 503,
}
"""The HTTP status each core error answers with. Every subclass is listed."""

ONEBERTH_REFUSALS: dict[str, type[NeorcError]] = {
    "Unauthorized": AuthenticationError,
    "TwoCredentials": AuthenticationError,
    "Forbidden": PermissionDeniedError,
    "BearerRefused": PermissionDeniedError,
    "FreshSignInRequired": PermissionDeniedError,
    "CrossSite": CrossSiteRequestError,
    "CrossOrigin": CrossSiteRequestError,
    "UnsupportedMediaType": UnsupportedMediaTypeError,
    "PayloadTooLarge": PayloadTooLargeError,
    "InvalidRequest": InvalidValueError,
    "InvalidValue": InvalidValueError,
}
"""The refusals oneberth answers before a route runs, by the name it gives them."""

WITH_PROBLEMS: tuple[type[NeorcError], ...] = (FlowDefinitionError, HandlerError)
"""The errors built from a list of problems, carried across as such."""


def status_of(exc: NeorcError) -> int:
    """The status for ``exc``: its class's, or the nearest base class's."""
    for cls in type(exc).__mro__:
        if issubclass(cls, NeorcError) and cls in STATUS_OF:
            return STATUS_OF[cls]
    raise AssertionError("unreachable: NeorcError itself has a status")


def error_body(exc: NeorcError) -> dict[str, Any]:
    """The JSON body an error crosses as."""
    body: dict[str, Any] = {"error": type(exc).__name__, "detail": str(exc)}
    if isinstance(exc, FlowDefinitionError | HandlerError):
        body["problems"] = list(exc.problems)
    return body


def error_from(body: Any, status: int) -> NeorcError:
    """The exception an error body names, with its problems intact.

    A body that names no core error, or is not an error body at all, is a
    ``ManagerUnavailableError``: the manager did not answer as one. So is any
    5xx, whatever its body names: the manager could not serve the request,
    which is not the same as refusing it, and a caller retries rather than
    acting on a refusal.
    """
    if not isinstance(body, dict):
        return ManagerUnavailableError(f"the manager answered {status}: {body!r}")
    name = body.get("error")
    detail = str(body.get("detail", body))
    if status >= 500:
        return ManagerUnavailableError(f"the manager answered {status}: {detail}")
    cls: Any = None
    if isinstance(name, str):
        cls = ONEBERTH_REFUSALS.get(name) or getattr(neorc_core, name, None)
    if not (isinstance(cls, type) and issubclass(cls, NeorcError)):
        return ManagerUnavailableError(f"the manager answered {status}: {detail}")
    if issubclass(cls, WITH_PROBLEMS):
        problems = body.get("problems")
        if isinstance(problems, list) and all(isinstance(p, str) for p in problems):
            return cls(problems)
        return cls([detail])
    return cls(detail)
