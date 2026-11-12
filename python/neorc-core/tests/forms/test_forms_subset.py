# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""What a form may hold, and what it asks of a run's inputs.

Everything about forms is tested in this directory and nowhere else:
docs/architecture/forms.md.
"""

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from neorc_core import InvalidValueError
from neorc_core.forms import CATALOG_ID, VERSION, check_form_inputs, form_problems

EXAMPLE = Path(__file__).parents[4] / "examples" / "code_task" / "flows"
INPUTS = {
    "description": "string",
    "repo": "string",
    "model": "string",
    "attempts": "number",
    "draft": "boolean",
}


def example() -> list[Any]:
    form: list[Any] = json.loads((EXAMPLE / "code_task.form.json").read_text())
    return form


def components(form: list[Any]) -> list[dict[str, Any]]:
    listed: list[dict[str, Any]] = form[1]["updateComponents"]["components"]
    return listed


def component(form: list[Any], cid: str) -> dict[str, Any]:
    return next(c for c in components(form) if c["id"] == cid)


def changed(change: Callable[[list[Any]], object]) -> list[Any]:
    form = copy.deepcopy(example())
    change(form)
    return form


def test_the_example_form_is_within_the_subset() -> None:
    assert form_problems(example(), INPUTS) == []


def test_a_form_of_one_field_is_enough() -> None:
    form = [
        {
            "version": VERSION,
            "createSurface": {"surfaceId": "s", "catalogId": CATALOG_ID},
        },
        {
            "version": VERSION,
            "updateComponents": {
                "surfaceId": "s",
                "components": [
                    {"id": "root", "component": "Column", "children": ["name"]},
                    {
                        "id": "name",
                        "component": "TextField",
                        "label": "Name",
                        "value": {"path": "/name"},
                    },
                ],
            },
        },
    ]

    assert form_problems(form, {"name": "string"}) == []


def add_a_video(form: list[Any]) -> None:
    components(form).append({"id": "clip", "component": "Video", "url": "x"})
    component(form, "root")["children"].append("clip")


REFUSED: list[tuple[str, Callable[[list[Any]], object], str]] = [
    (
        "a component of A2UI's catalog the subset leaves out",
        add_a_video,
        "form.clip: component 'Video' is not in the subset",
    ),
    (
        "a property the subset leaves out",
        lambda f: component(f, "repo").update(filterable=True),
        "form.repo: ChoicePicker has no 'filterable' in the subset",
    ),
    (
        "a choice of several",
        lambda f: component(f, "repo").update(variant="multipleSelection"),
        "form.repo: variant must be 'mutuallyExclusive'",
    ),
    (
        "a label that is bound to the data",
        lambda f: component(f, "draft").update(label={"path": "/label"}),
        "form.draft: label must be a string",
    ),
    (
        "a text that calls a function",
        lambda f: component(f, "intro").update(
            text={"call": "formatString", "args": {"value": "${/repo}"}}
        ),
        "form.intro: text must be a string",
    ),
    (
        "a check that is not one of the control's",
        lambda f: component(f, "description")["checks"][0]["condition"].update(
            call="regex", args={"value": {"path": "/description"}, "pattern": "^a"}
        ),
        "'regex' is not a check of this control: required, length",
    ),
    (
        "a check combined from others",
        lambda f: component(f, "attempts")["checks"][0].update(
            condition={"call": "and", "args": {"values": [True, True]}}
        ),
        "'and' is not a check of this control: required, numeric",
    ),
    (
        "a check on a check box",
        lambda f: component(f, "draft").update(
            checks=[
                {
                    "condition": {
                        "call": "required",
                        "args": {"value": {"path": "/draft"}},
                    },
                    "message": "Say so.",
                }
            ]
        ),
        "form.draft: CheckBox has no 'checks' in the subset",
    ),
    (
        "a check about another control's value",
        lambda f: component(f, "description")["checks"][0]["condition"]["args"].update(
            value={"path": "/repo"}
        ),
        "form.description.checks[0]: must be about the control's own value",
    ),
    (
        "a length with no limit",
        lambda f: component(f, "description")["checks"][0]["condition"].update(
            args={"value": {"path": "/description"}}
        ),
        "length takes min, max or both",
    ),
    (
        "a control bound to no input of the flow",
        lambda f: component(f, "draft").update(value={"path": "/urgent"}),
        "form.draft: the flow declares no input 'urgent'",
    ),
    (
        "a control bound below an input",
        lambda f: component(f, "draft").update(value={"path": "/options/draft"}),
        "form.draft: value must be {'path': '/<input>'}",
    ),
    (
        "two controls for one input",
        lambda f: component(f, "model").update(value={"path": "/repo"}),
        "form.model: input 'repo' already has a control",
    ),
    (
        "a control that submits another type than the input's",
        lambda f: component(f, "attempts").update(variant="shortText", checks=[]),
        "input 'attempts' is a number, and this control submits a string",
    ),
    (
        "a child that does not exist",
        lambda f: component(f, "root")["children"].append("nowhere"),
        "form.root: no component with id 'nowhere'",
    ),
    (
        "a component that is placed nowhere",
        lambda f: component(f, "root")["children"].remove("intro"),
        "form.intro: not placed under 'root'",
    ),
    (
        "a component placed twice",
        lambda f: component(f, "root")["children"].append("intro"),
        "form.intro: placed more than once",
    ),
    (
        "two components with one id",
        lambda f: components(f).append(
            {"id": "intro", "component": "Text", "text": "x"}
        ),
        "form.intro: id used more than once",
    ),
    (
        "two options with one value",
        lambda f: component(f, "model")["options"].append(
            {"label": "Again", "value": "small"}
        ),
        "form.model: two options have the same value",
    ),
    (
        "another catalog",
        lambda f: f[0]["createSurface"].update(catalogId="https://a2ui.org/basic"),
        f"catalogId must be {CATALOG_ID!r}",
    ),
    (
        "another version of A2UI",
        lambda f: f[1].update(version="1.0"),
        "form[1]: version must be 'v0.9'",
    ),
    (
        "a message a form does not hold",
        lambda f: f.append({"version": VERSION, "deleteSurface": {"surfaceId": "s"}}),
        "form[3]: must be one of createSurface, updateComponents, updateDataModel",
    ),
    (
        "a theme",
        lambda f: f[0]["createSurface"].update(theme={"primaryColor": "#f00"}),
        "form[0].createSurface: 'theme' is not in the subset",
    ),
    (
        "data to start from that is no option",
        lambda f: f[2]["updateDataModel"]["value"].update(model=["huge"]),
        "updateDataModel must set 'model' to a list of at most one of its options",
    ),
    (
        "data to start from for no control",
        lambda f: f[2]["updateDataModel"]["value"].update(urgent=True),
        "form: updateDataModel sets 'urgent', which has no control",
    ),
    (
        "data set below the root",
        lambda f: f[2]["updateDataModel"].update(path="/model"),
        "form[2].updateDataModel: path must be '/'",
    ),
]


@pytest.mark.parametrize(
    ("change", "problem"),
    [(change, problem) for _, change, problem in REFUSED],
    ids=[name for name, _, _ in REFUSED],
)
def test_a_form_outside_the_subset_is_refused_with_the_reason(
    change: Callable[[list[Any]], object], problem: str
) -> None:
    problems = form_problems(changed(change), INPUTS)

    assert any(problem in found for found in problems), problems


def test_every_input_of_the_flow_needs_a_control() -> None:
    assert form_problems(example(), {**INPUTS, "urgent": "boolean"}) == [
        "form: no control for input 'urgent'"
    ]


def test_no_control_submits_a_datetime() -> None:
    problems = form_problems(example(), {**INPUTS, "description": "datetime"})

    assert problems == [
        "form.description: input 'description' is a datetime, and this control "
        "submits a string"
    ]


@pytest.mark.parametrize("form", [None, {}, [], "code_task.form.json", [1]])
def test_what_is_not_a_list_of_messages_is_refused(form: Any) -> None:
    assert form_problems(form, INPUTS)


def test_every_problem_is_reported_at_once() -> None:
    def break_two(form: list[Any]) -> None:
        component(form, "repo").update(filterable=True)
        component(form, "draft").update(value={"path": "/urgent"})

    problems = form_problems(changed(break_two), INPUTS)

    assert "form.repo: ChoicePicker has no 'filterable' in the subset" in problems
    assert "form.draft: the flow declares no input 'urgent'" in problems
    assert "form: no control for input 'draft'" in problems


# What a form asks of a run's inputs.

GIVEN: dict[str, Any] = {
    "description": "Add a retry to the upload step.",
    "repo": "acme/billing",
    "model": "medium",
    "attempts": 2,
    "draft": True,
}


def content(form: Any) -> dict[str, Any]:
    return {"name": "code_task", "version": "1.0.0", "form": form}


def test_inputs_that_keep_what_the_form_asks_are_let_through() -> None:
    check_form_inputs("code_task", content(example()), GIVEN)


@pytest.mark.parametrize(
    ("given", "problem"),
    [
        ({"repo": "acme/unknown"}, "input 'repo' is not one of acme/billing, "),
        ({"model": ""}, "input 'model' is not one of large, medium, small"),
        ({"description": "short"}, "input 'description' has fewer than 10 characters"),
        ({"attempts": 0}, "input 'attempts' is below 1"),
        ({"attempts": 5.5}, "input 'attempts' is above 5"),
    ],
)
def test_inputs_that_break_what_the_form_asks_are_refused(
    given: dict[str, Any], problem: str
) -> None:
    with pytest.raises(InvalidValueError, match=problem) as refused:
        check_form_inputs("code_task", content(example()), {**GIVEN, **given})

    assert str(refused.value).startswith("code_task: ")


def test_every_broken_input_is_named_at_once() -> None:
    with pytest.raises(InvalidValueError) as refused:
        check_form_inputs(
            "code_task", content(example()), {**GIVEN, "repo": "x", "attempts": 9}
        )

    assert "input 'repo'" in str(refused.value)
    assert "input 'attempts' is above 5" in str(refused.value)


def test_required_refuses_an_empty_text_and_length_its_upper_limit() -> None:
    def ask_more(form: list[Any]) -> None:
        about = {"value": {"path": "/description"}}
        component(form, "description")["checks"] = [
            {"condition": {"call": "required", "args": about}, "message": "Say it."},
            {
                "condition": {"call": "length", "args": {**about, "max": 12}},
                "message": "Keep it short.",
            },
        ]

    form = changed(ask_more)
    assert form_problems(form, INPUTS) == []

    with pytest.raises(InvalidValueError, match="input 'description' is empty"):
        check_form_inputs("code_task", content(form), {**GIVEN, "description": ""})
    with pytest.raises(InvalidValueError, match="has more than 12 characters"):
        check_form_inputs("code_task", content(form), GIVEN)


@pytest.mark.parametrize("stored", [{"name": "a"}, {"form": None}, {"form": 3}, None])
def test_a_flow_with_no_form_asks_nothing(stored: Any) -> None:
    check_form_inputs("a", stored, {"anything": "at all"})
