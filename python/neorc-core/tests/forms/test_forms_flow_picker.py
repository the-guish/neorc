# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""A choice of one of the manager's flows: the ``FlowPicker`` of a form.

It is this catalog's own component. What it submits names a flow the manager
holds, for a flow's form and for a task's: docs/architecture/forms.md.
"""

from typing import Any

import pytest

from neorc_core import InvalidValueError, Manager, QueueKind, TaskStatus
from neorc_core._values import JsonValue
from neorc_core.flows import Address
from neorc_core.forms import (
    CATALOG_ID,
    VERSION,
    check_form_inputs,
    check_form_result,
    form_fields,
    form_problems,
    picked_flows,
)
from neorc_core.local import MemoryStore, MemoryTaskNotifier

ABOUT = {"value": {"path": "/target"}}
REQUIRED = {
    "condition": {"call": "required", "args": ABOUT},
    "message": "Choose a flow.",
}


def form(*, start: Any = None, **changes: Any) -> list[Any]:
    """A form of one FlowPicker, bound to ``/target``, with ``changes`` to it."""
    picker = {
        "id": "target",
        "component": "FlowPicker",
        "label": "Flow",
        "value": {"path": "/target"},
        **changes,
    }
    messages: list[Any] = [
        {
            "version": VERSION,
            "createSurface": {"surfaceId": "s", "catalogId": CATALOG_ID},
        },
        {
            "version": VERSION,
            "updateComponents": {
                "surfaceId": "s",
                "components": [
                    {"id": "root", "component": "Column", "children": ["target"]},
                    picker,
                ],
            },
        },
    ]
    if start is not None:
        messages.append(
            {
                "version": VERSION,
                "updateDataModel": {"surfaceId": "s", "path": "/", "value": start},
            }
        )
    return messages


def content(messages: Any) -> dict[str, Any]:
    return {"name": "launcher", "version": "1.0.0", "form": messages}


# What a form may say of it.


def test_a_flow_picker_fills_a_string_input() -> None:
    assert form_problems(form(), {"target": "string"}) == []
    assert form_problems(form(checks=[REQUIRED]), {"target": "string"}) == []
    assert form_problems(form(start={"target": "hello"}), {"target": "string"}) == []


def test_a_flow_picker_fills_no_other_type() -> None:
    assert form_problems(form(), {"target": "number"}) == [
        "form.target: input 'target' is a number, and this control submits a string"
    ]


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        (
            {"options": [{"label": "Hello", "value": "hello"}]},
            "form.target: FlowPicker has no 'options' in the subset",
        ),
        ({"label": 3}, "form.target: label must be a string"),
        (
            {
                "checks": [
                    {
                        "condition": {"call": "length", "args": {**ABOUT, "min": 2}},
                        "message": "Longer.",
                    }
                ]
            },
            "form.target.checks[0]: 'length' is not a check of this control: required",
        ),
        (
            {"start": {"target": ["hello"]}},
            "form: updateDataModel must set 'target' to a string",
        ),
    ],
    ids=["options of its own", "a label that is no text", "a length", "a list"],
)
def test_a_flow_picker_outside_the_subset_is_refused(
    changes: dict[str, Any], problem: str
) -> None:
    assert form_problems(form(**changes), {"target": "string"}) == [problem]


def test_a_tasks_flow_picker_is_a_field_of_the_result() -> None:
    assert form_problems(form(), None) == []
    assert form_fields(form()) == {"target": "string"}


# What it asks of what is submitted.


@pytest.mark.parametrize(
    ("values", "picked"),
    [
        ({"target": "hello"}, {"hello"}),
        ({"target": ["hello", "bye", "hello"]}, {"hello", "bye"}),
        ({"target": 3}, set()),
        ({"other": "hello"}, set()),
        ("hello", set()),
    ],
)
def test_the_flows_a_form_is_answered_with_are_named(
    values: Any, picked: set[str]
) -> None:
    assert picked_flows(form(), values) == picked


def test_a_form_with_no_flow_picker_names_no_flow() -> None:
    field = form(component="TextField")

    assert picked_flows(field, {"target": "hello"}) == set()
    assert picked_flows(None, {"target": "hello"}) == set()


def test_an_input_names_a_flow_the_manager_holds() -> None:
    check_form_inputs("launcher", content(form()), {"target": "hello"}, flows={"hello"})

    with pytest.raises(InvalidValueError) as refused:
        check_form_inputs("launcher", content(form()), {"target": "hello"})

    assert str(refused.value) == "launcher: input 'target' names no flow 'hello'"


def test_a_field_of_a_result_names_a_flow_the_manager_holds() -> None:
    check_form_result("pick", form(), {"target": "hello"}, flows={"hello"})

    with pytest.raises(InvalidValueError) as refused:
        check_form_result("pick", form(), {"target": ""}, flows={"hello"})

    assert str(refused.value) == "pick: field 'target' names no flow ''"


def test_a_flow_picker_in_a_template_names_a_flow_per_item() -> None:
    per_item = form(value={"path": "target"})
    components = per_item[1]["updateComponents"]["components"]
    components[0]["children"] = ["each"]
    components.append(
        {
            "id": "each",
            "component": "List",
            "children": {"componentId": "target", "path": "/inputs/jobs"},
        }
    )
    inputs = {"jobs": ["first", "second"]}

    assert form_problems(per_item, None, params=["jobs"]) == []
    assert form_fields(per_item) == {"target": "list of strings"}
    check_form_result(
        "pick", per_item, {"target": ["hello", "bye"]}, inputs, flows={"hello", "bye"}
    )
    with pytest.raises(InvalidValueError) as refused:
        check_form_result(
            "pick", per_item, {"target": ["hello", "bye"]}, inputs, flows={"hello"}
        )

    assert str(refused.value) == "pick: field 'target', item 2, names no flow 'bye'"


# The manager: at every start, and at every completion.

HELLO: JsonValue = {
    "name": "hello",
    "version": "1.0.0",
    "steps": {"greet": {"handler": "tasks:greet"}},
}
LAUNCHER: JsonValue = {
    "name": "launcher",
    "version": "1.0.0",
    "inputs": {"target": "string"},
    "form": form(),
    "steps": {
        "note": {"handler": "tasks:note", "params": {"target": "inputs.target"}},
        # A person names the flow to run next, on a form of the same picker.
        "pick": {"queue": "editors", "handler": "pick-flow", "form": form()},
    },
}


@pytest.fixture
def manager() -> Manager:
    queues = {"default": QueueKind.WORKER, "editors": QueueKind.EXTERNAL}
    return Manager(
        MemoryStore(queues=queues),
        tasks=MemoryTaskNotifier(),
        events=MemoryTaskNotifier(),
    )


async def test_a_run_is_started_with_a_flow_the_manager_holds(manager: Manager) -> None:
    await manager.upload_flows([LAUNCHER, HELLO])

    run = await manager.start_run("launcher", {"target": "hello"})

    assert run.inputs == {"target": "hello"}


@pytest.mark.parametrize("named", ["nope", "", "not a name", "Hello"])
async def test_a_run_is_not_started_with_a_flow_the_manager_lacks(
    manager: Manager, named: str
) -> None:
    await manager.upload_flows([LAUNCHER, HELLO])

    with pytest.raises(InvalidValueError) as refused:
        await manager.start_run("launcher", {"target": named})

    assert str(refused.value) == f"launcher: input 'target' names no flow {named!r}"


async def test_a_flow_uploaded_since_is_one_to_pick(manager: Manager) -> None:
    await manager.upload_flows([LAUNCHER])
    with pytest.raises(InvalidValueError, match="names no flow 'hello'"):
        await manager.start_run("launcher", {"target": "hello"})

    await manager.upload_flows([HELLO])

    assert await manager.start_run("launcher", {"target": "hello"})


async def test_a_completion_names_a_flow_the_manager_holds(manager: Manager) -> None:
    await manager.upload_flows([LAUNCHER, HELLO])
    run = await manager.start_run("launcher", {"target": "hello"})
    task_id = (await manager.publish_task(run.id, Address("pick"))).id

    with pytest.raises(InvalidValueError) as refused:
        await manager.complete_task(task_id, result={"target": "nope"})

    assert str(refused.value) == "pick: field 'target' names no flow 'nope'"
    assert (await manager.get_task(task_id)).status is TaskStatus.PENDING
    completed = await manager.complete_task(task_id, result={"target": "launcher"})
    assert completed.result == {"target": "launcher"}
