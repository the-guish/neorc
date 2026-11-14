# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The handlers of the flow that waits for a person. See README.md.

Plain functions that do not import neorc. The two tasks between them have no
function: a person answers each on the queue's page of the web UI.
"""


def write_draft(topic):
    return f"A first draft about {topic}."


def publish(draft, review, title):
    if review["decision"] != "approve":
        return f"Not published. The editor asked for changes: {review['comment']}"
    return f"Published as {title!r}: {draft}"
