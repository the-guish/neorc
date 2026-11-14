# neorc

A next generation orchestration system: flows of tasks, defined in YAML files
under version control, run by workers wherever the work is.

This repository is a monorepo. The packages it publishes:

| Package                           | Description                             |
| --------------------------------- | --------------------------------------- |
| [`neorc`](python/neorc)           | Manager service, scheduler and worker commands, Postgres and HTTP adapters |
| [`neorc-core`](python/neorc-core) | The mechanism: flow definitions, the planner, ports, and the manager, scheduler and worker logic |

## How it works

A flow file names its tasks, the queue each runs on, the function that runs it,
and where its inputs come from: the flow's inputs, or other tasks' results. A
**manager** service holds the flows and their runs. A **scheduler** moves runs
forward as tasks finish. **Workers** on other hosts receive the next task on
their queue by long-polling the manager, claim it, run its handler, and report
back.

    scheduler > http > manager > database
    worker    > http > manager > database

Receiving a task takes a *lease*, and the worker claims the task before running
it: the worker heartbeats while it runs, and a task whose lease lapses goes back
to the queue, so nothing is lost when a worker dies. Delivery is at-least-once, so handlers should be idempotent.

## Quick start

Try a directory of flow files in one process, with nothing to deploy:

    uv run neorc run examples/wordplay --flow word_picker \
        --inputs '{"sentence": "potato tomate berry watermelon", "preferred_letter": "t"}'

A deployment installs what each host needs — the base install pulls in nothing:

    pip install 'neorc[manager,postgres]'   # the manager host
    pip install 'neorc[http]'               # scheduler and worker hosts

Start the manager, declare the queues, and create an API token for each
process that will reach the manager. Every request needs one; `neorc tokens
create` prints its secret once:

    export NEORC_DATABASE_URL=postgresql://localhost/neorc
    neorc manager start --create-schema \
        --ssl-certfile manager.pem --ssl-keyfile manager-key.pem
    neorc queues declare default --kind worker
    neorc tokens create github-actions --role ci
    neorc tokens create scheduler --role scheduler
    neorc tokens create worker-1 --role worker --queue default
    neorc tokens create bookings-webhook --role external-trigger

Each token has a role, and may do only what its role allows
([roles.md](docs/specs/roles.md)):

| Role               | May                                                                    |
| ------------------ | ---------------------------------------------------------------------- |
| `ci`               | upload flows                                                           |
| `scheduler`        | read flows, runs and events; publish tasks, start sub-runs, end runs   |
| `worker`           | fetch, receive, claim, heartbeat and finish the tasks of its one queue |
| `responder`        | list and complete the pending tasks of its one queue                   |
| `user`             | read flows, runs and events; start and cancel runs                     |
| `external-trigger` | start runs                                                             |

A person signed in to the UI is a `user`; a `user` token is for their scripts.
A token is sent only over HTTPS, or to a loopback address: serve the manager
with a certificate as above, or behind a proxy that terminates TLS. `neorc
tokens list` and `neorc tokens revoke <name>` manage them. To try it on one
machine nobody else reaches, `neorc manager start --no-auth` asks for none,
and allows everything.

Write the work, as plain functions that need not import neorc:

```python
# myapp/tasks.py
def greet(name):
    print(f"hello {name}")
```

Describe it in a flow file:

```yaml
# flows/hello.yaml
name: hello
version: 1.0.0
inputs: {name: string}
steps:
  greet:
    handler: myapp.tasks:greet
    params: {name: inputs.name}
```

Upload the flows, start the scheduler, and a worker on any host that can reach
the manager, with the code the handlers import from, each with its token in
`NEORC_API_TOKEN`:

    export NEORC_MANAGER_ADDRESS=https://manager.internal:8420
    NEORC_API_TOKEN=<github-actions> neorc flows upload flows
    NEORC_API_TOKEN=<scheduler>      neorc scheduler start
    NEORC_API_TOKEN=<worker-1>       neorc worker start --code-location .

Start a run from anywhere, with a token that may:

    curl -X POST https://manager.internal:8420/flows/hello/runs \
        -H "authorization: Bearer <bookings-webhook>" \
        -H 'content-type: application/json' -d '{"inputs": {"name": "world"}}'

Or from the web UI the manager serves at `https://manager.internal:8420/ui/`:
the flows, each with its steps drawn as a graph, the runs and every task of
each, live, as an outline or as a zoomable graph of the run's steps, with a
form to start a run and a button to cancel one. People sign in to it with an identity provider, as below; a
manager started with `--no-auth` asks nobody, and says so on every page.

A flow can give that form its own fields, a list of values, radio buttons, a
check box, in a file next to the flow's: [docs/architecture/forms.md](docs/architecture/forms.md),
and [examples/code_task](examples/code_task) to try one.

[examples/hello](examples/hello) and [examples/wordplay](examples/wordplay)
walk through both ways of running, step by step.

## Tasks for a person or another system

Some tasks are done outside a neorc worker: an approval a person gives, or
work another system does. Such a task waits on an *external* queue until a
*responder* completes it by its id, with no lease to keep
([task-completion.md](docs/specs/task-completion.md)).

Declare the queue external, and create a responder token for it:

    neorc queues declare editors --kind external
    neorc tokens create inbox-app --role responder --queue editors

A step names that queue. Its handler is a text for the responder:

```yaml
approve_draft:
  queue: editors
  handler: approve-draft
  params: {draft: tasks.write_draft}
```

The responder lists the pending tasks of its queue, each with its inputs
filled in, and completes one with a result, or with an error that fails it:

    curl https://manager.internal:8420/queues/editors/tasks/pending \
        -H "authorization: Bearer <inbox-app>"
    curl -X POST https://manager.internal:8420/tasks/<task id>/complete \
        -H "authorization: Bearer <inbox-app>" \
        -H 'content-type: application/json' -d '{"result": {"approved": true}}'

A task is completed once: a second completion answers 409. Workers are kept
off an external queue. A responder token for a worker queue completes a task
there until a worker receives it.

Every queue is declared before it is used, a worker queue with `--kind
worker`. An upload naming a queue nobody declared is refused, and so is a
token for one. `neorc queues list` shows the declared queues. `neorc run`
declares the queues of its flow files as worker queues and starts no
responder, so a run with a task for a person waits there.

## Signing in with Google or Okta

The UI signs people in with OpenID Connect. Describe the providers, and who
may sign in, in a file the manager reads; secrets stay in the environment:

```toml
# auth.toml
public_url = "https://manager.internal:8420"   # where people open the UI

[providers.google]
title = "Google"
issuer = "https://accounts.google.com"
client_id = "1234.apps.googleusercontent.com"
client_secret_env = "NEORC_GOOGLE_CLIENT_SECRET"

[providers.okta]
title = "Okta"
issuer = "https://example.okta.com/oauth2/default"
client_id = "0oa..."
client_secret_env = "NEORC_OKTA_CLIENT_SECRET"
groups_claim = "groups"
scopes = ["openid", "email", "profile", "groups"]

[[allow]]
provider = "google"
hosted_domain = "example.com"        # everyone in the example.com Workspace

[[allow]]
provider = "google"
email = "ada@gmail.com"              # one person, by a verified address

[[allow]]
provider = "okta"
group = "neorc-users"
```

    export NEORC_GOOGLE_CLIENT_SECRET=... NEORC_OKTA_CLIENT_SECRET=...
    neorc manager start --auth-config auth.toml \
        --ssl-certfile manager.pem --ssl-keyfile manager-key.pem

Register `{public_url}/auth/callback/<provider id>` with each provider as the
redirect URI, here `https://manager.internal:8420/auth/callback/google`. An
`[[allow]]` entry names a provider and one of `everyone = true`, `subject`,
`email`, `email_domain`, `hosted_domain` (Google only) or `group`; anyone no
entry matches is refused. For Google, an address counts only in a Workspace
or at Gmail, since Google verifies personal accounts made with any address,
so `email_domain` is refused for it: use `hosted_domain`. Everyone signed in,
and every token, may do everything the API allows; roles are for later.

`neorc sessions clear` signs everyone out, and a changed allow list applies
from each person's next sign-in. The manager is served from the root of its
origin; behind a proxy, `public_url` is the address people type.

## Documentation

- [docs/specs/core.md](docs/specs/core.md) — the components and what they owe
  each other
- [docs/specs/flows.md](docs/specs/flows.md) — flow files: tasks, loops,
  fan-outs, sub-flows and references
- [docs/specs/task-completion.md](docs/specs/task-completion.md) — tasks a
  person or another system completes: queue kinds, pending tasks, responders
- [docs/specs/postgres-implementation.md](docs/specs/postgres-implementation.md)
  — how the reference persistence layer receives, leases and recovers
- [docs/working-notes/sso-plan.md](docs/working-notes/sso-plan.md) — API
  tokens and signing in: what is checked, where, and why
- [docs/working-notes/web-ui-plan.md](docs/working-notes/web-ui-plan.md) — the
  web UI: what it shows, how it is built and shipped, and why
- [CONTRIBUTING.md](CONTRIBUTING.md) — repository layout and development setup

Licensed under the [Apache License 2.0](LICENSE).
