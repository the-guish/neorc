# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""``queues.toml``: the queues a set of flow files names, each with its kind."""

from pathlib import Path

import pytest

from neorc_core import FlowDefinitionError, QueueKind
from neorc_core.flows import read_queues


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", {}),
        ("# no queue yet\n", {}),
        ('[queues.editors]\nkind = "external"\n', {"editors": QueueKind.EXTERNAL}),
        (
            '[queues.default]\nkind = "worker"\n[queues.editors]\nkind = "external"\n',
            {"default": QueueKind.WORKER, "editors": QueueKind.EXTERNAL},
        ),
        (
            '[queues.heavy-jobs_2]\nkind = "worker"\n',
            {"heavy-jobs_2": QueueKind.WORKER},
        ),
    ],
)
def test_a_queue_file_names_each_queue_with_its_kind(
    tmp_path: Path, text: str, expected: dict[str, QueueKind]
) -> None:
    (tmp_path / "queues.toml").write_text(text)

    assert read_queues(tmp_path) == expected


def test_a_folder_without_a_queue_file_names_no_queue(tmp_path: Path) -> None:
    assert read_queues(tmp_path) == {}


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("kind =", "queues.toml: "),
        ("[other]\n", "queues.toml other: unknown key"),
        ("queues = 3\n", "queues.toml queues: a table of queues by name"),
        ("[queues]\neditors = 3\n", "queues.toml [queues.editors]: a table with kind"),
        ("[queues.editors]\n", "queues.toml [queues.editors] kind: worker or external"),
        (
            '[queues.editors]\nkind = "human"\n',
            "queues.toml [queues.editors] kind: worker or external",
        ),
        (
            '[queues.editors]\nkind = "external"\nlabel = "x"\n',
            "queues.toml [queues.editors] label: unknown key",
        ),
        ('[queues."two words"]\nkind = "worker"\n', "'two words' is not a queue name"),
    ],
)
def test_a_queue_file_with_a_mistake_names_the_file_and_the_key(
    tmp_path: Path, text: str, problem: str
) -> None:
    (tmp_path / "queues.toml").write_text(text)

    with pytest.raises(FlowDefinitionError) as raised:
        read_queues(tmp_path)

    assert any(problem in found for found in raised.value.problems), (
        raised.value.problems
    )
