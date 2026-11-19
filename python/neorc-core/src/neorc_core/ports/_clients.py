# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The ports that reach a manager for flows: one for workers, one for the rest.

Values cross them in their JSON form, datetimes tagged. Errors are the core
exceptions, whatever carries the call. The HTTP implementation in ``neorc``
long-polls the manager; Redis or SQS could take its place for receiving
tasks, while claiming them stays with the manager.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from datetime import datetime

from neorc_core._errors import InvalidValueError
from neorc_core._queues import QueueKind
from neorc_core._runs import Event, Run, RunId, TaskDelivery
from neorc_core._task import TaskId
from neorc_core._values import JsonValue
from neorc_core.flows import (
    Address,
    FlowDefinition,
    Reference,
    RunState,
    TaskStep,
    Version,
)

DEFAULT_LEASE_SECONDS = 60.0

MAX_LEASE_SECONDS = 24 * 60 * 60.0
"""The longest lease a worker may ask for.

A bound every store can add to a clock: an infinite or astronomical lease would
fail in each store in its own way, and hand no task back either way.
"""


def check_lease_seconds(lease_seconds: float) -> None:
    """Raise ``InvalidValueError`` unless ``lease_seconds`` is a lease to grant."""
    if (
        isinstance(lease_seconds, bool)
        or not isinstance(lease_seconds, int | float)
        or not math.isfinite(lease_seconds)
        or not 0 < lease_seconds <= MAX_LEASE_SECONDS
    ):
        raise InvalidValueError(
            f"lease_seconds must be over 0 and at most {MAX_LEASE_SECONDS}, "
            f"not {lease_seconds!r}"
        )


class QueueClient(ABC):
    """How a worker gets its queue's tasks and reports on them."""

    @abstractmethod
    async def task_definitions(self, queue: str) -> list[TaskStep]:
        """Every task on ``queue`` in the latest flows, to check at startup."""
        raise NotImplementedError

    @abstractmethod
    async def receive_task(
        self,
        queue: str,
        *,
        timeout: float,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> TaskDelivery | None:
        """Lease the next task on ``queue``, waiting up to ``timeout`` for one.

        Receiving is not the final word: a queue backend may deliver a task
        again, so a worker claims the task before running it.
        """
        raise NotImplementedError

    @abstractmethod
    async def extend_lease(
        self, task_id: TaskId, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> datetime:
        """Heartbeat: push the lease out, and return its new expiry."""
        raise NotImplementedError

    @abstractmethod
    async def claim_task(self, task_id: TaskId) -> None:
        """Claim the task from the manager, before executing it.

        ``RunStateError`` means its run is no longer active: drop the task.
        """
        raise NotImplementedError

    @abstractmethod
    async def report_finished(
        self, task_id: TaskId, *, result: JsonValue = None, error: str | None = None
    ) -> None:
        """Report the task's result in its JSON form, or its failure."""
        raise NotImplementedError


class ManagerClient(ABC):
    """How flows are uploaded and run, and how the scheduler drives runs."""

    @abstractmethod
    async def upload_flows(self, contents: Sequence[JsonValue]) -> list[bool]:
        """Upload flows deployed together; per flow, whether a version was stored."""
        raise NotImplementedError

    @abstractmethod
    async def declare_queue(self, name: str, kind: QueueKind) -> bool:
        """Declare a new queue of ``kind``; whether it is new.

        A queue declared already with ``kind`` is left as it is; one declared
        with another kind raises ``QueueKindError``.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_flow(
        self, name: str, version: Version | None = None
    ) -> FlowDefinition:
        """A flow version's definition, the latest when ``version`` is ``None``."""
        raise NotImplementedError

    @abstractmethod
    async def start_run(self, flow: str, inputs: Mapping[str, JsonValue]) -> Run:
        """Start a run of a flow's latest version."""
        raise NotImplementedError

    @abstractmethod
    async def get_run(self, run_id: RunId) -> Run:
        """A run."""
        raise NotImplementedError

    @abstractmethod
    async def cancel_run(self, run_id: RunId) -> None:
        """Cancel a run by hand, with its whole tree."""
        raise NotImplementedError

    @abstractmethod
    async def wait_for_events(
        self, after: int, *, timeout: float, limit: int = 100
    ) -> list[Event]:
        """Events after the sequence ``after``, waiting up to ``timeout`` for one."""
        raise NotImplementedError

    @abstractmethod
    async def run_state(self, run_id: RunId) -> RunState:
        """What a run's tasks and sub-flow runs have produced so far."""
        raise NotImplementedError

    @abstractmethod
    async def publish_task(self, run_id: RunId, address: Address) -> None:
        """Publish the task at ``address`` in a run."""
        raise NotImplementedError

    @abstractmethod
    async def start_sub_run(self, parent_id: RunId, address: Address) -> None:
        """Start the sub-flow run at ``address`` in a run."""
        raise NotImplementedError

    @abstractmethod
    async def succeed_run(self, run_id: RunId, output: Reference | None) -> None:
        """Mark a run succeeded, with the value of ``output``."""
        raise NotImplementedError

    @abstractmethod
    async def fail_run(self, run_id: RunId, reason: str) -> None:
        """Fail a run and the rest of its tree."""
        raise NotImplementedError
