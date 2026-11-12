# Task completion

Some tasks are done outside a neorc worker, by a person or by another system.
Such a task waits in its queue until someone completes it.

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

Listing does not lease or change any task. A task stays in the list until
someone completes it or its run ends.

Uploading a new version of a flow cancels its runs in progress (see
[flows.md](flows.md), "Sub-flows and versions"). Their pending tasks leave the
list.

## In a flow file

A flow file declares the task as any other task, with a queue, a handler and
params. The queue says who completes it.

```yaml
approve_draft:
  queue: editors
  handler: approve-draft
  params:
    draft: tasks.write_draft
```

The handler is a text for whoever completes the task. The manager and the
scheduler store it and pass it on.

## Who may complete

Completing has its own role, `responder`. A responder token is bound to one
queue, as a worker token is (see [roles.md](roles.md)).

| Role        | May, on its one queue                                     |
| ----------- | --------------------------------------------------------- |
| `worker`    | fetch task definitions, receive, claim, heartbeat, finish |
| `responder` | list pending tasks, complete                              |

- Each role has only the operations in its row. A worker takes a task by
  receiving it.
- A queue takes completions once it has a responder token. Tokens are created
  on the database, and the operator chooses those queues.
- A queue belongs to workers or to responders. Its tokens all have the same
  role.

## Not in scope

- A person signed in to the UI completing tasks.
- Declaring the shape of a result, for a form to collect it.
- Deadlines and reminders for a task that waits.
