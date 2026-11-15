# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Clients that call a manager in this process, through JSON all the same.

Every request and every response is written as JSON text and read back, as it
would be over HTTP, so values fail here exactly where they would deployed.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime

from neorc_core._manager import Manager
from neorc_core._runs import Event, Run, RunId, RunStarter, TaskDelivery
from neorc_core._task import TaskId
from neorc_core._values import JsonValue
from neorc_core._wire import (
    address_from,
    address_to,
    delivery_from,
    delivery_to,
    event_from,
    event_to,
    run_from,
    run_state_from,
    run_state_to,
    run_to,
    task_step_from,
    task_step_to,
    transmit,
)
from neorc_core.flows import (
    Address,
    FlowDefinition,
    Reference,
    RunState,
    TaskStep,
    Version,
    parse_flow,
)
from neorc_core.ports._clients import (
    DEFAULT_LEASE_SECONDS,
    ManagerClient,
    QueueClient,
)


class DirectQueueClient(QueueClient):
    """A worker's client for a manager in this process."""

    def __init__(self, manager: Manager) -> None:
        self._manager = manager

    async def task_definitions(self, queue: str) -> list[TaskStep]:
        request = transmit({"queue": queue})
        steps = await self._manager.task_definitions(request["queue"])
        return [
            task_step_from(wire) for wire in transmit([task_step_to(s) for s in steps])
        ]

    async def receive_task(
        self,
        queue: str,
        *,
        timeout: float,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> TaskDelivery | None:
        request = transmit(
            {"queue": queue, "timeout": timeout, "lease_seconds": lease_seconds}
        )
        delivery = await self._manager.receive_task(
            request["queue"],
            timeout=request["timeout"],
            lease_seconds=request["lease_seconds"],
        )
        if delivery is None:
            return None
        return delivery_from(transmit(delivery_to(delivery)))

    async def extend_lease(
        self, task_id: TaskId, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> datetime:
        request = transmit({"task_id": str(task_id), "lease_seconds": lease_seconds})
        expires_at = await self._manager.extend_lease(
            uuid.UUID(request["task_id"]), lease_seconds=request["lease_seconds"]
        )
        return datetime.fromisoformat(transmit(expires_at.isoformat()))

    async def claim_task(self, task_id: TaskId) -> None:
        request = transmit({"task_id": str(task_id)})
        await self._manager.claim_task(uuid.UUID(request["task_id"]))

    async def report_finished(
        self, task_id: TaskId, *, result: JsonValue = None, error: str | None = None
    ) -> None:
        request = transmit({"task_id": str(task_id), "result": result, "error": error})
        await self._manager.report_finished(
            uuid.UUID(request["task_id"]),
            result=request["result"],
            error=request["error"],
        )


class DirectManagerClient(ManagerClient):
    """The scheduler's and a deploy script's client for a manager in this process.

    ``started_by`` is who the client asks for: the runs it starts are theirs,
    as a credential names the starter over HTTP. ``None`` starts runs for no
    one, as ``neorc run`` does.
    """

    def __init__(
        self, manager: Manager, *, started_by: RunStarter | None = None
    ) -> None:
        self._manager = manager
        self._started_by = started_by

    async def upload_flows(self, contents: Sequence[JsonValue]) -> list[bool]:
        request = transmit({"flows": list(contents)})
        stored = await self._manager.upload_flows(request["flows"])
        return list(transmit(stored))

    async def get_flow(
        self, name: str, version: Version | None = None
    ) -> FlowDefinition:
        request = transmit({"name": name, "version": str(version) if version else None})
        stored = await self._manager.get_flow(
            request["name"],
            Version.parse(request["version"]) if request["version"] else None,
        )
        return parse_flow(transmit(stored.content))

    async def start_run(self, flow: str, inputs: Mapping[str, JsonValue]) -> Run:
        request = transmit({"flow": flow, "inputs": dict(inputs)})
        run = await self._manager.start_run(
            request["flow"], request["inputs"], started_by=self._started_by
        )
        return run_from(transmit(run_to(run)))

    async def get_run(self, run_id: RunId) -> Run:
        run = await self._manager.get_run(uuid.UUID(transmit(str(run_id))))
        return run_from(transmit(run_to(run)))

    async def cancel_run(self, run_id: RunId) -> None:
        await self._manager.cancel_run(uuid.UUID(transmit(str(run_id))))

    async def wait_for_events(
        self, after: int, *, timeout: float, limit: int = 100
    ) -> list[Event]:
        request = transmit({"after": after, "timeout": timeout, "limit": limit})
        events = await self._manager.wait_for_events(
            request["after"], timeout=request["timeout"], limit=request["limit"]
        )
        return [event_from(wire) for wire in transmit([event_to(e) for e in events])]

    async def run_state(self, run_id: RunId) -> RunState:
        state = await self._manager.run_state(uuid.UUID(transmit(str(run_id))))
        return run_state_from(transmit(run_state_to(state)))

    async def publish_task(self, run_id: RunId, address: Address) -> None:
        request = transmit({"run_id": str(run_id), "address": address_to(address)})
        await self._manager.publish_task(
            uuid.UUID(request["run_id"]), address_from(request["address"])
        )

    async def start_sub_run(self, parent_id: RunId, address: Address) -> None:
        request = transmit(
            {"parent_id": str(parent_id), "address": address_to(address)}
        )
        await self._manager.start_sub_run(
            uuid.UUID(request["parent_id"]), address_from(request["address"])
        )

    async def succeed_run(self, run_id: RunId, output: Reference | None) -> None:
        request = transmit(
            {"run_id": str(run_id), "output": str(output) if output else None}
        )
        await self._manager.succeed_run(
            uuid.UUID(request["run_id"]),
            Reference.parse(request["output"]) if request["output"] else None,
        )

    async def fail_run(self, run_id: RunId, reason: str) -> None:
        request = transmit({"run_id": str(run_id), "reason": reason})
        await self._manager.fail_run(uuid.UUID(request["run_id"]), request["reason"])
