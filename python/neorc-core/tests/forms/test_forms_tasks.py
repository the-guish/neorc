# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""A task's form: what it may hold, and what it asks of the task's result.

A task of an external queue may name a form, as a flow does. Its controls are
the fields of the task's result: docs/architecture/forms.md.
"""

import asyncio
import copy
import json
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
    TaskStatus,
)
from neorc_core._values import JsonValue
from neorc_core.flows import (
    Address,
    load_flow_file,
    parse_flow,
    read_flow_json,
    read_flows,
)
from neorc_core.forms import (
    check_form_result,
    form_fields,
    form_problems,
    inline_form,
    step_forms,
)
from neorc_core.local import LocalCluster, MemoryStore, MemoryTaskNotifier
from neorc_core.testing.examples import wait_for_run

EXAMPLE = Path(__file__).parents[4] / "examples" / "approval"
QUEUES = {"default": QueueKind.WORKER, "editors": QueueKind.EXTERNAL}


def review_form() -> list[Any]:
    form: list[Any] = json.loads(
        (EXAMPLE / "flows" / "review_draft.form.json").read_text()
    )
    return form


def approval() -> Any:
    """The example's flow, as an upload sends it."""
    (content,) = read_flows(EXAMPLE / "flows")
    return content


def with_step(content: Any, name: str, **changes: Any) -> Any:
    """``content`` with one of its steps changed."""
    changed = copy.deepcopy(content)
    changed["steps"][name].update(changes)
    return changed


@pytest.fixture
def flows(tmp_path: Path) -> Path:
    """A copy of the example's flows, to change."""
    return Path(shutil.copytree(EXAMPLE / "flows", tmp_path / "flows"))


@pytest.fixture
def manager() -> Manager:
    return Manager(
        MemoryStore(queues=QUEUES),
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


# What a task's form may hold.


def test_a_tasks_form_declares_the_fields_of_the_result() -> None:
    form = review_form()

    assert form_problems(form, None) == []
    assert form_fields(form) == {"decision": "string", "comment": "string"}


def test_a_tasks_form_is_held_to_the_subset() -> None:
    form = review_form()
    components = form[1]["updateComponents"]["components"]
    components.append({"id": "clip", "component": "Video"})
    components[0]["children"].append("clip")

    assert form_problems(form, None) == [
        "form.clip: component 'Video' is not in the subset"
    ]


def test_two_controls_of_a_tasks_form_cannot_fill_one_field() -> None:
    form = review_form()
    components = form[1]["updateComponents"]["components"]
    components.append(
        {
            "id": "again",
            "component": "CheckBox",
            "label": "Again",
            "value": {"path": "/comment"},
        }
    )
    components[0]["children"].append("again")

    assert form_problems(form, None) == [
        "form.again: field 'comment' already has a control"
    ]


def test_a_tasks_form_needs_a_control() -> None:
    form = review_form()
    form[1]["updateComponents"]["components"] = [
        {"id": "root", "component": "Column", "children": ["intro"]},
        {"id": "intro", "component": "Text", "text": "Nothing to answer."},
    ]
    del form[2]

    assert form_problems(form, None) == [
        "form: needs at least one control, for the result to hold"
    ]


# What a form asks of a result.


def test_a_result_with_the_forms_fields_is_let_through() -> None:
    check_form_result(
        "review_draft", review_form(), {"decision": "changes", "comment": "Shorter."}
    )


@pytest.mark.parametrize(
    ("result", "problem"),
    [
        ("approve", "the result must be a mapping with the form's fields"),
        ({"decision": "approve"}, "no field 'comment'"),
        (
            {"decision": "approve", "comment": "", "extra": 1},
            "the form has no field 'extra'",
        ),
        ({"decision": "approve", "comment": 3}, "field 'comment' must be a string"),
        (
            {"decision": "later", "comment": ""},
            "field 'decision' is not one of approve, changes",
        ),
        (
            {"decision": "approve", "comment": "x" * 501},
            "field 'comment' has more than 500 characters",
        ),
    ],
)
def test_a_result_the_form_does_not_submit_is_refused(
    result: Any, problem: str
) -> None:
    with pytest.raises(InvalidValueError, match=problem):
        check_form_result("review_draft", review_form(), result)


# Where a flow file names a task's form.


def test_a_tasks_form_is_read_in_where_its_name_was(flows: Path) -> None:
    (content,) = read_flows(flows)

    assert content["steps"]["review_draft"]["form"] == read_flow_json(
        (flows / "review_draft.form.json").read_text()
    )
    assert "form" not in content["steps"]["suggest_title"]
    assert list(step_forms(content)) == ["review_draft"]
    assert load_flow_file(flows / "approval.yaml").name == "approval"


def test_a_tasks_form_in_a_loop_is_read_in_too(tmp_path: Path) -> None:
    (tmp_path / "ask.form.json").write_text(json.dumps(review_form()))
    data = {
        "name": "rounds",
        "version": "1.0.0",
        "steps": {
            "round": {
                "loop": {"max_cycles": 3, "exit_condition": "enough"},
                "steps": {
                    "ask": {
                        "queue": "editors",
                        "handler": "ask",
                        "form": "ask.form.json",
                    },
                    "enough": {"queue": "editors", "handler": "enough"},
                },
            }
        },
    }

    content = inline_form(data, tmp_path, read_flow_json)

    assert step_forms(content) == {"ask": review_form()}
    assert parse_flow(content).name == "rounds"


def test_a_tasks_form_file_that_is_missing_is_refused_naming_the_step(
    flows: Path,
) -> None:
    (flows / "review_draft.form.json").unlink()

    with pytest.raises(FlowDefinitionError) as refused:
        read_flows(flows)

    assert refused.value.problems == [
        "approval.yaml: steps.review_draft.form: cannot read "
        "review_draft.form.json: No such file or directory"
    ]


def test_a_tasks_form_outside_the_subset_is_refused_naming_the_step() -> None:
    content = approval()
    content["steps"]["review_draft"]["form"][0]["version"] = "v0.8"

    with pytest.raises(FlowDefinitionError) as refused:
        parse_flow(content)

    assert refused.value.problems == [
        "steps.review_draft.form[0]: version must be 'v0.9'"
    ]


def test_only_a_task_has_a_form() -> None:
    content = approval()
    content["steps"]["again"] = {"flow": "approval", "form": review_form()}

    with pytest.raises(FlowDefinitionError) as refused:
        parse_flow(content)

    assert "steps.again: unknown field 'form'" in refused.value.problems


def test_a_loops_exit_condition_has_no_form() -> None:
    content = {
        "name": "rounds",
        "version": "1.0.0",
        "steps": {
            "round": {
                "loop": {"max_cycles": 3, "exit_condition": "enough"},
                "steps": {
                    "enough": {
                        "queue": "editors",
                        "handler": "enough",
                        "form": review_form(),
                    }
                },
            }
        },
    }

    with pytest.raises(FlowDefinitionError) as refused:
        parse_flow(content)

    assert refused.value.problems == [
        "steps.round.loop.exit_condition: enough has a form, so its result is a "
        "mapping, and an exit condition is true or false"
    ]


def test_the_ui_is_tested_on_the_examples_form() -> None:
    """``ts/neorc-ui/src/forms`` keeps a copy of the form, for its own tests."""
    copy = Path(__file__).parents[4] / "ts/neorc-ui/src/forms/review_draft.form.json"

    assert copy.read_text() == (EXAMPLE / "flows/review_draft.form.json").read_text()


# The manager: at upload, and at every completion.


async def test_a_form_is_for_a_task_of_an_external_queue(manager: Manager) -> None:
    on_a_worker_queue = with_step(approval(), "review_draft", queue="default")

    with pytest.raises(FlowDefinitionError) as refused:
        await manager.upload_flows([on_a_worker_queue])

    assert refused.value.problems == [
        "approval: review_draft has a form and is on queue 'default', which is "
        "not an external queue"
    ]
    assert await manager.latest_flows() == []
    assert await manager.upload_flows([approval()]) == [True]


async def waiting_for_review(manager: Manager) -> Any:
    """A run of the example whose review is published; the review's task id."""
    await manager.upload_flows([approval()])
    run = await manager.start_run("approval", {"topic": "tea"})
    task = await manager.publish_task(run.id, Address("write_draft"))
    await manager.receive_task("default", timeout=0)
    await manager.report_finished(task.id, result="A first draft about tea.")
    return (await manager.publish_task(run.id, Address("review_draft"))).id


async def test_a_completion_is_held_to_the_form_of_its_task(manager: Manager) -> None:
    task_id = await waiting_for_review(manager)

    with pytest.raises(InvalidValueError, match="review_draft: no field 'comment'"):
        await manager.complete_task(task_id, result={"decision": "approve"})
    with pytest.raises(InvalidValueError, match="is not one of approve, changes"):
        await manager.complete_task(task_id, result={"decision": "no", "comment": ""})

    assert (await manager.get_task(task_id)).status is TaskStatus.PENDING
    answer: JsonValue = {"decision": "approve", "comment": "Good."}
    completed = await manager.complete_task(task_id, result=answer)
    assert (completed.status, completed.result) == (TaskStatus.SUCCEEDED, answer)


async def test_a_completion_with_an_error_is_not_held_to_the_form(
    manager: Manager,
) -> None:
    task_id = await waiting_for_review(manager)

    failed = await manager.complete_task(task_id, error="nobody could review it")

    assert (failed.status, failed.error) == (
        TaskStatus.FAILED,
        "nobody could review it",
    )


async def test_a_task_with_no_form_takes_any_result(manager: Manager) -> None:
    await manager.upload_flows([approval()])
    run = await manager.start_run("approval", {"topic": "tea"})
    task = await manager.publish_task(run.id, Address("write_draft"))
    await manager.receive_task("default", timeout=0)
    await manager.report_finished(task.id, result="A first draft about tea.")
    title = await manager.publish_task(run.id, Address("suggest_title"))

    completed = await manager.complete_task(title.id, result="On tea")

    assert completed.result == "On tea"


# The example, from start to end.


async def test_the_example_waits_for_its_two_answers() -> None:
    answers: dict[str, JsonValue] = {
        "review-draft": {"decision": "approve", "comment": ""},
        "suggest-title": "On tea",
    }
    async with LocalCluster(code_location=EXAMPLE, poll_timeout=0.2) as cluster:
        manager = cluster.manager
        await manager.declare_queue("editors", QueueKind.EXTERNAL)
        await cluster.upload(EXAMPLE / "flows")
        await cluster.start()
        run = await cluster.client.start_run("approval", {"topic": "tea"})
        async with asyncio.timeout(10):
            while answers:
                for delivery in await manager.pending_tasks("editors"):
                    assert delivery.inputs == {"draft": "A first draft about tea."}
                    await manager.complete_task(
                        delivery.task.id, result=answers.pop(delivery.task.handler)
                    )
                await asyncio.sleep(0.01)
        finished = await wait_for_run(cluster.client, run.id)

    assert finished.status is RunStatus.SUCCEEDED
    assert finished.output == "Published as 'On tea': A first draft about tea."
