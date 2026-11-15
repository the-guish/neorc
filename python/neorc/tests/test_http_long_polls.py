# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The manager's long polls on a real server, as oneberth's ``serve`` runs it.

A long poll ends when its client leaves, and when the backend stops: a server
waits for a handler in progress before it stops, up to its grace. Each poll
here could wait a minute. The events and a worker's receive are the manager's
two long polls.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from conftest import composed, service_token
from oneberth.web import serve

from neorc.http import HttpManagerClient, HttpQueueClient
from neorc_core import Manager, QueueKind, Role
from neorc_core.local import MemoryStore, MemoryTaskNotifier
from neorc_core.ports import Subscription, TaskNotifier

WAIT = 60.0
"""How long each long poll may wait: far past what a test takes."""


class Watched(TaskNotifier):
    """The memory notifier, which says when a waiter subscribes and when it leaves.

    A test awaits these instead of sleeping. Every call goes to the memory
    notifier the manager runs on.
    """

    def __init__(self) -> None:
        self._notifier = MemoryTaskNotifier()
        self.waiting = asyncio.Event()
        self.left = asyncio.Event()

    async def notify(self) -> None:
        await self._notifier.notify()

    def subscribe(self) -> AbstractAsyncContextManager[Subscription]:
        return self._subscribed()

    @asynccontextmanager
    async def _subscribed(self) -> AsyncIterator[Subscription]:
        async with self._notifier.subscribe() as subscription:
            self.waiting.set()
            try:
                yield subscription
            finally:
                self.left.set()


@dataclass
class Backend:
    """A manager served on a loopback socket, and the stop of its server."""

    address: str
    events: Watched
    tasks: Watched
    tokens: dict[Role, str] = field(default_factory=dict)
    stop: asyncio.Event = field(default_factory=asyncio.Event)


@pytest.fixture
async def backend() -> AsyncIterator[tuple[Backend, asyncio.Task[None]]]:
    events, tasks = Watched(), Watched()
    manager = Manager(
        MemoryStore(queues={"default": QueueKind.WORKER}), tasks=tasks, events=events
    )
    listening = socket.socket()
    listening.bind(("127.0.0.1", 0))
    address = f"http://127.0.0.1:{listening.getsockname()[1]}"
    composition = await composed(manager, public_url=address, long_poll_timeout=WAIT)
    served = Backend(address, events, tasks)
    ready = asyncio.Event()
    server = asyncio.create_task(
        serve(
            composition,
            listening,
            ready=ready.set,
            log_level="warning",
            stop=served.stop,
        )
    )
    async with asyncio.timeout(20):
        await ready.wait()
    served.tokens[Role.SCHEDULER] = await service_token(composition, Role.SCHEDULER)
    served.tokens[Role.WORKER] = await service_token(
        composition, Role.WORKER, "default"
    )
    try:
        yield served, server
    finally:
        served.stop.set()
        await server
        listening.close()


Poll = Callable[[Backend], Coroutine[Any, Any, object]]


async def _events(backend: Backend) -> object:
    async with HttpManagerClient(
        backend.address, token=backend.tokens[Role.SCHEDULER], poll_timeout=WAIT
    ) as client:
        return await client.wait_for_events(0, timeout=WAIT)


async def _receive(backend: Backend) -> object:
    async with HttpQueueClient(
        backend.address, token=backend.tokens[Role.WORKER], poll_timeout=WAIT
    ) as client:
        return await client.receive_task("default", timeout=WAIT)


POLLS = [
    pytest.param(_events, "events", [], id="events"),
    pytest.param(_receive, "tasks", None, id="a worker's receive"),
]


@pytest.mark.parametrize(("poll", "notifier", "nothing"), POLLS)
async def test_a_long_poll_ends_when_its_client_leaves(
    backend: tuple[Backend, asyncio.Task[None]],
    poll: Poll,
    notifier: str,
    nothing: object,
) -> None:
    served, _ = backend
    watched: Watched = getattr(served, notifier)
    client = asyncio.create_task(poll(served))
    async with asyncio.timeout(10):
        await watched.waiting.wait()

    client.cancel()  # closes the connection mid-poll
    loop = asyncio.get_running_loop()
    begun = loop.time()
    async with asyncio.timeout(5):
        await watched.left.wait()

    assert loop.time() - begun < 1


@pytest.mark.parametrize(("poll", "notifier", "nothing"), POLLS)
async def test_a_backend_with_a_waiting_client_stops_at_once(
    backend: tuple[Backend, asyncio.Task[None]],
    poll: Poll,
    notifier: str,
    nothing: object,
) -> None:
    served, server = backend
    watched: Watched = getattr(served, notifier)
    client = asyncio.create_task(poll(served))
    async with asyncio.timeout(10):
        await watched.waiting.wait()

    loop = asyncio.get_running_loop()
    begun = loop.time()
    served.stop.set()
    async with asyncio.timeout(8):  # under the server's grace of 10 s
        await server

    # The client stayed until the stop, and heard "nothing yet".
    assert loop.time() - begun < 2
    assert await client == nothing
