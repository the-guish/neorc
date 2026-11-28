# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Standalone neorc in oneberth's local mode: one person, at this machine.

The local mode is for development and for a desktop. It needs no sign-in
configuration: the person is signed in with a ticket, and the processes that
reach the manager send a service token, made with ``neorc tokens create``.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import os
import signal
import socket
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from conftest import free_port
from oneberth.config import local_config
from oneberth.web.testing import running, signed_in

from neorc import _cli
from neorc.manager import build_app


async def test_the_person_at_the_machine_and_a_worker_reach_the_manager(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "neorc.db"
    app = build_app(
        database_file=path,
        config=local_config("neorc", port=8000),
        ui=False,
        local=True,
    )

    async with running(app) as client:
        anonymous = await client.get("/neorc/flows")
        await signed_in(app, client)
        session = (await client.get("/auth/session")).json()
        flows = await client.get("/neorc/flows")
        on_file = ["--database-file", str(path)]
        # The commands, beside the running backend, as a person types them.
        declare = ["queues", "declare", "default", "--kind", "worker", *on_file]
        assert await asyncio.to_thread(_cli.main, declare) == 0
        capsys.readouterr()
        create = ["tokens", "create", "w", "--role", "neorc/worker"]
        create += ["--resource", "default", *on_file]
        assert await asyncio.to_thread(_cli.main, create) == 0
        worker = capsys.readouterr().out.strip()
        client.cookies.clear()
        definitions = await client.get(
            "/neorc/queues/default/tasks",
            headers={"authorization": f"Bearer {worker}"},
        )

    assert anonymous.status_code == 401
    assert session["mode"] == "local"
    assert session["principal"]["kind"] == "user"
    assert "neorc/user" in session["roles"]
    assert flows.json() == {"flows": []}
    assert definitions.json() == {"tasks": []}


async def test_the_local_mode_answers_another_host_by_its_ip_address_alone(
    tmp_path: Path,
) -> None:
    """What a host on the manager's private network sends as ``Host``. oneberth's
    local mode refuses a name, which a page on the internet could point here."""
    app = build_app(
        database_file=tmp_path / "neorc.db",
        config=local_config("neorc", port=8000),
        ui=False,
        local=True,
    )

    async with running(app) as client:
        by_address = await client.get("/health", headers={"host": "172.18.0.5:8000"})
        by_name = await client.get("/health", headers={"host": "manager:8000"})

    assert by_address.status_code == 200
    assert by_name.status_code == 403
    assert "localhost or an IP address" in by_name.json()["detail"]


@contextlib.contextmanager
def _started_by_hand(
    folder: Path, port: int, *options: str
) -> Iterator[tuple[str, subprocess.Popen[str]]]:
    """``neorc manager start --local``, in a process of its own, as a person
    starts it: the address it prints once it serves, and the process."""
    command = Path(sys.executable).with_name("neorc")
    started = subprocess.Popen(
        [
            str(command),
            *("manager", "start", "--local", "--port", str(port), "--no-ui"),
            *("--database-file", str(folder / "neorc.db")),
            *options,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env={**os.environ, "ONEBERTH_CONFIG": ""},
    )
    try:
        assert started.stdout is not None
        lines = []
        for line in started.stdout:
            lines.append(line.strip())
            if line.startswith("open "):
                break
        assert lines[0] == "ready", lines
        yield lines[-1].removeprefix("open "), started
    finally:
        started.send_signal(signal.SIGTERM)
        started.wait(timeout=30)


@pytest.mark.parametrize("options", [(), ("--host", "0.0.0.0")])
def test_a_start_by_hand_prints_the_address_that_signs_the_person_in(
    tmp_path: Path, options: tuple[str, ...]
) -> None:
    """Wherever the manager listens, the person opens its loopback address."""
    port = free_port()

    with (
        _started_by_hand(tmp_path, port, *options) as (url, started),
        httpx.Client(follow_redirects=True, timeout=10) as browser,
    ):
        page = browser.get(url)
        flows = browser.get(f"http://127.0.0.1:{port}/neorc/flows")
        again = browser.get(url)

    assert url.startswith(f"http://127.0.0.1:{port}/auth/local?ticket=")
    assert page.url.path == "/ui/"
    assert flows.json() == {"flows": []}
    assert again.history[0].status_code == 303  # a ticket works once
    assert started.returncode == 0
    assert (tmp_path / "oneberth.db").is_file()


def _address_beyond_loopback() -> str | None:
    """An address of this machine that another host reaches, if it has one."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))  # picks a route, and sends nothing
        except OSError:
            return None
        address = str(probe.getsockname()[0])
    return None if ipaddress.ip_address(address).is_loopback else address


def test_another_host_reaches_the_local_mode_only_when_it_listens_there(
    tmp_path: Path,
) -> None:
    """``--host`` opens the local mode to a private network, such as a
    container's. Without it, the manager listens on this machine alone."""
    address = _address_beyond_loopback()
    if address is None:
        pytest.skip("this machine has no address beyond loopback")
    port = free_port()
    health = f"http://{address}:{port}/health"

    # The machine's own address goes through no proxy of the environment.
    with httpx.Client(timeout=10, trust_env=False) as other_host:
        with (
            _started_by_hand(tmp_path, port),
            pytest.raises(httpx.ConnectError),
        ):
            other_host.get(health)
        with _started_by_hand(tmp_path, port, "--host", "0.0.0.0") as (url, _):
            reached = other_host.get(health)

    assert reached.status_code == 200
    assert url.startswith(f"http://127.0.0.1:{port}/auth/local?ticket=")
