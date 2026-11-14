# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The committed OpenAPI snapshot is the application's schema.

The UI generates its types from ``ts/neorc-ui/openapi.json`` without running
Python, so the file must be what ``create_app`` would produce today. When this
fails, run ``uv run python scripts/export_openapi.py`` and commit the result.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "export_openapi.py"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("export_openapi", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_openapi_snapshot_matches_the_application() -> None:
    script = _script()
    snapshot: Path = script.SNAPSHOT

    assert snapshot.is_file(), f"no snapshot: run {SCRIPT.relative_to(ROOT)}"
    assert snapshot.read_text() == script.openapi_text(), (
        f"{snapshot.relative_to(ROOT)} is stale: run {SCRIPT.relative_to(ROOT)}"
    )


def test_every_route_the_ui_reads_is_in_the_schema() -> None:
    """The listings this plan adds, by path and method, so a rename shows here."""
    import json

    paths = json.loads(_script().openapi_text())["paths"]

    for path, method in [
        ("/flows", "get"),
        ("/flows/{name}/versions", "get"),
        ("/runs", "get"),
        ("/runs/{run_id}/tasks", "get"),
        ("/runs/{run_id}/sub-runs", "get"),
        ("/tasks/{task_id}", "get"),
        ("/events", "get"),
        ("/events/latest", "get"),
        ("/flows/{name}/runs", "post"),
        ("/runs/{run_id}/cancel", "post"),
        ("/queues", "get"),
        ("/queues/{queue}/tasks/recent", "get"),
        ("/tasks/{task_id}/inputs", "get"),
        ("/tasks/{task_id}/complete", "post"),
    ]:
        assert method in paths[path], f"{method.upper()} {path}"


def test_every_refusal_is_documented_with_the_body_the_manager_sends() -> None:
    """Not FastAPI's default 422 shape, which the application never answers."""
    import json

    schema = json.loads(_script().openapi_text())

    assert "HTTPValidationError" not in schema["components"]["schemas"]
    error = schema["components"]["schemas"]["ErrorResponse"]["properties"]
    assert set(error) == {"error", "detail", "problems"}
    for path, methods in schema["paths"].items():
        if path == "/health" or path.startswith("/auth/"):
            continue  # no body, no parameter, nothing there to refuse
        for operation in methods.values():
            for code in ("404", "409", "413", "422"):
                body = operation["responses"][code]["content"]["application/json"]
                assert body["schema"]["$ref"].endswith("/ErrorResponse"), (
                    f"{path} {code}"
                )
