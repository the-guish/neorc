# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Test suites every implementation of a port must pass.

A suite is a class of tests written against the port alone. To run it on an
adapter, subclass it in a test module, under a name pytest collects, and
provide the fixture the suite names::

    class TestMyStore(StoreContract):
        @pytest.fixture
        def store(self, my_store: MyStore) -> Store:
            return my_store

The fixture should hand over an adapter with nothing in it. The tests are
coroutines, so the suite expects pytest-asyncio in ``auto`` mode. Call
``pytest.register_assert_rewrite("neorc_core.testing.contracts")`` in a
``conftest.py`` before importing a suite, for pytest's detailed assertion
messages.
"""

from neorc_core.testing.contracts._clients import (
    ManagerClientContract,
    QueueClientContract,
)
from neorc_core.testing.contracts._store import StoreContract

__all__ = [
    "ManagerClientContract",
    "QueueClientContract",
    "StoreContract",
]
