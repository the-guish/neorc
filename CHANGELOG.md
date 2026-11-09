# Changelog

All notable changes to this project are documented in this file. It covers
every package in the repository; entries name the package they affect.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). All packages share
one version, cut from a single tag on `main`.

## [Unreleased]

### Added

- `neorc-ui`: opened with `?embedded` in its URL (`/ui/?embedded#/runs`), the
  UI leaves out its sidebar and shows the page alone, for an application that
  frames it beside a navigation of its own.
- Initial monorepo structure: a uv workspace with the `neorc-core` and `neorc`
  Python packages under `python/`.
- `neorc-core`: a task's status transitions, the same in every store, and the
  `TaskNotifier` port.
- `neorc-core.local`: `MemoryTaskNotifier`, for exercising the system without
  a database.
- `neorc-core.testing.contracts`: test suites written against the ports, which
  the in-memory adapters, the Postgres adapters and the HTTP clients all run.
- `neorc-core.flows`: `RunState`, a run's step results addressed by loop
  iteration and fan-out index, and `resolve`, which gives a reference's value
  for a consumer as the "Resolving references" table of the flows spec does.
- `neorc-core`: the planner, `plan(definition, run_state)`, a pure function
  that publishes every ready task instance and moves loops and fan-outs on,
  starts sub-flow runs, succeeds a run with its output once every step
  finished, and fails it when a task or sub-flow run fails.
- `neorc-core`: the `Store` port for flow versions, runs, their tasks and events,
  one method per atomic operation; `MemoryStore`; and `StoreContract`, the
  test suite every store must pass.
- `neorc-core`: `Manager`, uploading flows deployed together under the
  version rules, starting runs on the latest version with their inputs checked
  against the declared types, and cancelling, failing and succeeding run trees.
- `neorc-core`: `Manager` publishes tasks and starts sub-flow runs for the
  scheduler, checking the size of the complete payload; hands workers their
  tasks with references filled in; records starts and results; and long-polls
  events for the scheduler.
- `neorc-core`: the `QueueClient` and `ManagerClient` ports, the direct
  clients that reach a manager in the same process through JSON, as HTTP would,
  and `QueueClientContract` and `ManagerClientContract`.
- `neorc-core`: `Worker`, which checks at startup that every handler on its
  queue imports and takes exactly its task's inputs, then runs handlers with
  their inputs as keyword arguments, plain functions in a thread. Handler
  resolution by import path moves from the `neorc` command to `neorc-core`.
- `neorc-core`: the `Scheduler`, which waits for events, plans each run they
  concern and applies the actions, failing a run whose request is rejected; and
  `LocalCluster` and `run_local`, a manager, scheduler and one worker per queue
  in one process.
- `neorc run <dir> --flow <name> --inputs <json>`: run a flow to its end in
  one process, with nothing to deploy. `examples/hello` and `examples/wordplay`
  run with it, and their outputs are asserted in the tests.
- Receiving a task takes a lease: workers claim a task before running it and
  heartbeat to hold it, and a task whose lease lapses returns to the queue. Chosen so the queue can move to SQS unchanged.
- `neorc.postgres`: a LISTEN/NOTIFY notifier whose waiters hold no connection.
- `neorc.manager`: the FastAPI application, and a service that assembles it from
  `NEORC_DATABASE_URL`.
- `neorc`: the console script — `neorc manager start` and `neorc worker start`.
  A command whose extra is missing says which one to install rather than
  raising `ModuleNotFoundError`.
- Workers stop on `SIGINT` and `SIGTERM`, cutting an idle long poll short so
  shutdown does not outlast a supervisor's grace period. A task already being
  executed is always allowed to finish.
- `neorc.postgres`: the tables for flows — `neorc_flow_versions`,
  `neorc_runs`, `neorc_flow_tasks` and `neorc_events` — created by
  `create_schema` next to `neorc_tasks`, with the row mapping to and from the
  core's dataclasses. Values are stored as their compact JSON text, so numbers
  read back exactly as written; a flow version's content is canonical JSON.
- `neorc.postgres`: `PostgresStore`, the `Store` for flows on Postgres:
  uploads under an advisory lock, runs and sub-flow runs, succeeding, failing
  and cancelling run trees under their root's row lock, and events appended
  last under a second advisory lock so their sequences commit in order; tasks
  published and claimed under the same root lock, received oldest first with
  `FOR UPDATE SKIP LOCKED`, and a run's state read in one snapshot. It passes
  the store contract in full. A version part is at most 2³¹ − 1, what the
  store's `integer` columns hold, in every store.
- `neorc.manager`: the flow routes: `POST /flows`, `GET /flows/{name}`
  and `/flows/{name}/versions/{version}`, `POST /flows/{name}/runs`,
  `GET /runs/{id}`, `POST /runs/{id}/cancel` and `GET /runs/{id}/state`.
  Bodies are read as bytes, capped at 16 MiB and checked for nesting depth
  before anything parses them. Every core error crosses as
  `{"error": "<class>", "detail": ...}` with a status per class, and a
  `FlowDefinitionError` carries its `problems`.
- `neorc.manager`: the routes the scheduler and workers use. For the
  scheduler, `GET /events` long-polled up to the manager's deadline,
  `POST /runs/{id}/tasks` and `/sub-runs` by address, `/succeed` with the
  output reference and `/fail`. For workers, `GET /queues/{queue}/tasks` for
  the task definitions, `POST /queues/{queue}/tasks/receive` long-polled, and
  `POST /tasks/{id}/claim`, `/heartbeat` and `/finished`. In core, a
  lease is at most a day and a run
  is succeeded only with a reference to one of its flow's tasks or
  sub-flows, so every client is refused the same requests.
- `neorc.http`: `HttpManagerClient` and `HttpQueueClient`, the flow
  clients over HTTP, held to the client contracts against the real
  application. An error body raises the core exception it names, with a
  `FlowDefinitionError`'s problems intact; a transport failure, or an answer
  that is not the manager's, is `ManagerUnavailableError`. `Worker` fails
  a result over the payload limit itself, once, rather than have the report
  refused on every lease, and reports an error message with what no store or
  transport could carry replaced. In core, a value nests at most 100 levels,
  half of what JSON text may, so it fits in whatever it travels in; and a
  queue name is letters, digits, `_` and `-`, as it travels in a URL path.
- `neorc.manager`: `build_app` assembles `Manager` on `PostgresStore`
  with two notification channels, `neorc_task_ready` for workers and
  `neorc_event_ready` for the scheduler, so a task or event recorded through
  one manager process wakes a waiter on another. `--create-schema` creates
  the flow tables too. An announcement is a hint, so a request never waits
  for one: `PostgresTaskNotifier` sends them from a background task, one per
  batch, on a connection it reopens when it breaks; the listening connection
  is reopened too, and both are kept alive by the kernel so a link dropped
  without a word is found dead within a bound.
- `neorc flows upload <dir> --manager-address`: read and validate the flow
  files as `neorc run` does, send them as one set, and print what was
  stored. `neorc scheduler start --manager-address` runs the scheduler on the
  HTTP client, stopping on `SIGINT` and `SIGTERM`. `neorc worker start
  --manager-address --code-location [--queue]` runs a worker for flows on the
  HTTP client; handlers that do not fit the queue's tasks stop it at startup
  with every problem listed.
- `neorc-core.testing.examples`: the example scenarios, `hello`,
  `word_picker` and `word_picker_rounds`, run through any `ManagerClient`
  with their outputs asserted. Core runs them on `LocalCluster`; `neorc` runs
  them deployed, on uvicorn, Postgres and the HTTP clients, with a scheduler
  and one worker per queue. The examples' deployed steps are runnable as
  their READMEs give them.
- `neorc-core`: a string holding NUL, in a value or anywhere in a flow
  definition, is refused in core before any store sees it, because Postgres
  `text` cannot hold it and the in-memory store must refuse what the deployed
  one would. Task errors and run reasons, which are messages, are stored with
  NUL replaced instead.

- `neorc-core`: the `Store` port lists, for a status page: `list_runs`,
  newest first with filters and a page cursor, `flow_versions`, `run_tasks`
  and `sub_runs`; `Manager` exposes them. Runs and tasks carry the times the
  store created, started and finished them, on the wire too. Both stores
  implement it; on Postgres the times are the server's `now()`, and runs get
  a `position` allocated under the event lock, so they list in the order
  they committed and a page never skips one. Existing tables get the new
  columns on `create_schema`.
- `neorc.manager`: the listings a status page reads, `GET /flows`,
  `GET /flows/{name}/versions`, `GET /runs` with filters and a page cursor,
  `GET /runs/{id}/tasks`, `GET /runs/{id}/sub-runs` and `GET /tasks/{id}`.
  The application's OpenAPI schema is committed as `ts/neorc-ui/openapi.json`,
  written by `scripts/export_openapi.py` and checked by a test, for the UI
  build to generate its types from without running Python. Every response is
  documented in it with the shape the manager sends, by models a test holds
  to the wire forms.
- `neorc-ui`: a new distribution carrying the manager's web UI as built
  assets, with `neorc_ui.static_dir()` to find them and no dependencies.
  `neorc[manager]` brings it. The UI's source is `ts/neorc-ui`: React,
  TypeScript and Vite, built by CI and by the wheel build when the assets are
  missing; a wheel from the sdist needs no Node.js. The build fails on a
  bundled package outside the license allowlist or over the size budget, and
  writes the committed `bundled-packages.txt`. Dependencies follow
  `contributing/js-dependencies.md`: exact versions, install scripts off,
  Dependabot with a cooldown, advisories and signatures checked in CI.
- `neorc manager start` serves the web UI at `/ui/` and sends `/` there;
  `--no-ui` serves the API alone. Hashed assets are cached as immutable and
  the page is not; every UI response carries a Content-Security-Policy that
  keeps scripts and connections to the manager's own origin.
- `neorc-ui`: the flows and runs pages. Flows with their versions and the
  definition as uploaded, read-only; runs newest first, a page at a time,
  filtered by flow and status, with a status badge shared with tasks; and a
  run's own page with its inputs, output and times. The page follows the
  manager's event log with the same long poll the scheduler uses, and
  refetches what an event concerns, starting from `GET /events/latest`, a
  new route giving the latest sequence so no reader has to read the whole
  log to find its end. Hash routing, TanStack Query, and component tests
  on Vitest against responses typed by the schema.
- `neorc-ui`: a run's page draws every step of its flow's definition with
  what the run did there: tasks with their status and details, loops with
  their iterations, fan-outs with their branches, and sub-flows linking to
  their runs; the run's parent and sub-runs; and, while the run is active,
  a refresh every two seconds, since a task being published, received or
  claimed records no event. Tested on a recorded
  `word_picker_rounds` run, written by `scripts/record_ui_fixture.py`.
- `neorc-ui`: a run is started from its flow's page, with a field per
  declared input typed as declared, a datetime sent with the browser's
  offset, and a JSON editor for what the fields cannot say; the manager's
  refusal is shown next to the form. An active run is cancelled from its
  page after a confirmation naming its active sub-runs.
- A browser test: Chromium on Playwright, against the manager on uvicorn and
  Postgres serving the built UI with the wordplay example deployed, starts a
  run from the form, watches it succeed with its fan-out's results, cancels
  another, and finds no browser complaint. It skips without a built UI or a
  browser; CI builds and fetches both for one Python.
- The rules for the UI's dependencies, `contributing/js-dependencies.md`,
  are pointed to from every place a change to them starts: the UI's README
  and an `AGENTS.md` for coding agents, the allowlist and bundled-package
  list in `vite.config.ts`, `.npmrc`, the Dependabot configuration, and the
  CI steps that fail on them.
- `neorc-ui`: a run's steps as a graph, beside the outline: one node per
  step instance, iterations and branches as boxes around them, an edge from
  every step instance a reference resolves to, the fan-out's `over` into
  each of its branches, laid out top to bottom by dagre a container level at
  a time and drawn by React Flow, zoomable, with a minimap and a task's
  details in a side panel. The choice of view is in the address,
  `#/runs/<id>?view=graph`, and remembered. `@xyflow/react` and
  `@dagrejs/dagre` join the bundle, vetted as `contributing/js-dependencies.md`
  asks; the bundle goes from 75 to 152 KB gzipped, against a budget of 500.
- `neorc-ui`: a new look. A sidebar with the flows, the latest runs with
  their status dots and a stand-in for a profile, collapsible to a rail of
  icons; a warm off-white ground with white cards, status pills, small
  uppercase labels, a monospace face for ids and durations, in light and
  dark; the runs list with counts by status among the runs loaded, and a
  run's page with a breadcrumb, its facts on one line and its steps as
  cards. The browser test also switches to the graph, zooms it and opens a
  task's details from a node.
- `neorc-core`: `Access`, API tokens, sessions and sign-ins in progress on
  the `CredentialStore` port, which keeps only the SHA-256 of a secret;
  `MemoryCredentialStore` and `CredentialStoreContract`. An allow list says
  who may sign in: everyone, a subject, a verified email or its domain,
  Google's hosted domain, or a group. `AuthenticationError` crosses HTTP as
  401, `SignInRefusedError` and `CrossSiteRequestError` as 403, and
  `UnsupportedMediaTypeError` as 415. The scheduler and worker stop on a
  refused token, failing no run; a heartbeat refused ends the handler
  holding the task, and `neorc worker start` its process.
- `neorc.postgres`: `PostgresCredentialStore`, on `neorc_api_tokens`,
  `neorc_sessions` and `neorc_pending_logins`, created by `create_schema`.
- `neorc.manager`: every route of the API needs an API token or a session;
  `/health`, the UI and `/openapi.json` stay open, and the Swagger and ReDoc
  pages go when authentication is on. A write is refused in every mode when a
  browser says another site sent it, or when it is not sent as JSON; a
  session's write must come from the public URL's origin. The HTTP clients
  send a token, never over plain http beyond loopback unless allowed.
- `neorc tokens create|list|revoke` and `neorc sessions clear`, on the
  manager's database. `neorc manager start` asks for tokens unless
  `--no-auth`, checks the credential tables are there, serves HTTPS with
  `--ssl-certfile` and `--ssl-keyfile`, and signs people in with
  `--auth-config`. `flows upload`, `scheduler start` and `worker start` send
  `NEORC_API_TOKEN`.
- `neorc.auth`: sign-in with OpenID Connect providers, Google and Okta
  among them, from `auth.toml`: the code flow with state, nonce and PKCE,
  the ID token's claims checked, sessions in `__Host-` cookies, at most
  10,000 sign-ins in progress. The `/auth` routes: `session`, `login`,
  `callback`, `logout`.
- `neorc-ui`: a sign-in page with a link per provider and the reason a
  sign-in failed; the profile shows who is signed in and signs them out;
  the no-authentication banner only for `--no-auth`. A session ending
  mid-page shows the sign-in page, and the page the reader was on is
  restored after signing in. The browser test signs in through a stand-in
  OpenID provider and runs its deployment with a token.
- Roles, as `docs/specs/roles.md` lists them. `neorc-core`: `Role`,
  `Permission`, `PERMISSIONS` and `authorize`; every token has one role, and a
  worker token is bound to one queue; a signed-in person is a `user`.
  `neorc.manager`: every route asks for the one permission it needs, and
  answers `PermissionDeniedError` with 403 when the role may not, or when a
  worker token names another queue; a task on another queue is not found.
  `neorc tokens create` takes `--role` and, for a worker, `--queue`; `tokens
  list` shows them. The scheduler, the worker and the CLI stop on a 403 as on
  a refused token, under their common base, `AccessError`.
- `neorc.postgres`: an optional `schema` on `PostgresStore`,
  `PostgresCredentialStore`, `create_schema`, `drop_schema` and
  `ensure_credential_tables`, to keep neorc's tables in a PostgreSQL schema of
  their own: the connections' `search_path` is that schema alone, and
  `create_schema` creates it if it does not exist.

### Changed

- The flow classes take the task API's names: `FlowManager` is `Manager`,
  `FlowWorker` is `Worker`, `FlowQueueClient` is `QueueClient`, `FlowTask` is
  `Task`, `HttpFlowQueueClient` is `HttpQueueClient`, `DirectFlowQueueClient`
  is `DirectQueueClient`, `FlowQueueClientContract` is `QueueClientContract`,
  and the worker routes move from `/flow-tasks` to `/tasks`. The table keeps
  its name, `neorc_flow_tasks`, since a database from before holds the old
  `neorc_tasks`, which create-if-absent would leave in place.
- `neorc-core`: a task's id is a random UUID made when it is first published,
  not derived from its run and address, so a worker cannot compute the id of a
  task it did not receive. Publishing an address again still returns the first
  task. `Manager.claim_task`, `extend_lease` and `report_finished` take the
  queue a worker token is bound to.
- `neorc.postgres`: the adapters run on asyncpg (Apache-2.0) instead of
  psycopg 3 and psycopg-pool (LGPL-3.0); the `postgres` extra installs
  `asyncpg`. The classes and functions `neorc.postgres` exports keep their
  signatures and accept the same connection strings: URLs, and libpq's
  `key=value` strings, which are translated, `connect_timeout` included;
  libpq's keepalive parameters are dropped, since asyncpg has none. A store's
  `pool` is now an asyncpg pool, `pool.acquire()` where it was
  `pool.connection()`. Opening a store fails at once when the database cannot
  be reached, where psycopg-pool retried for 30 seconds. The notifier's
  listening connection finds a silently dropped link by repeating its `LISTEN`
  every 10 seconds, with a 20-second bound on the answer, in place of libpq's
  TCP keepalives; sending an announcement is bounded at 30 seconds.

### Security

- `neorc-core`: a handler must be a function defined in a module under the
  worker's code location, at the path its name gives from there and not part
  of a package installed there, checked before the module is imported and again
  whenever a task names a handler the worker has not met, so an uploaded flow
  can no longer have a worker call `subprocess:run`, `os:system` or anything
  else importable. `Worker` and `LocalCluster` now require `code_location`.

### Removed

- The task API, which let a task be published on its own: `TaskStore`,
  `Manager`, `QueueClient`, `Worker`, `Task`, `Payload`, `MemoryTaskStore`,
  `DirectQueueClient`, `TaskStoreContract`, `QueueClientContract`,
  `PostgresTaskStore`, `HttpQueueClient`, the `/tasks` routes, the
  `neorc_tasks` table, and `neorc worker start --tasks`. Every task belongs to
  a flow; a basic queue is a flow with a single task, as `examples/hello`
  shows. `create_schema` leaves a `neorc_tasks` table from before in place.

[Unreleased]: https://github.com/open-shipyard/neorc/commits/main
