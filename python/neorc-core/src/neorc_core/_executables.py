# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Executables as handlers: an executable file of the worker's, run as a task.

A handler that starts with ``executables/`` names an executable file in the
``executables`` directory of the worker's code location. The worker starts it as
a process of its own, writes the task's inputs to its standard input as one
JSON object, and reads the task's result from its standard output as one JSON
value. So a task can be written in any language. See
docs/specs/workers-and-manager.md, "Executables".

Whoever uploads a flow chooses its handlers, so an executable is confined as a
function is. Its path is plain names under ``executables``, with no ``.``, no
``..`` and no empty part, and the file it leads to, links followed, is in that
directory too. The file is started as it is: no shell, no arguments, nothing
of the flow's on its command line.

The worker stays free while an executable runs: nothing here blocks its loop, and
a task that is cancelled ends its executable, with everything the executable
started.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import tempfile
import time
from asyncio.subprocess import PIPE, Process
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeGuard

from neorc_core import _values

EXECUTABLES_DIRECTORY = "executables"
"""The one directory of a code location that holds executables."""

HEARTBEAT_FILE_ENV = "NEORC_HEARTBEAT_FILE"
"""Names, to an executable, the file it may touch to say it is still working."""

HEARTBEAT_STALE_SECONDS = 60.0
"""How old a heartbeat may get before its executable is taken for stuck."""

MAX_LOG_BYTES = 16 * 1024
"""The most of an executable's standard error that is kept: its end."""

DRAIN_SECONDS = 1.0
"""How long an executable's output is still read after the executable has ended.

What the executable started may hold its output open for longer than that.
"""

_PREFIX = EXECUTABLES_DIRECTORY + "/"
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
_CHUNK = 64 * 1024

_log = logging.getLogger(__name__)


class ExecutableError(Exception):
    """An executable did not give a result: it failed, got stuck, or wrote no JSON.

    Raised by an executable's handler, as a function's handler raises its own
    errors: the worker reports it as the task's error, and it goes no further.
    """


def is_executable(target: object) -> TypeGuard[str]:
    """Whether a task's handler names an executable, not a function."""
    return isinstance(target, str) and target.startswith(_PREFIX)


def resolve_executable(
    target: str, code_location: Path
) -> Callable[..., Awaitable[Any]]:
    """The handler that runs the executable ``target`` names under ``code_location``.

    Raises ``ValueError`` saying why there is none, as ``locate_executable``
    does. The handler takes the task's inputs by name, whatever their names
    are. It looks for the executable again at every call, since the file may have
    changed since. An executable that beats is taken for stuck
    ``HEARTBEAT_STALE_SECONDS`` after its last beat.
    """
    locate_executable(target, code_location)

    async def run(**inputs: Any) -> Any:
        executable = locate_executable(target, code_location)
        return await _run(
            target,
            executable,
            code_location.resolve(),
            inputs,
            HEARTBEAT_STALE_SECONDS,
        )

    return run


def locate_executable(target: str, code_location: Path) -> Path:
    """The file ``target`` names in the executables directory of ``code_location``.

    Raises ``ValueError`` saying why there is none: the path is not plain
    names under ``executables``, it leads out of that directory, or there is no
    executable file there. The path returned has its links followed, so what
    was checked is what is started.
    """
    names = target.split("/")
    if names[0] != EXECUTABLES_DIRECTORY or len(names) < 2:
        raise ValueError(f"{target!r} is not a path under {_PREFIX!r}")
    if not all(_NAME.fullmatch(name) for name in names[1:]):
        raise ValueError(
            f"{target!r} is not an executable's path: after {_PREFIX!r}, names of "
            "letters, digits, '_', '-' and '.', joined by '/', none starting "
            "with '.' or '-'"
        )
    directory = code_location.resolve() / EXECUTABLES_DIRECTORY
    executable = directory.joinpath(*names[1:]).resolve()
    if not executable.is_relative_to(directory):
        raise ValueError(
            f"{target!r} leads out of the executables directory {directory}"
        )
    if not executable.is_file():
        raise ValueError(f"no executable {target!r} in the code location")
    if not os.access(executable, os.X_OK):
        raise ValueError(f"{target!r} is not an executable file")
    return executable


async def _run(
    target: str, executable: Path, location: Path, inputs: dict[str, Any], stale: float
) -> Any:
    """Run ``executable`` on ``inputs`` and return the value it wrote.

    The inputs were accepted already and collected into lists by the loops and
    fan-outs between, so they are written at the depth they arrived with.
    """
    sent = _values.dumps_json(
        _values.encode(inputs, path="inputs", limit=_values.MAX_JSON_DEPTH)
    ).encode("utf-8")
    output, log = bytearray(), bytearray()
    pipes: list[_Pipe] = []
    tasks: list[asyncio.Future[Any]] = []
    process: Process | None = None
    with tempfile.TemporaryDirectory(prefix="neorc-task-") as folder:
        heartbeat = _Heartbeat(Path(folder) / "heartbeat")
        try:
            pipes.append(written := await _Pipe.open())
            pipes.append(logged := await _Pipe.open())
            try:
                process = await asyncio.create_subprocess_exec(
                    str(executable),
                    stdin=PIPE,
                    stdout=written.writing,
                    stderr=logged.writing,
                    cwd=location,
                    env={**os.environ, HEARTBEAT_FILE_ENV: str(heartbeat.path)},
                    # A group of its own, so that ending the executable ends what
                    # it started too.
                    start_new_session=True,
                )
            except OSError as exc:
                raise ExecutableError(f"{target} could not be started: {exc}") from exc
            written.handed_over()
            logged.handed_over()
            assert process.stdin is not None
            exiting = asyncio.ensure_future(process.wait())
            reading = asyncio.ensure_future(
                _collect(written.reader, output, _values.MAX_PAYLOAD_BYTES)
            )
            tasks += [
                exiting,
                reading,
                asyncio.ensure_future(_feed(process.stdin, sent)),
                asyncio.ensure_future(_keep_end(logged.reader, log, MAX_LOG_BYTES)),
            ]
            while not exiting.done():
                watched = {exiting} if reading.done() else {exiting, reading}
                await asyncio.wait(
                    watched,
                    timeout=min(1.0, max(stale / 4, 0.01)),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                _ensure_fits(target, reading)
                if not exiting.done() and heartbeat.is_stale(stale):
                    raise ExecutableError(
                        f"{target} wrote no heartbeat for {stale:g} seconds"
                    )
            await asyncio.wait(tasks, timeout=DRAIN_SECONDS)
            _ensure_fits(target, reading)
        finally:
            if process is not None and process.returncode is None:
                _kill(process)
                await process.wait()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for pipe in pipes:
                pipe.close()
    said = log.decode("utf-8", errors="replace").strip()
    code = process.returncode
    assert code is not None
    if code != 0:
        ended = (
            f"exited with code {code}" if code > 0 else f"was ended by signal {-code}"
        )
        raise ExecutableError(f"{target} {ended}" + (f": {said}" if said else ""))
    if said:
        _log.info("%s wrote to its standard error:\n%s", target, said)
    return _written(target, bytes(output))


class _Pipe:
    """A pipe an executable writes to and the worker reads.

    The worker owns the reading end, and closes it when the task ends, whoever
    still holds the other: what an executable started may keep the executable's
    output open long after the executable has ended.
    """

    def __init__(
        self, transport: asyncio.ReadTransport, reader: asyncio.StreamReader, fd: int
    ) -> None:
        self._transport = transport
        self.reader = reader
        self.writing: int | None = fd
        """The end the executable writes to, until it is handed over."""

    @classmethod
    async def open(cls) -> _Pipe:
        reading, writing = os.pipe()
        reader = asyncio.StreamReader()
        try:
            transport, _ = await asyncio.get_running_loop().connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(reader),
                os.fdopen(reading, "rb", 0),
            )
        except BaseException:
            os.close(writing)
            raise
        return cls(transport, reader, writing)

    def handed_over(self) -> None:
        """Close this side's writing end: the executable has one of its own."""
        if self.writing is not None:
            os.close(self.writing)
            self.writing = None

    def close(self) -> None:
        self.handed_over()
        self._transport.close()


def _written(target: str, output: bytes) -> Any:
    """The value an executable wrote: JSON as it travels, or nothing for ``None``."""
    try:
        text = output.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExecutableError(
            f"{target} wrote output that is not UTF-8: {exc}"
        ) from None
    if not text.strip():
        return None
    _values.ensure_json_depth(text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExecutableError(
            f"{target} wrote output that is not JSON: {exc}"
        ) from None
    return _values.decode(parsed, path="output")


def _ensure_fits(target: str, reading: asyncio.Future[bool]) -> None:
    if reading.done() and not reading.cancelled() and not reading.result():
        raise ExecutableError(
            f"{target} wrote more than {_values.MAX_PAYLOAD_BYTES} bytes of output"
        )


class _Heartbeat:
    """The file an executable touches, read by the time it changes.

    The wall clock can jump, so the file's age is never compared with it. The
    worker remembers the last modification time it saw and the monotonic time
    it saw it change.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._seen: int | None = None
        self._changed = 0.0

    def is_stale(self, after: float) -> bool:
        """Whether a beating executable has stopped. A silent one is not watched."""
        try:
            beat = self.path.stat().st_mtime_ns
        except OSError:
            return False
        now = time.monotonic()
        if beat != self._seen:
            self._seen, self._changed = beat, now
        return now - self._changed > after


def _kill(process: Process) -> None:
    """End an executable that has not ended, with every process of its group."""
    try:
        if hasattr(os, "killpg"):
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass  # it ended in between


async def _feed(stdin: asyncio.StreamWriter, sent: bytes) -> None:
    """Write the inputs and close; an executable need not read them."""
    try:
        stdin.write(sent)
        await stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        stdin.close()


async def _collect(stream: asyncio.StreamReader, into: bytearray, limit: int) -> bool:
    """Read ``stream`` to its end; ``False`` as soon as it holds over ``limit``."""
    while chunk := await stream.read(_CHUNK):
        into += chunk
        if len(into) > limit:
            return False
    return True


async def _keep_end(stream: asyncio.StreamReader, into: bytearray, limit: int) -> None:
    """Read ``stream`` to its end, keeping the last ``limit`` bytes of it."""
    while chunk := await stream.read(_CHUNK):
        into += chunk
        del into[:-limit]
