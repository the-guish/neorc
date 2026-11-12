# In-memory first

neorc-core runs the whole system in one process: YAML flow definitions, the
manager, the scheduler and workers, with no Postgres, no HTTP and no services
to start. This is a rule for the life of the project, not a testing convenience.

Behaviour is built and tested in core, against in-memory adapters. The
deployment adapters in `neorc` — Postgres, FastAPI, the HTTP clients — add only
what they must: persistence, transport and their own concurrency. Their tests
check those additions, not flow semantics a second time.

## Why

- Most of neorc's behaviour — loops, fan-outs, reference resolution, run trees,
  cancellation, value encoding — is decided by logic, not by the database or
  the wire. Tests for it should be fast, deterministic and free of setup.
- An adapter that contains no logic cannot disagree with another adapter about
  what a flow means. The only things left to break are the things it adds.
- Users get the same property: an example runs with a single command, and a
  flow can be tried before anything is deployed.

## Where code goes

```
python/neorc-core/src/neorc_core/
  flows/            definitions, YAML loading and validation, reference resolution
  _planner.py       what happens next for a run: pure, no I/O
  _manager.py       manager behaviour, against ports
  _scheduler.py     the scheduler loop: I/O around the planner
  _worker.py        the worker loop, against ports
  _values.py        value encoding and validation
  ports/            the interfaces adapters implement
  local/            in-memory adapters and LocalCluster: the whole system in one process
  testing/contracts/  test suites every adapter implementation must pass

python/neorc/src/neorc/
  postgres/         the store and notifier ports, on Postgres
  sqlite/           the store and notifier ports, on one SQLite file
  manager/          FastAPI routes, translating HTTP to Manager calls
  http/             the client ports, over HTTP
  _cli.py           commands, including `neorc run` on LocalCluster
```

Ask of every change: does this decide something, or does it store or move
something? Deciding belongs in core. Storing and moving belong in an adapter.

## Rules

### 1. Logic lives in core, behind ports

`Manager`, `Scheduler`, `Worker` and the planner depend on ports only. No
module in `neorc_core` imports a database driver, a web framework or an HTTP
client. `neorc-core`'s only runtime dependency is its YAML parser.

### 2. Adapters translate; they do not decide

A FastAPI route parses a request, calls one `Manager` method and serialises the
result. A store method runs one operation. If an adapter needs an `if` about
flows, runs or tasks, that `if` belongs in core, where the in-memory setup runs
it too.

### 3. The planner has no I/O

`plan(definition, run_state) -> actions` is a pure function: no asyncio, no
clients, no clock reads, no randomness. It decides from the run's state, not
from the event that woke the scheduler, so a redelivered event plans the same
actions. Flow semantics are tested by calling it with plain data and asserting
on the actions it returns.

The scheduler around it only gathers input, calls `plan` and applies the
actions.

### 4. One store port, one method per atomic operation

The store is a single port. Each method is one atomic operation — storing a
flow version and cancelling its run trees, failing a run tree, receiving or
claiming a task — that Postgres runs as one transaction and the in-memory store
runs under one lock.

Core never composes several store calls into something that must be atomic:
the in-memory store would make that sequence look safe when Postgres could not.

### 5. In-memory adapters keep the deployed semantics

In-memory adapters are not simplified fakes. They reproduce what the deployed
adapters do wherever it is observable:

- Clients serialise every value through `_values.py` and JSON, as HTTP does, so
  datetime tags, reserved keys, unsupported types and the payload size limit
  fail locally exactly as they fail deployed.
- The store applies the same status transitions, lease expiry and receive rules
  as Postgres.
- The notifier's wakeups are hints, as `LISTEN`/`NOTIFY` wakeups are: a waiter
  that finds nothing goes back to waiting.
- Errors are the same core exceptions the HTTP clients raise.

When a deployed adapter's behaviour changes, its in-memory counterpart changes
in the same pull request.

### 6. Contract suites bind adapters together

`neorc_core.testing.contracts` holds test suites written against the ports:
one for the store, one for each client port. `neorc-core` runs them against
the in-memory adapters; `neorc` runs the same suites against Postgres and
against the HTTP clients talking to a real manager.

A behaviour an adapter must have is written once, as a contract test, and
proven on every implementation. A new adapter — another database, SQS — starts
by passing the suites.

### 7. Entry points are thin

`neorc run`, `neorc manager start` and the other commands parse arguments and
call core. Tests call the same functions, such as the one behind `neorc run`,
never the command line.

## Where tests go

**`python/neorc-core/tests`** — most of the testing, all in memory:

- Unit tests for flow loading and validation, reference resolution, value
  encoding and the planner.
- The contract suites, on the in-memory adapters.
- Behaviour tests for manager, scheduler and worker on `LocalCluster`.
- The examples under `examples/`, run on `LocalCluster`, with their outputs
  asserted.

**`python/neorc/tests`** — only what the adapters add:

- The contract suites, on Postgres and on the HTTP clients.
- What has no in-memory counterpart: SQL and schema creation, connection
  pooling, `LISTEN`/`NOTIFY`, HTTP status codes and request validation, CLI
  wiring.
- End-to-end runs of the same example scenarios on uvicorn, Postgres and HTTP
  clients, with the same assertions as in core. Passing both is what shows the
  in-memory setup is faithful.

A test in `neorc` that checks a loop, a fan-out, a reference or a cancellation
rule is in the wrong place: it belongs in core, where it runs in milliseconds.

## Checklist for a pull request

- New behaviour is implemented in `neorc_core` and tested there first.
- Nothing in `neorc_core` imports an adapter's dependencies.
- The planner stayed pure.
- New store operations are single atomic methods on the store port, with
  contract tests.
- The in-memory and deployed adapters changed together, and both pass the
  contract suites.
- Adapter tests cover only what the adapter adds.
