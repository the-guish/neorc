# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Record a ``word_picker_rounds`` run, as the API sends it, for the UI's tests.

    uv run python scripts/record_ui_fixture.py

Runs ``examples/wordplay`` in memory on a ``LocalCluster`` and writes the
flows, the run, its tasks, its sub-runs and their tasks, in their wire forms,
to ``ts/neorc-ui/src/test/word_picker_rounds.json``. The UI's component tests
render that file, so they see what a real run looks like: loops with their
iterations, a fan-out with its branches, a loop inside it, and sub-flow runs.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from neorc_core import _wire as wire
from neorc_core.local import LocalCluster
from neorc_core.testing.examples import REQUESTED_AT, WORDPLAY_INPUTS

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
FIXTURE = ROOT / "ts" / "neorc-ui" / "src" / "test" / "word_picker_rounds.json"


async def record() -> dict[str, Any]:
    async with LocalCluster(code_location=EXAMPLES / "wordplay") as cluster:
        await cluster.upload(EXAMPLES / "wordplay" / "flows")
        await cluster.start()
        run = await cluster.run(
            "word_picker_rounds",
            {**WORDPLAY_INPUTS, "requested_at": REQUESTED_AT},
            timeout=60,
        )
        manager = cluster.manager
        sub_runs = await manager.sub_runs(run.id)
        return {
            "flows": {
                flow.name: {
                    "name": flow.name,
                    "version": str(flow.version),
                    "content": flow.content,
                    "active": flow.active,
                }
                for flow in await manager.latest_flows()
            },
            "run": wire.run_to(run),
            "tasks": [wire.task_to(t) for t in await manager.run_tasks(run.id)],
            "sub_runs": [wire.run_to(r) for r in sub_runs],
            "sub_run_tasks": {
                str(r.id): [wire.task_to(t) for t in await manager.run_tasks(r.id)]
                for r in sub_runs
            },
        }


def main() -> int:
    recorded = asyncio.run(record())
    FIXTURE.write_text(json.dumps(recorded, indent=2, sort_keys=True) + "\n")
    print(
        f"wrote {FIXTURE}: {recorded['run']['status']}, {len(recorded['tasks'])} tasks"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
