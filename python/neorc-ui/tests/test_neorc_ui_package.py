# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The distribution that carries the built UI.

``static_dir()`` finds the assets or says how to get them; and a wheel and an
sdist built from the package carry the assets and the license files, without
Node.js, when the assets are already there.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import zipfile
from importlib.metadata import version
from pathlib import Path

import pytest

import neorc_ui

PACKAGE = Path(__file__).resolve().parents[1]


def test_version_matches_distribution_metadata() -> None:
    assert neorc_ui.__version__ == version("neorc-ui")


def test_static_dir_names_the_fix_when_there_is_no_built_ui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(neorc_ui, "STATIC", tmp_path / "static")

    with pytest.raises(FileNotFoundError, match="npm ci && npm run build"):
        neorc_ui.static_dir()


def test_static_dir_is_where_index_html_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html>")
    monkeypatch.setattr(neorc_ui, "STATIC", static)

    assert neorc_ui.static_dir() == static


def test_mcp_view_html_names_the_fix_when_the_view_is_not_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(neorc_ui, "MCP_VIEW", tmp_path / "mcp-view.html")

    with pytest.raises(FileNotFoundError, match="dist-mcp-view"):
        neorc_ui.mcp_view_html()


def test_mcp_view_html_is_the_built_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = tmp_path / "mcp-view.html"
    page.write_text("<!doctype html><title>view</title>", encoding="utf-8")
    monkeypatch.setattr(neorc_ui, "MCP_VIEW", page)

    assert neorc_ui.mcp_view_html() == "<!doctype html><title>view</title>"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The wheel and sdist of a copy of the package holding stand-in assets.

    The copy keeps the hook from reaching for ``ts/neorc-ui``: with assets in
    place it has nothing to build, so no Node.js is needed here. It takes the
    ``.gitignore`` along, which excludes the assets, so the build packages
    them only because ``pyproject.toml`` names them as artifacts, as a real
    build does.
    """
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not on PATH to build the package with")
    copy = tmp_path_factory.mktemp("neorc-ui")
    for name in ("pyproject.toml", "hatch_build.py", "README.md", ".gitignore"):
        shutil.copy(PACKAGE / name, copy / name)
    for name in ("LICENSE", "NOTICE", "AUTHORS"):
        shutil.copy(PACKAGE / name, copy / name)
    shutil.copytree(
        PACKAGE / "src",
        copy / "src",
        ignore=shutil.ignore_patterns("static", "mcp_view"),
    )
    static = copy / "src" / "neorc_ui" / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>stand-in</title>")
    (static / "assets" / "index-abc123.js").write_text("console.log(1)")
    (static / "THIRD_PARTY_LICENSES.txt").write_text("none: a stand-in")
    view = copy / "src" / "neorc_ui" / "mcp_view"
    view.mkdir()
    (view / "mcp-view.html").write_text("<!doctype html><title>view</title>")
    (view / "THIRD_PARTY_LICENSES.txt").write_text("none: a stand-in")
    out = tmp_path_factory.mktemp("dist")
    subprocess.run(
        [uv, "build", "--no-cache", "--out-dir", str(out), str(copy)],
        check=True,
        capture_output=True,
        env={**os.environ, "SETUPTOOLS_SCM_PRETEND_VERSION": "0.0.1"},
    )
    return out


def test_the_wheel_carries_the_assets_and_the_license_files(built: Path) -> None:
    (wheel,) = built.glob("*.whl")

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())

    assert "neorc_ui/static/index.html" in names
    assert "neorc_ui/static/assets/index-abc123.js" in names
    assert "neorc_ui/static/THIRD_PARTY_LICENSES.txt" in names
    assert "neorc_ui/mcp_view/mcp-view.html" in names
    assert "neorc_ui/mcp_view/THIRD_PARTY_LICENSES.txt" in names
    assert "neorc_ui/py.typed" in names
    for licence in ("LICENSE", "NOTICE", "AUTHORS"):
        assert f"neorc_ui-0.0.1.dist-info/licenses/{licence}" in names


def test_the_sdist_carries_the_assets_so_its_wheel_needs_no_node(
    built: Path,
) -> None:
    (sdist,) = built.glob("*.tar.gz")

    with tarfile.open(sdist) as archive:
        names = {name.split("/", 1)[1] for name in archive.getnames() if "/" in name}

    assert "src/neorc_ui/static/index.html" in names
    assert "src/neorc_ui/static/THIRD_PARTY_LICENSES.txt" in names
    assert "src/neorc_ui/mcp_view/mcp-view.html" in names
    assert "hatch_build.py" in names
