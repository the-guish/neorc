# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Standalone neorc in oneberth's local mode: one person, at this machine.

The local mode is for development and for a desktop. It needs no sign-in
configuration: the person is signed in with a ticket, and the processes that
reach the manager send a service token, made with ``neorc tokens create``.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
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


def test_a_start_by_hand_prints_the_address_that_signs_the_person_in(
    tmp_path: Path,
) -> None:
    """The command, in a process of its own, as a person starts it."""
    port = free_port()
    command = Path(sys.executable).with_name("neorc")
    started = subprocess.Popen(
        [
            str(command),
            *("manager", "start", "--local", "--port", str(port), "--no-ui"),
            *("--database-file", str(tmp_path / "neorc.db")),
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
        url = lines[-1].removeprefix("open ")
        assert url.startswith(f"http://127.0.0.1:{port}/auth/local?ticket=")

        with httpx.Client(follow_redirects=True, timeout=10) as browser:
            page = browser.get(url)
            flows = browser.get(f"http://127.0.0.1:{port}/neorc/flows")
            again = browser.get(url)
    finally:
        started.send_signal(signal.SIGTERM)
        started.wait(timeout=30)

    assert page.url.path == "/ui/"
    assert flows.json() == {"flows": []}
    assert again.history[0].status_code == 303  # a ticket works once
    assert started.returncode == 0
    assert (tmp_path / "oneberth.db").is_file()
