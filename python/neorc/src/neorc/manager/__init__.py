# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The manager service. Install with ``pip install neorc[manager]``.

``new_manager_webapp`` gives the manager as a oneberth component; a backend
composes it with ``oneberth.web.compose_app``. ``build_app`` and ``run`` compose
standalone neorc and serve it.
"""

from neorc.manager._component import (
    DEFAULT_LONG_POLL_TIMEOUT,
    NAME,
    manager_component,
    new_manager_webapp,
)
from neorc.manager._service import build_app, run

__all__ = [
    "DEFAULT_LONG_POLL_TIMEOUT",
    "NAME",
    "build_app",
    "manager_component",
    "new_manager_webapp",
    "run",
]
