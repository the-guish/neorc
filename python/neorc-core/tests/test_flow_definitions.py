# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Reading and validating flow files."""

import json
from pathlib import Path
from textwrap import dedent

import pytest

from neorc_core import FlowDefinitionError
from neorc_core.flows import (
    FanOutStep,
    InputType,
    LoopStep,
    Namespace,
    Reference,
    SubFlowStep,
    TaskStep,
    Version,
    load_flow_yaml,
    load_flows,
    parse_flow,
    read_flow_json,
    read_flow_yaml,
    validate_flow_set,
)

EXAMPLES = Path(__file__).parents[3] / "examples"


def problems(text: str) -> str:
    """The problems reported for a flow, joined, or fail if it is valid."""
    with pytest.raises(FlowDefinitionError) as caught:
        load_flow_yaml(dedent(text))
    return "\n".join(caught.value.problems)


def flow(steps: str, *, header: str = "") -> str:
    """A flow file with the given steps, indented under ``steps:``."""
    return (
        "name: f\nversion: 1.0.0\n"
        + dedent(header)
        + "steps:\n"
        + "".join(f"  {line}\n" for line in dedent(steps).splitlines())
    )


def test_examples_load() -> None:
    hello = load_flows(EXAMPLES / "hello" / "flows")
    wordplay = {f.name: f for f in load_flows(EXAMPLES / "wordplay" / "flows")}

    assert [f.name for f in hello] == ["a", "b"]
    picker = wordplay["word_picker"]
    assert picker.version == Version(1, 0, 0)
    assert picker.inputs == {"sentence": InputType.STRING, "preferred_letter": "string"}
    assert picker.output == Reference(Namespace.TASKS, "keep_matching_words")
    assert [c.name for c in picker.enclosing("pick_word")] == ["rounds", "picking"]


def test_example_steps_are_modelled() -> None:
    wordplay = {f.name: f for f in load_flows(EXAMPLES / "wordplay" / "flows")}
    picker = wordplay["word_picker"]
    rounds = wordplay["word_picker_rounds"]

    rounds_loop = picker.step("rounds")
    assert isinstance(rounds_loop, LoopStep)
    assert (rounds_loop.max_cycles, rounds_loop.exit_condition) == (10, "enough_rounds")
    assert [s.name for s in rounds_loop.steps] == [
        "picking",
        "collect_words",
        "enough_rounds",
    ]
    picking = picker.step("picking")
    assert isinstance(picking, FanOutStep)
    assert (picking.range, picking.over) == (3, None)
    decorate = rounds.step("decorate")
    assert isinstance(decorate, FanOutStep)
    assert decorate.over == Reference(Namespace.TASKS, "final_words")
    assert rounds.step("picker") == SubFlowStep(
        "picker",
        "word_picker",
        params={
            "sentence": Reference(Namespace.INPUTS, "sentence"),
            "preferred_letter": Reference(Namespace.INPUTS, "preferred_letter"),
        },
    )
    pad = rounds.step("pad")
    assert isinstance(pad, TaskStep)
    assert (pad.handler, pad.queue, pad.fixed_params) == (
        "tasks:pad",
        "default",
        {"pad_char": "*"},
    )
    assert {t.queue for t in picker.tasks()} == {"default", "scoring"}


def test_yaml_and_its_json_upload_are_the_same_structure() -> None:
    text = flow(
        """
        t:
          handler: m:f
          fixed_params:
            when: {"$datetime": "2026-09-13T10:00:00+00:00"}
            day: 2026-09-13
        """
    )

    data = read_flow_yaml(text)

    assert data["steps"]["t"]["fixed_params"]["day"] == "2026-09-13"
    assert parse_flow(data) == load_flow_yaml(text)
    assert read_flow_json(json.dumps(data)) == data


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "name: f\nversion: 1.0.0\nsteps: {t: {handler: m:a}, t: {handler: m:b}}",
            "line 3: 't' appears more than once",
        ),
        (
            "name: f\nversion: 1.0.0\nsteps:\n  t:\n    handler: m:a\n"
            "  t:\n    handler: m:b\n",
            "line 6: 't' appears more than once",
        ),
        (
            "name: f\nversion: 1.0.0\nversion: 2.0.0\nsteps: {t: {handler: m:a}}",
            "line 3: 'version' appears more than once",
        ),
        (
            "name: f\nversion: 1.0.0\n"
            "steps: {t: {handler: m:a, params: {x: tasks.u, x: tasks.v}}}",
            "'x' appears more than once",
        ),
    ],
    ids=["flow-style-step", "block-style-step", "top-level-field", "param"],
)
def test_keys_repeated_in_one_yaml_mapping_are_problems(
    text: str, expected: str
) -> None:
    assert expected in problems(text)


def test_repeated_keys_are_all_reported() -> None:
    reported = problems("name: f\nname: g\nversion: 1.0.0\nversion: 1.0.0\nsteps: {}")

    assert reported.count("appears more than once") == 2


def test_keys_repeated_in_one_json_object_are_problems() -> None:
    text = '{"name": "f", "version": "1.0.0", "steps": {"t": {}, "t": {}}}'

    with pytest.raises(FlowDefinitionError, match="'t' appears more than once"):
        read_flow_json(text)


@pytest.mark.parametrize(
    "text",
    ["steps: {t: [", "n: !!int oops", "n: !!float oops", "[" * 100_000],
    ids=["unclosed", "bad-int-tag", "bad-float-tag", "deep-nesting"],
)
def test_malformed_yaml_is_a_flow_definition_error(text: str) -> None:
    with pytest.raises(FlowDefinitionError, match="not valid YAML"):
        read_flow_yaml(text)


@pytest.mark.parametrize(
    "text",
    [
        "a: &x {queue: q}\nb: *x",
        "a: &x [*x]",
        "a: &x hello",
        "a: &x {q: 1}\nb: {<<: *x}",
    ],
    ids=["alias", "self-referencing-alias", "unused-anchor", "merge-key-alias"],
)
def test_yaml_anchors_and_aliases_are_rejected(text: str) -> None:
    with pytest.raises(
        FlowDefinitionError, match="anchors and aliases are not allowed"
    ):
        read_flow_yaml(text)


def test_self_referencing_alias_in_a_flow_is_a_flow_definition_error() -> None:
    with pytest.raises(FlowDefinitionError, match="anchors and aliases"):
        load_flow_yaml(flow("t: {handler: m:f, fixed_params: {a: &a [*a]}}"))


@pytest.mark.parametrize(
    "text",
    ["{", '{"n": ' + "1" * 5000 + "}", "[" * 100_000 + "]" * 100_000],
    ids=["unclosed", "integer-over-digit-limit", "deep-nesting"],
)
def test_malformed_json_is_a_flow_definition_error(text: str) -> None:
    with pytest.raises(FlowDefinitionError, match="not valid JSON"):
        read_flow_json(text)


@pytest.mark.parametrize(
    ("scalar", "expected"),
    [
        ("NO", "NO"),
        ("on", "on"),
        ("yes", "yes"),
        ("12:30", "12:30"),
        ("1_000", "1_000"),
        ("2026-09-13", "2026-09-13"),
        ("0x1F", "0x1F"),
        ("1.0.0", "1.0.0"),
        ("010", 10),
        ("-4", -4),
        ("1.5", 1.5),
        ("1e3", 1000.0),
        ("true", True),
        ("False", False),
        ("~", None),
        ("null", None),
    ],
)
def test_yaml_scalars_follow_the_yaml_1_2_core_schema(
    scalar: str, expected: object
) -> None:
    assert read_flow_yaml(f"value: {scalar}") == {"value": expected}


def test_yaml_1_1_boolean_words_can_be_names() -> None:
    definition = load_flow_yaml(
        flow(
            "on: {handler: m:f, params: {no: inputs.yes}}",
            header="inputs: {yes: string}\n",
        )
    )

    assert definition.step("on") == TaskStep(
        "on", "m:f", params={"no": Reference(Namespace.INPUTS, "yes")}
    )


def test_json_uploads_can_have_fields_in_any_order() -> None:
    upload = {"steps": {"t": {"handler": "m:f"}}, "version": "1.0.0", "name": "f"}

    definition = parse_flow(read_flow_json(json.dumps(upload, sort_keys=True)))

    assert (definition.name, str(definition.version)) == ("f", "1.0.0")


def test_field_order_is_reported_with_the_other_problems_of_a_file() -> None:
    reported = problems(
        "version: 1.0.0\nname: f\ncolour: red\nsteps: {t: {handler: m}}"
    )

    assert "first two fields" in reported
    assert "unknown field 'colour'" in reported


@pytest.mark.parametrize("queue", ["gpu/large", ".", "..", "a b", ""])
def test_a_queue_name_is_letters_digits_underscore_and_dash(queue: str) -> None:
    """It travels in a URL path, where a slash or a dot segment would be read."""
    assert "is not a queue name" in problems(
        flow(f"t: {{handler: m:f, queue: {queue!r}}}")
    )
    assert load_flow_yaml(flow("t: {handler: m:f, queue: python-default_1}"))


def test_queue_defaults_to_default() -> None:
    definition = load_flow_yaml(flow("t:\n  handler: m:f"))

    assert definition.step("t") == TaskStep("t", "m:f", "default")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("version: 1.0.0\nname: f\nsteps: {t: {handler: m:f}}", "first two fields"),
        ("name: f\nversion: 1.0\nsteps: {t: {handler: m:f}}", "must be a string"),
        ("name: f\nversion: 1.0.0-beta\nsteps: {t: {handler: m:f}}", "not a version"),
        (
            "name: f\nversion: 1.0.2147483648\nsteps: {t: {handler: m:f}}",
            "not between 0 and 2147483647",
        ),
        ("name: my-flow\nversion: 1.0.0\nsteps: {t: {handler: m:f}}", "not a name"),
        ("name: f\nversion: 1.0.0\nsteps: {}", "non-empty mapping"),
        ("name: f\nversion: 1.0.0\ncolor: red\nsteps: {t: {handler: m:f}}", "'color'"),
        ("name: f\nversion: 1.0.0\ninputs: {x: list}\nsteps: {t: {handler: m}}", "ty"),
        ("- not a mapping", "must be a mapping"),
    ],
    ids=[
        "name-not-first",
        "float-version",
        "prerelease",
        "version-part-too-large",
        "bad-name",
        "no-steps",
        "unknown-field",
        "unknown-input-type",
        "not-a-mapping",
    ],
)
def test_flow_shape_problems(text: str, expected: str) -> None:
    assert expected in problems(text)


def test_a_version_part_is_bounded_however_it_is_made() -> None:
    """What a store's integer column holds, so every store takes the same versions."""
    assert Version.parse("2147483647.0.0") == Version(2147483647, 0, 0)
    with pytest.raises(ValueError, match="between 0 and"):
        Version(0, 2147483648, 0)
    with pytest.raises(ValueError, match="between 0 and"):
        Version(-1, 0, 0)


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        ("t: {handler: m:f, loop: {max_cycles: 1, exit_condition: t}}", "exactly one"),
        ("t: {handler: m:f, colour: red}", "unknown field 'colour'"),
        ("t: {handler: ''}", "non-empty string"),
        (
            "a: {handler: m:f}\nl:\n  loop: {max_cycles: 1, exit_condition: x}\n"
            "  steps: {a: {handler: m:f}}",
            "used more than once",
        ),
        ("t: {handler: m:f, params: {x: 3}}", "not a reference"),
        ("t: {handler: m:f, params: {x: task.a}}", "not one of"),
        ("t: {handler: m:f, fixed_params: {x: {$other: 1}}}", "reserved"),
        ("t: {handler: m:f, fixed_params: {x: {$datetime: 34}}}", "ISO 8601"),
        (
            "l:\n  loop: {max_cycles: 0, exit_condition: t}\n"
            "  steps: {t: {handler: m:f}}",
            "at least 1",
        ),
        (
            "l:\n  fan_out: {range: 2, over: tasks.t}\n  steps: {u: {handler: m:f}}",
            "exactly one of 'range', 'over'",
        ),
        ("l:\n  fan_out: {range: true}\n  steps: {u: {handler: m:f}}", "at least 1"),
    ],
    ids=[
        "two-kinds",
        "unknown-step-field",
        "empty-handler",
        "duplicate-name-nested",
        "param-not-string",
        "unknown-namespace",
        "reserved-fixed-key",
        "bad-datetime-tag",
        "zero-cycles",
        "range-and-over",
        "boolean-range",
    ],
)
def test_step_shape_problems(steps: str, expected: str) -> None:
    assert expected in problems(flow(steps))


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        ("t: {handler: m:f, params: {x: inputs.missing}}", "no input 'missing'"),
        ("t: {handler: m:f, params: {x: tasks.missing}}", "no step 'missing'"),
        ("t: {handler: m:f, params: {x: tasks.t}}", "cannot refer to itself"),
        (
            "s: {flow: other}\nt: {handler: m:f, params: {x: tasks.s}}",
            "'s' is not a task",
        ),
        ("t: {handler: m:f, params: {x: flows.t}}", "'t' is not a sub-flow"),
        ("t: {handler: m:f, params: {x: neorc.colour}}", "is not one of neorc."),
        ("t: {handler: m:f, params: {x: neorc.index}}", "inside a fan-out"),
        ("t: {handler: m:f, params: {x: neorc.loop_count}}", "inside a loop"),
        (
            "l:\n  fan_out: {range: 2}\n"
            "  steps: {t: {handler: m:f, params: {x: neorc.item}}}",
            "fan-out over a list",
        ),
        ("s: {flow: other, params: {x: neorc.task_id}}", "only tasks"),
        (
            "t: {handler: m:f, params: {x: tasks.u}}\n"
            "u: {handler: m:f, params: {x: tasks.t}}",
            "t -> u -> t",
        ),
        (
            "l:\n  fan_out: {over: tasks.t}\n  steps: {t: {handler: m:f}}",
            "inside the fan-out it feeds",
        ),
        (
            "x: {handler: m:f, params: {y: tasks.inner}}\n"
            "l:\n  loop: {max_cycles: 2, exit_condition: inner}\n  steps:\n"
            "    inner: {handler: m:f, params: {x: tasks.x}}",
            "l -> x -> l",
        ),
        (
            "l:\n  loop: {max_cycles: 2, exit_condition: nope}\n"
            "  steps: {t: {handler: m:f}}",
            "is not a task directly in the loop",
        ),
        (
            "l:\n  loop: {max_cycles: 2, exit_condition: inner}\n  steps:\n"
            "    inner:\n      fan_out: {range: 2}\n      steps: {t: {handler: m:f}}",
            "must be a task",
        ),
        (
            "a: {handler: m:f, params: {x: inputs.x}, fixed_params: {x: 1}}",
            "in both params and fixed_params",
        ),
    ],
    ids=[
        "unknown-input",
        "unknown-step",
        "self-reference",
        "tasks-to-sub-flow",
        "flows-to-task",
        "unknown-metadata",
        "index-outside-fan-out",
        "loop-count-outside-loop",
        "item-in-range-fan-out",
        "metadata-on-sub-flow",
        "cycle",
        "fan-out-over-its-own-step",
        "cycle-through-a-container",
        "exit-condition-missing",
        "exit-condition-not-a-task",
        "param-twice",
    ],
)
def test_reference_and_structure_problems(steps: str, expected: str) -> None:
    assert expected in problems(flow(steps, header="inputs: {x: number}\n"))


def test_output_must_refer_to_a_task_a_sub_flow_or_a_switch() -> None:
    header = "inputs: {x: string}\noutput: inputs.x\n"

    assert "must refer to tasks., flows. or switches." in problems(
        flow("t: {handler: m:f}", header=header)
    )


@pytest.mark.parametrize(
    ("content", "path"),
    [
        ({"steps": {"t": {"handler": "m:f\x00"}}}, "flow.steps.t.handler"),
        (
            {"steps": {"t": {"handler": "m:f", "fixed_params": {"x": "\x00"}}}},
            "flow.steps.t.fixed_params.x",
        ),
        ({"steps": {"t\x00": {"handler": "\x00"}}}, "flow.steps: key 't\\x00'"),
        ({"unknown": ["\x00"]}, "flow.unknown[0]"),
    ],
    ids=["handler", "fixed-param", "key", "unknown-field"],
)
def test_text_no_store_can_hold_is_refused_on_its_own(
    content: dict[str, object], path: str
) -> None:
    """A NUL anywhere in the structure, which is stored whole, is the one problem."""
    with pytest.raises(FlowDefinitionError) as caught:
        parse_flow({"name": "f", "version": "1.0.0", **content})

    (problem,) = caught.value.problems
    assert problem.startswith(path)
    assert "text holds NUL" in problem
    assert "\x00" not in problem


def test_nul_in_a_flow_file_is_refused() -> None:
    assert "text holds NUL" in problems(flow('t: {handler: "m:f\\0"}'))


def test_every_problem_is_reported_at_once() -> None:
    reported = problems(
        flow(
            "t: {handler: m:f, params: {x: tasks.missing, y: inputs.missing}}\n"
            "u: {handler: m:f, params: {x: neorc.index}}"
        )
    )

    assert len(reported.splitlines()) == 3


def test_references_inside_loops_and_fan_outs_are_not_cycles() -> None:
    load_flow_yaml(
        flow(
            """
            first: {handler: m:f}
            l:
              loop: {max_cycles: 3, exit_condition: done}
              steps:
                fan:
                  fan_out: {range: 2}
                  steps:
                    a: {handler: m:f, params: {x: tasks.first, i: neorc.index}}
                    b: {handler: m:f, params: {x: tasks.a}}
                done: {handler: m:f, params: {x: tasks.b}}
            after: {handler: m:f, params: {x: tasks.b}}
            """
        )
    )


def sub_flow_set(child_inputs: str, params: str) -> None:
    child = load_flow_yaml(
        f"name: child\nversion: 1.0.0\ninputs: {child_inputs}\n"
        "steps: {t: {handler: m:f}}"
    )
    parent = load_flow_yaml(
        f"name: parent\nversion: 1.0.0\ninputs: {{a: string}}\n"
        f"steps: {{s: {{flow: child, params: {params}}}}}"
    )
    validate_flow_set([parent, child])


def test_sub_flow_params_must_match_the_called_flow_inputs() -> None:
    sub_flow_set("{a: string}", "{a: inputs.a}")

    with pytest.raises(FlowDefinitionError, match="missing input 'b'"):
        sub_flow_set("{a: string, b: string}", "{a: inputs.a}")
    with pytest.raises(FlowDefinitionError, match="has no input 'a'"):
        sub_flow_set("{}", "{a: inputs.a}")


def test_flow_set_problems() -> None:
    def calls(name: str, callee: str) -> str:
        return f"name: {name}\nversion: 1.0.0\nsteps: {{s: {{flow: {callee}}}}}"

    loop = [load_flow_yaml(calls("a", "b")), load_flow_yaml(calls("b", "a"))]
    with pytest.raises(FlowDefinitionError, match="cycle: a -> b -> a"):
        validate_flow_set(loop)

    with pytest.raises(FlowDefinitionError, match="unknown flow 'nope'"):
        validate_flow_set([load_flow_yaml(calls("a", "nope"))])

    twice = load_flow_yaml("name: a\nversion: 1.0.0\nsteps: {t: {handler: m:f}}")
    with pytest.raises(FlowDefinitionError, match="more than once"):
        validate_flow_set([twice, twice])


def test_steps_wait_on_siblings_in_the_scope_they_share() -> None:
    picker = {f.name: f for f in load_flows(EXAMPLES / "wordplay" / "flows")}[
        "word_picker"
    ]

    assert picker.waits_on("compose_payload") == frozenset()
    # Inside the loop, pick_word refers out to compose_payload: the loop waits.
    assert picker.waits_on("rounds") == {"compose_payload"}
    assert picker.waits_on("pick_word") == frozenset()
    assert picker.waits_on("extract_word") == {"pick_word"}
    # Referring into a fan-out waits for the whole fan-out.
    assert picker.waits_on("collect_words") == {"picking"}
    assert picker.waits_on("latest_round") == {"rounds"}
    assert picker.waits_on("grading") == {"latest_round"}
    assert picker.waits_on("keep_matching_words") == {"grading"}
