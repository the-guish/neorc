# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Waking waiting workers and schedulers with LISTEN/NOTIFY.

One connection per manager process carries the ``LISTEN``; waiters hold nothing
but an in-process event. Idle workers therefore cost no connections, however
many of them are waiting. A second connection sends the announcements, so an
announcement never queues behind the listening connection's own round trips.

An announcement is a hint: a waiter that is woken re-reads the store, and one
that is not woken falls back on its poll timeout. So a request never waits for
one to be sent, and neither connection may turn a lost link into a manager
that needs restarting. ``notify`` only marks that there is something to
announce; one background task sends a single ``NOTIFY`` for every batch of
marks, on a connection it reopens when it breaks. Opening a connection and
sending an announcement are bounded in time, and the listening connection
repeats its ``LISTEN`` every few seconds, with a bound on the answer, so a
link dropped without a word is found dead within a bound rather than at the
kernel's retransmission limit.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from types import TracebackType

from neorc.postgres._driver import DRIVER_ERRORS, Connection, connect, quote_identifier
from neorc_core import NeorcError, Subscription, TaskNotifier
from neorc_core._subscriptions import LocalSubscriptions

TASKS_CHANNEL = "neorc_task_ready"
"""The channel a published task is announced on, to wake waiting workers."""

EVENTS_CHANNEL = "neorc_event_ready"
"""The channel a new event is announced on, to wake a waiting scheduler."""

CHANNEL = TASKS_CHANNEL

RECONNECT_SECONDS = 1.0
"""How long to wait before reopening a connection that broke."""

CONNECT_TIMEOUT_SECONDS = 5
"""The most opening a connection may take, unless the DSN's ``connect_timeout``
says otherwise."""

SEND_TIMEOUT_SECONDS = 30
"""The most sending one announcement may take before its link is taken for dead."""

HEARTBEAT_SECONDS = 10
"""How often the listening connection checks that its link still answers."""

HEARTBEAT_TIMEOUT_SECONDS = 20
"""How long the listening connection's check may wait for its answer."""

CLOSE_TIMEOUT_SECONDS = 5
"""How long closing a connection politely may take before it is cut."""

_log = logging.getLogger(__name__)


class PostgresTaskNotifier(TaskNotifier):
    """Announces published tasks, or events, over a Postgres notification channel.

    Every manager process listening on the channel hears every announcement, so
    a worker waiting on one process is woken by a task published through
    another.
    """

    def __init__(self, dsn: str, *, channel: str = CHANNEL) -> None:
        self._dsn = dsn
        self._channel = channel
        self._pending = asyncio.Event()
        self._listening: asyncio.Task[None] | None = None
        self._announcing: asyncio.Task[None] | None = None
        self._subscriptions = LocalSubscriptions()

    async def __aenter__(self) -> PostgresTaskNotifier:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def start(self) -> None:
        """Open the connections and begin listening and announcing.

        Fails if the database cannot be reached now, so a misconfigured
        deployment does not start; a connection lost later is reopened.
        """
        if self._listening is not None:
            return
        listener = await self._listen()
        try:
            sender = await self._connect()
        except BaseException:
            await _close(listener.connection)
            raise
        self._listening = asyncio.create_task(self._dispatch(listener))
        self._announcing = asyncio.create_task(self._announce(sender))

    async def aclose(self) -> None:
        """Stop listening and announcing, and release the connections."""
        for task in (self._listening, self._announcing):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        self._listening = None
        self._announcing = None

    async def notify(self) -> None:
        """Mark that there is something to announce. Returns at once.

        The announcement follows from the background task; several marks
        before it gets to them are one announcement, which wakes every waiter
        all the same.
        """
        if self._announcing is None:
            raise NeorcError("the notifier is not started")
        self._pending.set()

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[Subscription]:
        async with self._subscriptions.subscribe() as subscription:
            yield subscription

    async def _connect(self) -> Connection:
        return await connect(self._dsn, timeout=CONNECT_TIMEOUT_SECONDS)

    async def _listen(self) -> _Listener:
        connection = await self._connect()
        try:
            listener = _Listener(connection, self._subscriptions.wake_all)
            await connection.add_listener(self._channel, listener.notified)
        except BaseException:
            await _close(connection)
            raise
        return listener

    async def _announce(self, sender: Connection | None) -> None:
        """Send one announcement per batch of marks, for as long as the notifier runs.

        A sending connection that breaks is dropped and reopened for the next
        announcement; one that cannot be reopened costs that batch, whose
        waiters fall back on their poll timeout.
        """
        try:
            while True:
                await self._pending.wait()
                self._pending.clear()
                sender = await self._send(sender)
        finally:
            if sender is not None:
                await _close(sender)

    async def _send(self, sender: Connection | None) -> Connection | None:
        """One announcement, on ``sender`` or a connection opened in its place.

        Returns the connection to send the next one on: ``None`` after a
        failure, so the next attempt reconnects.
        """
        for attempt in (1, 2):
            try:
                if sender is None or sender.is_closed():
                    sender = await self._connect()
                await sender.execute(
                    "SELECT pg_notify($1, '')",
                    self._channel,
                    timeout=SEND_TIMEOUT_SECONDS,
                )
                return sender
            except DRIVER_ERRORS:
                if sender is not None:
                    await _close(sender)
                sender = None
                if attempt == 1:
                    _log.warning("announcing on %r failed; reconnecting", self._channel)
                else:
                    _log.warning(
                        "announcement on %r lost; waiters fall back on their "
                        "poll timeout",
                        self._channel,
                        exc_info=True,
                    )
        return None

    async def _dispatch(self, listener: _Listener) -> None:
        """Wake local waiters for as long as the notifier runs.

        A listening connection that breaks is reopened after a pause, and every
        waiter is woken once it is, since announcements made meanwhile were
        missed: they re-read the store and find whatever arrived.
        """
        while True:
            try:
                await self._watch(listener)
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.warning(
                    "the listener on %r stopped; reconnecting in %ss",
                    self._channel,
                    RECONNECT_SECONDS,
                    exc_info=True,
                )
            while True:
                await asyncio.sleep(RECONNECT_SECONDS)
                try:
                    listener = await self._listen()
                    break
                except DRIVER_ERRORS:
                    _log.warning("could not reopen the listener on %r", self._channel)
            self._subscriptions.wake_all()

    async def _watch(self, listener: _Listener) -> None:
        """Hold the listening connection until its link is lost; then raise.

        Notifications arrive by themselves; this checks every
        ``HEARTBEAT_SECONDS`` that the link still answers, by repeating the
        ``LISTEN``, which changes nothing, and closes the connection on the way
        out, however that is.
        """
        try:
            while True:
                with suppress(TimeoutError):
                    async with asyncio.timeout(HEARTBEAT_SECONDS):
                        await listener.lost.wait()
                if listener.lost.is_set():
                    raise ConnectionError("the listening connection was closed")
                await listener.connection.execute(
                    f"LISTEN {quote_identifier(self._channel)}",
                    timeout=HEARTBEAT_TIMEOUT_SECONDS,
                )
        finally:
            await _close(listener.connection)


class _Listener:
    """The listening connection, with what it wakes and whether it was lost."""

    def __init__(self, connection: Connection, wake: Callable[[], None]) -> None:
        self.connection = connection
        self.wake = wake
        self.lost = asyncio.Event()
        connection.add_termination_listener(self._terminated)

    def notified(
        self, connection: object, pid: int, channel: str, payload: str
    ) -> None:
        self.wake()

    def _terminated(self, connection: object) -> None:
        self.lost.set()


async def _close(connection: Connection) -> None:
    """Close ``connection``, politely if its link answers, and never raise."""
    try:
        await connection.close(timeout=CLOSE_TIMEOUT_SECONDS)
    except Exception:
        with suppress(Exception):
            connection.terminate()
