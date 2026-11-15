# Plan: roles, with workers bound to their queue

> Superseded by oneberth's identity spec, `docs/specs/identity.md` in
> [oneberth](https://github.com/the-guish/oneberth): oneberth signs people in,
> keeps the tokens and checks the roles. This plan is kept as a record.

Goal: every request a manager takes is allowed by the role of whoever sends
it, as [roles.md](../specs/roles.md) lists, and nothing else is. A token has
one of five fixed roles; a worker's token is bound to one queue, and a task's
id, now random, is only good on that queue. A signed-in person has the User
role. `--no-auth` keeps allowing everything.

[sso-plan.md](sso-plan.md) deferred roles and left the places they go:
`Principal`, `ApiToken`, and the one guard every route passes. Follows
[contributing/in-memory-first.md](../../contributing/in-memory-first.md):
which role may do what, and whether a task is on a token's queue, are decided
in core and tested in memory; Postgres stores a token's role, and FastAPI
asks core before a route runs. Each step is one branch
`feature/roles-<n>-<name>`, stacked on the one before, starting from
`feature/roles-0-specs`, roughly under 1000 lines including tests, checked,
reviewed with `review_my_changes.sh <repo> <previous branch>` until it has no
confirmed or plausible finding worth fixing, then one signed commit.

## What exists, for a reader with no memory of it

- `neorc_core._access`: `Principal` (kind, subject, name, provider, email),
  `ApiToken` (id, name, created_at, expires_at), and `Access`, which creates,
  lists, revokes and authenticates tokens and opens sessions under the allow
  list. `CredentialStore` (`ports/_credentials.py`) stores them, with
  `MemoryCredentialStore` in `local/`, `PostgresCredentialStore` in
  `neorc.postgres`, and `CredentialStoreContract` in `testing/contracts/`.
  Sessions store their `Principal`; there is no user table.
- `neorc.manager._access.guard` is the router's one dependency
  (`_app.py`: `include_router(router, dependencies=[Depends(guard)])`): it
  refuses cross-site and non-JSON writes, and returns the `Principal` of a
  token or a session, or `None` with authentication off. Routes do not read
  the principal today. `/health`, the UI and `/auth/*` are outside the router.
  `test_http_access.py` walks `app.routes` and checks every one but the public
  ones needs a token.
- `neorc._errors.STATUS_OF` gives every core error its status, and the HTTP
  clients raise the class a body names; a test checks every `NeorcError`
  subclass is listed. `AuthenticationError` is 401, nothing is refused with a
  403 for lack of permission yet.
- `Worker.run` and `Scheduler.run` raise `AuthenticationError` rather than
  retry; `Worker`'s heartbeat stops the handler on it. `neorc worker start`
  exits on it at `prepare`, which fetches `GET /queues/{queue}/tasks`, and at
  `run`; `flows upload` and `scheduler start` exit too (`_cli.py`, `_refused`).
- Task ids are `task_id_for(run_id, address)`, a `uuid5`
  (`neorc_core/_runs.py`). The store port's `publish_task` promises that id,
  and publishing an address again returns the task already there: Postgres by
  `ON CONFLICT (id) DO NOTHING` (`postgres/_store.py`), memory by looking the
  computed id up. `Manager.publish_task` computes it for the payload size
  check, and `_filled_in` for a handler's `neorc.task_id`. Sub-run ids are
  `sub_run_id_for`, also a `uuid5`. Tests in core and `neorc` assert ids equal
  `task_id_for(...)`.
- `Manager.claim_task`, `extend_lease` and `report_finished` take a task id
  alone. The in-process clients (`local/`) call them directly, with no
  principal: `neorc run` and `LocalCluster` have no authentication.
- The UI signs in with a session and calls `/flows...`, `/runs...`,
  `POST /flows/{name}/runs`, `POST /runs/{id}/cancel`, and long-polls
  `/events` and `/events/latest`.

## Shape

### Permissions

A permission is one operation on one kind of resource. Every route in the
router needs exactly one:

| Permission          | Resource | Routes |
| ------------------- | -------- | ------ |
| `flows:upload`      | —        | `POST /flows` |
| `flows:read`        | —        | `GET /flows`, `/flows/{name}`, `/flows/{name}/versions`, `/flows/{name}/versions/{version}` |
| `runs:start`        | —        | `POST /flows/{name}/runs` |
| `runs:read`         | —        | `GET /runs`, `/runs/{id}`, `/runs/{id}/tasks`, `/runs/{id}/sub-runs`, `/runs/{id}/state`, `/tasks/{id}` |
| `runs:cancel`       | —        | `POST /runs/{id}/cancel` |
| `runs:schedule`     | —        | `POST /runs/{id}/tasks`, `/runs/{id}/sub-runs`, `/runs/{id}/succeed`, `/runs/{id}/fail` |
| `events:read`       | —        | `GET /events`, `/events/latest` |
| `queue:definitions` | queue    | `GET /queues/{queue}/tasks` |
| `queue:receive`     | queue    | `POST /queues/{queue}/tasks/receive` |
| `tasks:claim`       | queue    | `POST /tasks/{id}/claim` |
| `tasks:heartbeat`   | queue    | `POST /tasks/{id}/heartbeat` |
| `tasks:finish`      | queue    | `POST /tasks/{id}/finished` |

Public, outside the router, as today: `/health`, `/openapi.json`, `/auth/*`,
the UI.

### Roles

Fixed, in code; not stored.

| Role               | Permissions | Bound to |
| ------------------ | ----------- | -------- |
| `worker`           | `queue:definitions`, `queue:receive`, `tasks:claim`, `tasks:heartbeat`, `tasks:finish` | one queue |
| `scheduler`        | `flows:read`, `runs:read`, `runs:schedule`, `events:read` | — |
| `ci`               | `flows:upload` | — |
| `user`             | `flows:read`, `runs:start`, `runs:read`, `runs:cancel`, `events:read` | — |
| `external-trigger` | `runs:start` | — |

```python
class Permission(StrEnum):
    FLOWS_UPLOAD = "flows:upload"
    ...


class Role(StrEnum):
    WORKER = "worker"
    SCHEDULER = "scheduler"
    CI = "ci"
    USER = "user"
    EXTERNAL_TRIGGER = "external-trigger"


PERMISSIONS: Mapping[Role, frozenset[Permission]]
QUEUE_PERMISSIONS: frozenset[Permission]  # the ones bound to a queue
```

### Credentials

`Principal` gains `role: Role` and `queue: str | None`; `ApiToken` gains the
same. A session's principal is always `user` with no queue: sessions store
their principal as today, with the role added.

`Access.create_token(name, role, *, queue=None, expires_seconds=None)` refuses
with `InvalidValueError` a worker token without a queue, a queue on any other
role, and a queue that is not a queue name (the rule `Manager` already
checks). `CredentialStore.add_token` takes the role and queue.

On Postgres, edited in place (nothing is deployed):

```sql
CREATE TABLE IF NOT EXISTS neorc_api_tokens (
    id               uuid PRIMARY KEY,
    name             text NOT NULL UNIQUE,
    secret_hash      text NOT NULL UNIQUE,
    role             text NOT NULL
                     CHECK (role IN ('worker', 'scheduler', 'ci', 'user',
                                     'external-trigger')),
    queue            text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz,
    CHECK ((role = 'worker') = (queue IS NOT NULL))
);
```

and the sessions table a `role` column with the same check, always `user`
today.

### Checking

Core decides, the route asks:

- `authorize(principal: Principal | None, permission, *, queue=None)` in
  `neorc_core._access` raises `PermissionDeniedError`, a new `NeorcError`
  answering 403, unless the principal is `None` (authentication off) or its
  role has the permission and, for a queue permission given a `queue`, the
  principal is bound to it.
- `Manager.claim_task`, `extend_lease` and `report_finished` take
  `queue: str | None = None`. Given one, a task on another queue raises
  `TaskNotFoundError`, as an id that does not exist, so a token learns
  nothing about other queues' tasks. The in-process clients pass none.
- In `neorc.manager`, `permit(permission)` builds a dependency that takes
  the guard's principal (FastAPI runs the guard once per request) and calls
  `authorize`, with the path's `{queue}` for the two queue routes. Every route
  in the router declares one. The three task routes then pass
  `principal.queue` to `Manager`.
- `AuthenticationError` and `PermissionDeniedError` share a base,
  `AccessError`. `Worker` and `Scheduler` stop on it where they stop on
  `AuthenticationError` today: raised from `run`, not retried; the CLI exits
  on it with the manager's reason.

A worker started on a queue its token is not bound to is refused at
`prepare`, by `GET /queues/{queue}/tasks`: roles.md's start-up check needs no
route of its own.

### Task ids

- `Store.publish_task` gives a new task a random `uuid4`; publishing an
  address again still returns the task already there, with its first id.
- Postgres: `UNIQUE (run_id, address)` replaces the `run_id` index, which it
  covers, and the insert conflicts on it; a conflict reads the task by run and
  address.
- Memory: an index from `(run_id, address)` to the id.
- `Manager.publish_task` sizes the delivery with a placeholder UUID, which
  encodes to the same length. `_filled_in` takes the task's id for
  `neorc.task_id`; at publish time, the placeholder.
- `task_id_for` goes. `sub_run_id_for` stays: no worker holds a sub-run id.

## Steps

| Step | Branch                                  | Status |
| ---- | --------------------------------------- | ------ |
| 0    | `feature/roles-0-specs`                 | done   |
| 1    | `feature/roles-1-random-task-ids`       | done   |
| 2    | `feature/roles-2-token-roles`           | done   |
| 3    | `feature/roles-3-permissions`           | done   |
| 4    | `feature/roles-4-routes`                | done   |
| 5    | `feature/roles-5-end-to-end-docs`       | done   |

Re-split while carrying out step 2: giving `CredentialStore.add_token` a role
changes the Postgres adapter and `neorc tokens create` in the same commit, or
their tests fail. So credentials on every store and the command line come
first, with nothing enforced, and permissions after.

### 0. Specs and this plan

- roles.md, core.md ("APIs", "Task Queues") and workers-and-manager.md say
  what this plan builds. `roles-data-model.md` folded in here and deleted.

### 1. Random task ids

- `neorc_core`: `Store.publish_task`'s docstring and `StoreContract`: a new
  task gets an id no other task has, publishing the address again returns the
  same task and id, two runs publishing the same address get different ids.
  `MemoryStore` with its `(run_id, address)` index. `Manager.publish_task`
  and `_filled_in` as in "Task ids". `task_id_for` removed, and its uses in
  tests replaced by the id the store returned.
- `neorc.postgres`: the unique constraint, the conflict target and the read
  on conflict; the schema comment on `position` updated ("ids come from their
  address" no longer holds). `PostgresStore` passes the contract.
- A test that `neorc.task_id` in a delivery is the stored task's id.

### 2. Every token has a role

Nothing is enforced yet: a token of any role still reaches every route.

- `Role` in `neorc_core._access`, exported. `Principal` and `ApiToken` with
  `role` and `queue`; `Access.create_token` with its checks; `open_session`
  gives `user`; `authenticate_token` carries the token's role and queue.
- `CredentialStore.add_token` with role and queue; `MemoryCredentialStore`;
  `CredentialStoreContract` round-trips role and queue through tokens,
  `tokens()` and sessions.
- Postgres: `neorc_api_tokens` and `neorc_sessions` with `role` and `queue`,
  and their checks, edited in place. `PostgresCredentialStore` writes and
  reads them and passes the contract. The schema test keeps the role check in
  step with `Role`; a test that the database refuses a worker row without a
  queue, and another role with one, beneath the checks `Access` makes.
- `neorc tokens create NAME --role ROLE [--queue QUEUE] [--expires-days N]`:
  `--role` required, choices from `Role`; the refusals of `create_token` as
  one line each. `neorc tokens list`: role and queue columns.
- Every test that creates a token gives it a role, and every command in the
  docs that creates one.

### 3. Permissions in core

- `Permission`, `PERMISSIONS`, `QUEUE_PERMISSIONS` and `authorize` in
  `neorc_core._access`. `AccessError`, a new base of `AuthenticationError`
  and of the new `PermissionDeniedError`; `STATUS_OF` gives
  `PermissionDeniedError` 403, and the clients raise it.
- `Manager.claim_task`, `extend_lease`, `report_finished` with `queue`, and
  `TaskNotFoundError` for a task on another queue; the calls from `local/`
  pass none.
- `Worker` and `Scheduler` stop on any `AccessError`, where they stop on
  `AuthenticationError` today: in `run`, in `Scheduler.advance` (not a
  rejected action that fails the run), and in the worker's heartbeat. The CLI
  exits on it the same way, with the manager's reason. Tests beside
  `test_refused_tokens.py` and in `test_cli_auth.py`.
- Tests: `authorize` for every role against every permission, from the
  tables above written out in the test rather than read from `PERMISSIONS`;
  `None` allows everything; a queue permission on another queue is refused.

### 4. The manager asks before every route

- `permit(permission)` in `neorc/manager/_access.py`; every route in
  `_routes.py` declares its permission; the task routes pass the principal's
  queue to `Manager`.
- `test_http_access.py`:
  - every route in the router declares exactly one permission, walking
    `app.routes` as the existing test does, so a route added later without
    one fails;
  - for each role, a token of that role against every route: allowed routes
    pass the guard (whatever they then answer), the rest answer 403;
  - a worker token for `default` against `/queues/other/...` is 403, and
    against a task published on `other` is 404 on claim, heartbeat and
    finish;
  - a session is `user`; with `--no-auth` every route is allowed.
- `neorc worker start` on a queue its token is not bound to exits at
  `prepare`, with the manager's reason.
- `test_end_to_end.py`, `test_ui_browser.py` and the example READMEs, which
  use one token for every process: a token per role, since one role no
  longer does everything.
- `openapi.json` and the UI's generated types refreshed if the schema
  changed; the 403 body is already documented.

### 5. End to end, docs and changelog

- One end-to-end case with an `external-trigger` token starting a run over
  HTTP, and refused reading it.
- README: `neorc tokens create` with roles, as core.md's "Task Queues" shows;
  a short table of the roles.
- CHANGELOG: roles, `--role`/`--queue`, random task ids, the 403.
- `sso-plan.md`: a line under its choice about roles pointing here.

## Choices made while planning

Decided with the user while planning. Revisit if they are wrong.

- One id per task, made at publish, not a receipt per receive. A worker whose
  lease lapsed keeps a valid id after another worker receives the task;
  delivery is at-least-once and handlers are idempotent, and it keeps one
  stable id for the UI, events and links. A receipt per receive, as SQS has,
  can come later without changing roles.
- One `worker` role, bound to a queue, not a role per queue: queue names are
  whatever flow files say, and roles are fixed.
- A task on another queue answers 404, not 403, so a token cannot tell which
  ids exist.
- `external-trigger` may only start runs, of any flow; it gets the run back
  from the start and reads nothing else. Limiting it to some flows waits for
  flow resources.
- A worker learns at start-up that its token is refused for its queue from
  the definitions fetch it already makes, not a new route.
- `user` tokens are allowed, for a person's scripts, with a session's
  permissions.
- `user` reads events: the UI long-polls them to refresh.
- No UI change: every person is `user` and may do everything the UI offers.
  `/auth/session` does not report the role yet.
- Permissions decided in core (`authorize`, `Manager` with `queue`); FastAPI
  only asks, so the in-memory setup runs the same decisions.
- One role per token, as columns on the tokens table. A table of bindings,
  a role on a resource each, is for when flow resources give one credential
  several.
- Not in this plan: role editing, several user roles, per-flow permissions,
  hiding UI actions, a receipt per receive, an audit of who did what.
