# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The command and API tokens: manager start, the token commands, and refusals."""

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
def runs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_run(host: str, port: int, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr("neorc.manager.run", fake_run)
    return calls


def test_the_manager_needs_tokens_unless_told_not_to(
    runs: list[dict[str, Any]], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="neorc"):
        assert _cli.main(["manager", "start"]) == 0
        (tokens_only,) = caplog.records
        caplog.clear()
        assert _cli.main(["manager", "start", "--no-auth"]) == 0

    assert [call["auth"] for call in runs] == [True, False]
    assert [call["auth_config"] for call in runs] == [None, None]
    assert "nobody can sign in to the UI" in tokens_only.message
    (warning,) = caplog.records
    assert "no authentication" in warning.message
    assert "every interface" in warning.message
    assert "DNS rebinding" in warning.message


AUTH_TOML = """
public_url = "https://neorc.example.com"

[providers.google]
title = "Google"
issuer = "https://accounts.google.com/"
client_id = "1234.apps.googleusercontent.com"
client_secret_env = "NEORC_TEST_GOOGLE_SECRET"

[[allow]]
provider = "google"
hosted_domain = "example.com"
"""


def test_sign_in_is_configured_from_a_file_named_on_the_command_line_or_not(
    runs: list[dict[str, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "auth.toml"
    path.write_text(AUTH_TOML)
    monkeypatch.setenv("NEORC_TEST_GOOGLE_SECRET", "shh")

    assert _cli.main(["manager", "start", "--auth-config", str(path)]) == 0
    monkeypatch.setenv(_cli.AUTH_CONFIG_ENV, str(path))
    assert _cli.main(["manager", "start"]) == 0

    for call in runs:
        config = call["auth_config"]
        assert config.public_url == "https://neorc.example.com"
        assert config.providers["google"].client_secret == "shh"
        assert config.providers["google"].issuer == "https://accounts.google.com"


def test_a_sign_in_config_that_cannot_be_used_lists_every_problem(
    runs: list[dict[str, Any]], tmp_path: Path
) -> None:
    path = tmp_path / "auth.toml"
    path.write_text(AUTH_TOML.replace("hosted_domain", "email_domain"))

    with pytest.raises(SystemExit) as refused:
        _cli.main(["manager", "start", "--auth-config", str(path)])
    with pytest.raises(SystemExit, match="cannot read"):
        _cli.main(["manager", "start", "--auth-config", str(tmp_path / "none")])
    with pytest.raises(SystemExit, match="--no-auth asks for no identity"):
        _cli.main(["manager", "start", "--no-auth", "--auth-config", str(path)])

    message = str(refused.value)
    assert "NEORC_TEST_GOOGLE_SECRET is not set" in message
    assert "email_domain is refused for Google" in message
    assert runs == []


def test_signing_in_over_http_on_loopback_is_warned_about(
    runs: list[dict[str, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "auth.toml"
    path.write_text(
        AUTH_TOML.replace("https://neorc.example.com", "http://127.0.0.1:8420")
    )
    monkeypatch.setenv("NEORC_TEST_GOOGLE_SECRET", "shh")

    with caplog.at_level(logging.WARNING, logger="neorc"):
        assert _cli.main(["manager", "start", "--auth-config", str(path)]) == 0

    assert any("every other server" in r.message for r in caplog.records)


@pytest.fixture
def certificate(tmp_path: Path) -> tuple[str, str]:
    """A self-signed certificate and its key, as files, made by openssl."""
    import shutil
    import subprocess

    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("no openssl to make a certificate with")
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            openssl,
            *("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256"),
            *("-nodes", "-days", "1", "-subj", "/CN=localhost"),
            *("-keyout", str(key), "-out", str(cert)),
        ],
        check=True,
        capture_output=True,
    )
    return str(cert), str(key)


def test_the_manager_serves_https_with_a_certificate_and_its_key(
    runs: list[dict[str, Any]], certificate: tuple[str, str]
) -> None:
    cert, key = certificate
    tls = ["--ssl-certfile", cert, "--ssl-keyfile", key]

    assert _cli.main(["manager", "start", *tls]) == 0
    with pytest.raises(SystemExit, match="both --ssl-certfile and --ssl-keyfile"):
        _cli.main(["manager", "start", *tls[:2]])

    assert (runs[0]["ssl_certfile"], runs[0]["ssl_keyfile"]) == (cert, key)
    assert len(runs) == 1


def test_a_certificate_that_cannot_be_used_is_said_to_be_that(
    runs: list[dict[str, Any]], certificate: tuple[str, str], tmp_path: Path
) -> None:
    cert, key = certificate
    garbage = tmp_path / "garbage.pem"
    garbage.write_text("not a certificate")

    for certfile, keyfile in (
        (cert + ".missing", key),
        (str(garbage), key),
        (cert, cert),
    ):
        with pytest.raises(SystemExit) as refused:
            _cli.main(
                [
                    "manager",
                    "start",
                    *("--ssl-certfile", certfile, "--ssl-keyfile", keyfile),
                ]
            )
        message = str(refused.value)
        assert "cannot serve HTTPS" in message
        assert "--no-ui" not in message
    assert runs == []


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

    def fake_run(host: str, port: int, **kwargs: Any) -> None:
        raised = getattr(asyncpg.exceptions, error, None)
        if raised is None:
            raise ConnectionRefusedError("connection refused")
        raise raised("connection refused")

    monkeypatch.setattr("neorc.manager.run", fake_run)

    with pytest.raises(
        SystemExit, match="cannot reach the database: connection refused"
    ):
        _cli.main(["manager", "start"])


def test_a_manager_that_cannot_start_says_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(host: str, port: int, **kwargs: Any) -> None:
        raise RuntimeError("the database has no neorc_api_tokens: ... --create-schema")

    monkeypatch.setattr("neorc.manager.run", fake_run)

    with pytest.raises(SystemExit, match=r"neorc manager start: .*--create-schema"):
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
    assert f"no API token (set ${_cli.API_TOKEN_ENV})" in capsys.readouterr().err
    monkeypatch.setenv(_cli.API_TOKEN_ENV, "neorc_secret")
    monkeypatch.setenv(_cli.ALLOW_INSECURE_HTTP_ENV, "1")
    assert _cli.main([*upload, *address]) == 1
    refused = capsys.readouterr().err

    assert recording.built == [
        {},
        {"token": "neorc_secret", "allow_insecure": True},
    ]
    assert f"the API token in ${_cli.API_TOKEN_ENV}" in refused
    assert "revoked or expired" in refused
    assert "neorc_secret" not in refused
    with pytest.raises(SystemExit):
        _cli.main([*upload, *address, "--token", "neorc_secret"])


def test_a_token_is_not_sent_over_plain_http_by_any_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(_cli.API_TOKEN_ENV, "neorc_secret")
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

    with pytest.raises(SystemExit, match="refused a request with no API token") as e:
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
    monkeypatch.setenv(_cli.API_TOKEN_ENV, "neorc_secret")
    monkeypatch.setenv(_cli.ALLOW_INSECURE_HTTP_ENV, "1")
    upload = ["flows", "upload", str(EXAMPLES / "hello" / "flows")]

    assert _cli.main([*upload, "--manager-address", "m"]) == 1
    refused = capsys.readouterr().err
    assert f"the API token in ${_cli.API_TOKEN_ENV}" in refused
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
    monkeypatch.setenv(_cli.API_TOKEN_ENV, "neorc_secret")
    monkeypatch.setenv(_cli.ALLOW_INSECURE_HTTP_ENV, "1")

    with pytest.raises(SystemExit, match=r"the API token in \$NEORC_API_TOKEN"):
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
    assert "refused a request with no API token" in capsys.readouterr().err


# The token and session commands, on a real database.


def test_the_token_commands_need_a_database() -> None:
    for command in (
        ["tokens", "create", "ci", "--role", "ci"],
        ["tokens", "list"],
        ["tokens", "revoke", "ci"],
        ["sessions", "clear"],
    ):
        with pytest.raises(SystemExit, match=_cli.DATABASE_URL_ENV):
            _cli.main(command)


@pytest.mark.postgres
def test_tokens_are_created_listed_and_revoked(
    pg_schema: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(_cli.DATABASE_URL_ENV, pg_schema)
    assert _cli.main(["queues", "declare", "gpu", "--kind", "worker"]) == 0
    capsys.readouterr()

    worker = ["tokens", "create", "worker-1", "--role", "worker", "--queue", "gpu"]
    assert _cli.main(worker) == 0
    created = capsys.readouterr()
    ci = ["tokens", "create", "ci", "--role", "ci"]
    assert _cli.main([*ci, "--expires-days", "30"]) == 0
    capsys.readouterr()
    with pytest.raises(SystemExit, match="exists"):
        _cli.main(ci)
    assert _cli.main(["tokens", "list"]) == 0
    listed = capsys.readouterr().out
    assert _cli.main(["tokens", "revoke", "ci"]) == 0
    assert _cli.main(["tokens", "revoke", "ci"]) == 1
    capsys.readouterr()
    assert _cli.main(["tokens", "list", "--database-url", pg_schema]) == 0
    after = capsys.readouterr().out

    secret = created.out.strip()
    assert secret.startswith("neorc_") and "\n" not in secret
    assert "worker on queue 'gpu' token 'worker-1'" in created.err
    assert "never" in created.err
    assert secret not in created.err
    lines = listed.splitlines()
    assert [line.split("\t")[:2] for line in lines] == [
        ["ci", "ci"],
        ["worker-1", "worker on queue 'gpu'"],
    ]
    assert "expires 20" in lines[0] and "never expires" in lines[1]
    assert secret not in listed
    assert [line.split("\t")[0] for line in after.splitlines()] == ["worker-1"]


@pytest.mark.postgres
def test_a_created_token_is_one_the_manager_accepts(
    pg_schema: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from neorc.postgres import PostgresCredentialStore
    from neorc_core import Access, Principal, Role

    monkeypatch.setenv(_cli.DATABASE_URL_ENV, pg_schema)
    assert _cli.main(["queues", "declare", "default", "--kind", "worker"]) == 0
    capsys.readouterr()
    created = ["tokens", "create", "worker-1", "--role", "worker", "--queue", "default"]
    assert _cli.main(created) == 0
    secret = capsys.readouterr().out.strip()

    async def authenticate() -> Principal:
        async with PostgresCredentialStore(pg_schema) as credentials:
            return await Access(credentials).authenticate_token(secret)

    principal = asyncio.run(authenticate())
    assert (principal.name, principal.role, principal.queue) == (
        "worker-1",
        Role.WORKER,
        "default",
    )


@pytest.mark.postgres
def test_queues_are_declared_and_listed_on_the_database(
    pg_schema: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(_cli.DATABASE_URL_ENV, pg_schema)

    assert _cli.main(["queues", "list"]) == 0
    nothing = capsys.readouterr().out
    assert _cli.main(["queues", "declare", "editors", "--kind", "external"]) == 0
    declared = capsys.readouterr().out
    assert _cli.main(["queues", "declare", "gpu", "--kind", "worker"]) == 0
    capsys.readouterr()
    assert _cli.main(["queues", "list", "--database-url", pg_schema]) == 0
    listed = capsys.readouterr().out

    assert nothing == ""
    assert declared == "declared queue 'editors' as external\n"
    assert listed == "editors\texternal\ngpu\tworker\n"


@pytest.mark.postgres
def test_a_worker_token_and_an_external_queue_do_not_meet(
    pg_schema: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(_cli.DATABASE_URL_ENV, pg_schema)
    worker = ["tokens", "create", "w", "--role", "worker", "--queue"]
    responder = ["tokens", "create", "r", "--role", "responder", "--queue"]
    external = ["queues", "declare", "--kind", "external"]

    for token in (worker, responder):
        with pytest.raises(SystemExit, match="queue 'default' is not declared"):
            _cli.main([*token, "default"])
    assert _cli.main(["queues", "declare", "default", "--kind", "worker"]) == 0
    assert _cli.main([*external, "editors"]) == 0
    with pytest.raises(SystemExit, match="queue 'editors' is external"):
        _cli.main([*worker, "editors"])
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

    assert "responder on queue 'editors' token 'r'" in created
    assert queues == "default\texternal\neditors\texternal\n"
    assert [line.split("\t")[:2] for line in tokens.splitlines()] == [
        ["r", "responder on queue 'editors'"]
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
        (["--role", "worker"], "cannot create the token: a worker token is bound"),
        (["--role", "worker", "--queue", "a.b"], "'a.b' is not a queue name"),
        (["--role", "responder"], "a responder token is bound to a queue"),
        (
            ["--role", "ci", "--queue", "default"],
            "only a worker or responder token is bound",
        ),
    ],
)
def test_a_token_with_a_role_it_cannot_have_is_not_created(
    pg_schema: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    problem: str,
) -> None:
    monkeypatch.setenv(_cli.DATABASE_URL_ENV, pg_schema)

    with pytest.raises(SystemExit, match=problem):
        _cli.main(["tokens", "create", "t", *arguments])
    assert _cli.main(["tokens", "list"]) == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "arguments", [[], ["--role", "admin"]], ids=["no-role", "unknown-role"]
)
def test_a_token_needs_one_of_the_roles(
    capsys: pytest.CaptureFixture[str], arguments: list[str]
) -> None:
    with pytest.raises(SystemExit) as exited:
        _cli.main(["tokens", "create", "t", *arguments])

    assert exited.value.code == 2
    assert "--role" in capsys.readouterr().err


@pytest.mark.postgres
def test_sessions_are_cleared(
    pg_schema: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from neorc.postgres import PostgresCredentialStore
    from neorc_core import Access, Allow, Identity, Matcher

    async def sign_in() -> None:
        async with PostgresCredentialStore(pg_schema) as credentials:
            access = Access(credentials, allow=[Allow("google", Matcher.EVERYONE)])
            await access.open_session(Identity("google", "1"))
            await access.open_session(Identity("google", "2"))

    asyncio.run(sign_in())

    assert _cli.main(["sessions", "clear", "--database-url", pg_schema]) == 0
    assert capsys.readouterr().out == "ended 2 sessions\n"


@pytest.mark.postgres
def test_the_token_commands_create_no_tables(
    pg_schema: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from neorc.postgres._driver import connected
    from neorc.postgres._schema import TOKENS_TABLE

    async def drop() -> None:
        async with connected(pg_schema) as conn:
            await conn.execute(f"DROP TABLE {TOKENS_TABLE}")

    asyncio.run(drop())
    monkeypatch.setenv(_cli.DATABASE_URL_ENV, pg_schema)

    with pytest.raises(SystemExit, match="neorc manager start --create-schema"):
        _cli.main(["tokens", "create", "ci", "--role", "ci"])


@pytest.mark.postgres
def test_a_database_that_does_not_exist_is_said_in_a_line(database_url: str) -> None:
    """The server refuses the connection; the command says so, not a traceback."""
    base, _, query = database_url.partition("?")
    missing = f"{base.rsplit('/', 1)[0]}/neorc_no_such_database"
    if query:
        missing += f"?{query}"

    with pytest.raises(SystemExit, match="cannot reach the database"):
        _cli.main(["tokens", "list", "--database-url", missing])
