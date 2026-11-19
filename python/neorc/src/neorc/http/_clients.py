# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The clients that reach a manager over HTTP: one for workers, one for the rest.

Request and response bodies are the ``neorc_core._wire`` forms, the same JSON
the direct clients send. A body goes through the checks ``transmit`` makes
before it is sent, so a value fails here as it does on a direct client. An
error body raises the core exception its ``error`` field names; a body that
names none, and any transport failure, raise ``ManagerUnavailableError``.

A client is given a backend's address, and reaches the manager's API under
``/neorc/`` there. With a ``token``, oneberth's service token or a person's
token, every request carries it as ``Authorization: Bearer``. A token is not
sent over plain ``http`` to anything but a loopback address unless
``allow_insecure`` says so. Every ``POST`` is sent
as ``application/json``, body or not, as the manager takes writes.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from types import TracebackType
from typing import Any, Self, TypeVar
from urllib.parse import quote

import httpx

from neorc._errors import error_from
from neorc_core import (
    Event,
    InvalidValueError,
    ManagerClient,
    ManagerUnavailableError,
    NeorcError,
    QueueClient,
    QueueKind,
    Run,
    RunId,
    TaskDelivery,
    TaskId,
)
from neorc_core import _wire as wire
from neorc_core._values import JsonValue, dumps_json, ensure_json_depth
from neorc_core.flows import (
    Address,
    FlowDefinition,
    Reference,
    RunState,
    TaskStep,
    Version,
    is_name,
    is_queue_name,
    parse_flow,
)
from neorc_core.ports._clients import DEFAULT_LEASE_SECONDS

DEFAULT_POLL_TIMEOUT = 30.0

_POLL_GRACE = 5.0
"""Wait this much longer than the manager, so its own deadline answers first."""

_T = TypeVar("_T")


class _HttpClient:
    """What both clients share: the connection, sending, and errors."""

    def __init__(
        self,
        manager_address: str,
        *,
        poll_timeout: float = DEFAULT_POLL_TIMEOUT,
        token: str | None = None,
        allow_insecure: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        base_url = _base_url(manager_address)
        headers = {}
        if token is not None:
            if not allow_insecure:
                _ensure_private(base_url)
            headers["authorization"] = f"Bearer {token}"
        self._manager_address = manager_address
        self._poll_timeout = poll_timeout
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=httpx.Timeout(10.0, read=poll_timeout + _POLL_GRACE),
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Release the underlying connection pool."""
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        params: Mapping[str, Any] | None = None,
        read_timeout: float | None = None,
    ) -> httpx.Response:
        """Send a request; transport and status failures become neorc errors."""
        kwargs: dict[str, Any] = {"params": params}
        if body is not None:
            kwargs["content"] = _encoded(body)
        if method != "GET":  # oneberth takes a write only as JSON, body or not
            kwargs["headers"] = {"content-type": "application/json"}
        if read_timeout is not None:
            kwargs["timeout"] = httpx.Timeout(10.0, read=read_timeout + _POLL_GRACE)
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ManagerUnavailableError(
                f"{method} {self._manager_address}{path}: {exc}"
            ) from exc
        if not response.is_success:
            raise error_from(_json_or_text(response), response.status_code)
        return response


def _encoded(message: Any) -> bytes:
    """``message`` as JSON bytes, checked as ``transmit`` checks what it sends."""
    try:
        text = dumps_json(message)
        encoded = text.encode("utf-8")  # a lone surrogate cannot be sent
        ensure_json_depth(text)
    except (TypeError, ValueError, RecursionError) as exc:
        raise InvalidValueError(f"cannot be sent as JSON: {exc}") from None
    return encoded


def _json_or_text(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def _parsed(response: httpx.Response, parse: Callable[[Any], _T]) -> _T:
    """A successful response's body, read by ``parse``.

    The manager always answers in the shapes ``parse`` expects; anything else
    is not the manager talking, and is ``ManagerUnavailableError``, so a
    worker or scheduler loop retries rather than ending on a stray exception.
    """
    try:
        return parse(response.json())
    except NeorcError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ManagerUnavailableError(
            f"the manager answered with an unexpected body: {exc!r}"
        ) from None


API_PATH = "/neorc"
"""Where a backend serves the manager's API: the ``neorc`` component's address."""


def _base_url(manager_address: str) -> str:
    """The manager's API at a backend's address: a bare host, a host:port, or a URL.

    The address is the backend's origin, as standalone neorc and Robinauts
    alike serve neorc's API under ``/neorc/``.
    """
    if "://" in manager_address:
        return manager_address.rstrip("/") + API_PATH
    # A bare address is a local manager; _ensure_private refuses to send a
    # token over plain http to anything but loopback.
    return f"http://{manager_address.rstrip('/')}{API_PATH}"  # NOSONAR


def _ensure_private(base_url: str) -> None:
    """Refuse to send a token where the network between could read it."""
    url = httpx.URL(base_url)
    if url.scheme == "https" or _is_loopback(url.host):
        return
    raise InvalidValueError(
        f"refusing to send an API token over plain http to {url.host}: name the "
        "manager with an https:// address, or allow it with "
        "NEORC_ALLOW_INSECURE_HTTP=1 on a network nobody else can read"
    )


def _is_loopback(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _flow_segment(name: str) -> str:
    """A flow name as a path segment; refused first if it could not be one.

    The manager refuses the same names; here they would be read as part of the
    route before it could.
    """
    if not is_name(name):
        raise InvalidValueError(f"{name!r} is not a flow name: letters, digits and _")
    return quote(name, safe="")


def _queue_segment(queue: str) -> str:
    if not is_queue_name(queue):
        raise InvalidValueError(
            f"{queue!r} is not a queue name: letters, digits, _ and -"
        )
    return quote(queue, safe="")


class HttpQueueClient(_HttpClient, QueueClient):
    """A worker's client for a manager over HTTP.

    ``receive_task`` long-polls: the request stays open until a task is ready
    or the manager gives up, whichever comes first. The client-side timeout
    outlasts the manager's deadline by a little, so the manager answers first.
    """

    async def task_definitions(self, queue: str) -> list[TaskStep]:
        response = await self._request("GET", f"/queues/{_queue_segment(queue)}/tasks")
        return _parsed(
            response, lambda body: [wire.task_step_from(s) for s in body["tasks"]]
        )

    async def receive_task(
        self,
        queue: str,
        *,
        timeout: float,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> TaskDelivery | None:
        response = await self._request(
            "POST",
            f"/queues/{_queue_segment(queue)}/tasks/receive",
            params={"timeout": timeout, "lease_seconds": lease_seconds},
            read_timeout=timeout,
        )
        if response.status_code == httpx.codes.NO_CONTENT:
            return None
        return _parsed(response, wire.delivery_from)

    async def extend_lease(
        self, task_id: TaskId, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> datetime:
        response = await self._request(
            "POST",
            f"/tasks/{task_id}/heartbeat",
            body={"lease_seconds": lease_seconds},
        )
        return _parsed(
            response, lambda body: datetime.fromisoformat(body["lease_expires_at"])
        )

    async def claim_task(self, task_id: TaskId) -> None:
        await self._request("POST", f"/tasks/{task_id}/claim")

    async def report_finished(
        self, task_id: TaskId, *, result: JsonValue = None, error: str | None = None
    ) -> None:
        await self._request(
            "POST",
            f"/tasks/{task_id}/finished",
            body={"result": result, "error": error},
        )


class HttpManagerClient(_HttpClient, ManagerClient):
    """The scheduler's and a deploy script's client for a manager over HTTP."""

    async def upload_flows(self, contents: Sequence[JsonValue]) -> list[bool]:
        response = await self._request("POST", "/flows", body={"flows": list(contents)})
        return _parsed(response, lambda body: [bool(s) for s in body["stored"]])

    async def declare_queue(self, name: str, kind: QueueKind) -> bool:
        response = await self._request(
            "PUT", f"/queues/{_queue_segment(name)}", body={"kind": kind.value}
        )
        return _parsed(response, lambda body: response.status_code == 201)

    async def get_flow(
        self, name: str, version: Version | None = None
    ) -> FlowDefinition:
        path = f"/flows/{_flow_segment(name)}"
        if version is not None:
            path += f"/versions/{version}"
        response = await self._request("GET", path)
        return _parsed(response, lambda body: parse_flow(body["content"]))

    async def start_run(self, flow: str, inputs: Mapping[str, JsonValue]) -> Run:
        response = await self._request(
            "POST", f"/flows/{_flow_segment(flow)}/runs", body={"inputs": dict(inputs)}
        )
        return _parsed(response, wire.run_from)

    async def get_run(self, run_id: RunId) -> Run:
        response = await self._request("GET", f"/runs/{run_id}")
        return _parsed(response, wire.run_from)

    async def cancel_run(self, run_id: RunId) -> None:
        await self._request("POST", f"/runs/{run_id}/cancel")

    async def wait_for_events(
        self, after: int, *, timeout: float, limit: int = 100
    ) -> list[Event]:
        response = await self._request(
            "GET",
            "/events",
            params={"after": after, "limit": limit, "timeout": timeout},
            read_timeout=timeout,
        )
        return _parsed(
            response, lambda body: [wire.event_from(e) for e in body["events"]]
        )

    async def run_state(self, run_id: RunId) -> RunState:
        response = await self._request("GET", f"/runs/{run_id}/state")
        return _parsed(response, wire.run_state_from)

    async def publish_task(self, run_id: RunId, address: Address) -> None:
        await self._request(
            "POST", f"/runs/{run_id}/tasks", body={"address": wire.address_to(address)}
        )

    async def start_sub_run(self, parent_id: RunId, address: Address) -> None:
        await self._request(
            "POST",
            f"/runs/{parent_id}/sub-runs",
            body={"address": wire.address_to(address)},
        )

    async def succeed_run(self, run_id: RunId, output: Reference | None) -> None:
        await self._request(
            "POST",
            f"/runs/{run_id}/succeed",
            body={"output": str(output) if output is not None else None},
        )

    async def fail_run(self, run_id: RunId, reason: str) -> None:
        await self._request("POST", f"/runs/{run_id}/fail", body={"reason": reason})
