# Task completion

Some tasks are done outside a neorc worker, by a person or by another system.
Such a task waits in its queue until someone completes it.

## Queues

A queue has a kind, `worker` or `external`. Every task of a queue has the kind
of the queue.

- A worker queue is served by workers. A worker token is bound to a worker
  queue.
- An external queue is served by responders. Its tasks end only by a
  completion.
- A queue is declared on the database, as tokens are. A flow names only
  declared queues.

## Completing a task

Completing a task records its result and ends the task, in one step. A
completion names the task by its id.

- A pending task is the only task that can be completed.
- A task is completed once. A second completion of the same task is refused.
- A completion has a result or an error. An error fails the task, and the run
  fails as with any failed task.
- A run takes completions only while it is active.

The result is a value, as any task result is (see
[workers-and-manager.md](workers-and-manager.md), "Values"). Downstream tasks
read it by reference. The scheduler treats a completed task as any finished
task.

## Pending tasks

The manager lists the pending tasks of a queue. Each task in the list has its
inputs filled in, as a worker receives them (see [core.md](core.md), "Task
payloads").

Listing does not lease or change any task. A task leaves the list when someone
completes it, when a worker receives it, or when its run ends.

Uploading a new version of a flow cancels its runs in progress (see
[flows.md](flows.md), "Sub-flows and versions"). Their pending tasks leave the
list.

## In a flow file

A flow file declares the task as any other task, with a queue, a handler and
params. The queue is an external queue.

```yaml
approve_draft:
  queue: editors
  handler: approve-draft
  params:
    draft: tasks.write_draft
```

The handler of a task on an external queue is a text for the responder. The
manager and the scheduler store it and pass it on.

## Who may complete

A system completes with the role `responder`. A responder token is bound to
one queue of either kind (see [roles.md](roles.md)). A person completes with
the role `user`, which every signed-in person has.

| Role        | Queues        | May                                                       |
| ----------- | ------------- | --------------------------------------------------------- |
| `worker`    | its one queue | fetch task definitions, receive, claim, heartbeat, finish |
| `responder` | its one queue | list pending tasks, complete                              |
| `user`      | every queue   | list queues and their tasks, complete                     |

- Each role has only the operations in its row. A worker takes a task by
  receiving it.
- A responder completes any pending task of its queue. A task on a worker
  queue can be completed until a worker receives it.
- A manager with authentication off allows every operation to everyone.

## Not in scope

- A fixed form for a task of a worker queue: mark it succeeded, mark it
  failed, clear it.
- Giving a person some queues only.
- Recording who completed a task.
- Deadlines and reminders for a task that waits.
