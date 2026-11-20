# Changelog

All notable changes to this project are documented in this file. It covers
every package in the repository; entries name the package they affect.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). All packages share
one version, cut from a single tag on `main`.

## [Unreleased]

### Added

- `neorc-core`, `neorc`: `queues.toml` beside the flow files names each queue
  with its kind. `neorc flows upload` declares those queues, then uploads the
  flows; `LocalCluster.upload` does too. The new route `PUT /queues/{queue}`,
  with the permission `neorc/queues:declare` of `neorc/ci`, declares a new
  queue, and takes one again with the kind it has. A queue declared with
  another kind is refused with a 409, `QueueKindError`: its operator changes a
  kind. `ManagerClient.declare_queue` and `Manager.add_queue` do the same.
- `neorc`: the HTTP clients send every write as JSON, as oneberth asks.

### Changed

- `neorc`, `neorc-core`, `neorc-ui`: neorc is built on oneberth, pinned by
  commit, and gets FastAPI, Starlette and uvicorn through `oneberth[web]`.
  oneberth signs people in, keeps sessions and tokens, checks each route's
  permission, applies the checks on a request and the security headers.
  `new_manager_webapp` gives the manager as a oneberth component named
  `neorc`, and its API is under `/neorc/`. Standalone neorc composes it alone
  with `compose_app`. The roles are oneberth roles, `neorc/user`,
  `neorc/worker`, `neorc/responder`, `neorc/scheduler`, `neorc/ci` and
  `neorc/external-trigger`, with permissions named `neorc/...`. `neorc-core`
  keeps them as plain data and imports nothing of oneberth; the rules between
  a queue's kind and its tokens are `QueueDeclarations`, and oneberth runs
  neorc's check of a token's queue for every service token it creates.
  `neorc manager start`
  reads oneberth's TOML file, `--config` or `ONEBERTH_CONFIG`, or runs in
  oneberth's local mode with `--local`. `neorc tokens`, `sessions`, `devices`
  and `store` are oneberth's admin scripts, with neorc's roles and
  `--database-file` too. On a SQLite file, oneberth's store is `oneberth.db`
  beside it. The clients reach the API under `/neorc/` at a backend's
  address, and the UI reads the session from oneberth's `/auth/session`.
- `neorc-ui`: the interface is `@neorc/ui`, a node package with one root
  component, `NeorcApp`, for oneberth's shell (`@oneberth/ui`). A host gives
  it the address of neorc's API and what the shell gives every application;
  `neorcApp({ api, forms })` registers neorc with the shell. Its routes are
  under `#/neorc/`, through the shell. A hidden neorc stops its long poll and
  its polls. The shell draws the rail, the sign-in page and the account; neorc
  draws its own panel, which the shell puts in its drawer on a small screen.
  Every class starts with `neorc-`, and the colours are oneberth's tokens. A
  host may give `forms`, a source of A2UI forms that take the place of the
  form to start a run of a flow. The built interface is standalone neorc: the
  shell with one application, neorc. React is 19.3.1. `@oneberth/ui` comes
  from the oneberth commit `pyproject.toml` pins, packed by
  `scripts/oneberth-ui-from-source.sh`.
- `neorc`: the manager's long polls, the events and a worker's receive, end
  when the client leaves and when the backend begins to stop. The answer then
  says that nothing came yet. A backend with a waiting client stops at once,
  instead of waiting out the server's grace.
- `neorc`: oneberth is at `822e025`. A backend that does not start says why
  in its last line, where it said that the front listener did not start: the
  component that failed with its error, or oneberth's own part, such as a
  store that will not open.
- `neorc-ui`: `package-lock.json` holds the hash of `@oneberth/ui` packed
  from that oneberth. It held the hash from `52da9df`, so `npm ci` refused
  the tarball wherever npm had no cache, and `scripts/neorc-ui-pack.sh`
  failed there.

### Removed

- `neorc-ui`: the `?embedded` switch, its own session, sign-in page and
  profile with sign-out: they are the shell's.

- `neorc`, `neorc-core`: neorc's own identity core, sign-in configuration,
  OpenID Connect client, cookies, `/auth` routes, credential stores and
  credential tables; `--no-auth`, `--auth-config` and `--ssl-certfile`;
  `create_app`. TLS ends in front of the backend.

### Added

- `neorc-core`: a switch runs one of several named cases. A step with `switch`
  has `on` and `cases`. `on` refers to a string input, a task, a sub-flow or
  another switch, and gives the name of a case; another value fails the run.
  Each case has `steps`, which may be empty, and `output`, which is `null` or
  a step inside the case. The steps of the chosen case run, and no other
  case. `switches.<name>` is the output of the chosen case, for steps outside
  the switch and for a flow's `output`. A switch adds no list level. In a loop
  it chooses a case again in each iteration, and a step of a case reads `null`
  for an iteration where its case did not run. A flow file is refused when a
  step outside a case refers to a step inside it. `SwitchStep` and `Case` are
  the model, `FlowDefinition.cases_around` gives the cases a step is in, and
  `switch_case` the case a switch chose in a run. `examples/vip_booking` is
  the flow of [switch.md](docs/specs/switch.md).
- `neorc-ui`: a flow's page and a run's page show a switch. In the graph a
  switch is a box that holds one box per case, with the steps of the case
  inside. An edge joins what `on` reads to the switch, and the switch to each
  step that reads `switches.<name>`. In the outline a switch lists its cases.
  A run's page says which case the run chose: it reads what `on` names, an
  input, a task's result, a sub-flow's output or another switch's output. In
  a loop or fan-out the run has not entered, no case is chosen yet.
- `neorc-core`: a task's handler may be an executable file, so a task can be
  written in any language. The file is in the `executables` directory of the
  worker's code location, and the handler names its path:
  `handler: executables/close_month.sh`. The Python worker starts it as a
  process of its own, with no shell and no arguments. It writes the task's
  inputs to standard input as one JSON object. The JSON value on standard
  output is the task's result. An exit code other than 0 fails the task, with
  the end of standard error as its error. The worker refuses a path that is not
  plain names, or a file that leads out of `executables`, at its start and when
  a task names it. An executable may touch the file `NEORC_HEARTBEAT_FILE`
  names. Once it has, 60 seconds without a touch end the executable and fail
  the task. The worker stays free while an executable runs. A cancelled task
  ends its executable and what it started. `encode` takes a depth `limit`, as
  `decode` does. See [workers-and-manager.md](docs/specs/workers-and-manager.md),
  "Executables".
- `neorc-ui`: `@neorc/ui` exports `formOf`, the reader of a form, and its
  type `Form`. A host's test checks the form it gives neorc against neorc's
  subset with `formOf({ form: messages })`.
- `neorc`, `neorc-core`, `neorc-ui`: a run records who started it, as
  `RunStarter`: a person or a service, with oneberth's id and name for it. The
  manager's route that starts a run takes it from the principal; `Manager.start_run`
  and `Store.start_run` take `started_by`, and a sub-flow run gets its
  parent's. Every store keeps it. A run's
  answer has `started_by`, and the run's page shows it. A task asks for it as
  `neorc.started_by`.
- `neorc`: `neorc.NEORC_ROLES` and `neorc.registry()` give neorc's role table,
  and oneberth's with it, to a host that builds no manager.
- `neorc`: `neorc.sqlite.SqliteStore`, the `Store` in one SQLite file, on the
  standard library's `sqlite3`. It passes the store contract. One thread and
  one connection serve it, one transaction at a time, and the event loop
  awaits each. The file uses the write-ahead log, syncs every commit, and
  holds neorc's `application_id` and the schema version, `SCHEMA_VERSION`; a
  file of another version or application is refused with
  `SchemaVersionError`. `build_app` and `run` take `database_file`, and
  `neorc manager start --database-file` serves a manager on that file, its
  notifiers in memory. The SQLite store keeps no credentials, so a manager on
  it needs `--no-auth`, and is refused without it. `neorc queues declare` and
  `neorc queues list` take `--database-file` too.
  [sqlite-implementation.md](docs/specs/sqlite-implementation.md) has the
  choices.
- `neorc-core`, `neorc-ui`: a task's form can read the task's inputs and end
  with buttons, so a person triages a list on a queue's page. The inputs seed
  the form's data under `/inputs`, read only. A `Text` may show a value there,
  and a `List` may repeat a template once per item of a list in them; inside
  the template, a path without a leading slash is relative to the item. A
  control in the template submits one value per item. A `Button` completes
  the task, setting the one field its action's context names, and a form with
  buttons has no other "Complete". `Row`, `Card` and a `ChoicePicker` shown as
  `chips`, a bar of buttons, join the subset, for a flow's form too. The
  manager holds every completion to the form with the task's inputs: one value
  per item of the list, each one the control takes, and the field of the
  buttons one of their values. At upload, a task's form reads only the task's
  params, and a flow's form has no template, bound text or button.
  `check_form_result` takes the task's inputs, and `form_problems` the task's
  params. `examples/review_loop` is a loop that waits for a person to triage
  a review's issues each round.
- `neorc-core`, `neorc`: task completion, as
  [task-completion.md](docs/specs/task-completion.md) has it. A queue has a
  kind, `worker` or `external`, declared with `neorc queues declare` and
  listed with `neorc queues list`. Every queue is declared before it is used:
  an upload naming a queue nobody declared is refused, a worker or responder
  token is bound only to a declared queue, and no worker is served from an
  undeclared one. An existing deployment declares its queues, `default`
  included, before its next upload. `neorc run` and `LocalCluster.upload`
  declare the queues of their flow files as worker queues.
  The tasks of an external queue are handed to no worker: a responder lists a
  queue's pending tasks with their inputs filled in, at
  `GET /queues/{queue}/tasks/pending`, and completes one by its id, with a
  result or an error and no lease, at `POST /tasks/{task_id}/complete`. A task
  is completed once, only while it is pending and its run active. The new
  `responder` role holds the two permissions, `queue:pending` and
  `tasks:complete`, on the one queue its token is bound to, of either kind. A
  worker token is refused an external queue, and a queue with worker tokens
  is refused the external kind. `Store` gains `pending_tasks`,
  `complete_task`, `declare_queue`, `queues` and `queue_kind`, with their
  contract tests. A database created before this needs
  `neorc manager start --create-schema` for the new `neorc_queues` table, and
  keeps role constraints that refuse a responder token.
- `neorc-core`, `neorc`, `neorc-ui`: a person completes tasks in the web UI.
  "Queues" lists the declared queues, filtered by kind, in the sidebar and on
  a page of its own. In the sidebar, each entry of the navigation now has its
  list below it, the latest runs, the flows and the queues, and an arrow that
  folds the list away. A queue's page lists its tasks in any state that
  changed in the last 24 hours, or 7 days, 30 days or 1 year, filtered by
  state, pending at first, and by flow. A task that has not finished is listed
  only while its run is active. A pending task of an external queue opens with
  its inputs and a form that completes it, or fails it with a reason. The form
  is the one its step names, `form: <file>.form.json`, in the A2UI subset a
  flow's form uses: its controls are the fields of the task's result, and the
  manager holds every completion to them. A step with no form gets a text
  field, and a loop's exit condition a yes or no. A form on a task of a worker
  queue, or on an exit condition, is refused at upload. The `user` role gains
  `queues:read` and `tasks:complete`, on every queue. New routes: `GET
  /queues`, `GET /queues/{queue}/tasks/recent` and `GET
  /tasks/{task_id}/inputs`. `Store` gains `queue_tasks`, with its contract
  tests, and the tasks table an index on `(queue, position)`.
  `examples/approval` is a flow that waits for a person twice.
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
- `neorc-ui`: a flow's page draws its steps as a graph, before any run: a
  node per step with its handler and queue, loops and fan-outs as boxes, each
  drawn once, a sub-flow linking to the flow it calls, an edge from each step
  to the ones reading it, and a step's params and fixed params beside the
  graph when its node is clicked. A definition says all of this; only how
  many times a loop goes round and how many branches a fan-out over a list
  has wait for a run. The page now goes: version, steps, the form to start a
  run, then the definition as text.
- Forms: a flow file may name a form file next to it, `form:
  code_task.form.json`, written in a subset of A2UI v0.9: text and number
  fields, a check box, a choice of one from a list, and the checks `required`,
  `length` and `numeric`. `neorc-core`: `neorc_core.forms`, which reads the
  file in where a flow file is read, refuses at upload a form outside the
  subset or one that does not ask for exactly the flow's inputs, and holds
  every start of a run to what the form asks of its inputs. `neorc-ui`:
  "Start a run" draws a flow's form in the place of one field per input.
  `examples/code_task` is a flow with one; `docs/architecture/forms.md` says
  what a form may hold, where the feature is, and how to take it out.

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
- `neorc-ui`: a run's graph draws a loop once, as the definition has it,
  showing one iteration: the latest, which follows a live run, until another
  is picked on the loop, with its arrows or by its number. A loop inside a
  loop or a fan-out is picked apart in each. A task's details stay open on
  the iteration picked. The loop says what its exit condition returned in the
  iteration shown, and a loop that used up `max_cycles` without it met is
  marked failed, in the graph and in the outline: the manager stores tasks,
  not loops, so the run's reason was the only place that said so. A step
  after a loop is joined to the iteration shown, which stands for all of
  them; what it received from each is in its params. Before, the graph had a
  box per iteration, one under the other.
- `neorc-ui`: the graph's "fit view" control works after the graph has
  changed. It did nothing once a task's details had been opened or a live
  run had moved on, since React Flow waited to measure nodes it had already
  drawn.
- `neorc-ui`: the outline and the graph list a run's steps in the order they
  run, each after the ones it refers to. They were in the sorted order of
  the keys the manager stores a definition with, which put a loop's exit
  condition before the steps it reads.

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
