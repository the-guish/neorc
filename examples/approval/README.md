# approval

A flow that waits for a person. `flows/approval.yaml` has four tasks. A worker
writes a draft and publishes it. Between the two, a person reviews the draft
and suggests a title, on the web UI. How a task is completed is in
[docs/specs/task-completion.md](../../docs/specs/task-completion.md).

The two tasks for a person are on `editors`, an external queue:

- `review_draft` names a form, `flows/review_draft.form.json`. The page draws
  it, and the result has its fields: `decision` and `comment`. What a form may
  hold is in [docs/architecture/forms.md](../../docs/architecture/forms.md).
- `suggest_title` names no form. The page asks for a text, and the result is
  that text.

## On one machine

Run each step from the repository root in its own terminal, after `uv sync`.
The manager runs with `--no-auth`, which needs no token and no sign-in. Use it
on your own machine only.

1. Build the web UI, once. A checkout holds no built UI:

       (cd ts/neorc-ui && npm ci && npm run build)
       cp -r ts/neorc-ui/dist python/neorc-ui/src/neorc_ui/static

2. Postgres (skip if you have one; set `NEORC_DATABASE_URL` to it instead):

       uv run python examples/hello/postgres.py

   It prints an `export NEORC_DATABASE_URL=...` line. Run that line in every
   terminal below.

3. The manager:

       uv run neorc manager start --host 127.0.0.1 --create-schema --no-auth

4. The two queues, then the flow:

       uv run neorc queues declare default --kind worker
       uv run neorc queues declare editors --kind external
       uv run neorc flows upload --manager-address 127.0.0.1:8420 \
           examples/approval/flows

5. The scheduler:

       uv run neorc scheduler start --manager-address 127.0.0.1:8420

6. The worker of the `default` queue, with the code in this directory:

       uv run neorc worker start --manager-address 127.0.0.1:8420 \
           --code-location examples/approval

7. Open <http://127.0.0.1:8420/ui/>. Start a run of `approval` from its
   flow's page, with a topic.

8. Open "Queues", then `editors`. The page lists the two pending tasks. Open
   each one, read the draft, and complete it. The run then publishes, and its
   page shows the output.

The state filter of the queue's page starts at pending, so a completed task
leaves the list. Choose "all" to see it again.

## What the manager refuses

The form is not only how the page looks. The manager holds every completion of
`review_draft` to it, whoever sends it:

    curl -X POST 127.0.0.1:8420/tasks/<task id>/complete \
        -H 'content-type: application/json' \
        -d '{"result": {"decision": "later", "comment": ""}}'

    review_draft: field 'decision' is not one of approve, changes
