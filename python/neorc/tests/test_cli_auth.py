# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The command and tokens: manager start, oneberth's token scripts, and refusals.

The token commands are oneberth's admin scripts, shown under ``neorc`` with
neorc's roles; here, what neorc adds to them: its roles, the check of a token's
queue, and the database it finds them on.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
import uuid
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest

from neorc import _cli
from neorc_core import AuthenticationError, PermissionDeniedError, Task, TaskDelivery
from neorc_core.flows import Address, TaskStep, parse_flow
from neorc_core.ports._clients import DEFAULT_LEASE_SECONDS

EXAMPLES = Path(__file__).parents[3] / "examples"
REFUSED = AuthenticationError("the API token is unknown, revoked or expired")


@pytest.fixture(autouse=True)
def no_credentials_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        _cli.API_TOKEN_ENV,
        _cli.ALLOW_INSECURE_HTTP_ENV,
        _cli.MANAGER_ADDRESS_ENV,
        _cli.DATABASE_URL_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


# manager start.


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "oneberth.toml"
    path.write_text('[apps.neorc]\npublic_url = "http://127.0.0.1:8420"\n')
    monkeypatch.setenv(_cli.CONFIG_ENV, str(path))
    return path


@pytest.mark.usefixtures("config")
def test_signing_in_over_http_on_loopback_is_warned_about(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("neorc.manager.build_app", lambda *a, **k: None)
    monkeypatch.setattr("neorc.manager.run", lambda *a, **k: None)

    with caplog.at_level(logging.WARNING, logger="neorc"):
        assert _cli.main(["manager", "start"]) == 0

    assert any("every other server" in r.message for r in caplog.records)


@pytest.mark.usefixtures("config")
@pytest.mark.parametrize(
    "error",
    [
        "ConnectionRefusedError",
        "InvalidPasswordError",
        "InvalidCatalogNameError",
        "CannotConnectNowError",
        "TooManyConnectionsError",
    ],
)
def test_a_database_that_cannot_be_reached_is_said_in_a_line(
    monkeypatch: pytest.MonkeyPatch, error: str
) -> None:
    import asyncpg.exceptions  # type: ignore[import-untyped]

    def build_app(*args: Any, **kwargs: Any) -> None:
        raised = getattr(asyncpg.exceptions, error, None)
        if raised is None:
            raise ConnectionRefusedError("connection refused")
        raise raised("connection refused")

    monkeypatch.setattr("neorc.manager.build_app", build_app)

    with pytest.raises(
        SystemExit, match="cannot reach the database: connection refused"
    ):
        _cli.main(["manager", "start"])


@pytest.mark.usefixtures("config")
def test_a_manager_that_cannot_start_says_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def build_app(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("no database to serve: pass one or set NEORC_DATABASE_URL")

    monkeypatch.setattr("neorc.manager.build_app", build_app)

    with pytest.raises(SystemExit, match=r"neorc manager start: no database"):
        _cli.main(["manager", "start"])


# The token, sent by the commands that reach a manager.


class _Recording:
    built: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, address: str, **kwargs: Any) -> None:
        type(self).built.append(kwargs)

    async def __aenter__(self) -> _Recording:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def upload_flows(self, contents: Any) -> list[bool]:
        raise REFUSED


@pytest.fixture
def recording(monkeypatch: pytest.MonkeyPatch) -> type[_Recording]:
    _Recording.built = []
    monkeypatch.setattr("neorc.http.HttpManagerClient", _Recording)
    monkeypatch.setattr("neorc.http.HttpQueueClient", _Recording)
    return _Recording


def test_the_token_comes_from_the_environment_only(
    recording: type[_Recording],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    upload = ["flows", "upload", str(EXAMPLES / "hello" / "flows")]
    address = ["--manager-address", "m"]

    assert _cli.main([*upload, *address]) == 1
    assert f"no token (set ${_cli.API_TOKEN_ENV})" in capsys.readouterr().err
    monkeypatch.setenv(_cli.API_TOKEN_ENV, "ob_svc_secret")
    monkeypatch.setenv(_cli.ALLOW_INSECURE_HTTP_ENV, "1")
    assert _cli.main([*upload, *address]) == 1
    refused = capsys.readouterr().err

    assert recording.built == [
        {},
        {"token": "ob_svc_secret", "allow_insecure": True},
    ]
    assert f"the token in ${_cli.API_TOKEN_ENV}" in refused
    assert "revoked or expired" in refused
    assert "ob_svc_secret" not in refused
    with pytest.raises(SystemExit):
        _cli.main([*upload, *address, "--token", "ob_svc_secret"])


def test_a_token_is_not_sent_over_plain_http_by_any_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(_cli.API_TOKEN_ENV, "ob_svc_secret")
    address = ["--manager-address", "manager.internal:8420"]
    commands = [
        ["flows", "upload", str(EXAMPLES / "hello" / "flows")],
        ["scheduler", "start"],
        ["worker", "start", "--code-location", str(tmp_path)],
    ]

    for command in commands:
        with pytest.raises(SystemExit, match=r"https://.*INSECURE_HTTP"):
            _cli.main([*command, *address])


NOT_PERMITTED = PermissionDeniedError("a worker token may not events:read")


@pytest.mark.parametrize(
    ("refusal", "reason"),
    [(REFUSED, "revoked or expired"), (NOT_PERMITTED, "may not events:read")],
    ids=["unknown", "no-permission"],
)
def test_a_refused_scheduler_exits_saying_so(
    recording: type[_Recording],
    monkeypatch: pytest.MonkeyPatch,
    refusal: Exception,
    reason: str,
) -> None:
    class Refused:
        def __init__(self, client: Any, **kwargs: Any) -> None:
            pass

        def stop(self) -> None:
            pass

        async def run(self) -> None:
            raise refusal

    monkeypatch.setattr(_cli, "Scheduler", Refused)

    with pytest.raises(SystemExit, match="refused a request with no token") as e:
        _cli.main(["scheduler", "start", "--manager-address", "m"])
    assert reason in str(e.value)


def test_an_upload_the_token_may_not_make_exits_saying_why(
    recording: type[_Recording],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def not_permitted(self: _Recording, contents: Any) -> list[bool]:
        raise PermissionDeniedError("a worker token may not flows:upload")

    monkeypatch.setattr(_Recording, "upload_flows", not_permitted)
    monkeypatch.setenv(_cli.API_TOKEN_ENV, "ob_svc_secret")
    monkeypatch.setenv(_cli.ALLOW_INSECURE_HTTP_ENV, "1")
    upload = ["flows", "upload", str(EXAMPLES / "hello" / "flows")]

    assert _cli.main([*upload, "--manager-address", "m"]) == 1
    refused = capsys.readouterr().err
    assert f"the token in ${_cli.API_TOKEN_ENV}" in refused
    assert "a worker token may not flows:upload" in refused


@pytest.mark.parametrize(
    "refusal", [REFUSED, NOT_PERMITTED], ids=["unknown", "no-permission"]
)
def test_a_worker_refused_at_startup_exits_rather_than_retrying(
    recording: type[_Recording], monkeypatch: pytest.MonkeyPatch, refusal: Exception
) -> None:
    class Refused:
        def __init__(self, client: Any, **kwargs: Any) -> None:
            pass

        def stop(self) -> None:
            pass

        async def prepare(self) -> None:
            raise refusal

    monkeypatch.setattr(_cli, "Worker", Refused)
    monkeypatch.setenv(_cli.API_TOKEN_ENV, "ob_svc_secret")
    monkeypatch.setenv(_cli.ALLOW_INSECURE_HTTP_ENV, "1")

    with pytest.raises(SystemExit, match=r"the token in \$NEORC_API_TOKEN"):
        _cli.main(
            [
                "worker",
                "start",
                "--manager-address",
                "m",
                "--code-location",
                str(EXAMPLES / "hello"),
            ]
        )


@pytest.fixture
def own_module(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(sys, "path", list(sys.path))
    yield


@pytest.mark.usefixtures("own_module")
def test_a_worker_whose_heartbeat_is_refused_exits_with_its_handler_still_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Its lease no longer beats: the process ends before the task runs twice."""
    module = f"stuck_{uuid.uuid4().hex}"
    done = tmp_path / "done"
    (tmp_path / f"{module}.py").write_text(
        "import pathlib, time\n\n"
        "def work():\n"
        "    time.sleep(1.5)\n"
        f"    pathlib.Path({str(done)!r}).touch()\n"
    )
    step = parse_flow(
        {
            "name": "f",
            "version": "1.0.0",
            "steps": {"work": {"handler": f"{module}:work"}},
        }
    ).tasks()
    run_id = uuid.uuid4()

    class Client:
        """A manager that hands out one task, then refuses every heartbeat."""

        handed = False

        def __init__(self, address: str, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def task_definitions(self, queue: str) -> list[TaskStep]:
            return list(step)

        async def receive_task(
            self, queue: str, **kwargs: float
        ) -> TaskDelivery | None:
            if type(self).handed:
                await asyncio.sleep(kwargs["timeout"])
                return None
            type(self).handed = True
            task = Task(
                id=uuid.uuid4(),
                run_id=run_id,
                address=Address("work"),
                queue="default",
                handler=f"{module}:work",
                attempts=1,
            )
            return TaskDelivery(task, {})

        async def claim_task(self, task_id: uuid.UUID) -> None:
            return None

        async def extend_lease(
            self, task_id: uuid.UUID, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
        ) -> datetime:
            raise REFUSED

        async def report_finished(self, task_id: uuid.UUID, **kwargs: Any) -> None:
            raise AssertionError("a refused worker reports nothing")

    monkeypatch.setattr("neorc.http.HttpQueueClient", Client)
    busy_at_exit: list[bool] = []

    def exit_now(code: int) -> None:
        busy_at_exit.append(not done.exists())

    monkeypatch.setattr(_cli, "_exit_now", exit_now)
    started = time.monotonic()

    exit_code = _cli.main(
        [
            "worker",
            "start",
            "--manager-address",
            "m",
            "--code-location",
            str(tmp_path),
            "--lease-seconds",
            "0.3",
            "--poll-timeout",
            "0.1",
        ]
    )

    assert exit_code == 1
    assert busy_at_exit == [True]
    assert time.monotonic() - started < 5
    assert "refused a request with no token" in capsys.readouterr().err


# oneberth's token and session scripts, under neorc, on a real database.


def test_the_token_commands_need_a_database(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for command in (
        ["tokens", "create", "ci", "--role", "neorc/ci"],
        ["tokens", "list"],
        ["tokens", "revoke", "ci"],
        ["sessions", "clear"],
        ["devices", "list"],
    ):
        assert _cli.main(command) == 2
        assert _cli.DATABASE_URL_ENV in capsys.readouterr().err


@pytest.fixture
def on_database(pg_schema: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """The database, with oneberth's store set up as ``neorc store init`` does."""
    monkeypatch.setenv(_cli.DATABASE_URL_ENV, pg_schema)
    assert _cli.main(["store", "init"]) == 0
    return pg_schema


@pytest.mark.postgres
def test_tokens_are_created_listed_and_revoked(
    on_database: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _cli.main(["queues", "declare", "gpu", "--kind", "worker"]) == 0
    capsys.readouterr()

    worker = ["tokens", "create", "worker-1", "--role", "neorc/worker"]
    assert _cli.main([*worker, "--resource", "gpu"]) == 0
    created = capsys.readouterr()
    ci = ["tokens", "create", "ci", "--role", "neorc/ci"]
    assert _cli.main([*ci, "--expires-days", "30"]) == 0
    capsys.readouterr()
    assert _cli.main(ci) == 1
    assert "exists" in capsys.readouterr().err
    assert _cli.main(["tokens", "list"]) == 0
    listed = capsys.readouterr().out
    assert _cli.main(["tokens", "revoke", "ci"]) == 0
    assert _cli.main(["tokens", "revoke", "ci"]) == 1
    capsys.readouterr()
    assert _cli.main(["tokens", "list", "--database-url", on_database]) == 0
    after = capsys.readouterr().out

    secret = created.out.strip()
    assert secret.startswith("ob_svc_")
    assert "\n" not in secret
    assert "'worker-1', neorc/worker on gpu" in created.err
    assert "never" in created.err
    assert secret not in created.err
    lines = sorted(listed.splitlines())
    assert [line.split("\t")[:2] for line in lines] == [
        ["ci", "neorc/ci"],
        ["worker-1", "neorc/worker on gpu"],
    ]
    assert "expires 20" in lines[0] and "never expires" in lines[1]
    assert secret not in listed
    assert [line.split("\t")[0] for line in after.splitlines()] == ["worker-1"]


@pytest.mark.postgres
def test_a_created_token_is_one_the_manager_accepts(
    on_database: str, capsys: pytest.CaptureFixture[str]
) -> None:
    from oneberth.identity import Principal
    from oneberth.stores import opened_store

    from neorc._roles import registry

    assert _cli.main(["queues", "declare", "default", "--kind", "worker"]) == 0
    capsys.readouterr()
    created = ["tokens", "create", "worker-1", "--role", "neorc/worker"]
    assert _cli.main([*created, "--resource", "default"]) == 0
    secret = capsys.readouterr().out.strip()

    async def authenticate() -> tuple[Principal, bool, bool]:
        from oneberth.identity import Access

        async with opened_store(database_url=on_database) as store:
            access = Access(store, registry())
            principal = await access.authenticate(secret, bearer=True)
            return (
                principal,
                access.allows(principal, "neorc/queue:receive", "default"),
                access.allows(principal, "neorc/queue:receive", "gpu"),
            )

    principal, on_default, on_gpu = asyncio.run(authenticate())
    assert principal.name == "worker-1"
    assert principal.roles == {"neorc/worker"}
    assert (on_default, on_gpu) == (True, False)


@pytest.mark.postgres
def test_queues_are_declared_and_listed_on_the_database(
    on_database: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _cli.main(["queues", "list"]) == 0
    nothing = capsys.readouterr().out
    assert _cli.main(["queues", "declare", "editors", "--kind", "external"]) == 0
    declared = capsys.readouterr().out
    assert _cli.main(["queues", "declare", "gpu", "--kind", "worker"]) == 0
    capsys.readouterr()
    assert _cli.main(["queues", "list", "--database-url", on_database]) == 0
    listed = capsys.readouterr().out

    assert nothing == ""
    assert declared == "declared queue 'editors' as external\n"
    assert listed == "editors\texternal\ngpu\tworker\n"


@pytest.mark.postgres
def test_a_worker_token_and_an_external_queue_do_not_meet(
    on_database: str, capsys: pytest.CaptureFixture[str]
) -> None:
    worker = ["tokens", "create", "w", "--role", "neorc/worker", "--resource"]
    responder = ["tokens", "create", "r", "--role", "neorc/responder", "--resource"]
    external = ["queues", "declare", "--kind", "external"]

    for token in (worker, responder):
        assert _cli.main([*token, "default"]) == 1
        assert "queue 'default' is not declared" in capsys.readouterr().err
    assert _cli.main(["queues", "declare", "default", "--kind", "worker"]) == 0
    assert _cli.main([*external, "editors"]) == 0
    capsys.readouterr()
    assert _cli.main([*worker, "editors"]) == 1
    assert "queue 'editors' is external" in capsys.readouterr().err
    assert _cli.main([*responder, "editors"]) == 0
    created = capsys.readouterr().err
    assert _cli.main([*worker, "default"]) == 0
    with pytest.raises(
        SystemExit, match=r"cannot declare the queue: .* revoke w first"
    ):
        _cli.main([*external, "default"])
    with pytest.raises(SystemExit, match=r"'a\.b' is not a queue name"):
        _cli.main([*external, "a.b"])
    capsys.readouterr()
    assert _cli.main(["tokens", "revoke", "w"]) == 0
    assert _cli.main([*external, "default"]) == 0
    capsys.readouterr()
    assert _cli.main(["queues", "list"]) == 0
    queues = capsys.readouterr().out
    assert _cli.main(["tokens", "list"]) == 0
    tokens = capsys.readouterr().out

    assert "'r', neorc/responder on editors" in created
    assert queues == "default\texternal\neditors\texternal\n"
    assert [line.split("\t")[:2] for line in tokens.splitlines()] == [
        ["r", "neorc/responder on editors"]
    ]


def test_a_queue_kind_is_one_of_the_two(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        _cli.main(["queues", "declare", "editors", "--kind", "manual"])
    assert "invalid choice: 'manual'" in capsys.readouterr().err
    with pytest.raises(SystemExit, match="no database"):
        _cli.main(["queues", "declare", "editors", "--kind", "external"])


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("arguments", "problem"),
    [
        (["--role", "neorc/worker"], "role 'neorc/worker' needs a queue"),
        (["--role", "neorc/worker", "--resource", "a/b"], "'a/b' is not a queue name"),
        (["--role", "neorc/responder"], "role 'neorc/responder' needs a queue"),
        (
            ["--role", "neorc/ci", "--resource", "default"],
            "role 'neorc/ci' holds for every resource",
        ),
        (["--role", "admin"], "'admin' is not a registered role"),
    ],
)
def test_a_token_with_a_role_it_cannot_have_is_not_created(
    on_database: str,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    problem: str,
) -> None:
    assert _cli.main(["tokens", "create", "t", *arguments]) == 1
    assert problem in capsys.readouterr().err
    assert _cli.main(["tokens", "list"]) == 0
    assert capsys.readouterr().out == ""


def test_a_token_needs_a_role(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        _cli.main(["tokens", "create", "t"])

    assert exited.value.code == 2
    assert "--role" in capsys.readouterr().err


@pytest.mark.postgres
def test_sessions_are_cleared(
    on_database: str, capsys: pytest.CaptureFixture[str]
) -> None:
    from oneberth.identity import Access, Identity, Match, Matcher, Policy
    from oneberth.stores import opened_store

    from neorc._roles import registry

    async def sign_in() -> None:
        async with opened_store(database_url=on_database) as store:
            policy = Policy(allow=(Match("google", Matcher.EVERYONE),))
            access = Access(store, registry(), policy)
            await access.sign_in(Identity("google", "1"))
            await access.sign_in(Identity("google", "2"))

    asyncio.run(sign_in())

    assert _cli.main(["sessions", "clear", "--database-url", on_database]) == 0
    assert capsys.readouterr().out == "ended 2 sessions\n"


@pytest.mark.postgres
def test_the_token_commands_create_no_tables(
    pg_schema: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """oneberth's store is not set up: ``neorc store init`` or a start sets it up."""
    monkeypatch.setenv(_cli.DATABASE_URL_ENV, pg_schema)

    assert _cli.main(["tokens", "create", "ci", "--role", "neorc/ci"]) == 2
    assert "neorc store init" in capsys.readouterr().err


@pytest.mark.postgres
def test_a_database_that_does_not_exist_is_said_in_a_line(
    database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """The server refuses the connection; the command says so, not a traceback."""
    base, _, query = database_url.partition("?")
    missing = f"{base.rsplit('/', 1)[0]}/neorc_no_such_database"
    if query:
        missing += f"?{query}"

    assert _cli.main(["tokens", "list", "--database-url", missing]) == 2
    assert "cannot reach the database" in capsys.readouterr().err
