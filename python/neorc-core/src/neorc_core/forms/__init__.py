# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Forms: a screen a flow gives for starting a run of it.

A flow file may name a form file next to it. The form is written in a small
subset of A2UI v0.9, and the rest of neorc meets it in three places only:

- ``inline_form``, where a flow file is read: the file it names is read in;
- ``form_problems``, where a flow is validated: is the form within the subset,
  and does it ask for the flow's inputs and nothing else?
- ``check_form_inputs``, where a run is started: do the inputs keep what the
  form asks of them, whoever sends them?

Nothing here imports the rest of ``neorc_core`` but its errors, and nothing
outside this package knows what a form holds. The whole of it, and how to take
it out, is in docs/architecture/forms.md.
"""

from neorc_core.forms._files import inline_form
from neorc_core.forms._subset import (
    CATALOG_ID,
    VERSION,
    check_form_inputs,
    form_problems,
)

__all__ = [
    "CATALOG_ID",
    "VERSION",
    "check_form_inputs",
    "form_problems",
    "inline_form",
]
