# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The direct clients, on the in-memory adapters, held to the client contracts."""

import pytest

from neorc_core import (
    Manager,
    ManagerClient,
    QueueClient,
    QueueKind,
    RunStarter,
    StarterKind,
)
from neorc_core.local import (
    DirectManagerClient,
    DirectQueueClient,
    MemoryStore,
    MemoryTaskNotifier,
)
from neorc_core.testing.contracts import ManagerClientContract, QueueClientContract

SCRIPT = RunStarter(StarterKind.SERVICE, "1", "a deploy script")
"""Who the direct client asks for, as a token would name a service over HTTP."""


@pytest.fixture
def manager() -> Manager:
    return Manager(
        MemoryStore(
            queues=dict.fromkeys(
                (
                    "default",
                    "voice",
                ),
                QueueKind.WORKER,
            )
        ),
        tasks=MemoryTaskNotifier(),
        events=MemoryTaskNotifier(),
    )


class _Direct:
    @pytest.fixture
    def manager_client(self, manager: Manager) -> ManagerClient:
        return DirectManagerClient(manager, started_by=SCRIPT)

    @pytest.fixture
    def queue_client(self, manager: Manager) -> QueueClient:
        return DirectQueueClient(manager)


class TestDirectManagerClient(_Direct, ManagerClientContract):
    pass


class TestDirectQueueClient(_Direct, QueueClientContract):
    pass
