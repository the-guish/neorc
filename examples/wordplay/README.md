# wordplay

Run it in one process, with nothing to deploy, from the repository root after
`uv sync`:

    uv run neorc run examples/wordplay --flow word_picker \
        --inputs '{"sentence": "potato tomate berry watermelon", "preferred_letter": "t"}'

It prints `["potato", "tomate"]`. `word_picker_rounds` also takes a datetime,
written as a tagged value, and has no output, so it prints `null`:

    uv run neorc run examples/wordplay --flow word_picker_rounds \
        --inputs '{"sentence": "potato tomate berry watermelon", "preferred_letter": "t",
                   "requested_at": {"$datetime": "2026-09-13T10:00:00+00:00"}}'

[python/neorc-core/tests/test_examples.py](../../python/neorc-core/tests/test_examples.py)
runs both flows and asserts what they produce, in memory;
[python/neorc/tests/test_end_to_end.py](../../python/neorc/tests/test_end_to_end.py)
runs them deployed, as below.

## Deployed

The same flows on a manager, a scheduler and a worker per queue. Run each step
from the repository root in its own terminal, after `uv sync`; the first two
are the same as in [hello](../hello/README.md#deployed).

1. Postgres (skip if you have one; set `NEORC_DATABASE_URL` to it instead):

       uv run python examples/hello/postgres.py

2. The manager, and an API token for each process, as in
   [hello](../hello/README.md#deployed): `export NEORC_API_TOKEN=<secret>`
   in each terminal below, with the token the step names; or start the
   manager with `--no-auth` to use the UI without configuring an identity
   provider to sign in with, and no token anywhere:

       export NEORC_DATABASE_URL=...   # from step 1
       uv run neorc manager start --host 127.0.0.1 --create-schema --no-ui

       export NEORC_DATABASE_URL=...   # in another terminal
       uv run neorc queues declare default --kind worker
       uv run neorc queues declare scoring --kind worker
       uv run neorc tokens create ci --role ci
       uv run neorc tokens create scheduler --role scheduler
       uv run neorc tokens create worker --role worker --queue default
       uv run neorc tokens create scoring-worker --role worker --queue scoring
       uv run neorc tokens create me --role user

3. Upload the flows, with the `ci` token:

       uv run neorc flows upload --manager-address 127.0.0.1:8420 \
           examples/wordplay/flows

4. The scheduler, with the `scheduler` token:

       uv run neorc scheduler start --manager-address 127.0.0.1:8420

5. Two workers, one per queue, each with the token bound to it: `score_words`
   runs on `scoring`, the rest on `default`. Both serve the code in this
   directory:

       uv run neorc worker start --manager-address 127.0.0.1:8420 \
           --code-location examples/wordplay

       uv run neorc worker start --manager-address 127.0.0.1:8420 \
           --code-location examples/wordplay --queue scoring

6. Start a run, with the `me` token:

       curl -X POST 127.0.0.1:8420/flows/word_picker/runs \
           -H "authorization: Bearer $NEORC_API_TOKEN" \
           -H 'content-type: application/json' \
           -d '{"inputs": {"sentence": "potato tomate berry watermelon", "preferred_letter": "t"}}'

   The response carries the run's `id`; once it has succeeded,
   `curl -H "authorization: Bearer $NEORC_API_TOKEN" 127.0.0.1:8420/runs/<id>`
   shows `"output": ["potato", "tomate"]`, and `/runs/<id>/state` every step's
   result.

[`flow_example.py`](../../docs/specs/flow_example.py) written the neorc way: the
`while` and `for` loops move into flow files, and `tasks.py` keeps only plain
functions.

- [`flows/word_picker.yaml`](flows/word_picker.yaml): `word_picker`.
- [`flows/word_picker_rounds.yaml`](flows/word_picker_rounds.yaml): `word_picker_rounds`,
  extended to use the features `word_picker` does not.

Started with `{"sentence": "potato tomate berry watermelon", "preferred_letter": "t", "requested_at": ...}`,
`word_picker` returns `["potato", "tomate"]`, and `report` pads each word to 8
characters: `["potato**", "tomate**"]`.

## Features covered

| Feature                                         | Where                                                                          |
| ----------------------------------------------- | ------------------------------------------------------------------------------ |
| Single-task flow (a basic queue)                | [`../hello`](../hello)                                                         |
| Flow name and semver version                    | both flows                                                                     |
| Flow inputs, with types                         | `inputs` in both flows                                                         |
| Datetime input and result                       | `requested_at`, `report`                                                       |
| Flow output                                     | `word_picker`: `output: tasks.keep_matching_words`                             |
| Flow without output                             | `word_picker_rounds`                                                           |
| Handler as import path                          | every task                                                                     |
| Same handler behind several tasks, across flows | `pick_last`: `latest_round`, `final_words`                                     |
| Queue per task                                  | `score_words` on `scoring`, the rest on `default`                              |
| References to task outputs and flow inputs      | `params`                                                                       |
| Fixed values                                    | `fixed_params` in `pad`, `long_enough`                                         |
| neorc metadata as inputs                        | `neorc.index`, `.item`, `.loop_count`, `.task_id`, `.attempts`, `.flow_run_id` |
| Loop, max cycles, boolean exit task             | `rounds`, `runs`, `padding`                                                    |
| Loop results in order, consumer picks the last  | `enough_rounds`, `pick_last`, `long_enough`                                    |
| Fan-out over a range                            | `picking`, `grading`                                                           |
| Fan-out over an upstream list                   | `decorate`                                                                     |
| Fan-out wrapping a chain of tasks               | `picking`: `pick_word` then `extract_word`                                     |
| Fan-in ordered by index                         | `keep_matching_words`, `report`                                                |
| Fan-out nested in a loop                        | `picking` in `rounds`                                                          |
| Loop nested in a fan-out                        | `padding` in `decorate`                                                        |
| Sub-flow, latest version                        | `picker` in `runs`                                                             |
| Idempotent handler, the user's responsibility   | `report`                                                                       |

## Resolving references, by example

| Consumer              | Reference               | Receives                                                      |
| --------------------- | ----------------------- | ------------------------------------------------------------- |
| `pick_word`           | `tasks.compose_payload` | a string: `compose_payload` is outside every loop and fan-out |
| `extract_word`        | `tasks.pick_word`       | its own branch's `pick_word` result per iteration so far      |
| `collect_words`       | `tasks.extract_word`    | iterations so far, each a list of 3 branch results            |
| `enough_rounds`       | `tasks.collect_words`   | `collect_words` results per iteration so far                  |
| `latest_round`        | `tasks.collect_words`   | every iteration's `collect_words` result                      |
| `keep_matching_words` | `tasks.score_words`     | 3 branch results, ordered by index                            |
| `report`              | `tasks.pad`             | one list per word, each the `pad` result per iteration        |
