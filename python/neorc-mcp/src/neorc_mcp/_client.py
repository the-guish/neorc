# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The manager's API over HTTP, as neorc-mcp calls it: a person's token for
most routes, and CI's for the two that only CI may call.

A refusal keeps the manager's own words: the core exception it names, its
detail, and the problems of an invalid flow set. A manager that cannot be
reached says so, with the address. A token is not sent over plain ``http`` to
anything but a loopback address unless ``allow_insecure`` says so, as the
``neorc`` command's clients do.
"""

from __future__ import annotations

import enum
import ipaddress
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx

API_PATH = "/neorc"
"""Where a backend serves the manager's API: the ``neorc`` component's address."""


class As(enum.Enum):
    """Whose token a request sends."""

    PERSON = "person"
    """The person's own token, of the role ``neorc/user``: reads, runs, completions."""
    CI = "ci"
    """A service token of the role ``neorc/ci``: uploads and queue declarations."""


class ManagerError(Exception):
    """The manager refused a request, or could not be reached."""


class ManagerRefused(ManagerError):
    """The manager answered with an error body: its status, and its words."""

    def __init__(
        self, status: int, error: str, detail: str, problems: list[str] | None = None
    ) -> None:
        self.status = status
        self.error = error
        self.detail = detail
        self.problems = problems or []
        lines = [f"the manager refused ({status} {error}): {detail}"]
        lines += [f"  {problem}" for problem in self.problems]
        super().__init__("\n".join(lines))


class ManagerUnreachable(ManagerError):
    """No answer from the manager, or one that is not the manager's."""


class MissingToken(ManagerError):
    """The request needs a token this server was not given."""


def segment(value: str) -> str:
    """A name or an id as one path segment, whatever it holds."""
    return quote(value, safe="")


class ManagerApi:
    """The manager at a backend's ``address``, reached with the tokens given."""

    def __init__(
        self,
        address: str,
        *,
        person_token: str | None = None,
        ci_token: str | None = None,
        allow_insecure: bool = False,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url(address)
        if (person_token or ci_token) and not allow_insecure:
            ensure_private(self.base_url)
        self._tokens = {As.PERSON: person_token, As.CI: ci_token}
        self._client = httpx.AsyncClient(
            base_url=self.base_url, timeout=timeout, transport=transport
        )

    def has_token(self, who: As) -> bool:
        return self._tokens[who] is not None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        return await self.request("GET", path, params=params)

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        params: Mapping[str, Any] | None = None,
        who: As = As.PERSON,
    ) -> Any:
        """Send a request; the body of its answer, or ``None`` for a 204."""
        headers = {}
        token = self._tokens[who]
        if who is As.CI and token is None:
            raise MissingToken(
                "this server holds no CI token: NEORC_CI_TOKEN names one"
            )
        if token is not None:
            headers["authorization"] = f"Bearer {token}"
        if method != "GET":  # oneberth takes a write only as JSON, body or not
            headers["content-type"] = "application/json"
        kept = {
            key: value for key, value in (params or {}).items() if value is not None
        }
        try:
            response = await self._client.request(
                method,
                path,
                params=kept,
                json=body if method != "GET" else None,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise ManagerUnreachable(
                f"the manager at {self.base_url} cannot be reached: {exc}"
            ) from None
        if response.status_code == 204:
            return None
        try:
            answer = response.json()
        except ValueError:
            raise ManagerUnreachable(
                f"{method} {path} answered {response.status_code} with no JSON: "
                "is NEORC_MANAGER_ADDRESS a neorc manager?"
            ) from None
        if not response.is_success:
            if isinstance(answer, dict) and isinstance(answer.get("error"), str):
                problems = answer.get("problems")
                raise ManagerRefused(
                    response.status_code,
                    answer["error"],
                    str(answer.get("detail", "")),
                    [str(p) for p in problems] if isinstance(problems, list) else None,
                )
            raise ManagerUnreachable(
                f"{method} {path} answered {response.status_code}: {answer!r}"
            )
        return answer


def base_url(address: str) -> str:
    """The manager's API at a backend's address: a host:port, or a URL."""
    if "://" in address:
        return address.rstrip("/") + API_PATH
    return f"http://{address.rstrip('/')}{API_PATH}"  # NOSONAR: a local manager


def ensure_private(url: str) -> None:
    """Refuse to send a token where the network between could read it."""
    parsed = httpx.URL(url)
    if parsed.scheme == "https" or is_loopback(parsed.host):
        return
    raise ValueError(
        f"refusing to send a token over plain http to {parsed.host}: name the "
        "manager with an https:// address, or allow it with "
        "NEORC_ALLOW_INSECURE_HTTP=1 on a network nobody else can read"
    )


def is_loopback(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
