# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The manager for flows: uploads, runs and run trees, on the memory store."""

from pathlib import Path

import pytest

from neorc_core import (
    FlowDefinitionError,
    FlowNotFoundError,
    FlowVersionError,
    InvalidValueError,
    Manager,
    QueueKind,
    RunStateError,
    RunStatus,
    Store,
)
from neorc_core._values import JsonValue
from neorc_core.flows import Address, Reference, Version, read_flow_yaml
from neorc_core.local import MemoryStore, MemoryTaskNotifier

EXAMPLES = Path(__file__).parents[3] / "examples"


def wordplay() -> list[JsonValue]:
    return [
        read_flow_yaml(path.read_text())
        for path in sorted((EXAMPLES / "wordplay" / "flows").glob("*.yaml"))
    ]


def single(
    name: str = "a",
    version: str = "1.0.0",
    *,
    inputs: JsonValue = None,
    steps: JsonValue = None,
    output: str | None = None,
) -> JsonValue:
    content: dict[str, JsonValue] = {
        "name": name,
        "version": version,
        "steps": steps or {"work": {"handler": "tasks:work"}},
    }
    if inputs is not None:
        content["inputs"] = inputs
    if output is not None:
        content["output"] = output
    return content


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore(queues=dict.fromkeys(("default", "scoring"), QueueKind.WORKER))


@pytest.fixture
def flows(store: MemoryStore) -> Manager:
    return Manager(store, tasks=MemoryTaskNotifier(), events=MemoryTaskNotifier())


NOW: JsonValue = {"$datetime": "2026-09-13T10:00:00+00:00"}

ROUNDS_INPUTS: dict[str, JsonValue] = {
    "sentence": "potato tomate",
    "preferred_letter": "t",
    "requested_at": {"$datetime": "2026-09-13T10:00:00+00:00"},
}


# Uploads.


async def test_flows_deployed_together_are_stored(flows: Manager, store: Store) -> None:
    assert await flows.upload_flows(wordplay()) == [True, True]

    latest = await store.latest_flows()

    assert [flow.name for flow in latest] == ["word_picker", "word_picker_rounds"]


async def test_uploading_the_same_flows_again_changes_nothing(
    flows: Manager,
) -> None:
    await flows.upload_flows(wordplay())

    assert await flows.upload_flows(wordplay()) == [False, False]


async def test_a_sub_flow_may_already_be_stored(flows: Manager) -> None:
    picker, rounds = wordplay()
    await flows.upload_flows([picker])

    assert await flows.upload_flows([rounds]) == [True]


async def test_a_call_to_a_flow_nobody_uploaded_is_rejected(
    flows: Manager, store: Store
) -> None:
    _, rounds = wordplay()

    with pytest.raises(FlowDefinitionError, match="unknown flow 'word_picker'"):
        await flows.upload_flows([rounds])

    assert await store.latest_flows() == []


async def test_a_call_must_match_the_stored_flows_inputs(
    flows: Manager,
) -> None:
    await flows.upload_flows([single("b", inputs={"x": "string"})])
    await flows.upload_flows([single("b", "2.0.0", inputs={"y": "string"})])
    caller = single(
        steps={"call": {"flow": "b", "params": {"x": "inputs.x"}}},
        inputs={"x": "string"},
    )

    with pytest.raises(FlowDefinitionError, match="b has no input 'x'"):
        await flows.upload_flows([caller])


async def test_every_invalid_flow_is_reported_and_nothing_is_stored(
    flows: Manager, store: Store
) -> None:
    bad = single(steps={"work": {"handler": "tasks:work", "params": {"x": "nope"}}})

    with pytest.raises(FlowDefinitionError) as caught:
        await flows.upload_flows([single("ok"), bad, {"name": "x"}])

    assert any(p.startswith("flow 2:") for p in caught.value.problems)
    assert any(p.startswith("flow 3:") for p in caught.value.problems)
    assert await store.latest_flows() == []


async def test_the_version_rules_apply_to_uploads(flows: Manager) -> None:
    await flows.upload_flows([single(version="1.1.0")])

    with pytest.raises(FlowVersionError):
        await flows.upload_flows([single(version="1.0.0")])
    with pytest.raises(FlowVersionError):
        await flows.upload_flows(
            [single(version="1.1.0", steps={"other": {"handler": "tasks:other"}})]
        )


# Runs.


async def test_a_run_starts_on_the_latest_version(flows: Manager) -> None:
    await flows.upload_flows([single(version="1.0.0")])
    await flows.upload_flows([single(version="1.2.0")])

    run = await flows.start_run("a", {})

    assert run.version == Version(1, 2, 0)
    assert run.status is RunStatus.ACTIVE
    assert await flows.get_run(run.id) == run


async def test_a_run_of_an_unknown_flow_is_refused(flows: Manager) -> None:
    with pytest.raises(FlowNotFoundError):
        await flows.start_run("nothing", {})


async def test_inputs_of_their_declared_types_start_a_run(
    flows: Manager,
) -> None:
    await flows.upload_flows(wordplay())

    run = await flows.start_run("word_picker_rounds", ROUNDS_INPUTS)

    assert run.inputs == ROUNDS_INPUTS


@pytest.mark.parametrize(
    ("inputs", "problem"),
    [
        ({"s": "x", "n": 1, "b": True}, "missing input 'd'"),
        ({"s": "x", "n": 1, "b": True, "d": NOW, "e": 1}, "no input 'e'"),
        ({"s": 1, "n": 1, "b": True, "d": NOW}, "'s' is not a string"),
        ({"s": "x", "n": True, "b": True, "d": NOW}, "'n' is not a number"),
        ({"s": "x", "n": "1", "b": True, "d": NOW}, "'n' is not a number"),
        ({"s": "x", "n": 1, "b": 1, "d": NOW}, "'b' is not a boolean"),
        ({"s": "x", "n": 1, "b": True, "d": "2026-09-13"}, "'d' is not a datetime"),
        (
            {"s": "x", "n": 1, "b": True, "d": {"$datetime": "2026-09-13T10:00:00"}},
            "'d' is not a datetime",
        ),
        ({"s": "x\x00", "n": 1, "b": True, "d": NOW}, "input 's': text holds NUL"),
    ],
    ids=[
        "missing",
        "unknown",
        "string",
        "bool-number",
        "text-number",
        "int-boolean",
        "untagged-datetime",
        "naive-datetime",
        "nul",
    ],
)
async def test_inputs_are_checked_against_their_declared_types(
    flows: Manager, inputs: dict[str, JsonValue], problem: str
) -> None:
    typed = single(
        inputs={"s": "string", "n": "number", "b": "boolean", "d": "datetime"}
    )
    await flows.upload_flows([typed])

    with pytest.raises(InvalidValueError, match=problem):
        await flows.start_run("a", inputs)


async def test_cancelling_by_hand_cancels_the_whole_tree(
    flows: Manager, store: Store
) -> None:
    await flows.upload_flows(wordplay())
    root = await flows.start_run("word_picker_rounds", ROUNDS_INPUTS)
    child = await store.start_run(
        "word_picker",
        Version(1, 0, 0),
        {"sentence": "x", "preferred_letter": "t"},
        parent_id=root.id,
        parent_address=Address("picker", (("runs", 1),)),
    )

    await flows.cancel_run(child.id)

    assert (await flows.get_run(root.id)).status is RunStatus.CANCELLED
    assert (await flows.get_run(child.id)).reason == "cancelled by hand"
    with pytest.raises(RunStateError, match="already cancelled"):
        await flows.cancel_run(root.id)


async def test_failing_a_run_fails_its_tree_and_is_harmless_once_over(
    flows: Manager,
) -> None:
    await flows.upload_flows([single()])
    run = await flows.start_run("a", {})

    await flows.fail_run(run.id, "a task failed")
    await flows.fail_run(run.id, "again")

    finished = await flows.get_run(run.id)
    assert finished.status is RunStatus.FAILED
    assert finished.reason == "a task failed"


async def test_a_run_succeeds_with_the_value_of_its_output(
    flows: Manager, store: Store
) -> None:
    await flows.upload_flows([single(output="tasks.work")])
    run = await flows.start_run("a", {})
    await store.publish_task(
        run.id,
        Address("work"),
        queue="default",
        handler="tasks:work",
        params={},
        fixed_params={},
    )
    task = await store.receive_task("default")
    assert task is not None
    await store.finish_task(task.id, result={"words": ["red"]})

    succeeded = await flows.succeed_run(run.id, Reference.parse("tasks.work"))

    assert succeeded.status is RunStatus.SUCCEEDED
    assert succeeded.output == {"words": ["red"]}


async def test_a_run_without_output_succeeds_with_none(flows: Manager) -> None:
    await flows.upload_flows([single()])
    run = await flows.start_run("a", {})

    succeeded = await flows.succeed_run(run.id, None)

    assert succeeded.output is None


async def test_a_run_cannot_succeed_before_its_output_exists(
    flows: Manager,
) -> None:
    await flows.upload_flows([single(output="tasks.work")])
    run = await flows.start_run("a", {})

    with pytest.raises(RunStateError, match="not available"):
        await flows.succeed_run(run.id, Reference.parse("tasks.work"))

    assert (await flows.get_run(run.id)).status is RunStatus.ACTIVE


# Listing, for a status page.


async def test_the_manager_lists_flows_versions_runs_tasks_and_sub_runs(
    flows: Manager, store: Store
) -> None:
    await flows.upload_flows([single(version="1.0.0")])
    await flows.upload_flows([single(version="1.1.0")])
    first = await flows.start_run("a", {})
    second = await flows.start_run("a", {})
    task = await flows.publish_task(first.id, Address("work"))

    assert [str(f.version) for f in await flows.flow_versions("a")] == [
        "1.1.0",
        "1.0.0",
    ]
    assert [run.id for run in await flows.list_runs()] == [second.id, first.id]
    assert [run.id for run in await flows.list_runs(limit=1)] == [second.id]
    assert [run.id for run in await flows.list_runs(before=second.id)] == [first.id]
    assert await flows.run_tasks(first.id) == [task]
    assert await flows.sub_runs(first.id) == []


async def test_listing_refuses_what_cannot_name_a_flow_or_a_page(
    flows: Manager,
) -> None:
    with pytest.raises(InvalidValueError, match="not a flow name"):
        await flows.flow_versions("../etc")
    with pytest.raises(InvalidValueError, match="not a flow name"):
        await flows.list_runs(flow="a/b")
    with pytest.raises(InvalidValueError, match="limit"):
        await flows.list_runs(limit=0)


# Queues are declared before a flow names them.


async def test_a_flow_naming_a_queue_nobody_declared_is_refused(
    flows: Manager, store: MemoryStore
) -> None:
    on_gpu = single("a", steps={"work": {"queue": "gpu", "handler": "tasks:work"}})
    fine = single("b")

    with pytest.raises(FlowDefinitionError) as refused:
        await flows.upload_flows([on_gpu, fine])

    assert refused.value.problems == [
        "a: work is on queue 'gpu', which is not declared"
    ]
    assert await store.latest_flows() == []  # nothing of the set is stored

    await flows.declare_queue("gpu", QueueKind.EXTERNAL)
    assert await flows.upload_flows([on_gpu, fine]) == [True, True]
