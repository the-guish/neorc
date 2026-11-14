# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The manager's HTTP routes: a translation onto ``Manager``.

Flows and runs for whoever deploys and starts them, and the listings a status
page reads; events, tasks and sub-runs for the scheduler; task definitions,
long-polled task receives, claims, heartbeats and results for workers.

Bodies and responses are the ``neorc_core._wire`` forms, so the direct and
HTTP clients send the same JSON. A request body is read as bytes, capped, and
checked for nesting depth before it is parsed, and only then handed to a model:
FastAPI's own parsing would hand a deeply nested body to the C parser first.
The models check the shape of the envelope alone; values inside it are checked
by core, so a request the in-memory manager accepts is not refused by HTTP
first, and one it refuses is refused the same way.

Every route declares the one permission it needs, with ``permit``; a test
walks the routes to hold that. No route declares a body parameter: FastAPI
would read that body before the router's guard in ``_access.py`` runs, and a
request with no token, or a cross-site write, must be refused before its body
is read.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from neorc.manager._access import bound_queue, permit
from neorc.manager._schemas import (
    DeliveryResponse,
    EventListResponse,
    FlowListResponse,
    FlowResponse,
    FlowVersionsResponse,
    HeartbeatResponse,
    LatestEventResponse,
    PendingTasksResponse,
    QueueListResponse,
    QueueTasksResponse,
    RunListResponse,
    RunResponse,
    RunStateResponse,
    TaskDefinitionsResponse,
    TaskListResponse,
    TaskResponse,
    UploadResponse,
    documented,
)
from neorc_core import (
    InvalidValueError,
    Manager,
    PayloadTooLargeError,
    Permission,
    Principal,
    RunId,
    RunStatus,
    TaskId,
    TaskStatus,
)
from neorc_core import _wire as wire
from neorc_core._values import ensure_json_depth
from neorc_core.flows import Address, Reference, Version
from neorc_core.ports._clients import DEFAULT_LEASE_SECONDS
from neorc_core.ports._store import DEFAULT_PAGE

MAX_BODY_BYTES = 16 * 1024 * 1024
"""The largest request body, well above the payload limit core enforces.

Only to protect the process: the size rules stay in core, where they apply to
what the in-memory manager receives too.
"""

MAX_PAGE = 500
"""The most runs, or tasks of a queue, one page of a listing may ask for."""


def _get_manager(request: Request) -> Manager:
    """The manager this application was built around."""
    manager: Manager = request.app.state.manager
    return manager


Managed = Annotated[Manager, Depends(_get_manager)]
"""The manager, injected into a route; a module-level name, for FastAPI."""

_M = TypeVar("_M", bound=BaseModel)


async def read_body(request: Request, model: type[_M]) -> _M:
    """The request body as ``model``, read and checked before anything parses it.

    Raises ``PayloadTooLargeError`` past ``MAX_BODY_BYTES``, and
    ``InvalidValueError`` for a body that is not UTF-8, not JSON, nests too
    deep, or is not the shape ``model`` describes.
    """
    return _validate(await read_json(request), model)


async def read_json(request: Request) -> Any:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise PayloadTooLargeError(f"request body is over {MAX_BODY_BYTES} bytes")
        chunks.append(chunk)
    try:
        text = b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidValueError(f"request body is not valid UTF-8: {exc}") from None
    ensure_json_depth(text)
    try:
        return json.loads(
            text, object_pairs_hook=_unique_keys, parse_constant=_not_a_number
        )
    except ValueError as exc:
        raise InvalidValueError(f"request body is not valid JSON: {exc}") from None


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    # json.loads would keep the last of two values under one key and drop the
    # rest without a word; what is stored must be what was sent.
    mapping: dict[str, Any] = {}
    for key, value in pairs:
        if key in mapping:
            raise InvalidValueError(
                f"request body: {key!r} appears more than once in the same object"
            )
        mapping[key] = value
    return mapping


def _not_a_number(token: str) -> Any:
    # json.loads takes NaN and Infinity, which JSON text cannot carry back.
    raise InvalidValueError(f"request body: {token} is not a JSON number")


def _validate(data: Any, model: type[_M]) -> _M:
    try:
        return model.model_validate(data, strict=True)
    except ValidationError as exc:
        raise InvalidValueError(f"request body: {exc}") from None


class UploadRequest(BaseModel):
    flows: list[Any]


class StartRunRequest(BaseModel):
    inputs: dict[str, Any]


class ErrorResponse(BaseModel):
    """What a refused request answers, whatever its status; see ``neorc._errors``.

    ``error`` names the core exception, so a client raises the same one;
    ``problems`` lists each fault of an invalid flow set.
    """

    error: str
    detail: str
    problems: list[str] | None = None


_ERROR = {"model": ErrorResponse, "description": "The exception the manager raised"}

# Every refusal has this one body, so the schema says so instead of FastAPI's
# default 422 shape, which the application never sends; the UI generates its
# types from the schema.
router = APIRouter(
    responses={
        status.HTTP_401_UNAUTHORIZED: _ERROR,
        status.HTTP_403_FORBIDDEN: _ERROR,
        status.HTTP_404_NOT_FOUND: _ERROR,
        status.HTTP_409_CONFLICT: _ERROR,
        status.HTTP_413_CONTENT_TOO_LARGE: _ERROR,
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: _ERROR,
        status.HTTP_422_UNPROCESSABLE_CONTENT: _ERROR,
    }
)


def _flow_response(flow: Any) -> dict[str, Any]:
    return {"name": flow.name, "version": str(flow.version), "content": flow.content}


@router.post(
    "/flows",
    dependencies=[permit(Permission.FLOWS_UPLOAD)],
    response_model=None,
    responses=documented(UploadResponse),
)
async def upload_flows(request: Request, manager: Managed) -> dict[str, list[bool]]:
    """Upload flows deployed together; per flow, whether a version was stored."""
    body = await read_body(request, UploadRequest)
    return {"stored": await manager.upload_flows(body.flows)}


@router.get(
    "/flows",
    dependencies=[permit(Permission.FLOWS_READ)],
    response_model=None,
    responses=documented(FlowListResponse),
)
async def list_flows(manager: Managed) -> dict[str, list[dict[str, Any]]]:
    """The latest version of every flow, by name."""
    return {"flows": [_flow_response(f) for f in await manager.latest_flows()]}


@router.get(
    "/flows/{name}",
    dependencies=[permit(Permission.FLOWS_READ)],
    response_model=None,
    responses=documented(FlowResponse),
)
async def get_latest_flow(manager: Managed, name: str) -> dict[str, Any]:
    """A flow's latest version, with its content as uploaded."""
    return _flow_response(await manager.get_flow(name))


@router.get(
    "/flows/{name}/versions",
    dependencies=[permit(Permission.FLOWS_READ)],
    response_model=None,
    responses=documented(FlowVersionsResponse),
)
async def flow_versions(manager: Managed, name: str) -> dict[str, list[dict[str, Any]]]:
    """Every stored version of a flow, newest first."""
    return {"versions": [_flow_response(f) for f in await manager.flow_versions(name)]}


@router.get(
    "/flows/{name}/versions/{version}",
    dependencies=[permit(Permission.FLOWS_READ)],
    response_model=None,
    responses=documented(FlowResponse),
)
async def get_flow_version(manager: Managed, name: str, version: str) -> dict[str, Any]:
    """One version of a flow."""
    try:
        parsed = Version.parse(version)
    except ValueError as exc:
        raise InvalidValueError(str(exc)) from None
    return _flow_response(await manager.get_flow(name, parsed))


@router.post(
    "/flows/{name}/runs",
    dependencies=[permit(Permission.RUNS_START)],
    status_code=status.HTTP_201_CREATED,
    responses=documented(RunResponse, status.HTTP_201_CREATED),
)
async def start_run(request: Request, manager: Managed, name: str) -> JSONResponse:
    """Start a run of the flow's latest version."""
    body = await read_body(request, StartRunRequest)
    run = await manager.start_run(name, body.inputs)
    return JSONResponse(wire.run_to(run), status_code=status.HTTP_201_CREATED)


@router.get(
    "/runs",
    dependencies=[permit(Permission.RUNS_READ)],
    response_model=None,
    responses=documented(RunListResponse),
)
async def list_runs(
    manager: Managed,
    flow: str | None = None,
    status: RunStatus | None = None,
    root_only: bool = True,
    before: RunId | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = DEFAULT_PAGE,
) -> dict[str, list[dict[str, Any]]]:
    """A page of runs, newest first; ``before`` is the last run of the previous page."""
    runs = await manager.list_runs(
        flow=flow, status=status, root_only=root_only, before=before, limit=limit
    )
    return {"runs": [wire.run_to(run) for run in runs]}


@router.get(
    "/runs/{run_id}",
    dependencies=[permit(Permission.RUNS_READ)],
    response_model=None,
    responses=documented(RunResponse),
)
async def get_run(manager: Managed, run_id: RunId) -> dict[str, Any]:
    """A run, for a status query."""
    return wire.run_to(await manager.get_run(run_id))


@router.get(
    "/runs/{run_id}/tasks",
    dependencies=[permit(Permission.RUNS_READ)],
    response_model=None,
    responses=documented(TaskListResponse),
)
async def run_tasks(manager: Managed, run_id: RunId) -> dict[str, list[dict[str, Any]]]:
    """A run's tasks, in the order they were published."""
    return {"tasks": [wire.task_to(t) for t in await manager.run_tasks(run_id)]}


@router.get(
    "/runs/{run_id}/sub-runs",
    dependencies=[permit(Permission.RUNS_READ)],
    response_model=None,
    responses=documented(RunListResponse),
)
async def sub_runs(manager: Managed, run_id: RunId) -> dict[str, list[dict[str, Any]]]:
    """A run's direct sub-flow runs, in the order they started."""
    return {"runs": [wire.run_to(r) for r in await manager.sub_runs(run_id)]}


@router.post(
    "/runs/{run_id}/cancel",
    dependencies=[permit(Permission.RUNS_CANCEL)],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def cancel_run(manager: Managed, run_id: RunId) -> Response:
    """Cancel a run by hand, and with it every active run in its tree."""
    await manager.cancel_run(run_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/runs/{run_id}/state",
    dependencies=[permit(Permission.RUNS_READ)],
    response_model=None,
    responses=documented(RunStateResponse),
)
async def run_state(manager: Managed, run_id: RunId) -> dict[str, Any]:
    """What a run's tasks and sub-flow runs have produced so far."""
    return wire.run_state_to(await manager.run_state(run_id))


# The scheduler's requests.


class AddressRequest(BaseModel):
    address: dict[str, Any]


class SucceedRequest(BaseModel):
    output: str | None = None


class FailRequest(BaseModel):
    reason: str


def _address(data: dict[str, Any]) -> Address:
    """An address from its wire form; ``InvalidValueError`` if it is not one."""
    step = data.get("step")
    scope = data.get("scope")
    if (
        not isinstance(step, str)
        or not isinstance(scope, list)
        or not all(
            isinstance(level, list)
            and len(level) == 2
            and isinstance(level[0], str)
            and isinstance(level[1], int)
            and not isinstance(level[1], bool)
            for level in scope
        )
    ):
        raise InvalidValueError(f"request body: {data!r} is not an address")
    return wire.address_from(data)


def _waited(request: Request, timeout: float | None) -> float:
    """How long a long poll waits: the caller's ask, capped at the manager's deadline.

    The deadline stays under the idle timeout of any proxy in front of the
    service; a caller that hits it simply asks again.
    """
    limit: float = request.app.state.long_poll_timeout
    return limit if timeout is None else min(timeout, limit)


@router.get(
    "/events",
    dependencies=[permit(Permission.EVENTS_READ)],
    response_model=None,
    responses=documented(EventListResponse),
)
async def wait_for_events(
    request: Request,
    manager: Managed,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    timeout: Annotated[float | None, Query(ge=0)] = None,
) -> dict[str, list[dict[str, Any]]]:
    """Events after a sequence, long-polled; an empty list when the wait ends."""
    events = await manager.wait_for_events(
        after, timeout=_waited(request, timeout), limit=limit
    )
    return {"events": [wire.event_to(event) for event in events]}


@router.get(
    "/events/latest",
    dependencies=[permit(Permission.EVENTS_READ)],
    response_model=None,
    responses=documented(LatestEventResponse),
)
async def latest_event(manager: Managed) -> dict[str, int]:
    """The latest event's sequence, or 0: a reader wanting only news starts after it."""
    return {"sequence": await manager.last_sequence()}


@router.post(
    "/runs/{run_id}/tasks",
    dependencies=[permit(Permission.RUNS_SCHEDULE)],
    status_code=status.HTTP_201_CREATED,
    responses=documented(TaskResponse, status.HTTP_201_CREATED),
)
async def publish_task(
    request: Request, manager: Managed, run_id: RunId
) -> JSONResponse:
    """Publish the task at an address in a run."""
    body = await read_body(request, AddressRequest)
    task = await manager.publish_task(run_id, _address(body.address))
    return JSONResponse(wire.task_to(task), status_code=status.HTTP_201_CREATED)


@router.post(
    "/runs/{run_id}/sub-runs",
    dependencies=[permit(Permission.RUNS_SCHEDULE)],
    status_code=status.HTTP_201_CREATED,
    responses=documented(RunResponse, status.HTTP_201_CREATED),
)
async def start_sub_run(
    request: Request, manager: Managed, run_id: RunId
) -> JSONResponse:
    """Start the sub-flow run at an address in a run."""
    body = await read_body(request, AddressRequest)
    run = await manager.start_sub_run(run_id, _address(body.address))
    return JSONResponse(wire.run_to(run), status_code=status.HTTP_201_CREATED)


@router.post(
    "/runs/{run_id}/succeed",
    dependencies=[permit(Permission.RUNS_SCHEDULE)],
    response_model=None,
    responses=documented(RunResponse),
)
async def succeed_run(
    request: Request, manager: Managed, run_id: RunId
) -> dict[str, Any]:
    """Mark a run succeeded, with the value of its output reference."""
    body = await read_body(request, SucceedRequest)
    output = None
    if body.output is not None:
        try:
            output = Reference.parse(body.output)
        except ValueError as exc:
            raise InvalidValueError(f"request body: {exc}") from None
    return wire.run_to(await manager.succeed_run(run_id, output))


@router.post(
    "/runs/{run_id}/fail",
    dependencies=[permit(Permission.RUNS_SCHEDULE)],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def fail_run(request: Request, manager: Managed, run_id: RunId) -> Response:
    """Fail a run and the rest of its tree."""
    body = await read_body(request, FailRequest)
    await manager.fail_run(run_id, body.reason)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Workers.


class HeartbeatRequest(BaseModel):
    lease_seconds: float = DEFAULT_LEASE_SECONDS


class FinishedRequest(BaseModel):
    result: Any = None
    error: str | None = None


@router.get(
    "/queues/{queue}/tasks",
    dependencies=[permit(Permission.QUEUE_DEFINITIONS)],
    response_model=None,
    responses=documented(TaskDefinitionsResponse),
)
async def task_definitions(
    manager: Managed, queue: str
) -> dict[str, list[dict[str, Any]]]:
    """Every task on a queue in the latest flows, for a worker to check."""
    steps = await manager.task_definitions(queue)
    return {"tasks": [wire.task_step_to(step) for step in steps]}


@router.post(
    "/queues/{queue}/tasks/receive",
    dependencies=[permit(Permission.QUEUE_RECEIVE)],
    responses={
        **documented(DeliveryResponse),
        status.HTTP_204_NO_CONTENT: {"description": "The wait ended with no task"},
    },
)
async def receive_task(
    request: Request,
    manager: Managed,
    queue: str,
    timeout: Annotated[float | None, Query(ge=0)] = None,
    lease_seconds: Annotated[float, Query(gt=0)] = DEFAULT_LEASE_SECONDS,
) -> Response:
    """Long-poll for a task on a queue. 204 when the wait ends empty."""
    # A worker that left mid-poll must not be leased a task: the wait asks
    # whether the client is still there before every receive.
    delivery = await manager.receive_task(
        queue,
        timeout=_waited(request, timeout),
        lease_seconds=lease_seconds,
        abandoned=request.is_disconnected,
    )
    if delivery is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return JSONResponse(wire.delivery_to(delivery))


@router.get(
    "/tasks/{task_id}",
    dependencies=[permit(Permission.RUNS_READ)],
    response_model=None,
    responses=documented(TaskResponse),
)
async def get_task(manager: Managed, task_id: TaskId) -> dict[str, Any]:
    """A task, for a status query."""
    return wire.task_to(await manager.get_task(task_id))


@router.post("/tasks/{task_id}/claim", status_code=status.HTTP_204_NO_CONTENT)
async def claim_task(
    manager: Managed,
    task_id: TaskId,
    principal: Annotated[Principal | None, permit(Permission.TASKS_CLAIM)],
) -> Response:
    """A worker claims a task it received; 409 if its run is no longer active."""
    await manager.claim_task(task_id, queue=bound_queue(principal))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/tasks/{task_id}/heartbeat",
    response_model=None,
    responses=documented(HeartbeatResponse),
)
async def extend_lease(
    request: Request,
    manager: Managed,
    task_id: TaskId,
    principal: Annotated[Principal | None, permit(Permission.TASKS_HEARTBEAT)],
) -> dict[str, str]:
    """A worker keeping the task it holds; when its lease now lapses."""
    body = await read_body(request, HeartbeatRequest)
    expires_at = await manager.extend_lease(
        task_id, lease_seconds=body.lease_seconds, queue=bound_queue(principal)
    )
    return {"lease_expires_at": expires_at.isoformat()}


@router.post("/tasks/{task_id}/finished", status_code=status.HTTP_204_NO_CONTENT)
async def report_finished(
    request: Request,
    manager: Managed,
    task_id: TaskId,
    principal: Annotated[Principal | None, permit(Permission.TASKS_FINISH)],
) -> Response:
    """A worker's result in its JSON form, or its failure."""
    body = await read_body(request, FinishedRequest)
    await manager.report_finished(
        task_id, result=body.result, error=body.error, queue=bound_queue(principal)
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Responders: a person or another system, completing tasks with no lease.
# See docs/specs/task-completion.md.


class CompleteRequest(BaseModel):
    result: Any = None
    error: str | None = None


@router.get(
    "/queues/{queue}/tasks/pending",
    dependencies=[permit(Permission.QUEUE_PENDING)],
    response_model=None,
    responses=documented(PendingTasksResponse),
)
async def pending_tasks(
    manager: Managed,
    queue: str,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = DEFAULT_PAGE,
) -> dict[str, list[dict[str, Any]]]:
    """A queue's pending tasks, oldest first, inputs filled in. Leases nothing."""
    deliveries = await manager.pending_tasks(queue, limit=limit)
    return {"tasks": [wire.delivery_to(delivery) for delivery in deliveries]}


@router.post("/tasks/{task_id}/complete", status_code=status.HTTP_204_NO_CONTENT)
async def complete_task(
    request: Request,
    manager: Managed,
    task_id: TaskId,
    principal: Annotated[Principal | None, permit(Permission.TASKS_COMPLETE)],
) -> Response:
    """End a pending task with a result, or an error; 409 unless it is pending."""
    body = await read_body(request, CompleteRequest)
    await manager.complete_task(
        task_id, result=body.result, error=body.error, queue=bound_queue(principal)
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# A page for people: the queues, a queue's tasks in any status, and a task
# with its inputs filled in. A person completes with the route above.


@router.get(
    "/queues",
    dependencies=[permit(Permission.QUEUES_READ)],
    response_model=None,
    responses=documented(QueueListResponse),
)
async def list_queues(manager: Managed) -> dict[str, list[dict[str, Any]]]:
    """Every declared queue, by name, with its kind."""
    return {"queues": [wire.queue_to(queue) for queue in await manager.queues()]}


@router.get(
    "/queues/{queue}/tasks/recent",
    dependencies=[permit(Permission.QUEUES_READ)],
    response_model=None,
    responses=documented(QueueTasksResponse),
)
async def queue_tasks(
    manager: Managed,
    queue: str,
    since: datetime | None = None,
    status: TaskStatus | None = None,
    flow: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = DEFAULT_PAGE,
) -> dict[str, list[dict[str, Any]]]:
    """A queue's tasks that changed since ``since``, 24 hours ago when not given.

    In any status, the task published last first; ``status`` and ``flow`` keep
    only the tasks in that status and of that flow.
    """
    listed = await manager.queue_tasks(
        queue, since=since, status=status, flow=flow, limit=limit
    )
    return {"tasks": [wire.queue_task_to(task) for task in listed]}


@router.get(
    "/tasks/{task_id}/inputs",
    dependencies=[permit(Permission.RUNS_READ)],
    response_model=None,
    responses=documented(DeliveryResponse),
)
async def task_inputs(manager: Managed, task_id: TaskId) -> dict[str, Any]:
    """A task with its inputs filled in, as a worker would receive it."""
    return wire.delivery_to(await manager.task_inputs(task_id))
