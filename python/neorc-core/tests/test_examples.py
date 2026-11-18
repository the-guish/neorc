# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The examples under examples/, run in memory on ``LocalCluster``.

The scenarios live in ``neorc_core.testing.examples``; the deployed tests in
``neorc`` run the same ones on Postgres and HTTP.
"""

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from neorc_core import RunStatus
from neorc_core.local import LocalCluster, run_local
from neorc_core.testing import examples

EXAMPLES = Path(__file__).parents[3] / "examples"


@pytest.fixture(autouse=True)
def own_tasks_module(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Each example has a ``tasks`` module: import this one's, not another's."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "tasks", raising=False)
    yield
    sys.modules.pop("tasks", None)


@pytest.mark.parametrize("flow", ["a", "b"])
async def test_hello_runs_each_single_task_flow(
    flow: str, capfd: pytest.CaptureFixture[str]
) -> None:
    async with LocalCluster(code_location=EXAMPLES / "hello") as cluster:
        await cluster.upload(EXAMPLES / "hello" / "flows")
        await cluster.start()
        await examples.hello(cluster.client, EXAMPLES, flow)

    assert capfd.readouterr().out == f"{flow}\n"


async def test_word_picker_keeps_the_words_with_the_preferred_letter() -> None:
    async with LocalCluster(code_location=EXAMPLES / "wordplay") as cluster:
        await cluster.upload(EXAMPLES / "wordplay" / "flows")
        await cluster.start()
        await examples.word_picker(cluster.client, EXAMPLES)


async def test_word_picker_rounds_pads_each_word_to_eight_characters() -> None:
    async with LocalCluster(code_location=EXAMPLES / "wordplay") as cluster:
        await cluster.upload(EXAMPLES / "wordplay" / "flows")
        await cluster.start()
        await examples.word_picker_rounds(cluster.client, EXAMPLES)


async def test_vip_booking_sends_a_gift_for_the_vip_booking_only() -> None:
    async with LocalCluster(code_location=EXAMPLES / "vip_booking") as cluster:
        await cluster.upload(EXAMPLES / "vip_booking" / "flows")
        await cluster.start()
        await examples.vip_booking(cluster.client, EXAMPLES)


async def test_run_local_runs_an_example_to_its_end() -> None:
    """What ``neorc run`` does, on the same flows."""
    run = await run_local(
        EXAMPLES / "wordplay" / "flows",
        "word_picker",
        examples.WORDPLAY_INPUTS,
        timeout=examples.DEFAULT_TIMEOUT,
    )

    assert run.status is RunStatus.SUCCEEDED
    assert run.output == ["potato", "tomate"]
