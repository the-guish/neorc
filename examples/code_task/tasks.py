# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The handler of the flow with a form. See README.md.

A plain function that does not import neorc. It does no coding: it says back
what it was asked, which is what the example is about.
"""


def plan(description, repo, model, attempts, draft):
    kind = "a draft pull request" if draft else "a pull request"
    return (
        f"{kind} on {repo}, written by the {model} model "
        f"in at most {attempts} attempts: {description}"
    )
