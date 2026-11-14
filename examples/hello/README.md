# hello

Run a flow in one process, with nothing to deploy, from the repository root
after `uv sync`:

    uv run neorc run examples/hello --flow a

The worker prints `a`, and the command prints the run's output, `null`: these
flows have none. `--flow b` prints `b`.

## Deployed

A manager, a scheduler, a worker with two handlers (`a` prints `a`, `b` prints
`b`), and flow runs started on demand with `curl`. Every task belongs to a
flow, so each handler gets a single-task flow in `flows/`, which names its
function in `tasks.py` by import path. `tasks.py` does not import neorc. Run
each step from the repository root in its own terminal, after `uv sync`.

1. Postgres (skip if you have one; set `NEORC_DATABASE_URL` to it instead):

       uv run python examples/hello/postgres.py

   It prints an `export NEORC_DATABASE_URL=...` line; copy it for step 2.

2. The manager, and an API token for each process below, of its role:

       export NEORC_DATABASE_URL=...   # from step 1
       uv run neorc manager start --host 127.0.0.1 --create-schema --no-ui

       export NEORC_DATABASE_URL=...   # in another terminal
       uv run neorc queues declare default --kind worker
       uv run neorc tokens create ci --role ci
       uv run neorc tokens create scheduler --role scheduler
       uv run neorc tokens create worker --role worker --queue default
       uv run neorc tokens create me --role user

   Each prints its token's secret, once. In each terminal below,
   `export NEORC_API_TOKEN=<secret>` first, with the token the step names:
   the manager answers no request without one, and a role may do only what
   [roles.md](../../docs/specs/roles.md) lists. A token is sent over plain
   `http` only to a loopback address, as here.

   `--no-ui` because a checkout holds no built web UI; the `neorc-ui` wheel
   does, and `neorc manager start` serves it at `/ui/` unless told not to.
   To serve it from a checkout, build it and put it where the manager
   looks (`npm ci && npm run build` in `ts/neorc-ui`, then copy `dist/` to
   `python/neorc-ui/src/neorc_ui/static/`), and drop `--no-ui`. People sign
   in to the UI with an identity provider, configured as the
   [README](../../README.md#signing-in-with-google-or-okta) says; to open
   <http://127.0.0.1:8420/ui/> without one, and watch the runs below, start
   more and cancel them, start the manager with `--no-auth` instead, which
   needs no token anywhere: anyone who reaches the manager can then do
   everything.

3. Upload the flows, as CI/CD would, with the `ci` token:

       uv run neorc flows upload --manager-address 127.0.0.1:8420 \
           examples/hello/flows

4. The scheduler, with the `scheduler` token:

       uv run neorc scheduler start --manager-address 127.0.0.1:8420

5. The worker, with the `worker` token, serving the `default` queue it is
   bound to with the code in this directory:

       uv run neorc worker start --manager-address 127.0.0.1:8420 \
           --code-location examples/hello

6. Start runs, as often as you like, with the `me` token. The worker prints
   `a` or `b`:

       curl -X POST 127.0.0.1:8420/flows/a/runs \
           -H "authorization: Bearer $NEORC_API_TOKEN" \
           -H 'content-type: application/json' -d '{"inputs": {}}'

       curl -X POST 127.0.0.1:8420/flows/b/runs \
           -H "authorization: Bearer $NEORC_API_TOKEN" \
           -H 'content-type: application/json' -d '{"inputs": {}}'

   Each response carries the run's `id`; check on it with
   `curl -H "authorization: Bearer $NEORC_API_TOKEN" 127.0.0.1:8420/runs/<id>`.

[python/neorc/tests/test_end_to_end.py](../../python/neorc/tests/test_end_to_end.py)
runs these steps, on a Postgres of its own, and asserts what the worker
prints.
