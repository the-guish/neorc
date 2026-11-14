# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""A flow with a form, uploaded and run: in memory, as every flow is first."""

import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from neorc_core import (
    FlowDefinitionError,
    InvalidValueError,
    Manager,
    QueueKind,
    RunStatus,
)
from neorc_core.flows import read_flows
from neorc_core.local import LocalCluster, MemoryStore, MemoryTaskNotifier, run_local
from neorc_core.testing.examples import wait_for_run

EXAMPLE = Path(__file__).parents[4] / "examples" / "code_task"
GIVEN: dict[str, Any] = {
    "description": "Add a retry to the upload step.",
    "repo": "acme/billing",
    "model": "medium",
    "attempts": 2,
    "draft": True,
}
SAID = (
    "a draft pull request on acme/billing, written by the medium model in at "
    "most 2 attempts: Add a retry to the upload step."
)


@pytest.fixture
def manager() -> Manager:
    return Manager(
        MemoryStore(queues=dict.fromkeys(("default",), QueueKind.WORKER)),
        tasks=MemoryTaskNotifier(),
        events=MemoryTaskNotifier(),
    )


@pytest.fixture(autouse=True)
def own_tasks_module(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Each example has a ``tasks`` module: import this one's, not another's."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "tasks", raising=False)
    yield
    sys.modules.pop("tasks", None)


async def test_a_flow_is_stored_with_its_form(manager: Manager) -> None:
    contents = read_flows(EXAMPLE / "flows")

    assert await manager.upload_flows(contents) == [True]
    assert await manager.upload_flows(contents) == [False]
    stored = await manager.get_flow("code_task")
    assert isinstance(stored.content, dict)
    assert stored.content["form"] == contents[0]["form"]


async def test_a_form_outside_the_subset_is_not_stored(manager: Manager) -> None:
    (content,) = read_flows(EXAMPLE / "flows")
    content["form"][0]["createSurface"]["theme"] = {"primaryColor": "#f00"}

    with pytest.raises(FlowDefinitionError, match="'theme' is not in the subset"):
        await manager.upload_flows([content])


async def test_a_run_is_started_with_inputs_the_form_allows(manager: Manager) -> None:
    await manager.upload_flows(read_flows(EXAMPLE / "flows"))

    run = await manager.start_run("code_task", GIVEN)

    assert run.inputs == GIVEN


async def test_a_run_is_refused_inputs_the_form_does_not_allow(
    manager: Manager,
) -> None:
    await manager.upload_flows(read_flows(EXAMPLE / "flows"))

    with pytest.raises(InvalidValueError, match="input 'repo' is not one of"):
        await manager.start_run("code_task", {**GIVEN, "repo": "acme/unknown"})

    assert await manager.list_runs() == []


async def test_the_declared_types_are_checked_before_the_form(
    manager: Manager,
) -> None:
    await manager.upload_flows(read_flows(EXAMPLE / "flows"))

    with pytest.raises(InvalidValueError, match="input 'attempts' is not a number"):
        await manager.start_run("code_task", {**GIVEN, "attempts": "2"})


async def test_the_example_runs_to_its_end() -> None:
    async with LocalCluster(code_location=EXAMPLE) as cluster:
        await cluster.upload(EXAMPLE / "flows")
        await cluster.start()
        run = await cluster.client.start_run("code_task", GIVEN)
        run = await wait_for_run(cluster.client, run.id)

    assert run.status is RunStatus.SUCCEEDED
    assert run.output == SAID


async def test_run_local_runs_the_example_as_neorc_run_does() -> None:
    run = await run_local(EXAMPLE / "flows", "code_task", GIVEN)

    assert run.output == SAID


async def test_a_flow_calling_this_one_is_held_to_the_form_too(tmp_path: Path) -> None:
    flows = Path(shutil.copytree(EXAMPLE / "flows", tmp_path / "flows"))
    (flows / "caller.yaml").write_text(
        """\
name: caller
version: 1.0.0
steps:
  ask:
    flow: code_task
    fixed_params:
      description: Add a retry to the upload step.
      repo: acme/unknown
      model: medium
      attempts: 2
      draft: true
"""
    )

    async with LocalCluster(code_location=EXAMPLE) as cluster:
        await cluster.upload(flows)
        await cluster.start()
        run = await cluster.client.start_run("caller", {})
        run = await wait_for_run(cluster.client, run.id)

    assert run.status is RunStatus.FAILED
    assert run.reason is not None
    assert "input 'repo' is not one of" in run.reason
