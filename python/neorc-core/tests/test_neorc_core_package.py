# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

import importlib
import importlib.metadata
from importlib.metadata import PackageNotFoundError, version

import pytest

import neorc_core


def test_version_matches_distribution_metadata() -> None:
    assert neorc_core.__version__ == version("neorc-core")


def test_imports_without_distribution_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    def not_installed(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", not_installed)
    try:
        assert importlib.reload(neorc_core).__version__ == "0+unknown"
    finally:
        monkeypatch.undo()
        importlib.reload(neorc_core)


ADAPTERS = ("oneberth", "neorc", "fastapi", "starlette", "uvicorn", "httpx", "asyncpg")
"""What core never imports: oneberth, the application, and the adapters' libraries.

docs/specs/core.md in oneberth: ``neorc-core`` imports nothing of oneberth.
contributing/in-memory-first.md: no web framework, HTTP client or database
driver either.
"""


def _imported(source: str) -> set[str]:
    """The top-level packages a module imports, at any depth of its code."""
    import ast

    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.partition(".")[0])
    return found


def test_core_imports_nothing_of_oneberth_or_of_an_adapter() -> None:
    from pathlib import Path

    package = Path(neorc_core.__file__).parent
    modules = sorted(package.rglob("*.py"))
    assert len(modules) > 20

    for module in modules:
        imported = _imported(module.read_text())
        assert not imported & set(ADAPTERS), (
            f"{module.relative_to(package)} imports {sorted(imported & set(ADAPTERS))}"
        )


def test_the_yaml_parser_is_cores_one_runtime_dependency() -> None:
    from importlib.metadata import requires

    assert [r.partition(">")[0] for r in requires("neorc-core") or []] == ["pyyaml"]
