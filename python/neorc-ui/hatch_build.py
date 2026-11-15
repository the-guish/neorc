# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Build the UI into ``src/neorc_ui/static`` when it is not there.

Runs before hatchling collects files, for a wheel or an sdist. From a checkout
that means ``scripts/oneberth-ui-from-source.sh`` when ``@oneberth/ui`` is not
packed yet, then ``npm ci && npm run build`` in ``ts/neorc-ui``, with Node.js
at the version in its ``.nvmrc``; from an sdist the assets are already inside, so
nothing runs and Node.js is not needed. An editable install never builds:
Python contributors need no Node.js, and ``neorc_ui.static_dir()`` says what
to do when the assets are missing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

STATIC = Path("src", "neorc_ui", "static")


class UiBuildHook(BuildHookInterface):  # type: ignore[type-arg]
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if version == "editable":
            return
        root = Path(self.root)
        static = root / STATIC
        if (static / "index.html").is_file():
            return
        source = root.parents[1] / "ts" / "neorc-ui"
        if not (source / "package.json").is_file():
            raise RuntimeError(
                f"{static} holds no built UI and {source} is not here to build "
                "it from: build from a checkout of the repository, or from an "
                "sdist, which carries the assets"
            )
        build(source, static)


#: Where ``scripts/oneberth-ui-from-source.sh`` packs ``@oneberth/ui``.
ONEBERTH_UI = Path("vendor", "oneberth-ui-0.0.0.tgz")


def build(source: Path, static: Path) -> None:
    """``npm ci && npm run build`` in ``source``; its ``dist`` becomes ``static``.

    ``@oneberth/ui`` comes from the oneberth commit the root ``pyproject.toml``
    pins: the script packs it first when it is not there.
    """
    wanted = (source / ".nvmrc").read_text().strip()
    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError(
            f"building the UI needs Node.js {wanted} and npm, and npm is not on "
            "PATH: see contributing/dev-environment.md"
        )
    running = subprocess.run(
        ["node", "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if running.lstrip("v").split(".")[0] != wanted.split(".")[0]:
        raise RuntimeError(
            f"building the UI needs Node.js {wanted}, as {source / '.nvmrc'} "
            f"says, and {running} is on PATH"
        )
    env = {**os.environ, "CI": "true"}
    if not (source / ONEBERTH_UI).is_file():
        script = source.parents[1] / "scripts" / "oneberth-ui-from-source.sh"
        subprocess.run(["sh", str(script)], check=True, env=env)
    subprocess.run([npm, "ci"], cwd=source, check=True, env=env)
    subprocess.run([npm, "run", "build"], cwd=source, check=True, env=env)
    if static.exists():
        shutil.rmtree(static)
    shutil.copytree(source / "dist", static)
