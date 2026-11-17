# Workers and manager

Tasks and flows definitions are data, not code.

So the manager and the scheduler do not need to have the code or be redeployed when task code or definition change.

Workers would only need to be redeployed when the code needed in their queue change.

A worker on startup should:
1. receive from the startup command: a code location, a queue name (will be "default" if not specified), the manager address
2. contact the manager and pull the task definitions for their queue, sending its API token; a token the manager refuses for that queue stops the worker (see [roles.md](roles.md)).
3. verify every task's handler can be imported and its named parameters match the task's inputs in the flow definition.
4. confirm the manager it is healthy
5. start receiving tasks.

A worker validates its queue's task definitions only at startup. A mechanism for
the manager to tell workers to perform actions, such as revalidating flows
after a new version is uploaded, will come later; it is not designed yet.


Tasks and flows are defined in YAML and stored as text files under version control.

Will be one file per flow.

Flow files need to have in the first fields, a name and a version following semver.

A utility is provided for reading all the files and call the manager endpoints for updating. It is meant to run in CI/CD.

Storing a new version of a flow and cancelling that flow's active run trees
happen in the same transaction. See [core.md](core.md), "Cancellation and
failure".

The `manager` exposes one method for creating or updating a flow, in the payload will receive the exact same structure as the file is in the repo, but always translated to json if needed.

The manager will keep history of the different versions of a flow.

Uploading a flow whose name and version already exist:
- with content identical to the stored definition is a no-op: nothing is stored and no run is cancelled. Identical means the same JSON structure, so formatting and comments in the file do not count.
- with different content is rejected as a bad request: changed content needs a new version.

Uploading a version lower than the flow's highest stored version is rejected as a bad request. Versions only move forward, so the latest version is always the highest one.

Workers can be of multiple languages, not only Python. In their implementations they only need to know how to fetch their queue's task definitions, receive, claim, heartbeat and report tasks finished with the manager, and route them to the executable code, transmitting back the results by http.

## Handlers

Each task in a flow file names its handler as an import path, in the format of
the runtime that serves the task's queue. For Python it is `module:function`,
resolved from the worker's code location:

```yaml
my_task:
  queue: python-default
  handler: myapp.tasks:my_task_handler
```

Whoever uploads a flow chooses its handlers and their fixed params, so a
handler is confined to the worker's own code: the module must be found at the
path its name gives from the code location, `myapp/tasks.py` or
`myapp/tasks/__init__.py` for `myapp.tasks`, which is checked before any of it
is imported, and the function must be defined in such a module too.
`subprocess:run`, a module elsewhere on the import path, a package installed
in a virtual environment inside the code location or into it with
`pip install --target`, and a function a module under the code location
imports from elsewhere are all refused. Dependencies vendored by copying,
with no metadata beside them, cannot be told from the worker's code: keep
them out of the code location. The check is made whenever a
handler is first met, at startup or when a task names one uploaded later, and
a refused handler fails its task without being called. Importing any module
under the code location runs its body, so the code location holds handler
code, not scripts.

A worker in another language defines its own format for the same field. The
manager and the scheduler treat the handler as an opaque string; only the
worker resolves it.

A queue name is letters, digits, `_` and `-`: it travels in a URL path, where a
`/` or a `.` segment would be read as part of the route.

Task definitions are local to their flow. One handler can back several tasks,
in the same flow or in different flows.

## Executables

A task can run an executable file, written in any language. The Python worker
starts it as a process of its own.

```yaml
close_month:
  queue: python-default
  handler: executables/billing/close_month.sh
  params:
    month: inputs.month
```

A handler that starts with `executables/` names an executable. Every
executable of a code location is in its `executables` directory.

### Which files count

- The path is plain names joined by `/`. A name has letters, digits, `_`, `-`
  and `.`, and does not start with `.` or `-`. So the path has no `.`, no `..`
  and no empty part.
- The file is in the `executables` directory after links are followed. A link
  inside the directory is followed, as for a mounted ConfigMap. A link out of
  it is refused. The `executables` directory is not itself a link.
- The file is a regular file the worker may execute.

The worker checks all of it where it checks any handler: at startup, for the
tasks of its queue, and when a task names an executable uploaded later. It
checks again each time a task runs. A refused executable fails its task and is
never started.

Whoever uploads a flow chooses the executable and the task's inputs, and
nothing else. The worker starts the executable as it is. It has no shell and
no arguments, its working directory is the code location, and its environment
is the worker's. So the `executables` directory holds the executables of tasks
only.

### What an executable reads and writes

| | |
|---|---|
| standard input | the task's inputs, as one JSON object by name, then the end of input |
| standard output | the task's result: one JSON value and nothing else; nothing at all is `null` |
| standard error | the executable's log |
| exit code | 0 when the task succeeded; any other fails the task |

- Values are written as they travel (see [Values](#values)). A datetime among
  the inputs is `{"$datetime": "..."}`, and an executable writes one the same
  way.
- The result is at most 1 MiB. The worker ends an executable that writes more,
  and the task fails.
- Output that is not JSON, or not a value neorc carries, fails the task.
- When an executable fails, the task's error is the exit code, or the signal
  that ended it, and the end of standard error, 16 KiB at most. When an
  executable succeeds, the worker logs that end.

### Heartbeat

An executable may say it is still working. Its environment has
`NEORC_HEARTBEAT_FILE`, the name of a file. The executable touches the file or
writes to it.

- An executable that never touches the file is not watched.
- Once the file is there, the executable touches it again within 60 seconds.
  Otherwise the worker takes it for stuck, ends it and fails the task.
- The worker measures those 60 seconds on its monotonic clock, from the last
  change it saw in the file's modification time. A jump of the system clock
  changes nothing.

The heartbeat is between an executable and its worker. The worker keeps the
task's lease with the manager while the executable runs, as for any handler.

### Ending an executable

An executable has a process group of its own. The worker ends it by killing the
group, so what the executable started ends with it. The worker does so when the
heartbeat stops, when the output passes the limit, and when the task is
cancelled, as when the manager refuses the worker's token.

- An executable that ends by itself leaves what it started running. Its output
  is what it wrote up to one second after it ended.
- A killed worker leaves its executable running. The task's lease lapses,
  another worker takes the task, and the task runs twice. Delivery is
  at-least-once, so an executable is idempotent, as any handler is.

The worker stays free while an executable runs. It keeps the lease and stops
when asked. It runs one task at a time.

### Not in scope

- How an executable gets to the `executables` directory of a worker.
- A limit on how long an executable runs, other than its heartbeat.
- An environment of its own for an executable, or a sandbox.
- A list of the inputs an executable reads. The worker checks a function's
  parameters against the task's inputs, and has nothing to check an executable
  against.

## Values

Flow inputs, task inputs and task results are JSON values plus datetimes: dicts,
lists, strings, numbers, booleans, null and datetimes. A result is at most
1 MiB, and is stored in its task row.

User code always sees a datetime as a datetime, whether it arrives as a flow
input or as an upstream task's result. neorc serializes datetimes to strings on
the wire and in storage, and deserializes them back before calling a handler;
handlers never parse them.

### Datetime encoding

A datetime is encoded as an object with a single `$datetime` key holding an
ISO 8601 string, at any depth of a value:

```python
def record_order(order_id):
    return {
        "order_id": order_id,
        "when": datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc),
    }
```

is stored and transmitted as

```json
{"order_id": 42, "when": {"$datetime": "2026-09-13T10:00:00+00:00"}}
```

and a handler reading that value receives `when` as a datetime again.

Rules, applied by every worker runtime wherever it encodes or decodes a value:

- An object whose only key is `$datetime` is a datetime. Its value must be an
  ISO 8601 string with a timezone; anything else, such as `{"$datetime": 34}`,
  is a value error.
- Datetimes must carry a timezone. Encoding a naive datetime is a value error.
- Keys starting with `$` are reserved for neorc. A user value containing one,
  in any object, is a value error. There is no escape.


