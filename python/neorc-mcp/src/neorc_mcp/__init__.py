# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""neorc's manager as an MCP server: a tool per route a person or CI may call,
and an MCP App that draws a flow or a run as neorc's UI does.

The server is a client of the manager's API over HTTP, and a process of its
own. docs/specs/mcp.md.
"""

from importlib.metadata import PackageNotFoundError, version

from neorc_mcp._cli import RequireKey, app_from_environment, main
from neorc_mcp._client import As, ManagerApi, ManagerRefused, ManagerUnreachable
from neorc_mcp._server import PANEL_TOOL, VIEW_MIME_TYPE, VIEW_URI, build_server

try:
    __version__ = version("neorc-mcp")
except PackageNotFoundError:  # a source checkout on sys.path, not installed
    __version__ = "0+unknown"

__all__ = [
    "PANEL_TOOL",
    "VIEW_MIME_TYPE",
    "VIEW_URI",
    "As",
    "ManagerApi",
    "ManagerRefused",
    "ManagerUnreachable",
    "RequireKey",
    "__version__",
    "app_from_environment",
    "build_server",
    "main",
]
