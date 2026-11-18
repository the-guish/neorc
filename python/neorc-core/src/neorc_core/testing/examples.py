# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The example scenarios: ``examples/hello``, ``wordplay`` and ``vip_booking``.

Each function uploads an example's flows through a ``ManagerClient``, starts a
run, waits for it to finish, and asserts what it produced: the example runs to
its end. What runs the
scheduler and the workers is the caller's choice: ``LocalCluster`` in memory,
or a deployment on Postgres and HTTP. The scenarios are the same either way,
which is the point.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

from neorc_core._runs import Run, RunId, RunStatus
from neorc_core._values import JsonValue
from neorc_core.flows import Address, read_flows
from neorc_core.ports._clients import ManagerClient

DEFAULT_TIMEOUT = 30.0

WORDPLAY_INPUTS: dict[str, JsonValue] = {
    "sentence": "potato tomate berry watermelon",
    "preferred_letter": "t",
}

REQUESTED_AT: JsonValue = {"$datetime": "2026-09-13T10:00:00+00:00"}


async def wait_for_run(
    client: ManagerClient, run_id: RunId, *, timeout: float = DEFAULT_TIMEOUT
) -> Run:
    """The run once it has finished, however it ended; ``TimeoutError`` otherwise."""
    async with asyncio.timeout(timeout):
        after = 0
        while True:
            run = await client.get_run(run_id)
            if run.status is not RunStatus.ACTIVE:
                return run
            events = await client.wait_for_events(after, timeout=1)
            if events:
                after = events[-1].sequence


async def run_flow(
    client: ManagerClient,
    flows_dir: Path,
    flow: str,
    inputs: Mapping[str, JsonValue],
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> Run:
    """Upload the flows in ``flows_dir`` and run ``flow`` to its end."""
    await client.upload_flows(read_flows(flows_dir))
    run = await client.start_run(flow, inputs)
    return await wait_for_run(client, run.id, timeout=timeout)


async def hello(
    client: ManagerClient,
    examples: Path,
    flow: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> Run:
    """``examples/hello``: a single-task flow, ``a`` or ``b``, with no output.

    The worker prints the flow's name; the caller checks that where it can.
    """
    run = await run_flow(
        client, examples / "hello" / "flows", flow, {}, timeout=timeout
    )
    assert run.status is RunStatus.SUCCEEDED, run
    assert run.output is None
    return run


async def word_picker(
    client: ManagerClient, examples: Path, *, timeout: float = DEFAULT_TIMEOUT
) -> Run:
    """``examples/wordplay``: the words with the preferred letter, as the output."""
    run = await run_flow(
        client,
        examples / "wordplay" / "flows",
        "word_picker",
        WORDPLAY_INPUTS,
        timeout=timeout,
    )
    assert run.status is RunStatus.SUCCEEDED, run
    assert run.output == ["potato", "tomate"]
    return run


async def word_picker_rounds(
    client: ManagerClient, examples: Path, *, timeout: float = DEFAULT_TIMEOUT
) -> Run:
    """``examples/wordplay``: loops, fan-outs, a sub-flow and metadata, no output."""
    inputs: dict[str, JsonValue] = {**WORDPLAY_INPUTS, "requested_at": REQUESTED_AT}
    run = await run_flow(
        client,
        examples / "wordplay" / "flows",
        "word_picker_rounds",
        inputs,
        timeout=timeout,
    )
    state = await client.run_state(run.id)

    assert run.status is RunStatus.SUCCEEDED, run
    assert run.output is None
    assert state.last((), "runs") == 2
    report = state.steps[Address("report")].value
    assert isinstance(report, dict)
    assert report["words"] == ["potato**", "tomate**"]
    assert report["requested_at"] == REQUESTED_AT
    assert report["flow_run_id"] == str(run.id)
    assert report["attempts"] == 1
    return run


async def vip_booking(
    client: ManagerClient, examples: Path, *, timeout: float = DEFAULT_TIMEOUT
) -> list[Run]:
    """``examples/vip_booking``: a switch, run once for each of its two cases.

    The gift's tasks run for the VIP booking and for no other.
    """
    flows = examples / "vip_booking" / "flows"
    confirmed = {
        1: "Booking 1 is confirmed.",
        2: "Booking 2 is confirmed: champagne is on its way to Grace.",
    }
    gift = {Address("compute_benefit"), Address("send_gift")}
    runs = []
    for booking_id, output in confirmed.items():
        run = await run_flow(
            client, flows, "vip_booking", {"booking_id": booking_id}, timeout=timeout
        )
        state = await client.run_state(run.id)

        assert run.status is RunStatus.SUCCEEDED, run
        assert run.output == output
        assert (gift & set(state.steps)) == (gift if booking_id == 2 else set())
        runs.append(run)
    return runs
