# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Resolving references: the table in docs/specs/flows.md, against run states."""

from pathlib import Path
from textwrap import dedent

import pytest

from neorc_core import ResolutionError
from neorc_core._values import JsonValue
from neorc_core.flows import (
    Address,
    FanOutStep,
    FlowDefinition,
    Level,
    Namespace,
    Outcome,
    Reference,
    Resolved,
    RunState,
    StepResult,
    SubFlowStep,
    TaskStep,
    fan_out_width,
    load_flow_yaml,
    load_flows,
    resolve,
    shape,
)

EXAMPLES = Path(__file__).parents[3] / "examples"
WORDPLAY = {f.name: f for f in load_flows(EXAMPLES / "wordplay" / "flows")}
PICKER = WORDPLAY["word_picker"]
ROUNDS = WORDPLAY["word_picker_rounds"]

LOOP_SO_FAR = Level.ITERATIONS_SO_FAR
LOOP = Level.ITERATIONS
BRANCHES = Level.BRANCHES

TABLE = load_flow_yaml(
    dedent(
        """\
        name: table
        version: 1.0.0
        steps:
          fan:
            fan_out: {range: 2}
            steps:
              in_branch: {handler: m:f}
              same_branch: {handler: m:f, params: {x: tasks.in_branch}}
          after_fan: {handler: m:f, params: {x: tasks.in_branch}}
          repeat:
            loop: {max_cycles: 3, exit_condition: done}
            steps:
              in_loop: {handler: m:f}
              done: {handler: m:f, params: {x: tasks.in_loop}}
          after_loop: {handler: m:f, params: {x: tasks.in_loop}}
        """
    )
)


def ok(value: JsonValue) -> StepResult:
    return StepResult(Outcome.SUCCEEDED, value)


RUNNING = StepResult(Outcome.RUNNING)
FAILED = StepResult(Outcome.FAILED)


def at(step: str, *scope: tuple[str, int]) -> Address:
    return Address(step, tuple(scope))


def param(definition: FlowDefinition, consumer: str, name: str) -> Reference:
    step = definition.step(consumer)
    assert isinstance(step, TaskStep | SubFlowStep)
    return step.params[name]


def over(definition: FlowDefinition, fan_out: str) -> Reference:
    step = definition.step(fan_out)
    assert isinstance(step, FanOutStep) and step.over is not None
    return step.over


def tasks(name: str) -> Reference:
    return Reference(Namespace.TASKS, name)


def neorc(name: str) -> Reference:
    return Reference(Namespace.NEORC, name)


# The table, row by row.


@pytest.mark.parametrize(
    ("consumer", "expected"),
    [
        ("same_branch", ()),  # in the consumer's own fan-out branch
        ("after_fan", (BRANCHES,)),  # in a fan-out the consumer is outside of
        ("done", (LOOP_SO_FAR,)),  # in a loop the consumer is inside of
        ("after_loop", (LOOP,)),  # in a loop the consumer comes after
    ],
)
def test_each_row_of_the_table(consumer: str, expected: tuple[Level, ...]) -> None:
    assert shape(TABLE, consumer, param(TABLE, consumer, "x")) == expected


def test_a_branch_value_a_fan_in_and_the_iterations() -> None:
    state = RunState(
        steps={
            at("in_branch", ("fan", 1)): ok("b1"),
            at("in_branch", ("fan", 2)): ok("b2"),
            at("in_loop", ("repeat", 1)): ok("i1"),
            at("done", ("repeat", 1)): ok(False),
            at("in_loop", ("repeat", 2)): ok("i2"),
            at("done", ("repeat", 2)): ok(True),
        }
    )

    def value(consumer: Address) -> JsonValue:
        resolved = resolve(TABLE, state, consumer, param(TABLE, consumer.step, "x"))
        assert resolved is not None
        return resolved.value

    assert value(at("same_branch", ("fan", 2))) == "b2"
    assert value(at("after_fan")) == ["b1", "b2"]
    assert value(at("done", ("repeat", 1))) == ["i1"]
    assert value(at("done", ("repeat", 2))) == ["i1", "i2"]
    assert value(at("after_loop")) == ["i1", "i2"]


# Every reference in the wordplay examples.


@pytest.mark.parametrize(
    ("definition", "consumer", "reference", "expected"),
    [
        (PICKER, "pick_word", "payload", ()),
        (PICKER, "extract_word", "picks", (LOOP_SO_FAR,)),
        (PICKER, "collect_words", "all_rounds", (LOOP_SO_FAR, BRANCHES)),
        (PICKER, "enough_rounds", "collections", (LOOP_SO_FAR,)),
        (PICKER, "latest_round", "values", (LOOP,)),
        (PICKER, "score_words", "latest_round", ()),
        (PICKER, "score_words", "preferred_letter", ()),
        (PICKER, "score_words", "index", ()),
        (PICKER, "keep_matching_words", "all_scores", (BRANCHES,)),
        (ROUNDS, "ran_enough", "picker_results", (LOOP_SO_FAR,)),
        (ROUNDS, "final_words", "values", (LOOP,)),
        (ROUNDS, "pad", "word", ()),
        (ROUNDS, "long_enough", "pads", (LOOP_SO_FAR,)),
        (ROUNDS, "report", "padded", (BRANCHES, LOOP)),
    ],
    ids=lambda value: value.name if isinstance(value, FlowDefinition) else None,
)
def test_the_shape_of_every_wordplay_param(
    definition: FlowDefinition,
    consumer: str,
    reference: str,
    expected: tuple[Level, ...],
) -> None:
    assert shape(definition, consumer, param(definition, consumer, reference)) == (
        expected
    )


def test_the_shape_of_a_fan_out_list_and_of_an_output() -> None:
    assert shape(ROUNDS, "decorate", over(ROUNDS, "decorate")) == ()
    assert PICKER.output is not None
    assert shape(PICKER, None, PICKER.output) == ()


def picker_state(
    *iterations: tuple[list[str], bool], extra: dict[Address, StepResult] | None = None
) -> RunState:
    """word_picker after some iterations: each one's words and exit result."""
    steps = {at("compose_payload"): ok("payload")}
    for number, (words, exit_value) in enumerate(iterations, start=1):
        loop = ("rounds", number)
        for index, word in enumerate(words, start=1):
            branch = ("picking", index)
            steps[at("pick_word", loop, branch)] = ok(f"pick {word}")
            steps[at("extract_word", loop, branch)] = ok(word)
        steps[at("collect_words", loop)] = ok({"len": number})
        steps[at("enough_rounds", loop)] = ok(exit_value)
    steps.update(extra or {})
    return RunState(
        inputs={"sentence": "red green blue", "preferred_letter": "e"}, steps=steps
    )


def test_a_step_outside_every_container_is_a_single_value() -> None:
    state = picker_state()
    consumer = at("pick_word", ("rounds", 1), ("picking", 2))

    resolved = resolve(PICKER, state, consumer, param(PICKER, "pick_word", "payload"))

    assert resolved == Resolved("payload")


def test_same_branch_inside_a_loop_is_that_branch_over_the_iterations() -> None:
    state = picker_state((["a", "b", "c"], False), (["d", "e", "f"], False))
    consumer = at("extract_word", ("rounds", 2), ("picking", 3))

    resolved = resolve(PICKER, state, consumer, param(PICKER, "extract_word", "picks"))

    assert resolved == Resolved(["pick c", "pick f"])


def test_a_fan_in_inside_a_loop_is_iterations_of_branches() -> None:
    state = picker_state((["a", "b", "c"], False), (["d", "e", "f"], False))
    consumer = at("collect_words", ("rounds", 2))

    resolved = resolve(
        PICKER, state, consumer, param(PICKER, "collect_words", "all_rounds")
    )

    assert resolved == Resolved([["a", "b", "c"], ["d", "e", "f"]])


def test_iterations_so_far_stop_at_the_consumers_own() -> None:
    state = picker_state((["a", "b", "c"], False), (["d", "e", "f"], True))
    consumer = at("enough_rounds", ("rounds", 1))

    resolved = resolve(
        PICKER, state, consumer, param(PICKER, "enough_rounds", "collections")
    )

    assert resolved == Resolved([{"len": 1}])


@pytest.mark.parametrize("outcome", [RUNNING, FAILED])
def test_a_fan_in_waits_for_every_branch(outcome: StepResult) -> None:
    state = picker_state(
        (["a", "b", "c"], False),
        extra={at("extract_word", ("rounds", 1), ("picking", 2)): outcome},
    )
    consumer = at("collect_words", ("rounds", 1))

    resolved = resolve(
        PICKER, state, consumer, param(PICKER, "collect_words", "all_rounds")
    )

    assert resolved is None


def test_a_missing_branch_is_not_available_either() -> None:
    state = picker_state((["a", "b", "c"], False))
    steps = dict(state.steps)
    del steps[at("extract_word", ("rounds", 1), ("picking", 3))]
    consumer = at("collect_words", ("rounds", 1))

    resolved = resolve(
        PICKER,
        RunState(state.inputs, steps),
        consumer,
        param(PICKER, "collect_words", "all_rounds"),
    )

    assert resolved is None


@pytest.mark.parametrize(
    "last_exit",
    [ok(False), RUNNING, FAILED, ok("yes"), ok(1)],
    ids=["false", "running", "failed", "truthy-string", "one"],
)
def test_after_a_loop_waits_for_its_exit_task_to_return_true(
    last_exit: StepResult,
) -> None:
    state = picker_state(
        (["a", "b", "c"], False),
        extra={at("enough_rounds", ("rounds", 1)): last_exit},
    )

    resolved = resolve(
        PICKER, state, at("latest_round"), param(PICKER, "latest_round", "values")
    )

    assert resolved is None


def test_after_a_loop_is_every_iteration() -> None:
    state = picker_state(
        (["a", "b", "c"], False), (["d", "e", "f"], False), (["g", "h", "i"], True)
    )

    resolved = resolve(
        PICKER, state, at("latest_round"), param(PICKER, "latest_round", "values")
    )

    assert resolved == Resolved([{"len": 1}, {"len": 2}, {"len": 3}])


def test_a_loop_that_has_not_started_is_not_available() -> None:
    resolved = resolve(
        PICKER,
        picker_state(),
        at("latest_round"),
        param(PICKER, "latest_round", "values"),
    )

    assert resolved is None


def test_branches_are_ordered_by_index_not_by_when_they_finished() -> None:
    steps = {
        at("score_words", ("grading", 3)): ok({"c": 3}),
        at("score_words", ("grading", 1)): ok({"a": 1}),
        at("score_words", ("grading", 2)): ok({"b": 2}),
        at("keep_matching_words"): ok(["a", "b", "c"]),
    }
    state = RunState(steps=steps)

    fan_in = resolve(
        PICKER,
        state,
        at("keep_matching_words"),
        param(PICKER, "keep_matching_words", "all_scores"),
    )
    assert PICKER.output is not None
    output = resolve(PICKER, state, None, PICKER.output)

    assert fan_in == Resolved([{"a": 1}, {"b": 2}, {"c": 3}])
    assert output == Resolved(["a", "b", "c"])


def test_inputs_are_handed_on_in_their_json_form() -> None:
    requested_at: JsonValue = {"$datetime": "2026-09-13T10:00:00+00:00"}
    state = RunState(inputs={"requested_at": requested_at})

    resolved = resolve(
        ROUNDS, state, at("report"), param(ROUNDS, "report", "requested_at")
    )

    assert resolved == Resolved(requested_at)


def test_index_and_loop_count_come_from_the_consumers_address() -> None:
    consumer = at("pick_word", ("rounds", 4), ("picking", 2))
    pad = at("pad", ("decorate", 1), ("padding", 3))

    assert resolve(PICKER, RunState(), consumer, neorc("index")) == Resolved(2)
    assert resolve(ROUNDS, RunState(), pad, neorc("loop_count")) == Resolved(3)


def rounds_state(
    final_words: JsonValue,
    *pads: list[str],
    extra: dict[Address, StepResult] | None = None,
) -> RunState:
    """word_picker_rounds past its first loop, with each branch's pad iterations."""
    steps = {
        at("picker", ("runs", 1)): ok(["x"]),
        at("ran_enough", ("runs", 1)): ok(False),
        at("picker", ("runs", 2)): ok(["red", "blue"]),
        at("ran_enough", ("runs", 2)): ok(True),
        at("final_words"): ok(final_words),
    }
    for index, padded in enumerate(pads, start=1):
        for number, word in enumerate(padded, start=1):
            scope = (("decorate", index), ("padding", number))
            steps[Address("pad", scope)] = ok(word)
            steps[Address("long_enough", scope)] = ok(number == len(padded))
    steps.update(extra or {})
    return RunState(steps=steps)


def test_a_sub_flow_is_referred_to_like_a_task() -> None:
    state = rounds_state(["red", "blue"])
    picker = param(ROUNDS, "final_words", "values")

    inside = resolve(ROUNDS, state, at("ran_enough", ("runs", 2)), picker)
    after = resolve(ROUNDS, state, at("final_words"), picker)

    assert inside == Resolved([["x"], ["red", "blue"]])
    assert after == Resolved([["x"], ["red", "blue"]])


def test_a_fan_out_over_a_list_has_one_branch_per_element() -> None:
    state = rounds_state(["red", "blue"])
    decorate = ROUNDS.step("decorate")
    assert isinstance(decorate, FanOutStep)
    second = at("pad", ("decorate", 2), ("padding", 1))

    assert fan_out_width(ROUNDS, state, decorate, ()) == 2
    assert resolve(ROUNDS, state, second, neorc("item")) == Resolved("blue")


def test_the_width_of_a_fan_out_over_a_list_waits_for_the_list() -> None:
    state = RunState(steps={at("final_words"): RUNNING})
    decorate = ROUNDS.step("decorate")
    assert isinstance(decorate, FanOutStep)
    pad = at("pad", ("decorate", 1), ("padding", 1))

    assert fan_out_width(ROUNDS, state, decorate, ()) is None
    assert resolve(ROUNDS, state, pad, neorc("item")) is None
    assert resolve(ROUNDS, state, at("report"), param(ROUNDS, "report", "padded")) is (
        None
    )


def test_a_loop_in_each_branch_can_run_a_different_number_of_times() -> None:
    state = rounds_state(["red", "blue"], ["red*", "red**", "red***"], ["blue*"])

    resolved = resolve(ROUNDS, state, at("report"), param(ROUNDS, "report", "padded"))

    assert resolved == Resolved([["red*", "red**", "red***"], ["blue*"]])


def test_a_loop_inside_a_branch_stays_in_that_branch() -> None:
    state = rounds_state(["red", "blue"], ["red*", "red**"], ["blue*"])
    consumer = at("long_enough", ("decorate", 1), ("padding", 2))

    resolved = resolve(ROUNDS, state, consumer, param(ROUNDS, "long_enough", "pads"))

    assert resolved == Resolved(["red*", "red**"])


def test_a_fan_in_waits_for_a_loop_unfinished_in_one_branch() -> None:
    state = rounds_state(
        ["red", "blue"],
        ["red*"],
        ["blue*"],
        extra={at("long_enough", ("decorate", 2), ("padding", 1)): ok(False)},
    )

    resolved = resolve(ROUNDS, state, at("report"), param(ROUNDS, "report", "padded"))

    assert resolved is None


def test_a_fan_out_over_an_empty_list_fans_in_to_an_empty_list() -> None:
    state = rounds_state([])

    resolved = resolve(ROUNDS, state, at("report"), param(ROUNDS, "report", "padded"))

    assert resolved == Resolved([])


@pytest.mark.parametrize("final_words", ["red", {"red": 1}, None])
def test_a_fan_out_over_something_not_a_list_cannot_be_resolved(
    final_words: JsonValue,
) -> None:
    state = rounds_state(final_words)
    decorate = ROUNDS.step("decorate")
    assert isinstance(decorate, FanOutStep)

    with pytest.raises(ResolutionError, match="not a list"):
        fan_out_width(ROUNDS, state, decorate, ())
    with pytest.raises(ResolutionError, match="not a list"):
        resolve(ROUNDS, state, at("report"), param(ROUNDS, "report", "padded"))


def test_an_item_past_the_end_of_its_list_cannot_be_resolved() -> None:
    state = rounds_state(["red"])
    pad = at("pad", ("decorate", 2), ("padding", 1))

    with pytest.raises(ResolutionError, match="past the end"):
        resolve(ROUNDS, state, pad, neorc("item"))


@pytest.mark.parametrize(
    "consumer",
    [
        at("pick_word"),
        at("pick_word", ("rounds", 1)),
        at("pick_word", ("picking", 1), ("rounds", 1)),
        at("latest_round", ("rounds", 1)),
    ],
    ids=str,
)
def test_a_consumer_address_must_match_the_definition(consumer: Address) -> None:
    with pytest.raises(ValueError, match="does not match"):
        resolve(PICKER, picker_state(), consumer, tasks("compose_payload"))


@pytest.mark.parametrize("name", ["task_id", "flow_run_id", "started_by", "attempts"])
def test_metadata_known_only_to_the_manager_or_worker_is_not_resolved(
    name: str,
) -> None:
    with pytest.raises(ValueError, match="not resolved from a run's state"):
        resolve(ROUNDS, RunState(), at("report"), neorc(name))


def test_an_address_reads_as_its_path() -> None:
    assert str(at("pick_word", ("rounds", 2), ("picking", 3))) == (
        "rounds[2].picking[3].pick_word"
    )
    assert str(at("compose_payload")) == "compose_payload"


def test_the_last_iteration_or_index_started_within_a_scope() -> None:
    state = rounds_state(["red", "blue"], ["red*", "red**"], ["blue*"])

    assert state.last((), "runs") == 2
    assert state.last((), "decorate") == 2
    assert state.last((("decorate", 1),), "padding") == 2
    assert state.last((("decorate", 2),), "padding") == 1
    assert state.last((("decorate", 3),), "padding") == 0


GROWING = load_flow_yaml(
    dedent(
        """\
        name: growing
        version: 1.0.0
        steps:
          rounds:
            loop: {max_cycles: 3, exit_condition: done}
            steps:
              make_list: {handler: m:f}
              picking:
                fan_out: {over: tasks.make_list}
                steps:
                  pick: {handler: m:f}
                  use: {handler: m:f, params: {picks: tasks.pick}}
              done: {handler: m:f}
        """
    )
)


def test_a_branch_missing_from_an_earlier_iteration_cannot_be_resolved() -> None:
    """The fan-out's list is the loop's iterations so far, so it grows each time.

    Branch 2 of the second iteration has no first iteration to wait on, and
    waiting for it would never end.
    """
    steps = {
        at("make_list", ("rounds", 1)): ok("first"),
        at("pick", ("rounds", 1), ("picking", 1)): ok("1.1"),
        at("done", ("rounds", 1)): ok(False),
        at("make_list", ("rounds", 2)): ok("second"),
        at("pick", ("rounds", 2), ("picking", 1)): ok("2.1"),
        at("pick", ("rounds", 2), ("picking", 2)): ok("2.2"),
    }
    state = RunState(steps=steps)
    picking = GROWING.step("picking")
    assert isinstance(picking, FanOutStep)
    picks = param(GROWING, "use", "picks")

    assert fan_out_width(GROWING, state, picking, (("rounds", 1),)) == 1
    assert fan_out_width(GROWING, state, picking, (("rounds", 2),)) == 2
    first = resolve(GROWING, state, at("use", ("rounds", 2), ("picking", 1)), picks)
    assert first == Resolved(["1.1", "2.1"])

    with pytest.raises(ResolutionError, match=r"rounds\[1\]\.picking\[2\]\.pick"):
        resolve(GROWING, state, at("use", ("rounds", 2), ("picking", 2)), picks)


NESTED = load_flow_yaml(
    dedent(
        """\
        name: nested
        version: 1.0.0
        steps:
          outer:
            loop: {max_cycles: 5, exit_condition: outer_done}
            steps:
              inner:
                loop: {max_cycles: 5, exit_condition: inner_done}
                steps:
                  work: {handler: m:f}
                  use: {handler: m:f, params: {works: tasks.work}}
                  inner_done: {handler: m:f}
              outer_done: {handler: m:f}
        """
    )
)


def nested_state(*inner_runs: int) -> RunState:
    """Each outer iteration, with its inner loop run that many times."""
    steps = {}
    for outer, runs in enumerate(inner_runs, start=1):
        for inner in range(1, runs + 1):
            scope = (("outer", outer), ("inner", inner))
            steps[Address("work", scope)] = ok(f"{outer}.{inner}")
            steps[Address("inner_done", scope)] = ok(inner == runs)
        steps[at("outer_done", ("outer", outer))] = ok(False)
    return RunState(steps=steps)


@pytest.mark.parametrize(
    ("first_outer_runs", "expected_first"),
    [(1, ["1.1"]), (4, ["1.1", "1.2", "1.3", "1.4"])],
    ids=["fewer", "more"],
)
def test_an_inner_loop_counts_its_own_iterations_in_earlier_outer_ones(
    first_outer_runs: int, expected_first: JsonValue
) -> None:
    """Only the consumer's own outer iteration stops at its inner iteration."""
    state = nested_state(first_outer_runs, 2)
    consumer = at("use", ("outer", 2), ("inner", 2))

    resolved = resolve(NESTED, state, consumer, param(NESTED, "use", "works"))

    assert shape(NESTED, "use", param(NESTED, "use", "works")) == (
        LOOP_SO_FAR,
        LOOP_SO_FAR,
    )
    assert resolved == Resolved([expected_first, ["2.1", "2.2"]])
