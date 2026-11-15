# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

import importlib
import importlib.metadata
from importlib.metadata import PackageNotFoundError, version

import pytest

import neorc


def test_version_matches_distribution_metadata() -> None:
    assert neorc.__version__ == version("neorc")


def test_imports_without_distribution_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    def not_installed(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", not_installed)
    try:
        assert importlib.reload(neorc).__version__ == "0+unknown"
    finally:
        monkeypatch.undo()
        importlib.reload(neorc)


def test_depends_on_neorc_core() -> None:
    import neorc_core

    assert neorc_core.__version__


def test_the_role_table_is_read_without_a_manager() -> None:
    from neorc.manager import new_manager_webapp
    from neorc_core import Manager
    from neorc_core.local import MemoryStore, MemoryTaskNotifier

    manager = Manager(
        MemoryStore(), tasks=MemoryTaskNotifier(), events=MemoryTaskNotifier()
    )

    assert new_manager_webapp(manager=manager).roles == neorc.NEORC_ROLES
    assert neorc.registry().apps == ("oneberth", "neorc")
