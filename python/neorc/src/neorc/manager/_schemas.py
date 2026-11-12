# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The shapes of the manager's responses, for the OpenAPI schema alone.

Responses are the ``neorc_core._wire`` forms, built by that module and sent
as they are; nothing here serialises or validates a response. These models
say in the schema what those forms look like, so the UI can generate types
from it. A test holds each wire form to its model, so the two cannot drift.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from neorc_core import EventKind, RunStatus, TaskStatus
from neorc_core.flows import Outcome


class _Exact(BaseModel):
    """A shape with exactly these keys: what the wire form sends, no more."""

    model_config = ConfigDict(extra="forbid")


class FlowResponse(_Exact):
    name: str
    version: str
    content: Any
    """The flow's definition as it was uploaded."""


class FlowListResponse(_Exact):
    flows: list[FlowResponse]


class FlowVersionsResponse(_Exact):
    versions: list[FlowResponse]


class UploadResponse(_Exact):
    stored: list[bool]


class AddressResponse(_Exact):
    step: str
    scope: list[tuple[str, int]]
    """The enclosing loops and fan-outs, outermost first, each with its number."""


class RunResponse(_Exact):
    id: str
    flow: str
    version: str
    inputs: dict[str, Any]
    status: RunStatus
    root_id: str
    parent_id: str | None
    parent_address: AddressResponse | None
    output: Any
    reason: str | None
    created_at: str | None
    finished_at: str | None


class RunListResponse(_Exact):
    runs: list[RunResponse]


class TaskResponse(_Exact):
    id: str
    run_id: str
    address: AddressResponse
    queue: str
    handler: str
    params: dict[str, str]
    """Each input still a reference, as text."""
    fixed_params: dict[str, Any]
    status: TaskStatus
    attempts: int
    lease_expires_at: str | None
    result: Any
    error: str | None
    created_at: str | None
    started_at: str | None
    finished_at: str | None


class TaskListResponse(_Exact):
    tasks: list[TaskResponse]


class StepResponse(_Exact):
    address: AddressResponse
    outcome: Outcome
    value: Any


class RunStateResponse(_Exact):
    inputs: dict[str, Any]
    steps: list[StepResponse]


class EventResponse(_Exact):
    sequence: int
    run_id: str
    kind: EventKind


class EventListResponse(_Exact):
    events: list[EventResponse]


class LatestEventResponse(_Exact):
    sequence: int
    """The latest event's sequence, or 0 with none: where a new reader starts."""


class DeliveryResponse(_Exact):
    task: TaskResponse
    inputs: dict[str, Any]
    """Every param and fixed param in its JSON form, references filled in."""


class PendingTasksResponse(_Exact):
    tasks: list[DeliveryResponse]
    """A queue's pending tasks, oldest first, each as a worker would receive it."""


class TaskStepResponse(_Exact):
    name: str
    handler: str
    queue: str
    params: dict[str, str]
    fixed_params: dict[str, Any]


class TaskDefinitionsResponse(_Exact):
    tasks: list[TaskStepResponse]


class HeartbeatResponse(_Exact):
    lease_expires_at: str


class ProviderResponse(_Exact):
    id: str
    title: str


class PrincipalResponse(_Exact):
    kind: str
    name: str
    email: str | None
    """A verified address, if the provider gave one."""
    provider: str | None


class SessionResponse(_Exact):
    authentication: bool
    """Whether the manager asks who is calling; with ``False``, anyone may."""
    public_url: str | None
    """Where to sign in, when sign-in is configured."""
    providers: list[ProviderResponse]
    principal: PrincipalResponse | None
    """Who the session cookie sent with the request belongs to, if anyone."""


def documented(
    model: type[BaseModel], status_code: int = 200
) -> dict[int | str, dict[str, Any]]:
    """The ``responses`` entry that puts ``model`` in the schema for a status."""
    return {status_code: {"model": model}}
