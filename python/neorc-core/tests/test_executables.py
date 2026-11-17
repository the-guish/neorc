# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Executables as handlers: an executable file of the worker's, run as a task."""

import asyncio
import os
import sys
import time
import uuid
from pathlib import Path
from textwrap import dedent

import pytest

from neorc_core import HandlerError, Manager, QueueKind, Worker, _executables
from neorc_core._executables import (
    ExecutableError,
    locate_executable,
    resolve_executable,
)
from neorc_core._handlers import resolve_handler
from neorc_core._values import MAX_PAYLOAD_BYTES, MAX_VALUE_DEPTH, JsonValue
from neorc_core.flows import Address, Outcome
from neorc_core.local import DirectQueueClient, MemoryStore, MemoryTaskNotifier

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the executables here are POSIX scripts"
)

WHEN: JsonValue = {"$datetime": "2026-09-13T10:00:00+00:00"}


@pytest.fixture
def flows() -> Manager:
    return Manager(
        MemoryStore(queues={"default": QueueKind.WORKER}),
        tasks=MemoryTaskNotifier(),
        events=MemoryTaskNotifier(),
    )


def executable(location: Path, name: str, body: str, *, run_by: str = "/bin/sh") -> str:
    """An executable file in ``location``'s executables directory; its handler."""
    path = location / "executables" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{run_by}\n{dedent(body)}")
    path.chmod(0o755)
    return f"executables/{name}"


def in_python(location: Path, name: str, body: str) -> str:
    return executable(location, name, body, run_by=sys.executable)


def flow(task: JsonValue, **inputs: str) -> JsonValue:
    declared: JsonValue = dict(inputs)
    return {
        "name": "f",
        "version": "1.0.0",
        "inputs": declared,
        "steps": {"work": task},
    }


async def published(
    flows: Manager, content: JsonValue, inputs: dict[str, JsonValue]
) -> uuid.UUID:
    await flows.upload_flows([content])
    run = await flows.start_run("f", inputs)
    await flows.publish_task(run.id, Address("work"))
    return run.id


def worker(flows: Manager, location: Path, lease_seconds: float = 5) -> Worker:
    return Worker(
        DirectQueueClient(flows),
        code_location=location,
        poll_timeout=0.1,
        lease_seconds=lease_seconds,
    )


async def ran(
    flows: Manager, location: Path, handler: str
) -> tuple[Outcome, JsonValue, str | None]:
    """Run a task with ``handler`` and no inputs: how it ended, its value, its error."""
    run_id = await published(flows, flow({"handler": handler}), {})
    running = worker(flows, location)
    await running.prepare()
    await running.run_once()
    step = (await flows.run_state(run_id)).steps[Address("work")]
    (task,) = await flows.run_tasks(run_id)
    return step.outcome, step.value, task.error


async def test_a_executable_reads_its_inputs_and_what_it_writes_is_the_result(
    flows: Manager, tmp_path: Path
) -> None:
    handler = in_python(
        tmp_path,
        "pad.py",
        """
        import json, sys

        given = json.load(sys.stdin)
        padded = given["word"] + given["pad"] * given["tries"]
        json.dump({"padded": padded, "when": given["when"]}, sys.stdout)
        """,
    )
    task: JsonValue = {
        "handler": handler,
        "params": {
            "word": "inputs.word",
            "when": "inputs.when",
            "tries": "neorc.attempts",
        },
        "fixed_params": {"pad": "*"},
    }
    run_id = await published(
        flows,
        flow(task, word="string", when="datetime"),
        {"word": "red", "when": WHEN},
    )
    running = worker(flows, tmp_path)
    await running.prepare()

    await running.run_once()

    # A datetime reaches an executable as it travels, and comes back the same way.
    state = await flows.run_state(run_id)
    assert state.steps[Address("work")].value == {"padded": "red*", "when": WHEN}


async def test_a_executable_in_any_language_is_a_task(
    flows: Manager, tmp_path: Path
) -> None:
    handler = executable(
        tmp_path, "count.sh", """printf '{"bytes": %d}' "$(wc -c)"\n"""
    )
    task: JsonValue = {"handler": handler, "params": {"word": "inputs.word"}}
    run_id = await published(flows, flow(task, word="string"), {"word": "red"})
    running = worker(flows, tmp_path)
    await running.prepare()

    await running.run_once()

    # The inputs are one compact JSON object, and nothing after it.
    state = await flows.run_state(run_id)
    assert state.steps[Address("work")].value == {"bytes": len('{"word":"red"}')}


async def test_a_executable_that_writes_nothing_gives_null(
    flows: Manager, tmp_path: Path
) -> None:
    handler = executable(tmp_path, "quiet.sh", "cat > /dev/null\n")

    outcome, value, error = await ran(flows, tmp_path, handler)

    assert (outcome, value, error) == (Outcome.SUCCEEDED, None, None)


async def test_a_executable_need_not_read_its_inputs(
    flows: Manager, tmp_path: Path
) -> None:
    handler = executable(tmp_path, "deaf.sh", "echo 7\n")

    outcome, value, _ = await ran(flows, tmp_path, handler)

    assert (outcome, value) == (Outcome.SUCCEEDED, 7)


async def test_a_executable_that_exits_with_an_error_fails_its_task_with_what_it_said(
    flows: Manager, tmp_path: Path
) -> None:
    handler = executable(
        tmp_path, "fails.sh", "echo 'half done'\necho 'no such order' >&2\nexit 3\n"
    )

    outcome, value, error = await ran(flows, tmp_path, handler)

    assert (outcome, value) == (Outcome.FAILED, None)
    assert (
        error
        == "ExecutableError: executables/fails.sh exited with code 3: no such order"
    )


async def test_output_that_is_not_a_value_fails_the_task(
    flows: Manager, tmp_path: Path
) -> None:
    not_json = executable(tmp_path, "chatty.sh", "echo 'working on it'\n")
    reserved = executable(tmp_path, "reserved.sh", """echo '{"$mine": 1}'\n""")

    _, _, said = await ran(flows, tmp_path, not_json)
    flows = Manager(
        MemoryStore(queues={"default": QueueKind.WORKER}),
        tasks=MemoryTaskNotifier(),
        events=MemoryTaskNotifier(),
    )
    _, _, kept = await ran(flows, tmp_path, reserved)

    assert said is not None and "wrote output that is not JSON" in said
    assert kept is not None and "reserved for neorc" in kept


async def test_a_executable_that_writes_too_much_is_ended_and_its_task_fails(
    flows: Manager, tmp_path: Path
) -> None:
    handler = in_python(
        tmp_path,
        "flood.py",
        f"""
        import sys, time

        sys.stdout.write("x" * {MAX_PAYLOAD_BYTES + 1})
        sys.stdout.flush()
        time.sleep(30)
        """,
    )
    started = time.monotonic()

    outcome, _, error = await ran(flows, tmp_path, handler)

    assert outcome is Outcome.FAILED
    assert error is not None and "bytes of output" in error
    assert time.monotonic() - started < 10


async def test_a_executable_that_cannot_be_started_fails_its_task(
    flows: Manager, tmp_path: Path
) -> None:
    handler = executable(tmp_path, "lost", "", run_by="/no/such/interpreter")

    outcome, _, error = await ran(flows, tmp_path, handler)

    assert outcome is Outcome.FAILED
    assert error is not None and "executables/lost could not be started" in error


async def test_a_executable_whose_heartbeat_stops_is_ended_and_its_task_fails(
    flows: Manager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_executables, "HEARTBEAT_STALE_SECONDS", 0.3)
    handler = executable(
        tmp_path, "stuck.sh", 'touch "$NEORC_HEARTBEAT_FILE"\nsleep 30\n'
    )
    started = time.monotonic()

    outcome, _, error = await ran(flows, tmp_path, handler)

    assert outcome is Outcome.FAILED
    assert error == (
        "ExecutableError: executables/stuck.sh wrote no heartbeat for 0.3 seconds"
    )
    assert time.monotonic() - started < 10


async def test_a_executable_that_keeps_beating_runs_to_its_end(
    flows: Manager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_executables, "HEARTBEAT_STALE_SECONDS", 0.5)
    handler = executable(
        tmp_path,
        "alive.sh",
        """
        for beat in 1 2 3 4 5 6 7 8; do
            touch "$NEORC_HEARTBEAT_FILE"
            sleep 0.1
        done
        echo '"alive"'
        """,
    )

    outcome, value, _ = await ran(flows, tmp_path, handler)

    assert (outcome, value) == (Outcome.SUCCEEDED, "alive")


async def test_a_executable_that_never_beats_is_not_watched(
    flows: Manager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_executables, "HEARTBEAT_STALE_SECONDS", 0.2)
    handler = executable(tmp_path, "silent.sh", "sleep 0.7\necho 7\n")

    outcome, value, _ = await ran(flows, tmp_path, handler)

    assert (outcome, value) == (Outcome.SUCCEEDED, 7)


async def test_a_running_executable_leaves_the_worker_free_to_keep_its_lease(
    flows: Manager, tmp_path: Path
) -> None:
    handler = executable(tmp_path, "slow.sh", "sleep 0.6\necho '\"done\"'\n")
    run_id = await published(flows, flow({"handler": handler}), {})
    running = worker(flows, tmp_path, lease_seconds=0.15)
    other = DirectQueueClient(flows)
    stolen: list[object] = []

    async def try_to_steal() -> None:
        await asyncio.sleep(0.4)
        stolen.append(await other.receive_task("default", timeout=0))

    async with asyncio.TaskGroup() as group:
        group.create_task(try_to_steal())
        await running.run_once()

    assert stolen == [None]
    state = await flows.run_state(run_id)
    assert state.steps[Address("work")].value == "done"


async def test_cancelling_a_task_ends_its_executable_and_what_the_executable_started(
    tmp_path: Path,
) -> None:
    # The executable's working directory is the code location: `ticks` is there.
    handler = executable(
        tmp_path,
        "spawns.sh",
        """
        (while :; do echo . >> ticks; sleep 0.05; done) &
        wait
        """,
    )
    ticks = tmp_path / "ticks"
    task = asyncio.ensure_future(resolve_executable(handler, tmp_path)())
    for _ in range(100):
        if ticks.exists():
            break
        await asyncio.sleep(0.05)

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert task.cancelled()
    written = ticks.stat().st_size
    assert written > 0
    await asyncio.sleep(0.3)
    assert ticks.stat().st_size == written


async def test_what_a_executable_leaves_running_does_not_hold_its_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_executables, "DRAIN_SECONDS", 0.2)
    plain = executable(tmp_path, "plain.sh", "echo 7\n")
    # The one left behind holds the executable's output open for as long as it runs.
    leaves = executable(tmp_path, "leaves.sh", "(sleep 5) &\necho 7\n")
    descriptors = Path("/proc/self/fd")

    async def held() -> int:
        await asyncio.sleep(0.05)  # a closed pipe is let go at the loop's next turn
        return len(list(descriptors.iterdir())) if descriptors.is_dir() else 0

    await resolve_executable(plain, tmp_path)()
    before = await held()
    started = time.monotonic()

    value = await resolve_executable(leaves, tmp_path)()

    assert value == 7
    assert time.monotonic() - started < 3
    # The worker lets go of the output it was reading, though it never ended.
    assert await held() == before


async def test_inputs_as_deep_as_loops_and_fan_outs_make_them_reach_a_executable(
    tmp_path: Path,
) -> None:
    handler = executable(tmp_path, "size.sh", "wc -c\n")
    deep: JsonValue = "leaf"
    for _ in range(MAX_VALUE_DEPTH + 10):
        deep = [deep]

    size = await resolve_executable(handler, tmp_path)(collected=deep)

    assert size > 2 * (MAX_VALUE_DEPTH + 10)


async def test_startup_reports_every_executable_that_is_not_there(
    flows: Manager, tmp_path: Path
) -> None:
    (tmp_path / "executables").mkdir()
    (tmp_path / "executables" / "notes.txt").write_text("not an executable\n")
    await flows.upload_flows(
        [
            {
                "name": "f",
                "version": "1.0.0",
                "steps": {
                    "missing": {"handler": "executables/missing.sh"},
                    "plain": {"handler": "executables/notes.txt"},
                    "outside": {"handler": "executables/../escape.sh"},
                },
            }
        ]
    )

    with pytest.raises(HandlerError) as refused:
        await worker(flows, tmp_path).prepare()

    assert refused.value.problems == [
        "missing: no executable 'executables/missing.sh' in the code location",
        "plain: 'executables/notes.txt' is not an executable file",
        "outside: 'executables/../escape.sh' is not an executable's path: after "
        "'executables/', names of letters, digits, '_', '-' and '.', joined by '/', "
        "none starting with '.' or '-'",
    ]


async def test_a_path_out_of_the_executables_directory_is_never_started(
    flows: Manager, tmp_path: Path
) -> None:
    (tmp_path / "executables").mkdir()
    outside = tmp_path / "escape.sh"
    outside.write_text("#!/bin/sh\ntouch started\n")
    outside.chmod(0o755)
    running = worker(flows, tmp_path)
    await running.prepare()  # nothing uploaded yet: the flow comes later
    run_id = await published(flows, flow({"handler": "executables/../escape.sh"}), {})

    await running.run_once()

    (task,) = await flows.run_tasks(run_id)
    state = await flows.run_state(run_id)
    assert state.steps[Address("work")].outcome is Outcome.FAILED
    assert task.error is not None and "is not an executable's path" in task.error
    assert not (tmp_path / "started").exists()


@pytest.mark.parametrize(
    "target",
    [
        "executables/",
        "executables//run.sh",
        "executables/./run.sh",
        "executables/../run.sh",
        "executables/sub/../run.sh",
        "executables/run.sh/..",
        "executables/.hidden",
        "executables/-rf",
        "executables/run me.sh",
        "executables/run.sh\n",
        "executables/sub\\run.sh",
        "executables/run.sh:main",
        "executables/~/run.sh",
        "executables/$HOME/run.sh",
    ],
)
def test_a_path_that_is_not_plain_names_is_refused(tmp_path: Path, target: str) -> None:
    executable(tmp_path, "run.sh", "echo 1\n")
    executable(tmp_path, "sub/run.sh", "echo 1\n")

    with pytest.raises(ValueError, match="is not an executable's path"):
        resolve_handler(target, tmp_path)


def test_a_executable_may_live_in_a_directory_under_executables(tmp_path: Path) -> None:
    handler = executable(tmp_path, "billing/v2/close_month.sh", "echo 1\n")

    assert locate_executable(handler, tmp_path) == (
        tmp_path.resolve() / "executables" / "billing" / "v2" / "close_month.sh"
    )


def test_a_link_out_of_the_executables_directory_is_refused(tmp_path: Path) -> None:
    (tmp_path / "executables").mkdir()
    outside = tmp_path / "tool.sh"
    outside.write_text("#!/bin/sh\necho 1\n")
    outside.chmod(0o755)
    (tmp_path / "executables" / "tool.sh").symlink_to(outside)
    (tmp_path / "executables" / "shell").symlink_to("/bin/sh")

    with pytest.raises(ValueError, match="leads out of the executables directory"):
        locate_executable("executables/tool.sh", tmp_path)
    with pytest.raises(ValueError, match="leads out of the executables directory"):
        locate_executable("executables/shell", tmp_path)


def test_a_executables_directory_that_is_a_link_is_refused(tmp_path: Path) -> None:
    executable(tmp_path / "elsewhere", "run.sh", "echo 1\n")
    (tmp_path / "executables").symlink_to(tmp_path / "elsewhere" / "executables")

    with pytest.raises(ValueError, match="leads out of the executables directory"):
        locate_executable("executables/run.sh", tmp_path)


def test_a_link_that_stays_in_the_executables_directory_is_followed(
    tmp_path: Path,
) -> None:
    # As a mounted ConfigMap has: the name is a link to the file of a release.
    executable(tmp_path, "..data/run.sh", "echo 1\n")
    (tmp_path / "executables" / "run.sh").symlink_to("..data/run.sh")

    assert locate_executable("executables/run.sh", tmp_path) == (
        tmp_path.resolve() / "executables" / "..data" / "run.sh"
    )


def test_what_is_not_an_executable_file_is_not_a_executable(tmp_path: Path) -> None:
    executable(tmp_path, "tools/run.sh", "echo 1\n")
    (tmp_path / "executables" / "notes.txt").write_text("not an executable\n")

    with pytest.raises(ValueError, match="no executable 'executables/tools'"):
        locate_executable("executables/tools", tmp_path)
    with pytest.raises(ValueError, match="is not an executable file"):
        locate_executable("executables/notes.txt", tmp_path)
    with pytest.raises(ValueError, match=r"no executable 'executables/gone\.sh'"):
        locate_executable("executables/gone.sh", tmp_path)


async def test_a_executable_removed_since_the_worker_started_fails_its_task(
    tmp_path: Path,
) -> None:
    handler = executable(tmp_path, "run.sh", "echo 1\n")
    run = resolve_executable(handler, tmp_path)
    (tmp_path / "executables" / "run.sh").unlink()

    with pytest.raises(ValueError, match=r"no executable 'executables/run\.sh'"):
        await run()


async def test_a_executable_ended_by_a_signal_says_which(tmp_path: Path) -> None:
    handler = executable(tmp_path, "dies.sh", "kill -TERM $$\n")

    with pytest.raises(
        ExecutableError, match=r"executables/dies\.sh was ended by signal 15"
    ):
        await resolve_executable(handler, tmp_path)()


def test_a_jump_of_the_wall_clock_does_not_make_a_heartbeat_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    beat = _executables._Heartbeat(tmp_path / "heartbeat")
    assert not beat.is_stale(60)  # never touched: not watched
    beat.path.touch()
    assert not beat.is_stale(60)

    # The clock steps an hour forward, as an NTP step or a resumed VM does.
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 3600)

    assert not beat.is_stale(60)


def test_a_heartbeat_that_stops_changing_goes_stale_by_the_monotonic_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    beat = _executables._Heartbeat(tmp_path / "heartbeat")
    beat.path.touch()
    assert not beat.is_stale(60)

    now[0] += 59
    assert not beat.is_stale(60)
    now[0] += 2
    assert beat.is_stale(60)

    # A touch makes it fresh again.
    os.utime(beat.path, ns=(1, 1))
    assert not beat.is_stale(60)
