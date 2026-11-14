# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""How flow requests and responses are written as JSON, between clients and a manager.

The direct clients send everything through here, as HTTP clients do, so a
value that cannot travel fails in memory exactly where it would fail deployed.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from neorc_core import _values
from neorc_core._errors import InvalidValueError
from neorc_core._queues import Queue
from neorc_core._runs import (
    Event,
    EventKind,
    QueueTask,
    Run,
    RunStatus,
    Task,
    TaskDelivery,
)
from neorc_core._task import TaskStatus
from neorc_core.flows import (
    Address,
    Outcome,
    Reference,
    RunState,
    StepResult,
    TaskStep,
    Version,
)

Wire = dict[str, Any]


def transmit(message: Any) -> Any:
    """Send ``message`` through JSON text and back, as it would cross a network.

    Raises ``InvalidValueError`` for anything JSON cannot carry: types it has no
    form for, text that is not valid UTF-8, and nesting past the depth limit.
    """
    try:
        text = _values.dumps_json(message)
        encoded = text.encode("utf-8")  # a lone surrogate cannot be sent
        _values.ensure_json_depth(text)
        return json.loads(encoded)
    except (TypeError, ValueError, RecursionError) as exc:
        raise InvalidValueError(f"cannot be sent as JSON: {exc}") from None


def address_to(address: Address) -> Wire:
    return {"step": address.step, "scope": [[name, n] for name, n in address.scope]}


def address_from(wire: Wire) -> Address:
    return Address(wire["step"], tuple((name, n) for name, n in wire["scope"]))


def params_to(params: Mapping[str, Reference]) -> dict[str, str]:
    return {name: str(reference) for name, reference in params.items()}


def params_from(wire: Mapping[str, str]) -> dict[str, Reference]:
    return {name: Reference.parse(text) for name, text in wire.items()}


def datetime_to(moment: datetime | None) -> str | None:
    """A timestamp as ISO 8601 text with its offset, or ``None``."""
    return moment.isoformat() if moment is not None else None


def datetime_from(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text is not None else None


def run_to(run: Run) -> Wire:
    return {
        "id": str(run.id),
        "flow": run.flow,
        "version": str(run.version),
        "inputs": dict(run.inputs),
        "status": run.status.value,
        "root_id": str(run.root_id),
        "parent_id": str(run.parent_id) if run.parent_id else None,
        "parent_address": (
            address_to(run.parent_address) if run.parent_address else None
        ),
        "output": run.output,
        "reason": run.reason,
        "created_at": datetime_to(run.created_at),
        "finished_at": datetime_to(run.finished_at),
    }


def run_from(wire: Wire) -> Run:
    return Run(
        id=uuid.UUID(wire["id"]),
        flow=wire["flow"],
        version=Version.parse(wire["version"]),
        inputs=wire["inputs"],
        status=RunStatus(wire["status"]),
        root_id=uuid.UUID(wire["root_id"]),
        parent_id=uuid.UUID(wire["parent_id"]) if wire["parent_id"] else None,
        parent_address=(
            address_from(wire["parent_address"]) if wire["parent_address"] else None
        ),
        output=wire["output"],
        reason=wire["reason"],
        created_at=datetime_from(wire["created_at"]),
        finished_at=datetime_from(wire["finished_at"]),
    )


def event_to(event: Event) -> Wire:
    return {
        "sequence": event.sequence,
        "run_id": str(event.run_id),
        "kind": event.kind.value,
    }


def event_from(wire: Wire) -> Event:
    return Event(wire["sequence"], uuid.UUID(wire["run_id"]), EventKind(wire["kind"]))


def run_state_to(state: RunState) -> Wire:
    return {
        "inputs": dict(state.inputs),
        "steps": [
            {
                "address": address_to(address),
                "outcome": result.outcome.value,
                "value": result.value,
            }
            for address, result in state.steps.items()
        ],
    }


def run_state_from(wire: Wire) -> RunState:
    return RunState(
        wire["inputs"],
        {
            address_from(step["address"]): StepResult(
                Outcome(step["outcome"]), step["value"]
            )
            for step in wire["steps"]
        },
    )


def task_to(task: Task) -> Wire:
    return {
        "id": str(task.id),
        "run_id": str(task.run_id),
        "address": address_to(task.address),
        "queue": task.queue,
        "handler": task.handler,
        "params": params_to(task.params),
        "fixed_params": dict(task.fixed_params),
        "status": task.status.value,
        "attempts": task.attempts,
        "lease_expires_at": datetime_to(task.lease_expires_at),
        "result": task.result,
        "error": task.error,
        "created_at": datetime_to(task.created_at),
        "started_at": datetime_to(task.started_at),
        "finished_at": datetime_to(task.finished_at),
    }


def task_from(wire: Wire) -> Task:
    return Task(
        id=uuid.UUID(wire["id"]),
        run_id=uuid.UUID(wire["run_id"]),
        address=address_from(wire["address"]),
        queue=wire["queue"],
        handler=wire["handler"],
        params=params_from(wire["params"]),
        fixed_params=wire["fixed_params"],
        status=TaskStatus(wire["status"]),
        attempts=wire["attempts"],
        lease_expires_at=datetime_from(wire["lease_expires_at"]),
        result=wire["result"],
        error=wire["error"],
        created_at=datetime_from(wire["created_at"]),
        started_at=datetime_from(wire["started_at"]),
        finished_at=datetime_from(wire["finished_at"]),
    )


def delivery_to(delivery: TaskDelivery) -> Wire:
    return {"task": task_to(delivery.task), "inputs": dict(delivery.inputs)}


def delivery_from(wire: Wire) -> TaskDelivery:
    return TaskDelivery(task_from(wire["task"]), wire["inputs"])


def queue_to(queue: Queue) -> Wire:
    return {"name": queue.name, "kind": queue.kind.value}


def queue_task_to(listed: QueueTask) -> Wire:
    return {
        "task": task_to(listed.task),
        "flow": listed.flow,
        "version": str(listed.version),
    }


def task_step_to(step: TaskStep) -> Wire:
    return {
        "name": step.name,
        "handler": step.handler,
        "queue": step.queue,
        "params": params_to(step.params),
        "fixed_params": dict(step.fixed_params),
    }


def task_step_from(wire: Wire) -> TaskStep:
    return TaskStep(
        wire["name"],
        handler=wire["handler"],
        queue=wire["queue"],
        params=params_from(wire["params"]),
        fixed_params=wire["fixed_params"],
    )
