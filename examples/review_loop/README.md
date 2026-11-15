# review_loop

A loop that waits for a person each round. An AI reviewer lists the issues
it finds in a change. A person triages them on the web UI, one card per
issue, and then chooses Ready, which ends the loop, or Re-work, which starts
another round. How a task is completed is in
[docs/specs/task-completion.md](../../docs/specs/task-completion.md).

Two flows are in `flows/`:

- `review_loop` loops over `review_round`, at most three times. After each
  round, `ready` reads the person's verdict. After the loop, `report` says
  what the last triage decided.
- `review_round` has two tasks. `review` is a stand-in for the AI reviewer:
  it finds the same three issues each time. `triage` is on `editors`, an
  external queue, and names a form, `flows/triage.form.json`.

The form reads the task's input `review`, the reviewer's result. It shows the
summary, then one card per item of `issues`, each with its severity, title
and detail, and a bar of three buttons: Fix, Defer and Discard. Re-work and
Ready, at the end, complete the task. The result is one decision per issue,
in order, and the verdict:

    {"decision": ["fix", "defer", "discard"], "verdict": "ready"}

What such a form may hold is in
[docs/architecture/forms.md](../../docs/architecture/forms.md).

`review_round` is a sub-flow so that the triage reads one review. Inside a
loop, a task reads every round's result of a task in that loop, the current
one last.

## On one machine

Follow [examples/approval](../approval/README.md), "On one machine", with
`examples/review_loop` in the place of `examples/approval`. Then start a run
of `review_loop` from its flow's page, with a change to review, for example
"Add a retry".

Open "Queues", then `editors`, and open the `triage` task. Press a decision
in each card, then Re-work: a new round starts, and a new `triage` task
waits. Decide again, and press Ready. The run then reports, and its page
shows the output:

    Ready after 2 rounds: 2 to fix, 0 deferred, 1 discarded.

## What the manager refuses

The form is not only how the page looks. The manager holds every completion
of `triage` to it, whoever sends it: one decision per issue, each one of the
options, and a verdict one of the buttons' values. A responder of `editors`
sends it so, with a token from `neorc tokens create`:

    curl -X POST 127.0.0.1:8420/neorc/tasks/<task id>/complete \
        -H "authorization: Bearer <inbox>" \
        -H 'content-type: application/json' \
        -d '{"result": {"decision": ["fix"], "verdict": "ready"}}'

    triage: field 'decision' has 1 values, and /inputs/review/issues has 3 items
