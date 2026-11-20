# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The built web UI of the neorc manager, and where it is.

This distribution holds static assets and nothing to run: the manager mounts
``static_dir()`` and serves it, and neorc-mcp serves ``mcp_view_html()``, the
view of its MCP App, one HTML document with its script and style inline. The
assets are built from ``ts/neorc-ui`` by CI, or by the wheel build; a source
checkout holds none until then.
"""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

try:
    __version__ = version("neorc-ui")
except PackageNotFoundError:  # a source checkout on sys.path, not installed
    __version__ = "0+unknown"

STATIC = Path(__file__).parent / "static"
"""Where the built assets live, whether they are there or not."""

MCP_VIEW = Path(__file__).parent / "mcp_view" / "mcp-view.html"
"""Where the MCP App's view lives, whether it is there or not."""

BUILD_HINT = "run `npm ci && npm run build` in ts/neorc-ui and copy dist/ there"


def static_dir() -> Path:
    """The directory holding the built UI, with its ``index.html``.

    Raises ``FileNotFoundError`` naming the fix when there is no built UI, as
    in an editable install of a checkout that was never built.
    """
    if not (STATIC / "index.html").is_file():
        raise FileNotFoundError(
            f"{STATIC} holds no built UI: install the neorc-ui wheel, or {BUILD_HINT}"
        )
    return STATIC


def mcp_view_html() -> str:
    """The MCP App's view: one HTML document, nothing for it to fetch.

    Raises ``FileNotFoundError`` naming the fix when it is not built, as in
    an editable install of a checkout that was never built.
    """
    if not MCP_VIEW.is_file():
        raise FileNotFoundError(
            f"{MCP_VIEW} is not built: install the neorc-ui wheel, or {BUILD_HINT}, "
            "and dist-mcp-view/ to mcp_view/"
        )
    return MCP_VIEW.read_text(encoding="utf-8")


__all__ = [
    "BUILD_HINT",
    "MCP_VIEW",
    "STATIC",
    "__version__",
    "mcp_view_html",
    "static_dir",
]
