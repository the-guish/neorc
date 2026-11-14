# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The whole system in one process: manager, scheduler and workers on one event loop."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Coroutine, Mapping
from pathlib import Path
from types import TracebackType

from neorc_core._errors import FlowDefinitionError, RunStateError
from neorc_core._manager import Manager
from neorc_core._queues import QueueKind
from neorc_core._runs import Run, RunId, RunStatus
from neorc_core._scheduler import Scheduler
from neorc_core._values import JsonValue
from neorc_core._worker import Worker
from neorc_core.flows import parse_flow, read_flows
from neorc_core.local._direct_clients import DirectManagerClient, DirectQueueClient
from neorc_core.local._memory_store import MemoryStore
from neorc_core.local._notifier import MemoryTaskNotifier
from neorc_core.ports._clients import DEFAULT_LEASE_SECONDS

_log = logging.getLogger(__name__)

DEFAULT_POLL_TIMEOUT = 5.0
"""How long idle loops wait in one poll. ``stop`` cuts a poll short.

A lease that lapses wakes nobody, as in a deployment: a waiting worker finds
the task when its poll ends and it asks again.
"""


class LocalCluster:
    """A manager, a scheduler and one worker per worker queue, in memory.

    Use it as an async context manager: leaving it stops the scheduler and the
    workers, letting a task in hand finish. Clients reach the manager through
    JSON, as they would over HTTP, so the cluster behaves as a deployment does.
    """

    def __init__(
        self,
        *,
        code_location: Path,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        poll_timeout: float = DEFAULT_POLL_TIMEOUT,
    ) -> None:
        self.manager = Manager(
            MemoryStore(), tasks=MemoryTaskNotifier(), events=MemoryTaskNotifier()
        )
        self.client = DirectManagerClient(self.manager)
        self._code_location = code_location
        self._lease_seconds = lease_seconds
        self._poll_timeout = poll_timeout
        self._scheduler: Scheduler | None = None
        self._workers: dict[str, Worker] = {}
        self._running: set[asyncio.Task[None]] = set()
        self._crash: BaseException | None = None
        self._crash_raised = False
        self._crashed = asyncio.Event()

    async def __aenter__(self) -> LocalCluster:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # Leaving on an error, a handler still running may never return.
        await self.close(cancel=exc is not None)

    async def upload(self, flows_dir: Path) -> list[bool]:
        """Upload every ``*.yaml`` and ``*.yml`` flow file in ``flows_dir`` as a set.

        A deployment's queues are declared by its operator. Here there is
        none, so the queues the files name are declared first, as worker
        queues, unless they are declared already: declare an external queue
        with ``manager.declare_queue`` before uploading.
        """
        contents = read_flows(flows_dir)
        for content in contents:
            try:
                definition = parse_flow(content)
            except FlowDefinitionError:
                continue  # the upload says what is wrong with it
            for task in definition.tasks():
                if await self.manager.queue_kind(task.queue) is None:
                    await self.manager.declare_queue(task.queue, QueueKind.WORKER)
        return await self.client.upload_flows(contents)

    async def start(self, *, workers: bool = True) -> None:
        """Start the scheduler, and a worker for every queue the latest flows use.

        Call it again after uploading flows with new queues. Raises
        ``HandlerError`` if a queue's handlers do not fit their tasks.
        """
        if self._scheduler is None:
            self._scheduler = Scheduler(self.client, poll_timeout=self._poll_timeout)
            self._spawn(self._scheduler.run())
        if workers:
            await self.start_workers()

    async def start_workers(self) -> None:
        """Start a worker for every worker queue in the latest flows that has none yet.

        An external queue gets none: its tasks wait for a completion, through
        ``manager.complete_task``.
        """
        queues = sorted(
            {
                task.queue
                for flow in await self.manager.latest_flows()
                for task in flow.definition.tasks()
            }
        )
        for queue in queues:
            if queue in self._workers:
                continue
            if await self.manager.queue_kind(queue) is QueueKind.EXTERNAL:
                continue
            worker = Worker(
                DirectQueueClient(self.manager),
                queue=queue,
                code_location=self._code_location,
                poll_timeout=self._poll_timeout,
                lease_seconds=self._lease_seconds,
            )
            await worker.prepare()
            self._workers[queue] = worker
            self._spawn(worker.run())

    async def run(
        self,
        flow: str,
        inputs: Mapping[str, JsonValue],
        *,
        timeout: float | None = None,
    ) -> Run:
        """Start a run of ``flow`` and wait for it to finish, however it ends."""
        run = await self.client.start_run(flow, inputs)
        try:
            return await self.wait(run.id, timeout=timeout)
        except TimeoutError:
            with contextlib.suppress(RunStateError):
                await self.client.cancel_run(run.id)
            raise

    async def wait(self, run_id: RunId, *, timeout: float | None = None) -> Run:
        """Wait for a run to finish; ``TimeoutError`` after ``timeout`` seconds.

        If the scheduler or a worker crashed, raises what it crashed with rather
        than waiting for a run that can no longer move.
        """
        async with asyncio.timeout(timeout):
            after = 0
            while True:
                self._raise_crash()
                run = await self.client.get_run(run_id)
                if run.status is not RunStatus.ACTIVE:
                    return run
                events = asyncio.ensure_future(
                    self.client.wait_for_events(after, timeout=self._poll_timeout)
                )
                crashed = asyncio.ensure_future(self._crashed.wait())
                try:
                    await asyncio.wait(
                        {events, crashed}, return_when=asyncio.FIRST_COMPLETED
                    )
                finally:
                    crashed.cancel()
                    if not events.done():
                        events.cancel()
                if events.done() and not events.cancelled():
                    new = events.result()
                    if new:
                        after = new[-1].sequence

    async def close(self, *, cancel: bool = False) -> None:
        """Stop the scheduler and the workers, and wait for them to return.

        A worker finishes the task in hand first, unless ``cancel``: then its
        lease is left to lapse. A crash not raised yet is logged.
        """
        if self._scheduler is not None:
            self._scheduler.stop()
        for worker in self._workers.values():
            worker.stop()
        running = list(self._running)
        if cancel:
            for task in running:
                task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        self._running.clear()
        if self._crash is not None and not self._crash_raised:
            _log.error("a cluster loop crashed", exc_info=self._crash)

    def _spawn(self, loop: Coroutine[None, None, None]) -> None:
        task = asyncio.ensure_future(loop)
        self._running.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[None]) -> None:
        self._running.discard(task)
        if self._crash is None and not task.cancelled() and task.exception():
            self._crash = task.exception()
            self._crashed.set()

    def _raise_crash(self) -> None:
        """Raise the crash, if any: every wait does, since nothing moves after it."""
        if self._crash is not None:
            self._crash_raised = True
            raise self._crash


async def run_local(
    flows_dir: Path,
    flow: str,
    inputs: Mapping[str, JsonValue],
    *,
    code_location: Path | None = None,
    timeout: float | None = None,
) -> Run:
    """Upload the flows in ``flows_dir``, run ``flow`` to its end, and return the run.

    ``inputs`` are in their JSON form, datetimes tagged. Handlers import from
    ``code_location``, by default the directory ``flows_dir`` is in. A task
    still in flight when the run finishes, as when a run fails while another
    branch is busy, is not waited for.
    """
    location = code_location if code_location is not None else flows_dir.parent
    cluster = LocalCluster(code_location=location)
    try:
        await cluster.upload(flows_dir)
        await cluster.start()
        return await cluster.run(flow, inputs, timeout=timeout)
    finally:
        # Once the run has finished, nothing a task still in flight produces
        # can matter: do not wait for its handler.
        await cluster.close(cancel=True)
