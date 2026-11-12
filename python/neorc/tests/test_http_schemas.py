# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The documented response shapes are the wire forms, key for key.

The models in ``neorc.manager._schemas`` exist for the OpenAPI schema; the
responses themselves come from ``neorc_core._wire``. Each form, built from a
fully populated object, must validate against its model with no key missing
and none to spare, or the UI's generated types would lie.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel

from neorc.manager import _schemas as schemas
from neorc.manager._routes import _flow_response
from neorc_core import (
    Event,
    EventKind,
    Run,
    RunStatus,
    StoredFlow,
    Task,
    TaskDelivery,
    TaskStatus,
)
from neorc_core import _wire as wire
from neorc_core._values import JsonValue
from neorc_core.flows import (
    Address,
    Outcome,
    Reference,
    RunState,
    StepResult,
    TaskStep,
    Version,
    parse_flow,
)

WHEN = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)
ADDRESS = Address("work", (("rounds", 2), ("picking", 3)))
RUN_ID = uuid.uuid4()

RUN = Run(
    id=RUN_ID,
    flow="a",
    version=Version(1, 2, 3),
    inputs={"x": 1},
    status=RunStatus.SUCCEEDED,
    root_id=uuid.uuid4(),
    parent_id=uuid.uuid4(),
    parent_address=ADDRESS,
    output={"words": ["red"]},
    reason=None,
    created_at=WHEN,
    finished_at=WHEN,
)

TASK = Task(
    id=uuid.uuid4(),
    run_id=RUN_ID,
    address=ADDRESS,
    queue="default",
    handler="tasks:work",
    params={"x": Reference.parse("inputs.x")},
    fixed_params={"n": 3},
    status=TaskStatus.RUNNING,
    attempts=2,
    lease_expires_at=WHEN,
    result=None,
    error=None,
    created_at=WHEN,
    started_at=WHEN,
    finished_at=None,
)

CONTENT: JsonValue = {
    "name": "a",
    "version": "1.2.3",
    "steps": {"work": {"handler": "tasks:work"}},
}


def _exactly(model: type[BaseModel], form: object) -> None:
    """``form`` is what ``model`` describes: same keys, and back to the same JSON."""
    validated = model.model_validate(form)
    assert validated.model_dump(mode="json") == form


def test_the_flow_forms() -> None:
    flow = StoredFlow(parse_flow(CONTENT), CONTENT)

    _exactly(schemas.FlowResponse, _flow_response(flow))
    _exactly(schemas.FlowListResponse, {"flows": [_flow_response(flow)]})
    _exactly(schemas.FlowVersionsResponse, {"versions": [_flow_response(flow)]})
    _exactly(schemas.UploadResponse, {"stored": [True, False]})


def test_the_run_forms() -> None:
    _exactly(schemas.RunResponse, wire.run_to(RUN))
    _exactly(schemas.RunListResponse, {"runs": [wire.run_to(RUN)]})
    state = RunState({"x": 1}, {ADDRESS: StepResult(Outcome.SUCCEEDED, ["r"])})
    _exactly(schemas.RunStateResponse, wire.run_state_to(state))


def test_the_task_forms() -> None:
    _exactly(schemas.TaskResponse, wire.task_to(TASK))
    _exactly(schemas.TaskListResponse, {"tasks": [wire.task_to(TASK)]})
    delivery = TaskDelivery(TASK, {"x": 1, "n": 3})
    _exactly(schemas.DeliveryResponse, wire.delivery_to(delivery))
    _exactly(schemas.PendingTasksResponse, {"tasks": [wire.delivery_to(delivery)]})
    step = TaskStep(
        "work",
        handler="tasks:work",
        queue="default",
        params={"x": Reference.parse("inputs.x")},
        fixed_params={"n": 3},
    )
    _exactly(schemas.TaskDefinitionsResponse, {"tasks": [wire.task_step_to(step)]})
    _exactly(schemas.HeartbeatResponse, {"lease_expires_at": WHEN.isoformat()})


def test_the_event_forms() -> None:
    event = Event(7, RUN_ID, EventKind.TASK_FINISHED)

    _exactly(schemas.EventResponse, wire.event_to(event))
    _exactly(schemas.EventListResponse, {"events": [wire.event_to(event)]})
    _exactly(schemas.LatestEventResponse, {"sequence": 7})
