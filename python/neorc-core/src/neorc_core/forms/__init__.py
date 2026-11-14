# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Forms: a screen a flow gives for starting a run of it, or for completing a task.

A flow file may name a form file next to it, for the flow and for each task
of an external queue. A form is written in a small subset of A2UI v0.9, and
the rest of neorc meets it in these places only:

- ``inline_form``, where a flow file is read: the files it names are read in;
- ``form_problems``, where a flow is validated: is the form within the subset,
  and does it ask for the flow's inputs and nothing else? A task's form
  declares the fields of the task's result itself;
- ``check_form_inputs``, where a run is started: do the inputs keep what the
  form asks of them, whoever sends them?
- ``step_forms`` and ``check_form_result``, where a flow is uploaded and a
  task completed: which steps have a form, and is the result what the form
  of its step submits, whoever sends it?

Nothing here imports the rest of ``neorc_core`` but its errors, and nothing
outside this package knows what a form holds. The whole of it, and how to take
it out, is in docs/architecture/forms.md.
"""

from neorc_core.forms._files import inline_form, step_forms
from neorc_core.forms._subset import (
    CATALOG_ID,
    VERSION,
    check_form_inputs,
    check_form_result,
    form_fields,
    form_problems,
)

__all__ = [
    "CATALOG_ID",
    "VERSION",
    "check_form_inputs",
    "check_form_result",
    "form_fields",
    "form_problems",
    "inline_form",
    "step_forms",
]
