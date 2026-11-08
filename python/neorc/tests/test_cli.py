# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The ``neorc`` command: what it parses, and what it refuses."""

from __future__ import annotations

import builtins
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4

import pytest

from neorc import _cli
from neorc_core import (
    FlowVersionError,
    HandlerError,
    InvalidValueError,
    ManagerUnavailableError,
    Run,
    RunStatus,
)
from neorc_core.flows import Version


def test_manager_start_takes_its_settings_from_the_command_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, ...]] = []

    def fake_run(host: str, port: int, **kwargs: Any) -> None:
        calls.append((host, port, kwargs))

    monkeypatch.setattr("neorc.manager.run", fake_run)

    exit_code = _cli.main(
        [
            "manager",
            "start",
            "--host",
            "127.0.0.1",
            "--port",
            "9000",
            "--database-url",
            "postgresql:///neorc",
            "--create-schema",
        ]
    )

    assert exit_code == 0
    assert calls == [
        (
            "127.0.0.1",
            9000,
            {
                "auth": True,
                "auth_config": None,
                "database_url": "postgresql:///neorc",
                "create_schema": True,
                "ui": True,
                "frame_ancestors": (),
                "ssl_certfile": None,
                "ssl_keyfile": None,
            },
        )
    ]


def test_manager_start_serves_the_api_alone_with_no_ui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(host: str, port: int, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr("neorc.manager.run", fake_run)

    exit_code = _cli.main(["manager", "start", "--no-ui"])

    assert exit_code == 0
    assert calls[0]["ui"] is False


def test_manager_start_lets_the_origins_it_is_given_frame_the_ui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(host: str, port: int, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr("neorc.manager.run", fake_run)

    exit_code = _cli.main(
        [
            "manager",
            "start",
            "--frame-ancestors",
            "http://127.0.0.1:8000",
            "--frame-ancestors",
            "HTTP://LocalHost:8000",
        ]
    )

    assert exit_code == 0
    assert calls[0]["frame_ancestors"] == (
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    )


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "http://*.example.com",
        "http://127.0.0.1:8000/ui/",
        "http://127.0.0.1:8000; script-src *",
        "'self'",
        "ftp://example.com",
        "http://user@example.com",
        "example.com",
    ],
)
def test_manager_start_refuses_a_frame_ancestor_that_is_not_an_origin(
    monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    monkeypatch.setattr("neorc.manager.run", lambda *args, **kwargs: None)

    with pytest.raises(SystemExit, match=r"--frame-ancestors .* is not an origin"):
        _cli.main(["manager", "start", "--frame-ancestors", origin])


def test_manager_start_refuses_frame_ancestors_with_no_ui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("neorc.manager.run", lambda *args, **kwargs: None)

    with pytest.raises(SystemExit, match=r"--frame-ancestors.*--no-ui"):
        _cli.main(
            [
                *("manager", "start", "--no-ui"),
                *("--frame-ancestors", "http://127.0.0.1:8000"),
            ]
        )


def test_manager_start_names_the_fix_when_there_is_no_built_ui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(host: str, port: int, **kwargs: Any) -> None:
        raise FileNotFoundError("static holds no built UI: run npm")

    monkeypatch.setattr("neorc.manager.run", fake_run)

    with pytest.raises(SystemExit, match=r"no built UI.*--no-ui"):
        _cli.main(["manager", "start"])


def test_an_unknown_command_exits_with_usage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        _cli.main(["orchestrate", "everything"])

    assert "usage: neorc" in capsys.readouterr().err


def test_the_console_script_is_installed() -> None:
    from importlib.metadata import entry_points

    scripts = entry_points(group="console_scripts")
    assert "neorc" in {script.name for script in scripts}
    assert sys.executable  # the script runs on this interpreter


def test_a_missing_extra_is_reported_as_the_command_that_installs_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The base install brings nothing, so this is the first thing a host meets."""
    real_import = builtins.__import__

    def without_httpx(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("httpx"):
            raise ImportError("No module named 'httpx'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_httpx)
    monkeypatch.delitem(sys.modules, "neorc.http", raising=False)
    monkeypatch.delitem(sys.modules, "neorc.http._clients", raising=False)

    with pytest.raises(SystemExit, match=r"pip install 'neorc\[http\]'"):
        _cli.main(["scheduler", "start", "--manager-address", "manager.test"])


def test_an_install_command_names_every_extra_it_needs() -> None:
    with (
        pytest.raises(SystemExit, match=r"pip install 'neorc\[manager,postgres\]'"),
        _cli._needs("manager", "postgres"),
    ):
        raise ImportError("No module named 'fastapi'")


_EXAMPLES = Path(__file__).parents[3] / "examples"


@pytest.fixture
def own_tasks_module(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The examples each have a ``tasks`` module: import the right one."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "tasks", raising=False)
    yield
    sys.modules.pop("tasks", None)


@pytest.mark.usefixtures("own_tasks_module")
def test_run_runs_a_flow_and_prints_its_output(
    capfd: pytest.CaptureFixture[str],
) -> None:
    exit_code = _cli.main(
        [
            "--log-level",
            "warning",
            "run",
            str(_EXAMPLES / "wordplay"),
            "--flow",
            "word_picker",
            "--inputs",
            '{"sentence": "potato tomate berry watermelon", "preferred_letter": "t"}',
        ]
    )

    assert exit_code == 0
    assert capfd.readouterr().out.splitlines()[-1] == '["potato", "tomate"]'


def test_run_passes_its_arguments_to_run_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "elsewhere").mkdir()
    calls: list[tuple[Any, ...]] = []

    async def fake_run_local(
        flows_dir: Path, flow: str, inputs: Any, **kwargs: Any
    ) -> Any:
        calls.append((flows_dir, flow, inputs, kwargs))
        return _run(RunStatus.SUCCEEDED, output={"when": {"$datetime": "x"}})

    monkeypatch.setattr(_cli, "run_local", fake_run_local)

    exit_code = _cli.main(
        [
            "run",
            str(tmp_path),
            "--flow",
            "f",
            "--inputs",
            '{"n": 1}',
            "--flows-dir",
            str(tmp_path / "elsewhere"),
            "--timeout",
            "3",
        ]
    )

    assert exit_code == 0
    assert calls == [
        (
            tmp_path / "elsewhere",
            "f",
            {"n": 1},
            {"code_location": tmp_path, "timeout": 3.0},
        )
    ]
    assert capsys.readouterr().out == '{"when": {"$datetime": "x"}}\n'


@pytest.mark.parametrize("status", [RunStatus.FAILED, RunStatus.CANCELLED])
def test_run_exits_1_with_the_reason_when_the_run_does_not_succeed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: RunStatus,
) -> None:
    (tmp_path / "flows").mkdir()

    async def fake_run_local(*args: Any, **kwargs: Any) -> Any:
        return _run(status, reason="work failed")

    monkeypatch.setattr(_cli, "run_local", fake_run_local)

    exit_code = _cli.main(["run", str(tmp_path), "--flow", "f"])

    assert exit_code == 1
    assert f"{status.value}: work failed" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("inputs", "message"),
    [("{", "not valid JSON"), ("[1]", "must be a JSON object")],
)
def test_run_refuses_inputs_that_are_not_a_json_object(
    tmp_path: Path, inputs: str, message: str
) -> None:
    (tmp_path / "flows").mkdir()

    with pytest.raises(SystemExit, match=message):
        _cli.main(["run", str(tmp_path), "--flow", "f", "--inputs", inputs])


def test_run_needs_a_flows_directory(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="no flows directory"):
        _cli.main(["run", str(tmp_path), "--flow", "f"])


@pytest.mark.usefixtures("own_tasks_module")
def test_run_reports_a_flow_it_cannot_run(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = _cli.main(["run", str(_EXAMPLES / "hello"), "--flow", "nothing"])

    assert exit_code == 1
    assert "cannot run 'nothing'" in capsys.readouterr().err


def _run(status: RunStatus, output: Any = None, reason: str | None = None) -> Any:
    run_id = uuid4()
    return Run(
        id=run_id,
        flow="f",
        version=Version(1, 0, 0),
        inputs={},
        status=status,
        root_id=run_id,
        output=output,
        reason=reason,
    )


def test_run_times_out_without_waiting_for_a_blocking_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "path", list(sys.path))
    module = f"blocking_{uuid4().hex}"
    (tmp_path / f"{module}.py").write_text(
        "import time\n\ndef stuck():\n    time.sleep(4)\n"
    )
    (tmp_path / "flows").mkdir()
    (tmp_path / "flows" / "slow.yaml").write_text(
        f"name: slow\nversion: 1.0.0\nsteps:\n  stuck: {{handler: {module}:stuck}}\n"
    )
    exits: list[int] = []

    def exit_now(code: int) -> None:
        exits.append(code)

    monkeypatch.setattr(_cli, "_exit_now", exit_now)
    started = time.monotonic()

    exit_code = _cli.main(["run", str(tmp_path), "--flow", "slow", "--timeout", "0.5"])

    assert time.monotonic() - started < 3
    assert exit_code == 1
    assert exits == [1]  # the handler's thread was still busy: exit at once
    assert "did not finish within 0.5 seconds" in capsys.readouterr().err


def test_a_failed_run_does_not_wait_for_a_handler_busy_elsewhere(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One queue's task fails the run while another queue's handler is stuck."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    module = f"mixed_{uuid4().hex}"
    (tmp_path / f"{module}.py").write_text(
        "import time\n\n"
        "def stuck():\n    time.sleep(4)\n\n"
        "def boom():\n    time.sleep(0.2)\n    raise RuntimeError('boom')\n"
    )
    (tmp_path / "flows").mkdir()
    (tmp_path / "flows" / "mixed.yaml").write_text(
        "name: mixed\nversion: 1.0.0\nsteps:\n"
        f"  stuck: {{queue: slow, handler: {module}:stuck}}\n"
        f"  boom: {{handler: {module}:boom}}\n"
    )
    exits: list[int] = []
    monkeypatch.setattr(_cli, "_exit_now", exits.append)
    started = time.monotonic()

    exit_code = _cli.main(["run", str(tmp_path), "--flow", "mixed", "--timeout", "30"])

    assert time.monotonic() - started < 3
    assert exit_code == 1
    assert exits == [1]
    assert "failed: boom failed" in capsys.readouterr().err


def test_a_flow_file_that_cannot_be_read_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "flows" / "old.yaml").mkdir(parents=True)

    exit_code = _cli.main(["run", str(tmp_path), "--flow", "old"])

    assert exit_code == 1
    assert "cannot run 'old'" in capsys.readouterr().err


def test_a_crash_while_running_is_reported_and_shut_down(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "flows").mkdir()
    finished: list[int] = []

    async def crashing(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("a worker loop died")

    def finish(loop: Any, executor: Any, threads: str, exit_code: int) -> int:
        finished.append(exit_code)
        return original_finish(loop, executor, threads, exit_code)

    original_finish = _cli._finish
    monkeypatch.setattr(_cli, "run_local", crashing)
    monkeypatch.setattr(_cli, "_finish", finish)

    exit_code = _cli.main(["run", str(tmp_path), "--flow", "f"])

    assert exit_code == 1
    assert finished == [1]
    assert "RuntimeError: a worker loop died" in capsys.readouterr().err


# The flows, scheduler and worker commands, with the core classes faked.


class _FakeClient:
    """An HTTP client that records how it was built and what it was asked."""

    built: ClassVar[list[tuple[str, dict[str, Any]]]] = []
    uploaded: ClassVar[list[Any]] = []
    stored: ClassVar[list[bool]] = [True, False]

    def __init__(self, address: str, **kwargs: Any) -> None:
        type(self).built.append((address, kwargs))

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def upload_flows(self, contents: Any) -> list[bool]:
        type(self).uploaded.append(contents)
        return type(self).stored


@pytest.fixture
def fake_clients(monkeypatch: pytest.MonkeyPatch) -> type[_FakeClient]:
    _FakeClient.built = []
    _FakeClient.uploaded = []
    _FakeClient.stored = [True, False]
    monkeypatch.setattr("neorc.http.HttpManagerClient", _FakeClient)
    monkeypatch.setattr("neorc.http.HttpQueueClient", _FakeClient)
    return _FakeClient


def test_flows_upload_sends_the_directory_as_one_set_and_says_what_was_stored(
    fake_clients: type[_FakeClient], capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = _cli.main(
        [
            "flows",
            "upload",
            str(_EXAMPLES / "hello" / "flows"),
            "--manager-address",
            "manager.test:8420",
        ]
    )

    assert exit_code == 0
    assert fake_clients.built == [("manager.test:8420", {})]
    (contents,) = fake_clients.uploaded
    assert [c["name"] for c in contents] == ["a", "b"]
    assert capsys.readouterr().out == "stored a 1.0.0\nunchanged b 1.0.0\n"


def test_flows_upload_reports_a_refused_upload(
    fake_clients: type[_FakeClient],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def refuse(self: Any, contents: Any) -> list[bool]:
        raise FlowVersionError("a 1.0.0 is already stored with different content")

    monkeypatch.setattr(_FakeClient, "upload_flows", refuse)

    exit_code = _cli.main(
        [
            "flows",
            "upload",
            str(_EXAMPLES / "hello" / "flows"),
            "--manager-address",
            "manager.test",
        ]
    )

    assert exit_code == 1
    assert "upload refused: a 1.0.0 is already stored" in capsys.readouterr().err


def test_flows_upload_refuses_invalid_files_before_sending(
    fake_clients: type[_FakeClient], tmp_path: Path
) -> None:
    (tmp_path / "broken.yaml").write_text("name: broken\nsteps: {}\n")

    with pytest.raises(SystemExit, match=r"broken\.yaml: .*missing field 'version'"):
        _cli.main(["flows", "upload", str(tmp_path), "--manager-address", "m"])

    assert fake_clients.uploaded == []


def test_flows_upload_needs_flow_files_and_a_manager(
    fake_clients: type[_FakeClient], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(_cli.MANAGER_ADDRESS_ENV, raising=False)

    with pytest.raises(SystemExit, match="no flow files"):
        _cli.main(["flows", "upload", str(tmp_path), "--manager-address", "m"])
    with pytest.raises(SystemExit, match="no such directory"):
        _cli.main(["flows", "upload", str(tmp_path / "x"), "--manager-address", "m"])
    with pytest.raises(SystemExit, match=_cli.MANAGER_ADDRESS_ENV):
        _cli.main(["flows", "upload", str(tmp_path)])


class _FakeLoop:
    """A scheduler or worker that records how it was built, and returns at once."""

    built: ClassVar[list[tuple[Any, dict[str, Any]]]] = []
    prepared = 0
    problems: ClassVar[list[str]] = []
    unreachable = 0
    """How many prepare calls find the manager unreachable before one succeeds."""

    def __init__(self, client: Any, **kwargs: Any) -> None:
        type(self).built.append((client, kwargs))

    async def prepare(self) -> None:
        type(self).prepared += 1
        if type(self).prepared <= type(self).unreachable:
            raise ManagerUnavailableError("connection refused")
        if type(self).problems:
            raise HandlerError(type(self).problems)

    def stop(self) -> None:
        return None

    async def run(self) -> None:
        return None


@pytest.fixture
def fake_loops(monkeypatch: pytest.MonkeyPatch) -> type[_FakeLoop]:
    _FakeLoop.built = []
    _FakeLoop.prepared = 0
    _FakeLoop.problems = []
    _FakeLoop.unreachable = 0
    monkeypatch.setattr(_cli, "Scheduler", _FakeLoop)
    monkeypatch.setattr(_cli, "Worker", _FakeLoop)
    return _FakeLoop


def test_scheduler_start_runs_a_scheduler_on_an_http_client(
    fake_clients: type[_FakeClient], fake_loops: type[_FakeLoop]
) -> None:
    exit_code = _cli.main(
        [
            "scheduler",
            "start",
            "--manager-address",
            "manager.test",
            "--poll-timeout",
            "7",
        ]
    )

    assert exit_code == 0
    assert fake_clients.built == [("manager.test", {"poll_timeout": 7.0})]
    ((client, kwargs),) = fake_loops.built
    assert isinstance(client, _FakeClient)
    assert kwargs == {"poll_timeout": 7.0}


def test_worker_start_with_a_code_location_serves_a_queue_of_flows(
    fake_clients: type[_FakeClient],
    fake_loops: type[_FakeLoop],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_cli.MANAGER_ADDRESS_ENV, "manager.internal:8420")

    exit_code = _cli.main(
        [
            "worker",
            "start",
            "--code-location",
            str(_EXAMPLES / "hello"),
            "--queue",
            "voice",
            "--poll-timeout",
            "3",
            "--lease-seconds",
            "9",
        ]
    )

    assert exit_code == 0
    assert fake_clients.built == [("manager.internal:8420", {"poll_timeout": 3.0})]
    ((client, kwargs),) = fake_loops.built
    assert isinstance(client, _FakeClient)
    assert kwargs == {
        "queue": "voice",
        "code_location": _EXAMPLES / "hello",
        "poll_timeout": 3.0,
        "lease_seconds": 9.0,
    }
    assert fake_loops.prepared == 1


def test_a_worker_waits_for_a_manager_it_cannot_reach_yet(
    fake_clients: type[_FakeClient],
    fake_loops: type[_FakeLoop],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Started alongside the manager, or during its restart, it keeps trying."""
    monkeypatch.setattr(_cli, "PREPARE_RETRY_SECONDS", 0.01)
    fake_loops.unreachable = 2

    exit_code = _cli.main(
        [
            "worker",
            "start",
            "--manager-address",
            "m",
            "--code-location",
            str(_EXAMPLES / "hello"),
        ]
    )

    assert exit_code == 0
    assert fake_loops.prepared == 3


def test_a_worker_refused_for_good_exits_rather_than_retrying(
    fake_clients: type[_FakeClient],
    fake_loops: type[_FakeLoop],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refused(self: Any) -> None:
        raise InvalidValueError("'q' is not a queue the manager serves")

    monkeypatch.setattr(_FakeLoop, "prepare", refused)
    command = ["worker", "start", "--manager-address", "m"]
    location = ["--code-location", str(_EXAMPLES / "hello")]

    with pytest.raises(SystemExit, match="cannot start the worker: 'q' is not"):
        _cli.main([*command, *location])
    with pytest.raises(SystemExit, match="'my queue' is not a queue name"):
        _cli.main([*command, *location, "--queue", "my queue"])


def test_a_worker_whose_handlers_do_not_fit_exits_listing_every_problem(
    fake_clients: type[_FakeClient], fake_loops: type[_FakeLoop]
) -> None:
    fake_loops.problems = ["a: cannot import 'tasks'", "b: takes no parameter 'x'"]

    with pytest.raises(SystemExit) as raised:
        _cli.main(
            [
                "worker",
                "start",
                "--manager-address",
                "m",
                "--code-location",
                str(_EXAMPLES / "hello"),
            ]
        )

    message = str(raised.value)
    assert "queue 'default'" in message
    assert "a: cannot import 'tasks'" in message
    assert "b: takes no parameter 'x'" in message


def test_a_worker_needs_its_code_location_and_a_manager(
    fake_clients: type[_FakeClient], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(_cli.MANAGER_ADDRESS_ENV, raising=False)

    with pytest.raises(SystemExit):  # argparse: --code-location is required
        _cli.main(["worker", "start", "--manager-address", "m"])
    with pytest.raises(SystemExit, match=_cli.MANAGER_ADDRESS_ENV):
        _cli.main(["worker", "start", "--code-location", str(tmp_path)])
    with pytest.raises(SystemExit, match="no such directory"):
        _cli.main(
            [
                "worker",
                "start",
                "--manager-address",
                "m",
                "--code-location",
                str(tmp_path / "x"),
            ]
        )


def test_the_parser_covers_every_service() -> None:
    parser = _cli.build_parser()

    upload = parser.parse_args(["flows", "upload", "dir"])
    scheduler = parser.parse_args(["scheduler", "start"])
    worker = parser.parse_args(["worker", "start", "--code-location", "here"])

    assert upload.handler is _cli.flows_upload_command
    assert upload.directory == Path("dir")
    assert scheduler.handler is _cli.scheduler_start_command
    assert worker.queue == "default"
    assert worker.code_location == Path("here")
