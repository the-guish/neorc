# Postgres implementation

The reference persistence layer, shipped in `neorc` behind the `postgres`
extra. Nothing here is visible from `neorc-core`: the manager service depends
on a port, and this is one adapter behind it. See
[core.md](core.md) for the component layout.

## Scope

Postgres backs two distinct concerns, and they are kept apart on purpose:

- **The task queue** — handing the next task to a waiting worker, which
  receives it. Replaceable by Redis or SQS later, which is why receiving a task
  is its own endpoint.
- **Task state** — the record of every task and its status. Stays in Postgres
  even if the queue moves elsewhere, which is why claiming a task is a
  separate endpoint.

## Waiting workers

Workers long-poll to receive a task. A deployment has many more workers than it
has database connections to spare, so a request that is waiting must not hold a
connection open.

A waiting request holds no connection. It waits on an in-process asyncio event,
and the manager runs one shared `LISTEN` connection for the whole service: when
a task is published, the publishing transaction issues `NOTIFY`, the listener
wakes the waiters, and only then does a waiter take a connection from the pool
to receive a task. Idle workers therefore cost one connection per manager
process, not one per worker.

Long polls need a deadline shorter than any proxy timeout in front of the
manager; a worker that reaches it re-polls. A wakeup is a hint, not a promise —
a woken waiter that finds nothing to receive goes back to waiting rather than
returning empty.

## Receiving a task

Receiving takes a lease, not a handoff: it makes the task invisible to other
workers for a bounded time rather than permanently. It is not the final word,
which is the claim (see [core.md](core.md), "APIs"), but in Postgres receiving
is atomic and never hands the same task to two workers at once:

    SELECT ... FROM tasks
     WHERE run_after <= now()
       AND (status = 'pending' OR lease_expires_at <= now())
     ORDER BY priority DESC, run_after
     FOR UPDATE SKIP LOCKED
     LIMIT 1

`SKIP LOCKED` lets concurrent receivers pass over rows another transaction is
already taking instead of queueing behind them. The same transaction sets
`lease_expires_at = now() + visibility timeout`, moves the task to `received`
and increments its attempt count, so a task is never observed as
received-but-unleased.

## Lost workers

A worker that dies mid-task is recovered by the lease expiring, not by anything
noticing the worker is gone.

While a worker executes a task it heartbeats, and each heartbeat pushes
`lease_expires_at` further out. Stop heartbeating — crash, network partition,
a host that goes away — and the lease runs out, at which point the task matches
the receive query above again and the next worker to ask takes it. Recovery is a
side effect of the normal receive path: there is no reaper process to run, and no
sweeper to fall behind.

This is chosen over an explicit reaper because it is the model SQS already has,
so the queue can move there without the semantics changing:

| neorc                | Postgres                        | SQS                       |
| -------------------- | ------------------------------- | ------------------------- |
| receive with a lease | `SKIP LOCKED` + `lease_expires_at` | `ReceiveMessage` with `VisibilityTimeout` |
| heartbeat            | push `lease_expires_at` out     | `ChangeMessageVisibility` |
| finish               | mark terminal                   | `DeleteMessage`           |
| worker died          | lease expires, task reappears   | visibility expires, message reappears |

A reaper has no counterpart in SQS, so building on one would mean rewriting
recovery when the backend changes.

Two consequences the rest of the system inherits:

- **Delivery is at-least-once.** A worker can finish its work and die before
  reporting it, and the task will be handed to someone else. Handlers have to be
  idempotent, and that is the user's responsibility: neorc does not detect or
  suppress duplicate runs. How a handler achieves it is up to the user — a
  unique id or hash of the work, an upsert, a check before a side effect.
- **The timeout has to outlast a missed heartbeat.** Heartbeat on an interval
  well under the visibility timeout — a third of it is a reasonable default — so
  a single slow or dropped beat does not hand a live worker's task to another.
  Tasks that run longer than the timeout are fine as long as they keep beating.

## Settled since

- **Statuses and transitions** live in `neorc_core`, not in an adapter: a task
  goes `pending` to `received` to `running` to `succeeded` or `failed`, and
  `ensure_transition` is what every store calls, so no backend can invent its
  own lifecycle. Claiming a task again is allowed, because delivery is
  at-least-once.
- **Payloads live in the task row**, as JSON text: fixed values and references
  to upstream results, filled in when a worker receives the task (see
  [core.md](core.md), "Task payloads"). Text rather than `jsonb`, which keeps
  numbers as `numeric` and would hand `1e16` back as an integer and `-0.0` as
  `0.0`, where the in-memory store keeps them as written. Addressing payloads
  elsewhere is something to do when one is too big for a row, not before.
- **Every task belongs to a flow.** The task API that let a task be published
  on its own, with its own store, queue client, worker and `neorc_tasks` table,
  is gone: a basic queue is a flow with a single task.
- **Migrations** are a create-if-absent step, `create_schema`, exposed as
  `neorc manager start --create-schema`. Not a migration tool: there is no
  released version to migrate from yet, and adding one before there is would be
  guessing at the changes.
- **The tables can live in a schema of their own.** `create_schema`, the
  stores and `ensure_credential_tables` take an optional `schema`, which sets
  the connections' `search_path` to it alone; `create_schema` creates it if it
  does not exist. Without one, the tables go wherever the connection's
  `search_path` puts them, `public` by default. Notification channels are
  per database, not per schema, so two deployments sharing a database wake
  each other's waiters needlessly, which costs a re-read and nothing else.
- **The driver is asyncpg**, Apache-2.0, as the rest of the project is. It
  reads connection URLs; libpq's `key=value` strings are translated for it.

## Open questions

- Retries and their backoff. A task that fails is terminal today; only a lapsed
  lease brings work back, and nothing distinguishes "the worker died" from "the
  work is impossible". Where a permanently failed task lands is part of this.
- Archiving finished tasks. The table only grows, and the partial index keeps
  receiving fast but not the table small.
