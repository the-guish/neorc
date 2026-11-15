# neorc

A next generation orchestration system: flows of tasks, defined in YAML files,
run by workers wherever the work is. This is the reference implementation on
top of [`neorc-core`](https://pypi.org/project/neorc-core/) — a manager
service, the scheduler and worker commands, and adapters for HTTP, Postgres
and SQLite.

Every dependency is optional, so a host installs only what its role needs:

    pip install neorc                       # nothing extra
    pip install 'neorc[http]'               # scheduler and worker hosts
    pip install 'neorc[manager,postgres]'   # the manager host

## Running it

Try a directory of flow files in one process, with nothing to deploy:

    neorc run examples/wordplay --flow word_picker \
        --inputs '{"sentence": "potato tomate", "preferred_letter": "t"}'

A deployment runs a manager, a scheduler, and a worker per queue wherever the
work is:

    export NEORC_DATABASE_URL=postgresql://localhost/neorc
    export ONEBERTH_CONFIG=oneberth.toml          # the manager's public URL
    neorc manager start --create-schema
    neorc queues declare default --kind worker
    neorc tokens create ci --role neorc/ci       # each prints its secret, once
    neorc tokens create scheduler --role neorc/scheduler
    neorc tokens create worker-1 --role neorc/worker --resource default

    export NEORC_MANAGER_ADDRESS=https://manager.internal
    NEORC_API_TOKEN=<ci>        neorc flows upload myapp/flows
    NEORC_API_TOKEN=<scheduler> neorc scheduler start
    NEORC_API_TOKEN=<worker-1>  neorc worker start --code-location myapp

The manager is a component of [oneberth](https://github.com/the-guish/oneberth),
which signs people in and keeps the tokens. A request needs a signed-in person
or a token behind it. A token is sent only over HTTPS or to a loopback address.
Its role must allow the request. A service token holds `neorc/ci`,
`neorc/scheduler`, `neorc/worker` or `neorc/responder` (bound to one queue),
or `neorc/external-trigger`. A person's own token holds `neorc/user`. The
manager's API is under `/neorc/` at its address.

A manager on one machine keeps its data in a SQLite file with `neorc manager
start --database-file neorc.db`. The file is created at the first start, with
oneberth's store `oneberth.db` beside it. `--local` needs no sign-in
configuration: it prints the address that signs in the person at the machine.

Each task in a flow file names its handler by import path, `module:function`,
resolved from the worker's code location first. Handlers are plain or `async`
functions taking the task's inputs as keyword arguments; plain ones run in a
thread, and they need not import neorc.

## What is in it

- `neorc` — `Scheduler`, `Worker` and friends, re-exported from `neorc-core`;
  `NEORC_ROLES`, neorc's role table for oneberth, and `registry()`, which a
  host reads without building a manager
- `neorc.http` — `HttpManagerClient` and `HttpQueueClient`, the
  long-polling clients the scheduler and workers use
- `neorc.manager` — the manager as a oneberth component, `new_manager_webapp`,
  and standalone neorc, which composes it alone
- `neorc.postgres` — the store, the LISTEN/NOTIFY notifier, and the schema
- `neorc.sqlite` — the store in one SQLite file, on the standard library

See the [repository](https://github.com/open-shipyard/neorc) for the full
picture, and
[CONTRIBUTING.md](https://github.com/open-shipyard/neorc/blob/main/CONTRIBUTING.md)
to get started.
