# Roles and access control

## Roles

Permissions described in these default roles are exhaustive: only these operations are allowed, and anything else is refused.

Today they can be granular on the "queue" resource. In future versions some users may have permissions on specific "flow" resources.

Every token has exactly one role. A signed-in person has the User role.

With authentication off (`neorc manager start --no-auth`) there are no roles: every operation is allowed.


### Workers

There is one worker role. Each worker token is bound to one queue when it is created, and its permissions apply only to that queue.

The worker permissions allow fetching task definitions, receiving, claiming, heartbeat and reporting a task finished, for a single, specific queue.

When a worker is started, it fetches the task definitions of the queue given at the start command, sending its token. If the manager refuses the token for that queue, the worker fails to start.

A task's id is a random UUID the manager creates when the scheduler publishes the task, so a worker cannot guess the id of a task it did not receive. It is provided to the worker on "receive", and the worker uses it through the task's lifecycle: claiming, heartbeat and reporting finished.

Claiming, heartbeating or finishing a task on another queue than the token's answers as if the task did not exist.

The id belongs to the task, not to one delivery. A worker whose lease lapsed still holds a valid id after another worker receives the task; delivery is at-least-once, and handlers are idempotent (see [postgres-implementation.md](postgres-implementation.md)).

A worker token is bound to a worker queue. An external queue has no workers (see [task-completion.md](task-completion.md)).


### Responders

There is one responder role. Each responder token is bound to one queue of either kind, and its permissions apply only to that queue.

The responder permissions allow listing the pending tasks of the queue and completing them (see [task-completion.md](task-completion.md)).

Listing the pending tasks of another queue than the token's is refused. Completing a task on another queue answers as if the task did not exist.


### Scheduler

Scheduler will have a dedicated default role with all needed to carry its responsibilities:

- fetch flow definitions and poll events;
- read runs and their state, including task results;
- publish tasks and sub-runs;
- mark runs succeeded or failed.

It should not be able to create, modify or delete flows, start top-level runs or cancel runs.

### CI

The CI should be able to post new versions of a flow.
Uploading a new version cancels that flow's active runs (see [core.md](core.md), "Cancellation and failure"). CI has no permission to cancel runs otherwise.

### User

Anyone signed in to the UI will be able to list flows and read their versions, start new runs, read runs with their tasks and results, and cancel them. The UI reads events to refresh its pages.

A user lists the queues and the tasks of any queue, in any state. A user completes a pending task of any queue (see [task-completion.md](task-completion.md)).

A User token can also be created, for a person's scripts; it has the same permissions as a signed-in session.


### External trigger

Can only start new runs, of any flow. It reads nothing but the run it gets back when starting one.


## Token creation

Tokens are created, listed and revoked with the CLI connecting to the DB. A token's role is required; a queue is required for the worker and responder roles and refused for any other. The queue is declared before a token is bound to it.


## Not in scope

At this version the following are not needed, and may be planned in future work.

- Role editing. Only the default, fixed roles are shipped.
- Multiple different users roles.
- UI for querying roles or users.
- Hiding in the UI what a person cannot do: every person has the User role.
- Limiting the External trigger to specific flows: it waits for "flow" resources.
