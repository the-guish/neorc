# Neorc

## The libs
neorc-core will host all the mechanism and interactions between components, but no specific persistence layer or vendor specific products.

neorc will provide a base reference implementation with postgres, when installed as neorc[postgres]
it may allow more extras in the future for other implementations.


## Installing

neorc-core has one runtime dependency, a YAML parser, because flow definitions
are YAML files. Every other dependency is optional. `pip install neorc` brings
no runtime dependency beyond neorc-core: it is up to the user to install the
extras their hosts need.

    pip install neorc                       # nothing extra
    pip install neorc[manager]              # FastAPI, to run the manager service
    pip install neorc[postgres]             # the Postgres driver, asyncpg
    pip install neorc[manager,postgres]     # the reference deployment

A worker host installs no extra unless its queue client needs one. Future
backends and persistence layers arrive as further extras.


## Task Queues

It will be implemented as three services: a "manager", a "scheduler" and one or more "workers".

After installing neorc users should be able to start the manager, and create a
token for each process that will reach it. Tokens are created on the database,
and each one has a role; a worker's token is bound to the queue it serves. See
[roles.md](roles.md).

```
export NEORC_DATABASE_URL=postgresql://localhost/neorc
neorc manager start --create-schema --ssl-certfile manager.pem --ssl-keyfile manager-key.pem

neorc queues declare default --kind worker
neorc queues declare python-gpu --kind worker

neorc tokens create --role ci github-actions
neorc tokens create --role scheduler scheduler
neorc tokens create --role worker --queue default worker-1
neorc tokens create --role worker --queue python-gpu gpu-worker-1
neorc tokens create --role external-trigger bookings-webhook
```

And, in different hosts, each with its token

```
export NEORC_MANAGER_ADDRESS="https://xx.xx.xx.xx:8420"
NEORC_API_TOKEN="<github-actions>" neorc flows upload flows/
NEORC_API_TOKEN="<scheduler>"      neorc scheduler start
NEORC_API_TOKEN="<worker-1>"       neorc worker start --code-location .
NEORC_API_TOKEN="<gpu-worker-1>"   neorc worker start --code-location . --queue python-gpu
```

An external system starts runs over HTTP with its token:

```
curl -X POST https://xx.xx.xx.xx:8420/flows/booking/runs \
     -H "Authorization: Bearer <bookings-webhook>" \
     -H "Content-Type: application/json" \
     -d '{"inputs": {"booking_id": 42}}'
```






```
from neorc import Worker
from neorc.http import HttpQueueClient


worker = Worker(HttpQueueClient(manager_address), code_location=Path("."))

await worker.prepare()
await worker.run()

```


The worker class uses its queue client to receive the next task, claim it and execute it.

The queue client receives tasks by long-polling the manager. That long polling could be replaced by Redis or SQS in the future, which is why it is a port, `QueueClient`, supporting multiple implementations. The worker receives it by its constructor; the scheduler reaches the manager through its own port, `ManagerClient`.

The "neorc" library implements both over HTTP.

scheduler > queue_cli > http > manager service > db

Then for receiving and claiming tasks.

worker > queue_cli > http > manager service > db



## APIs

The manager will use HTTP/JSON with FastAPI, async mechanism.

The "receive a task" endpoint should comfortably keep several workers waiting
without holding one database connection per waiting worker. See
[postgres-implementation.md](postgres-implementation.md).

The API exposes endpoints for uploading flows, starting and querying runs, and
for the scheduler's requests; and, for workers, for fetching a queue's task
definitions, receiving a task, and claiming it.

A worker takes a task in two steps:

- **Receive.** The worker asks for the next task on its queue. Receiving is a
  signal, not the final word: a queue such as SQS can deliver the same task
  again, or to two workers, so a received task may already be running in
  another worker. Receiving takes a lease, which keeps other workers from
  receiving the task as a best effort.
- **Claim.** Before running the task, the worker asks the manager for it. The
  manager checks, in one transaction, that the task is free to start, and
  changes its status. The claim is the final word, in every implementation.

A worker heartbeats while it executes, and a task whose lease expires can be
received by another worker. The API therefore also exposes an endpoint to
extend the lease. See [postgres-implementation.md](postgres-implementation.md)
for why, and for what this maps onto in SQS.

Receiving and claiming are separate endpoints, so receiving can be done with
SQS in the future while claiming stays with the manager in Postgres. No
references to Postgres or SQS will be in neorc-core.

A task already running can be claimed again, since a worker may retry the
call. A task's id is random, handed to a worker when it receives the task, and
claiming, heartbeating and finishing are refused for a token bound to another
queue (see [roles.md](roles.md)). The id belongs to the task, not to one
delivery: nothing checks that the worker using it is the one that received the
task last, which at-least-once delivery accepts.

## Task payloads

The scheduler publishes a task with its inputs as references to upstream
results, plus fixed values from the flow definition. The manager stores the
task that way, and fills the references in with the upstream results to build
the complete payload a worker receives:

- With the Postgres queue, the manager fills them in when a worker receives
  the task.
- With a queue such as SQS, the manager fills them in when it stores the task,
  and sends the complete payload to the queue in the same step. The worker
  receives everything from the queue.

Both give the same payload. A task is only published once every upstream task
it references has succeeded, and a succeeded result never changes, so when the
references are filled in makes no difference. The worker SDK hides which
backend delivered the task.

The manager checks the payload size when the scheduler publishes the task,
since every upstream result already exists by then. The limit, 1 MiB, applies
to the complete encoded payload: task name, metadata, datetime tags and all
inputs.

Metadata a task can request as inputs is filled in by whoever knows it:

- Known when the task is published — `task_id`, the flow run id, the loop count,
  the fan-out index: filled in by the manager.
- Known only when the task is delivered — `attempts`: filled in by the worker
  SDK, from the queue backend (with SQS, the message's receive count).

## The scheduler

Every task belongs to a flow; there are no tasks published on their own. A
basic queue is a flow with a single task.

The scheduler is always required, alongside the manager and the workers. It
decides everything that follows from executing a flow: it publishes a run's
first tasks when the run starts, publishes downstream tasks as tasks finish,
marks runs as succeeded, and asks the manager to mark a run as failed when one
of its tasks fails or one of its requests is rejected.

The manager records and queues; it does not decide what happens next. It
validates the scheduler's requests and rejects invalid ones: a task whose inputs
exceed the maximum payload size, or a task or sub-flow run published into a run
that is no longer active.

The scheduler will fetch the flow definitions from the manager, and poll events to decide next actions.
While no scheduler is running, those events wait in the manager. For an event
about a run, the scheduler reads the definition of that run's version, not the
flow's latest version.

## Cancellation and failure

The manager owns cancellation and failure propagation across a run tree: a
top-level run and every sub-flow run under it, at any depth.

A run is cancelled by an action from outside the flow: uploading a new version
of its flow, or a user cancelling it by hand. A run is failed when the scheduler
asks for it. Either way the manager applies the change to the whole tree in one
transaction, so no run is ever left waiting on a run that will not finish:

- Cancelling a run cancels every active run in its tree.
- Failing a run fails every active run in its tree.

Tasks already in flight may finish; no new tasks are published into the tree.
Their "task finished" events still reach the scheduler, which finds the run no
longer active and does nothing.

A task published before its run was cancelled can still reach a worker: a
message already sent to a queue such as SQS cannot be taken back. Claiming a
task always goes to the manager, whatever the queue backend, so that is where
it is caught: the manager refuses the claim of a task whose run is no longer
active, and the worker drops the task without running it.


## Out of scope

Neorc does not take care of application state as Temporal does.
It only transmit task results to downstream tasks.

