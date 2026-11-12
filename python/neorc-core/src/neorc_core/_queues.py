# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""A queue's kind: who takes its tasks. See docs/specs/task-completion.md.

A queue is a name a step gives. Declaring it says what kind it is; a queue
nobody declared is a worker queue, as every queue was before there were kinds.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class QueueKind(StrEnum):
    """Who takes the tasks of a queue."""

    WORKER = "worker"
    """Workers receive its tasks and run their handlers."""
    EXTERNAL = "external"
    """Its tasks wait for a completion: no worker receives them."""


@dataclass(frozen=True, slots=True)
class Queue:
    """A declared queue."""

    name: str
    kind: QueueKind
