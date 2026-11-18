# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""A switch: docs/specs/switch.md, section by section.

Reading and validating a switch, what its references resolve to, what the
planner does with it, and runs of it on ``LocalCluster``.
"""

import uuid
from collections.abc import Mapping
from pathlib import Path
from textwrap import dedent

import pytest

from neorc_core import FlowDefinitionError, ResolutionError, RunStatus
from neorc_core._planner import Action, FailRun, PublishTask, SucceedRun, plan
from neorc_core._values import JsonValue
from neorc_core.flows import (
    Address,
    Case,
    FlowDefinition,
    Namespace,
    Outcome,
    Reference,
    RunState,
    StepResult,
    SwitchStep,
    TaskStep,
    load_flow_yaml,
    parse_flow,
    read_flow_yaml,
    resolve,
    shape,
    switch_case,
)
from neorc_core.local import run_local

TIMEOUT = 10


def flow(steps: str, *, header: str = "") -> FlowDefinition:
    return load_flow_yaml(text(steps, header=header))


def text(steps: str, *, header: str = "") -> str:
    """A flow file with the given steps, indented under ``steps:``."""
    return (
        "name: f\nversion: 1.0.0\n"
        + dedent(header)
        + "steps:\n"
        + "".join(f"  {line}\n" for line in dedent(steps).splitlines())
    )


def problems(steps: str, *, header: str = "") -> str:
    """The problems reported for a flow, joined, or fail if it is valid."""
    with pytest.raises(FlowDefinitionError) as caught:
        flow(steps, header=header)
    return "\n".join(caught.value.problems)


def ok(value: JsonValue = None) -> StepResult:
    return StepResult(Outcome.SUCCEEDED, value)


RUNNING = StepResult(Outcome.RUNNING)


def at(step: str, *scope: tuple[str, int]) -> Address:
    return Address(step, tuple(scope))


def state(
    steps: Mapping[Address, StepResult], inputs: Mapping[str, JsonValue] | None = None
) -> RunState:
    return RunState(inputs or {}, dict(steps))


def published(actions: list[Action]) -> list[str]:
    """The addresses of the tasks the actions publish, as text."""
    assert all(isinstance(action, PublishTask) for action in actions), actions
    return [
        str(action.address) for action in actions if isinstance(action, PublishTask)
    ]


def tasks(name: str) -> Reference:
    return Reference(Namespace.TASKS, name)


def switches(name: str) -> Reference:
    return Reference(Namespace.SWITCHES, name)


def value_of(
    definition: FlowDefinition,
    run: RunState,
    consumer: Address | None,
    reference: Reference,
) -> JsonValue:
    resolved = resolve(definition, run, consumer, reference)
    assert resolved is not None, f"{reference} is not available to {consumer}"
    return resolved.value


# The flow of the spec: a gift for a VIP booking.

BOOKING_STEPS = """\
    load_booking:
      handler: tasks:load_booking
      params:
        booking_id: inputs.booking_id

    check_vip:
      handler: tasks:vip_case          # returns "is_vip" or "not_vip"
      params:
        booking: tasks.load_booking

    route:
      switch:
        on: tasks.check_vip
        cases:
          is_vip:
            steps:
              compute_benefit:
                handler: tasks:vip_benefit
                params:
                  booking: tasks.load_booking
              send_gift:
                handler: tasks:send_gift
                params:
                  booking: tasks.load_booking
                  benefit: tasks.compute_benefit
            output: tasks.send_gift
          not_vip:
            steps: {}
            output: null

    confirm:
      handler: tasks:confirm
      params:
        notice: switches.route
    """
BOOKING = flow(BOOKING_STEPS, header="inputs: {booking_id: number}\n")

LOADED = {at("load_booking"): ok({"id": 7}), at("check_vip"): ok("is_vip")}
NOT_VIP = {at("load_booking"): ok({"id": 7}), at("check_vip"): ok("not_vip")}


def booking(steps: Mapping[Address, StepResult]) -> RunState:
    return state(steps, {"booking_id": 7})


def test_the_flow_of_the_spec_is_modelled() -> None:
    route = BOOKING.step("route")

    assert isinstance(route, SwitchStep)
    assert route.on == tasks("check_vip")
    assert [case.name for case in route.cases] == ["is_vip", "not_vip"]
    is_vip = route.case("is_vip")
    assert is_vip is not None and is_vip.output == tasks("send_gift")
    assert [step.name for step in is_vip.steps] == ["compute_benefit", "send_gift"]
    assert route.case("not_vip") == Case("not_vip", (), None)
    assert route.case("gold") is None
    # The steps of a case are steps of the flow, in no loop or fan-out.
    assert isinstance(BOOKING.step("send_gift"), TaskStep)
    assert BOOKING.enclosing("send_gift") == ()
    assert [str(inside) for inside in BOOKING.cases_around("send_gift")] == [
        "case 'is_vip' of route"
    ]
    assert {task.name for task in BOOKING.tasks()} == {
        "load_booking",
        "check_vip",
        "compute_benefit",
        "send_gift",
        "confirm",
    }


def test_a_yaml_switch_and_its_json_upload_are_the_same_flow() -> None:
    source = text(BOOKING_STEPS, header="inputs: {booking_id: number}\n")

    assert parse_flow(read_flow_yaml(source)) == BOOKING


# Choosing a case.


def test_a_switch_waits_for_what_on_reads_and_what_its_cases_read() -> None:
    assert BOOKING.waits_on("route") == {"check_vip", "load_booking"}
    assert BOOKING.waits_on("compute_benefit") == frozenset()
    assert BOOKING.waits_on("send_gift") == {"compute_benefit"}
    assert BOOKING.waits_on("confirm") == {"route"}


def test_nothing_of_a_switch_starts_before_on_has_a_value() -> None:
    assert published(plan(BOOKING, booking({}))) == ["load_booking"]
    waiting = {at("load_booking"): ok({"id": 7}), at("check_vip"): RUNNING}

    assert plan(BOOKING, booking(waiting)) == []
    assert switch_case(BOOKING, booking(waiting), _route(), ()) is None


def _route() -> SwitchStep:
    route = BOOKING.step("route")
    assert isinstance(route, SwitchStep)
    return route


def test_the_steps_of_the_chosen_case_run_and_no_other() -> None:
    assert published(plan(BOOKING, booking(LOADED))) == ["compute_benefit"]
    benefit = {**LOADED, at("compute_benefit"): ok("champagne")}
    assert published(plan(BOOKING, booking(benefit))) == ["send_gift"]

    # A case with no steps: the switch has finished as soon as it is chosen.
    assert published(plan(BOOKING, booking(NOT_VIP))) == ["confirm"]


def test_a_switch_finishes_once_every_step_of_the_chosen_case_succeeded() -> None:
    sending = {
        **LOADED,
        at("compute_benefit"): ok("champagne"),
        at("send_gift"): RUNNING,
    }
    assert plan(BOOKING, booking(sending)) == []

    sent = {**sending, at("send_gift"): ok("sent")}
    assert published(plan(BOOKING, booking(sent))) == ["confirm"]
    assert plan(BOOKING, booking({**sent, at("confirm"): ok()})) == [SucceedRun(None)]
    # The other case never ran, and the run is over all the same.
    assert plan(BOOKING, booking({**NOT_VIP, at("confirm"): ok()})) == [
        SucceedRun(None)
    ]


@pytest.mark.parametrize(
    ("chosen", "given"),
    [
        ("gold", "'gold'"),
        ("IS_VIP", "'IS_VIP'"),
        ("x" * 100, "'" + "x" * 60 + "'..."),
        (True, "a bool"),
        (None, "null"),
        (1, "a int"),
        (["is_vip"], "a list"),
        ({"case": "is_vip"}, "a dict"),
    ],
    ids=["unknown", "other-case", "long", "boolean", "null", "number", "list", "map"],
)
def test_an_on_that_names_no_case_fails_the_run(chosen: JsonValue, given: str) -> None:
    steps = {at("load_booking"): ok({"id": 7}), at("check_vip"): ok(chosen)}

    assert plan(BOOKING, booking(steps)) == [
        FailRun(
            f"route: switch.on tasks.check_vip is {given}, not one of its cases: "
            "is_vip, not_vip"
        )
    ]
    with pytest.raises(ResolutionError, match="not one of its cases"):
        resolve(BOOKING, booking(steps), at("confirm"), switches("route"))


def test_on_reads_a_string_input() -> None:
    by_input = flow(
        """\
        route:
          switch:
            on: inputs.plan
            cases:
              paid:
                steps: {bill: {handler: m:bill}}
                output: tasks.bill
              free:
                steps: {}
                output: null
        """,
        header="inputs: {plan: string}\noutput: switches.route\n",
    )

    assert published(plan(by_input, state({}, {"plan": "paid"}))) == ["bill"]
    assert plan(by_input, state({}, {"plan": "free"})) == [
        SucceedRun(switches("route"))
    ]
    assert plan(by_input, state({}, {"plan": "gold"})) == [
        FailRun(
            "route: switch.on inputs.plan is 'gold', not one of its cases: free, paid"
        )
    ]


def test_on_reads_a_sub_flow_and_another_switch() -> None:
    chained = flow(
        """\
        classify: {flow: classifier}
        first:
          switch:
            on: flows.classify
            cases:
              a:
                steps: {name_b: {handler: m:f}}
                output: tasks.name_b
              b:
                steps: {}
                output: null
        second:
          switch:
            on: switches.first
            cases:
              left:
                steps: {go_left: {handler: m:f}}
                output: tasks.go_left
              right:
                steps: {}
                output: null
        """
    )
    assert chained.waits_on("first") == {"classify"}
    assert chained.waits_on("second") == {"first"}

    chose_a = {at("classify"): ok("a")}
    assert published(plan(chained, state(chose_a))) == ["name_b"]
    # The second switch reads the first one's output: the result of name_b.
    named = {**chose_a, at("name_b"): ok("left")}
    assert published(plan(chained, state(named))) == ["go_left"]
    # The first switch's output is null in case b, which names no case.
    assert plan(chained, state({at("classify"): ok("b")})) == [
        FailRun(
            "second: switch.on switches.first is null, not one of its cases: "
            "left, right"
        )
    ]


IN_FAN_OUT = flow(
    """\
    tier: {handler: m:tier}
    each:
      fan_out: {range: 2}
      steps:
        kind: {handler: m:kind, params: {i: neorc.index}}
        by_tier:
          switch:
            on: tasks.tier
            cases:
              gold:
                steps: {polish: {handler: m:polish, params: {i: neorc.index}}}
                output: tasks.polish
              plain:
                steps: {}
                output: null
        by_kind:
          switch:
            on: tasks.kind
            cases:
              odd:
                steps: {handle_odd: {handler: m:odd, params: {kind: tasks.kind}}}
                output: tasks.handle_odd
              even:
                steps: {handle_even: {handler: m:even}}
                output: tasks.handle_even
    gather:
      handler: m:gather
      params: {tiers: switches.by_tier, kinds: switches.by_kind}
    """
)


def test_a_task_before_a_fan_out_chooses_a_case_in_each_branch() -> None:
    assert IN_FAN_OUT.waits_on("each") == {"tier"}
    assert IN_FAN_OUT.waits_on("by_tier") == frozenset()

    gold = plan(IN_FAN_OUT, state({at("tier"): ok("gold")}))
    assert published(gold) == [
        "each[1].kind",
        "each[1].polish",
        "each[2].kind",
        "each[2].polish",
    ]
    plain = plan(IN_FAN_OUT, state({at("tier"): ok("plain")}))
    assert published(plain) == ["each[1].kind", "each[2].kind"]


def test_each_branch_of_a_fan_out_chooses_its_own_case() -> None:
    steps = {
        at("tier"): ok("plain"),
        at("kind", ("each", 1)): ok("odd"),
        at("kind", ("each", 2)): ok("even"),
    }

    assert published(plan(IN_FAN_OUT, state(steps))) == [
        "each[1].handle_odd",
        "each[2].handle_even",
    ]
    # Inside the case, a step of the branch is that branch's single value.
    assert shape(IN_FAN_OUT, "handle_odd", tasks("kind")) == ()
    assert (
        value_of(IN_FAN_OUT, state(steps), at("handle_odd", ("each", 1)), tasks("kind"))
        == "odd"
    )


def test_a_switch_in_a_fan_out_is_read_after_it_as_one_output_per_branch() -> None:
    steps = {
        at("tier"): ok("plain"),
        at("kind", ("each", 1)): ok("odd"),
        at("kind", ("each", 2)): ok("even"),
        at("handle_odd", ("each", 1)): ok("1 is odd"),
    }
    assert shape(IN_FAN_OUT, "gather", switches("by_kind")) == ("branches",)
    # Branch 2 has not finished its case: the fan-in is not available yet.
    assert resolve(IN_FAN_OUT, state(steps), at("gather"), switches("by_kind")) is None
    assert plan(IN_FAN_OUT, state(steps)) == [
        PublishTask(at("handle_even", ("each", 2)), "default", "m:even", {}, {})
    ]

    steps[at("handle_even", ("each", 2))] = ok("2 is even")
    run = state(steps)
    assert value_of(IN_FAN_OUT, run, at("gather"), switches("by_kind")) == [
        "1 is odd",
        "2 is even",
    ]
    assert value_of(IN_FAN_OUT, run, at("gather"), switches("by_tier")) == [None, None]
    assert published(plan(IN_FAN_OUT, run)) == ["gather"]


# Cases and output.


def test_switches_resolves_to_the_output_of_the_chosen_case() -> None:
    confirm, route = at("confirm"), switches("route")
    assert shape(BOOKING, "confirm", route) == ()
    # Not chosen yet, then chosen and its output not there yet.
    assert resolve(BOOKING, booking({}), confirm, route) is None
    assert resolve(BOOKING, booking(LOADED), confirm, route) is None

    sent = {
        **LOADED,
        at("compute_benefit"): ok("champagne"),
        at("send_gift"): ok("sent"),
    }
    assert value_of(BOOKING, booking(sent), confirm, route) == "sent"
    # A case states its result, even nothing.
    assert value_of(BOOKING, booking(NOT_VIP), confirm, route) is None


def test_a_flow_returns_the_output_of_a_switch() -> None:
    returning = flow(
        BOOKING_STEPS,
        header="inputs: {booking_id: number}\noutput: switches.route\n",
    )
    done = {
        **LOADED,
        at("compute_benefit"): ok("champagne"),
        at("send_gift"): ok("sent"),
        at("confirm"): ok(),
    }

    assert plan(returning, booking(done)) == [SucceedRun(switches("route"))]
    assert value_of(returning, booking(done), None, switches("route")) == "sent"


IN_CASE = flow(
    """\
    route:
      switch:
        on: inputs.how
        cases:
          each:
            steps:
              fan:
                fan_out: {range: 2}
                steps: {one: {handler: m:one, params: {i: neorc.index}}}
            output: tasks.one
          again:
            steps:
              repeat:
                loop: {max_cycles: 3, exit_condition: done}
                steps:
                  attempt: {handler: m:attempt}
                  done: {handler: m:done, params: {attempts: tasks.attempt}}
            output: tasks.attempt
    after: {handler: m:after, params: {all: switches.route}}
    """,
    header="inputs: {how: string}\n",
)


def test_a_fan_out_inside_the_case_adds_its_list_level_to_the_output() -> None:
    each = {"how": "each"}
    assert published(plan(IN_CASE, state({}, each))) == ["fan[1].one", "fan[2].one"]

    first = {at("one", ("fan", 1)): ok("a")}
    assert resolve(IN_CASE, state(first, each), at("after"), switches("route")) is None
    assert plan(IN_CASE, state({**first, at("one", ("fan", 2)): RUNNING}, each)) == []

    both = state({**first, at("one", ("fan", 2)): ok("b")}, each)
    assert value_of(IN_CASE, both, at("after"), switches("route")) == ["a", "b"]
    assert published(plan(IN_CASE, both)) == ["after"]


def test_a_loop_inside_the_case_adds_its_list_level_to_the_output() -> None:
    again = {"how": "again"}
    assert published(plan(IN_CASE, state({}, again))) == ["repeat[1].attempt"]
    once = {at("attempt", ("repeat", 1)): ok("x"), at("done", ("repeat", 1)): ok(False)}
    # The loop inside the case has not finished: nor has the switch.
    assert resolve(IN_CASE, state(once, again), at("after"), switches("route")) is None
    assert published(plan(IN_CASE, state(once, again))) == ["repeat[2].attempt"]

    twice = {
        **once,
        at("attempt", ("repeat", 2)): ok("y"),
        at("done", ("repeat", 2)): ok(True),
    }
    run = state(twice, again)
    assert value_of(IN_CASE, run, at("after"), switches("route")) == ["x", "y"]
    assert published(plan(IN_CASE, run)) == ["after"]


NESTED = flow(
    """\
    outer:
      switch:
        on: inputs.kind
        cases:
          parcel:
            steps:
              weigh: {handler: m:weigh}
              inner:
                switch:
                  on: tasks.weigh
                  cases:
                    heavy:
                      steps: {crate: {handler: m:crate, params: {w: tasks.weigh}}}
                      output: tasks.crate
                    light:
                      steps: {}
                      output: null
              label: {handler: m:label, params: {packed: switches.inner}}
            output: switches.inner
          letter:
            steps: {}
            output: null
    """,
    header="inputs: {kind: string}\noutput: switches.outer\n",
)


def test_a_switch_inside_a_case_chooses_on_a_step_of_that_case() -> None:
    parcel = {"kind": "parcel"}
    assert NESTED.waits_on("inner") == {"weigh"}
    assert NESTED.waits_on("label") == {"inner"}
    assert [str(c) for c in NESTED.cases_around("crate")] == [
        "case 'parcel' of outer",
        "case 'heavy' of inner",
    ]

    assert published(plan(NESTED, state({}, parcel))) == ["weigh"]
    heavy = {at("weigh"): ok("heavy")}
    assert published(plan(NESTED, state(heavy, parcel))) == ["crate"]
    crated = {**heavy, at("crate"): ok("crated")}
    assert published(plan(NESTED, state(crated, parcel))) == ["label"]

    done = state({**crated, at("label"): ok()}, parcel)
    assert plan(NESTED, done) == [SucceedRun(switches("outer"))]
    # The outer case's output is the inner switch's.
    assert value_of(NESTED, done, None, switches("outer")) == "crated"
    assert plan(NESTED, state({}, {"kind": "letter"})) == [
        SucceedRun(switches("outer"))
    ]


# Inside a loop.

ROUNDS = flow(
    """\
    rounds:
      loop: {max_cycles: 3, exit_condition: done}
      steps:
        check_vip: {handler: m:check, params: {round: neorc.loop_count}}
        route:
          switch:
            on: tasks.check_vip
            cases:
              is_vip:
                steps:
                  compute_benefit: {handler: m:benefit}
                  send_gift:
                    handler: m:send
                    params: {benefits: tasks.compute_benefit, checks: tasks.check_vip}
                output: tasks.send_gift
              not_vip:
                steps: {}
                output: null
        done: {handler: m:done, params: {notices: switches.route}}
    after: {handler: m:after, params: {notices: switches.route}}
    """
)

ROUND_1_NOT_VIP = {
    at("check_vip", ("rounds", 1)): ok("not_vip"),
    at("done", ("rounds", 1)): ok(False),
}


def test_a_switch_in_a_loop_chooses_a_case_again_in_each_iteration() -> None:
    first = {at("check_vip", ("rounds", 1)): ok("not_vip")}
    assert published(plan(ROUNDS, state(first))) == ["rounds[1].done"]
    assert published(plan(ROUNDS, state(ROUND_1_NOT_VIP))) == ["rounds[2].check_vip"]

    # on reads its task in the switch's own iteration: one value, no list.
    second = {**ROUND_1_NOT_VIP, at("check_vip", ("rounds", 2)): ok("is_vip")}
    assert published(plan(ROUNDS, state(second))) == ["rounds[2].compute_benefit"]
    route = ROUNDS.step("route")
    assert isinstance(route, SwitchStep)
    chosen = switch_case(ROUNDS, state(second), route, (("rounds", 2),))
    assert chosen is not None and chosen.name == "is_vip"


def test_an_iteration_where_the_case_did_not_run_gives_null() -> None:
    """The example of the spec: not_vip in iteration 1, is_vip in iteration 2."""
    steps = {
        **ROUND_1_NOT_VIP,
        at("check_vip", ("rounds", 2)): ok("is_vip"),
        at("compute_benefit", ("rounds", 2)): ok("champagne"),
    }
    send_gift = at("send_gift", ("rounds", 2))

    assert shape(ROUNDS, "send_gift", tasks("compute_benefit")) == (
        "iterations_so_far",
    )
    assert value_of(ROUNDS, state(steps), send_gift, tasks("compute_benefit")) == [
        None,
        "champagne",
    ]
    # A step outside the switch is every iteration's, as for any step of the loop.
    assert value_of(ROUNDS, state(steps), send_gift, tasks("check_vip")) == [
        "not_vip",
        "is_vip",
    ]
    assert published(plan(ROUNDS, state(steps))) == ["rounds[2].send_gift"]


def test_a_case_that_ran_in_every_iteration_reads_as_any_step_of_the_loop() -> None:
    steps = {
        at("check_vip", ("rounds", 1)): ok("is_vip"),
        at("compute_benefit", ("rounds", 1)): ok("flowers"),
        at("send_gift", ("rounds", 1)): ok("sent flowers"),
        at("done", ("rounds", 1)): ok(False),
        at("check_vip", ("rounds", 2)): ok("is_vip"),
    }
    send_gift, benefit = at("send_gift", ("rounds", 2)), tasks("compute_benefit")
    # This iteration's result is not there yet: neither is the list.
    assert resolve(ROUNDS, state(steps), send_gift, benefit) is None

    steps[at("compute_benefit", ("rounds", 2))] = ok("champagne")
    assert value_of(ROUNDS, state(steps), send_gift, benefit) == [
        "flowers",
        "champagne",
    ]


def test_a_switch_has_an_output_in_every_iteration() -> None:
    steps = {
        **ROUND_1_NOT_VIP,
        at("check_vip", ("rounds", 2)): ok("is_vip"),
        at("compute_benefit", ("rounds", 2)): ok("champagne"),
        at("send_gift", ("rounds", 2)): ok("sent champagne"),
    }
    done, route = at("done", ("rounds", 2)), switches("route")

    assert shape(ROUNDS, "done", route) == ("iterations_so_far",)
    assert value_of(
        ROUNDS, state(ROUND_1_NOT_VIP), at("done", ("rounds", 1)), route
    ) == [None]
    # The case's output is its step's result in the switch's own iteration.
    assert value_of(ROUNDS, state(steps), done, route) == [None, "sent champagne"]
    assert published(plan(ROUNDS, state(steps))) == ["rounds[2].done"]

    # After the loop: one entry per iteration, once the loop has finished.
    assert shape(ROUNDS, "after", route) == ("iterations",)
    assert resolve(ROUNDS, state(steps), at("after"), route) is None
    steps[at("done", ("rounds", 2))] = ok(True)
    assert value_of(ROUNDS, state(steps), at("after"), route) == [
        None,
        "sent champagne",
    ]
    assert published(plan(ROUNDS, state(steps))) == ["after"]


def test_a_case_missing_from_an_earlier_outer_iteration_gives_null_there() -> None:
    nested = flow(
        """\
        outer:
          loop: {max_cycles: 3, exit_condition: outer_done}
          steps:
            inner:
              loop: {max_cycles: 3, exit_condition: inner_done}
              steps:
                pick: {handler: m:pick}
                route:
                  switch:
                    on: tasks.pick
                    cases:
                      a:
                        steps:
                          first: {handler: m:first}
                          second: {handler: m:second, params: {firsts: tasks.first}}
                        output: tasks.second
                      b:
                        steps: {}
                        output: null
                inner_done: {handler: m:done, params: {x: switches.route}}
            outer_done: {handler: m:done, params: {x: switches.route}}
        """
    )

    def inner(outer: int, number: int) -> tuple[tuple[str, int], tuple[str, int]]:
        return ("outer", outer), ("inner", number)

    steps = {
        # The first outer iteration: case b, then case a, and the inner loop ends.
        at("pick", *inner(1, 1)): ok("b"),
        at("inner_done", *inner(1, 1)): ok(False),
        at("pick", *inner(1, 2)): ok("a"),
        at("first", *inner(1, 2)): ok("f12"),
        at("second", *inner(1, 2)): ok("s12"),
        at("inner_done", *inner(1, 2)): ok(True),
        at("outer_done", ("outer", 1)): ok(False),
        # The second: case a at once.
        at("pick", *inner(2, 1)): ok("a"),
        at("first", *inner(2, 1)): ok("f21"),
    }
    second = at("second", *inner(2, 1))

    assert shape(nested, "second", tasks("first")) == (
        "iterations_so_far",
        "iterations_so_far",
    )
    assert value_of(nested, state(steps), second, tasks("first")) == [
        [None, "f12"],
        ["f21"],
    ]
    assert published(plan(nested, state(steps))) == ["outer[2].inner[1].second"]
    # The switch read from the outer loop: a list per outer iteration.
    steps[second] = ok("s21")
    steps[at("inner_done", *inner(2, 1))] = ok(True)
    outer_done = at("outer_done", ("outer", 2))
    assert value_of(nested, state(steps), outer_done, switches("route")) == [
        [None, "s12"],
        ["s21"],
    ]


# What a flow file may say of a switch, and what it is told when it may not.


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        ("s: {switch: tasks.t}", "steps.s.switch: must be a mapping"),
        (
            "s: {switch: {cases: {a: {steps: {}, output: null}}}}",
            "steps.s.switch: missing field 'on'",
        ),
        ("s: {switch: {on: inputs.x}}", "steps.s.switch: missing field 'cases'"),
        (
            "s: {switch: {on: inputs.x, default: a, "
            "cases: {a: {steps: {}, output: null}}}}",
            "steps.s.switch: unknown field 'default'",
        ),
        (
            "s: {switch: {on: 3, cases: {a: {steps: {}, output: null}}}}",
            "not a reference",
        ),
        (
            "s: {switch: {on: inputs.x, cases: {}}}",
            "steps.s.switch.cases: must be a non-empty mapping of case names to cases",
        ),
        (
            "s: {switch: {on: inputs.x, cases: [a, b]}}",
            "steps.s.switch.cases: must be a non-empty mapping",
        ),
        (
            "s: {switch: {on: inputs.x, cases: {a: null}}}",
            "steps.s.switch.cases.a: must be a mapping with 'steps' and 'output'",
        ),
        (
            "s: {switch: {on: inputs.x, cases: {a: {output: null}}}}",
            "steps.s.switch.cases.a: missing field 'steps'",
        ),
        (
            "s: {switch: {on: inputs.x, cases: {a: {steps: {}}}}}",
            "steps.s.switch.cases.a: missing field 'output'",
        ),
        (
            "s: {switch: {on: inputs.x, "
            "cases: {a: {steps: {}, output: null, when: 1}}}}",
            "steps.s.switch.cases.a: unknown field 'when'",
        ),
        (
            "s: {switch: {on: inputs.x, cases: {a: {steps: [], output: null}}}}",
            "steps.s.switch.cases.a.steps: must be a mapping of step names to steps",
        ),
        (
            "s: {switch: {on: inputs.x, cases: {a: {steps: {}, output: 3}}}}",
            "steps.s.switch.cases.a.output: 3 is not a reference",
        ),
        (
            "s: {switch: {on: inputs.x, cases: {my-case: {steps: {}, output: null}}}}",
            "steps.s.switch.cases.my-case: 'my-case' is not a name",
        ),
        (
            "s: {switch: {on: inputs.x, cases: {1st: {steps: {}, output: null}}}}",
            "'1st' is not a name",
        ),
        (
            "s:\n  switch: {on: inputs.x, cases: {a: {steps: {}, output: null}}}\n"
            "  steps: {t: {handler: m:f}}",
            "steps.s: unknown field 'steps'",
        ),
        (
            "s:\n  handler: m:f\n"
            "  switch: {on: inputs.x, cases: {a: {steps: {}, output: null}}}",
            "exactly one of handler, loop, fan_out, flow, switch",
        ),
    ],
    ids=[
        "not-a-mapping",
        "no-on",
        "no-cases",
        "unknown-field",
        "on-not-a-reference",
        "no-case",
        "cases-not-a-mapping",
        "case-not-a-mapping",
        "case-without-steps",
        "case-without-output",
        "unknown-case-field",
        "case-steps-not-a-mapping",
        "output-not-a-reference",
        "case-name-with-a-dash",
        "case-name-starting-with-a-digit",
        "steps-beside-switch",
        "two-kinds",
    ],
)
def test_switch_shape_problems(steps: str, expected: str) -> None:
    assert expected in problems(steps, header="inputs: {x: string}\n")


def test_a_case_named_true_or_false_needs_quotes_in_yaml() -> None:
    unquoted = (
        "s: {switch: {on: inputs.x, cases: {true: {steps: {}, output: null}, "
        "false: {steps: {}, output: null}}}}"
    )
    reported = problems(unquoted, header="inputs: {x: string}\n")
    assert (
        "steps.s.switch.cases.True: true is read as a boolean: write it in quotes, "
        "'true', to name a case"
    ) in reported
    assert "'false', to name a case" in reported

    quoted = flow(
        "s: {switch: {on: inputs.x, cases: {'true': {steps: {}, output: null}, "
        '"false": {steps: {}, output: null}}}}',
        header="inputs: {x: string}\n",
    )
    switch = quoted.step("s")
    assert isinstance(switch, SwitchStep)
    assert [case.name for case in switch.cases] == ["true", "false"]
    assert plan(quoted, state({}, {"x": "true"})) == [SucceedRun(None)]


def test_a_json_upload_names_a_case_true_as_text() -> None:
    case: dict[str, object] = {"steps": {}, "output": None}
    uploaded = parse_flow(
        {
            "name": "f",
            "version": "1.0.0",
            "inputs": {"x": "string"},
            "steps": {"s": {"switch": {"on": "inputs.x", "cases": {"true": case}}}},
        }
    )

    assert uploaded.step("s") == SwitchStep(
        "s", Reference(Namespace.INPUTS, "x"), (Case("true", (), None),)
    )


SWITCH = "switch: {on: inputs.x, cases: {a: {steps: {}, output: null}}}"
"""A valid switch, for the flows below to put a step beside."""


def one_case(steps: str, output: str = "null", on: str = "inputs.x") -> str:
    """A switch ``s`` with one case ``a``, holding the given steps."""
    case = f"{{steps: {steps}, output: {output}}}"
    return f"s: {{switch: {{on: {on}, cases: {{a: {case}}}}}}}"


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        (
            one_case("{}", on="inputs.missing"),
            "s.switch.on: the flow has no input 'missing'",
        ),
        (
            one_case("{}", on="inputs.n"),
            "s.switch.on: inputs.n is a number, and on takes only string inputs",
        ),
        (
            one_case("{}", on="inputs.when"),
            "inputs.when is a datetime, and on takes only string inputs",
        ),
        (
            one_case("{}", on="neorc.index"),
            "s.switch.on: neorc.index must refer to inputs., tasks., flows. or "
            "switches.",
        ),
        (one_case("{}", on="tasks.missing"), "s.switch.on: there is no step 'missing'"),
        (one_case("{}", on="switches.s"), "s.switch.on: a step cannot refer to itself"),
        (
            one_case("{t: {handler: m:f}}", on="tasks.t"),
            "s.switch.on: tasks.t is inside the switch it chooses a case of",
        ),
        (
            "l:\n  loop: {max_cycles: 2, exit_condition: t}\n"
            "  steps: {t: {handler: m:f}}\n" + one_case("{}", on="tasks.t"),
            "s.switch.on: tasks.t is inside the loop l, which s is outside of, and "
            "on reads one value",
        ),
        (
            "fan:\n  fan_out: {range: 2}\n  steps: {t: {handler: m:f}}\n"
            + one_case("{}", on="tasks.t"),
            "s.switch.on: tasks.t is inside the fan-out fan, which s is outside of",
        ),
        (
            f"other:\n  {SWITCH}\nt: {{handler: m:f}}\n"
            + one_case("{}", on="tasks.other"),
            "s.switch.on: 'other' is not a task; tasks. refers to tasks",
        ),
        (
            "t: {handler: m:f}\n" + one_case("{}", on="switches.t"),
            "s.switch.on: 't' is not a switch; switches. refers to them",
        ),
        (
            "t: {handler: m:f, params: {x: switches.s}}\n"
            + one_case("{}", on="tasks.t"),
            "steps wait on each other in the flow: s -> t -> s",
        ),
    ],
    ids=[
        "unknown-input",
        "number-input",
        "datetime-input",
        "metadata",
        "unknown-step",
        "itself",
        "a-step-of-its-own-case",
        "in-a-loop-the-switch-is-outside-of",
        "in-a-fan-out-the-switch-is-outside-of",
        "tasks-to-a-switch",
        "switches-to-a-task",
        "cycle",
    ],
)
def test_what_on_may_not_refer_to(steps: str, expected: str) -> None:
    header = "inputs: {x: string, n: number, when: datetime}\n"

    assert expected in problems(steps, header=header)


def test_on_does_not_read_a_task_with_a_form() -> None:
    """Its result is a mapping, whichever of the two comes first in the file."""
    form = [
        {
            "version": "v0.9",
            "createSurface": {
                "surfaceId": "ask",
                "catalogId": "https://a2ui.org/specification/v0_9/basic_catalog.json",
            },
        }
    ]
    data = {
        "name": "f",
        "version": "1.0.0",
        "steps": {
            "route": {
                "switch": {
                    "on": "tasks.ask",
                    "cases": {"yes": {"steps": {}, "output": None}},
                }
            },
            "ask": {"handler": "ask", "queue": "editors", "form": form},
        },
    }

    with pytest.raises(FlowDefinitionError) as refused:
        parse_flow(data)

    assert (
        "steps.route.switch.on: ask has a form, so its result is a mapping, and on "
        "gives the name of a case"
    ) in refused.value.problems


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        (
            "t: {handler: m:f}\n" + one_case("{u: {handler: m:f}}", "tasks.t"),
            "s.switch.cases.a.output: tasks.t is not a step inside case 'a' of s",
        ),
        (
            one_case("{}", "tasks.missing"),
            "s.switch.cases.a.output: there is no step 'missing'",
        ),
        (
            one_case("{}", "inputs.x"),
            "s.switch.cases.a.output: inputs.x must be null, or refer to a step "
            "inside the case",
        ),
        (one_case("{}", "switches.s"), "switches.s is not a step inside case 'a' of s"),
        (
            one_case("{u: {flow: other}}", "tasks.u"),
            "s.switch.cases.a.output: 'u' is not a task",
        ),
        (
            "s:\n  switch:\n    on: inputs.x\n    cases:\n"
            "      a: {steps: {t: {handler: m:f}}, output: tasks.t}\n"
            "      b: {steps: {u: {handler: m:f}}, output: tasks.t}",
            "s.switch.cases.b.output: tasks.t is not a step inside case 'b' of s",
        ),
        (
            "s:\n  switch:\n    on: inputs.x\n    cases:\n      a:\n        steps:\n"
            "          inner:\n            switch:\n              on: inputs.x\n"
            "              cases:\n"
            "                deep: {steps: {t: {handler: m:f}}, output: tasks.t}\n"
            "        output: tasks.t",
            "s.switch.cases.a.output: tasks.t is inside case 'deep' of inner; "
            "outside inner, read switches.inner",
        ),
    ],
    ids=[
        "a-step-outside-the-switch",
        "unknown-step",
        "an-input",
        "the-switch-itself",
        "wrong-kind",
        "a-step-of-another-case",
        "a-step-of-a-deeper-case",
    ],
)
def test_what_a_case_output_may_not_refer_to(steps: str, expected: str) -> None:
    assert expected in problems(steps, header="inputs: {x: string}\n")


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        (
            one_case("{t: {handler: m:f}}")
            + "\nu: {handler: m:f, params: {x: tasks.t}}",
            "u.params.x: tasks.t is inside case 'a' of s; outside s, read switches.s",
        ),
        (
            "s:\n  switch:\n    on: inputs.x\n    cases:\n"
            "      a: {steps: {t: {handler: m:f}}, output: null}\n"
            "      b: {steps: {u: {handler: m:f, params: {x: tasks.t}}}, output: null}",
            "u.params.x: tasks.t is inside case 'a' of s, and a step of another "
            "case of s cannot read it",
        ),
        (
            one_case("{t: {handler: m:f}}")
            + "\nfan:\n  fan_out: {over: tasks.t}\n  steps: {u: {handler: m:f}}",
            "fan.fan_out.over: tasks.t is inside case 'a' of s",
        ),
        (
            one_case("{t: {handler: m:f, params: {x: switches.s}}}"),
            "t.params.x: switches.s is the switch this step is inside of, and it "
            "would wait on itself",
        ),
        (
            one_case("{t: {handler: m:f}}")
            + "\nu: {handler: m:f, params: {x: tasks.s}}",
            "u.params.x: 's' is not a task; tasks. refers to tasks",
        ),
        (
            "t: {handler: m:f}\nu: {handler: m:f, params: {x: switches.t}}",
            "u.params.x: 't' is not a switch; switches. refers to them",
        ),
        (
            one_case("{t: {handler: m:f}}") + "\nt: {handler: m:f}",
            "step name 't' is used more than once",
        ),
        (
            "s:\n  switch:\n    on: inputs.x\n    cases:\n"
            "      a: {steps: {t: {handler: m:f}}, output: null}\n"
            "      b: {steps: {t: {handler: m:f}}, output: null}",
            "steps.s.switch.cases.b.steps.t: step name 't' is used more than once",
        ),
        (
            "l:\n  loop: {max_cycles: 2, exit_condition: t}\n  steps:\n"
            "    " + one_case("{t: {handler: m:f}}"),
            "l: exit condition 't' is not a task directly in the loop",
        ),
        (
            one_case(
                "{t: {handler: m:f, params: {x: tasks.u}}, "
                "u: {handler: m:f, params: {x: tasks.t}}}"
            ),
            "steps wait on each other in case 'a' of s: t -> u -> t",
        ),
    ],
    ids=[
        "read-from-outside-the-switch",
        "read-from-another-case",
        "fanned-out-from-outside",
        "its-own-switch",
        "tasks-to-a-switch",
        "switches-to-a-task",
        "name-used-outside-too",
        "name-used-in-two-cases",
        "exit-condition-in-a-case",
        "cycle-in-a-case",
    ],
)
def test_reference_and_name_problems(steps: str, expected: str) -> None:
    assert expected in problems(steps, header="inputs: {x: string}\n")


def test_a_flow_does_not_return_a_step_of_a_case() -> None:
    header = "inputs: {x: string}\noutput: tasks.t\n"

    assert (
        "output: tasks.t is inside case 'a' of s; outside s, read switches.s"
    ) in problems(one_case("{t: {handler: m:f}}"), header=header)


def test_a_case_reads_steps_outside_its_switch_and_other_switches() -> None:
    reading = flow(
        """\
        before: {handler: m:f}
        first:
          switch:
            on: inputs.x
            cases:
              a:
                steps: {t: {handler: m:f, params: {x: tasks.before}}}
                output: tasks.t
        second:
          switch:
            on: inputs.x
            cases:
              a:
                steps: {u: {handler: m:f, params: {x: switches.first}}}
                output: tasks.u
        """,
        header="inputs: {x: string}\n",
    )

    assert reading.waits_on("first") == {"before"}
    assert reading.waits_on("second") == {"first"}
    done = {at("before"): ok(1), at("t"): ok("from t")}
    assert published(plan(reading, state(done, {"x": "a"}))) == ["u"]
    assert value_of(reading, state(done, {"x": "a"}), at("u"), switches("first")) == (
        "from t"
    )


# The whole system in one process.

HANDLERS = """\
    def load_booking(booking_id):
        return {"id": booking_id, "nights": booking_id}

    def vip_case(booking):
        return "is_vip" if booking["nights"] >= 10 else "not_vip"

    def vip_benefit(booking):
        return "champagne"

    def send_gift(booking, benefit):
        return f"{benefit} for booking {booking['id']}"

    def confirm(notice):
        return {"confirmed": True, "notice": notice}

    def check(round):
        return "is_vip" if round % 2 == 0 else "not_vip"

    def benefit(round):
        return f"gift {round}"

    def send(benefits, checks):
        return {"benefits": benefits, "checks": checks}

    def done(notices):
        return len(notices) == 4
    """


def project(tmp_path: Path, flow_text: str) -> Path:
    """A flow file and its handlers module, under a name no other test uses."""
    module = f"handlers_{uuid.uuid4().hex}"
    (tmp_path / f"{module}.py").write_text(dedent(HANDLERS))
    flows_dir = tmp_path / "flows"
    flows_dir.mkdir()
    (flows_dir / "f.yaml").write_text(flow_text.replace("tasks:", f"{module}:"))
    return flows_dir


@pytest.mark.parametrize(
    ("booking_id", "notice"),
    [(12, "champagne for booking 12"), (2, None)],
    ids=["is_vip", "not_vip"],
)
async def test_the_flow_of_the_spec_runs_to_its_end(
    tmp_path: Path, booking_id: int, notice: str | None
) -> None:
    header = "inputs: {booking_id: number}\noutput: tasks.confirm\n"
    flows_dir = project(tmp_path, text(BOOKING_STEPS, header=header))

    run = await run_local(flows_dir, "f", {"booking_id": booking_id}, timeout=TIMEOUT)

    assert run.status is RunStatus.SUCCEEDED, run
    assert run.output == {"confirmed": True, "notice": notice}


async def test_a_switch_in_a_loop_runs_a_case_in_some_iterations_only(
    tmp_path: Path,
) -> None:
    steps = """\
        rounds:
          loop: {max_cycles: 5, exit_condition: done}
          steps:
            check: {handler: tasks:check, params: {round: neorc.loop_count}}
            route:
              switch:
                on: tasks.check
                cases:
                  is_vip:
                    steps:
                      benefit:
                        handler: tasks:benefit
                        params: {round: neorc.loop_count}
                      send:
                        handler: tasks:send
                        params: {benefits: tasks.benefit, checks: tasks.check}
                    output: tasks.send
                  not_vip:
                    steps: {}
                    output: null
            done: {handler: tasks:done, params: {notices: switches.route}}
        """
    flows_dir = project(tmp_path, text(steps, header="output: switches.route\n"))

    run = await run_local(flows_dir, "f", {}, timeout=TIMEOUT)

    assert run.status is RunStatus.SUCCEEDED, run
    checks = ["not_vip", "is_vip", "not_vip", "is_vip"]
    assert run.output == [
        None,
        {"benefits": [None, "gift 2"], "checks": checks[:2]},
        None,
        {"benefits": [None, "gift 2", None, "gift 4"], "checks": checks},
    ]


async def test_an_on_that_names_no_case_fails_the_run_with_the_reason(
    tmp_path: Path,
) -> None:
    steps = """\
        load_booking:
          handler: tasks:load_booking
          params: {booking_id: inputs.booking_id}
        route:
          switch:
            on: tasks.load_booking
            cases:
              is_vip:
                steps: {}
                output: null
        """
    flows_dir = project(tmp_path, text(steps, header="inputs: {booking_id: number}\n"))

    run = await run_local(flows_dir, "f", {"booking_id": 1}, timeout=TIMEOUT)

    assert run.status is RunStatus.FAILED
    assert run.reason == (
        "route: switch.on tasks.load_booking is a dict, not one of its cases: is_vip"
    )
