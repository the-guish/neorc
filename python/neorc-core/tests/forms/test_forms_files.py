# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""A flow file names its form's file, and what is read in holds the form."""

import shutil
from pathlib import Path
from typing import Any

import pytest

from neorc_core import FlowDefinitionError
from neorc_core.flows import (
    load_flow_file,
    load_flow_yaml,
    load_flows,
    parse_flow,
    read_flow_json,
    read_flows,
)
from neorc_core.forms import inline_form

EXAMPLE = Path(__file__).parents[4] / "examples" / "code_task" / "flows"


@pytest.fixture
def flows(tmp_path: Path) -> Path:
    """A copy of the example's flows, to change."""
    return Path(shutil.copytree(EXAMPLE, tmp_path / "flows"))


def test_reading_a_directory_puts_the_form_where_its_name_was(flows: Path) -> None:
    (content,) = read_flows(flows)

    assert content["form"] == read_flow_json(
        (flows / "code_task.form.json").read_text()
    )
    assert list(content)[:2] == ["name", "version"]
    assert parse_flow(content).name == "code_task"


def test_a_flow_file_is_loaded_with_its_form(flows: Path) -> None:
    assert load_flow_file(flows / "code_task.yaml").name == "code_task"
    assert [flow.name for flow in load_flows(flows)] == ["code_task"]


def test_a_form_file_is_not_taken_for_a_flow(flows: Path) -> None:
    assert len(read_flows(flows)) == 1


def test_a_form_outside_the_subset_is_refused_naming_the_flow_file(flows: Path) -> None:
    form = flows / "code_task.form.json"
    form.write_text(form.read_text().replace('"CheckBox"', '"Slider"'))

    with pytest.raises(FlowDefinitionError) as refused:
        read_flows(flows)

    assert refused.value.problems[0] == (
        "code_task.yaml: form.draft: component 'Slider' is not in the subset"
    )


def test_a_form_file_that_is_missing_is_refused(flows: Path) -> None:
    (flows / "code_task.form.json").unlink()

    with pytest.raises(FlowDefinitionError) as refused:
        read_flows(flows)

    assert refused.value.problems == [
        "code_task.yaml: form: cannot read code_task.form.json: "
        "No such file or directory"
    ]


def test_a_form_file_that_is_not_json_is_refused(flows: Path) -> None:
    (flows / "code_task.form.json").write_text("[{")

    with pytest.raises(FlowDefinitionError) as refused:
        load_flow_file(flows / "code_task.yaml")

    assert refused.value.problems[0].startswith(
        "form: code_task.form.json: not valid JSON"
    )


@pytest.mark.parametrize(
    "name", ["../secrets.json", "forms/code_task.form.json", "/etc/passwd", "..", ""]
)
def test_a_form_is_a_file_next_to_the_flow_and_nowhere_else(
    flows: Path, name: str
) -> None:
    (flows.parent / "secrets.json").write_text("[]")
    flow = flows / "code_task.yaml"
    flow.write_text(flow.read_text().replace("code_task.form.json", f"'{name}'"))

    with pytest.raises(FlowDefinitionError, match="must be the name of a file next"):
        read_flows(flows)


def test_text_that_came_from_no_file_cannot_name_a_form() -> None:
    text = (EXAMPLE / "code_task.yaml").read_text()

    with pytest.raises(FlowDefinitionError, match="was not read from one"):
        load_flow_yaml(text)


def test_an_upload_holds_the_form_itself_and_not_its_name() -> None:
    content: dict[str, Any] = {
        "name": "a",
        "version": "1.0.0",
        "form": "a.form.json",
        "steps": {"work": {"handler": "tasks:work"}},
    }

    with pytest.raises(FlowDefinitionError, match="names a file, which is read in"):
        parse_flow(content)


def test_a_structure_that_names_no_file_is_left_as_it_is() -> None:
    for data in ({"name": "a"}, {"name": "a", "form": [1]}, None, [1]):
        assert inline_form(data, None, read_flow_json) is data


def test_the_ui_is_tested_on_the_examples_form() -> None:
    """``ts/neorc-ui/src/forms`` keeps a copy of the form, for its own tests."""
    copy = Path(__file__).parents[4] / "ts/neorc-ui/src/forms/code_task.form.json"

    assert copy.read_text() == (EXAMPLE / "code_task.form.json").read_text()
