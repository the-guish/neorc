# Flows: open questions

Flows are config driven: users define tasks in code, and every `while` and
`for ... in` in the example is replaced by the neorc engine.

Suggested order: settle 1, 2 and 6 first; loops and fan-out build on them.
Once answered, decisions move into the relevant spec and leave this file.

## 1. Task results and inputs

### 1.1 Storing results

Today a handler takes a `Task` and returns nothing; `report_finished` carries
only an error. Flows need a handler's return value stored.

- Must results be JSON? The example returns `set`s, which JSON cannot hold.
- Is there a size limit?
- Stored in the task row as `jsonb`, following the payload decision?

> **Answer:** 
- Results must be json serializable, user code sould only contain dict, lists, python standard types, such as str, datetime, int, float, that could be serialized and deserialized by workers in other programming languages. Worker will serialize datetime as strings.
- same size limit as AWS SQS, 1 MiB
- responses will be persisted in the task row
- datetime is always a datetime for user code, whether from a flow input or a task result. neorc serializes it from and to strings.
- encoded as `{"$datetime": "<ISO 8601>"}`; a non-ISO value is a value error; naive datetimes are rejected; keys starting with `$` are reserved, no escape.

> **Settled:** moved to [workers-and-manager.md](workers-and-manager.md), "Values".




### 1.2 How inputs reach a function

The example passes positional arguments, `pick_word(payload, index)`.
Workers can be written in any language, which suggests named JSON inputs
declared in the flow file.

- Does the handler signature become `f(**inputs)`, or stay `f(task)` with the
  inputs in `task.payload`?

> **Answer:** 
When the worker pulls the task from the queue, the payload should contain the task name and the named parameters with values.
Then the worker will pass those to the user handler.

When task B needs task A output as input:
- The task B record will be inserted when the scheduler request it.
- Task B in the DB will only have a reference needing task A output.
- The manager, when requested by the scheduler, will validate the total size of the B input fits in the max payload size. If it doesn't, the manager rejects the request, and the scheduler then requests the manager to mark the flow run as failed.
- When requested by the worker, the manager will pull the task b inline argument values and enrich with task A output, returning all of them in a single payload to the worker.
- With a queue such as SQS, the manager fills the references in when storing the task and sends the complete payload to the queue; the worker SDK hides the difference. `attempts` is filled in by the worker SDK from the queue backend.

> **Settled (payload filling):** moved to [core.md](core.md), "Task payloads".

They will be named, depending on the inputs requested by the task.
Populating the argument values for each task execution would be responsibility of another component, outside of the worker, outside of the manager. The scheduler will decide how to link tasks' inputs and outputs and tell that by reference to the manager.

The manager is the repository and tracker of what is happening and the queues. But it does not decide what to do next.
When declaring the tasks required inputs the user can refer in the config to some neorc metadata, such as task_id, attempts, or flow id, but those are passed as simple python types to the handler, not as neorc classes.
When declaring the task in the flow file the user will spec something like:


```
my_task:
   queue: default
   handler: my_task_handler
   params:
      my_input_1: tasks.previous_task_b
      my_input_2: tasks.previous_task_c
      other_data_i_need_1: neorc.attempts
  fixed_params:
      some_config: 3
```

the function will be as:
```
def my_task_handler(my_input_1, my_input_2, other_data_i_need_1, some_config):
  ...
```


### 1.3 Bare tasks

- Can a task still be published without a flow, or does every task become a
  one-task flow, as core.md hints?

> **Answer:** 
- no orphan tasks, every task will need at least one simple flow to contain it.

> **Settled:** moved to [core.md](core.md), "The scheduler".

## 2. Flow file format and wiring

### 2.1 File format

- TOML or YAML? Nested loops and fan-outs are awkward in TOML.

> **Answer:** Let's introduce yaml. It will be a dependency of neorc-core.

> **Settled:** moved to [core.md](core.md), "Installing", and
> [workers-and-manager.md](workers-and-manager.md).


### 2.2 Wiring between tasks

- How does a task refer to another's output: `inputs: {text: "${a.output}"}`,
  `depends_on` lists, or something else?
- Leaning: references only, no expressions or arithmetic in config.

> **Answer:** see response to 1.2

### 2.3 Branching

- Is there any branching besides a loop's exit condition? `if done: break`
  in word_picker_rounds is the exit condition. Assumed: no general if/else.

> **Answer:** at first, no other branching. Revised: a `switch` step runs one
> of several named cases.

> **Settled:** moved to [switch.md](switch.md).

### 2.4 Task definitions vs. flow definitions

The flow file declares each task: its queue, its handler and its inputs. There
is no separate file mapping task names to code.

- What exactly does a worker verify at startup against "the expected
  definition": the name, the input names, the queue?
- Are task definitions shared across flows, or local to each flow file?

> **Answer:**
- A worker pulls all the tasks definitions for its queue from the manager.
Then it validates the handler is reachable, the handler named parameters matches the names of the config.
- tasks definitions are local to each flow. Handlers can be references multiple times with different task_name in a same or multiple flows.

word_picker
  task1: 
    handler: my_func1
  task2:
    handler: my_func1

word_picker_rounds:
  task1:
    handler: something_else
  task2:
    handler: my_func1

Handlers are import paths, in the format of the worker runtime, e.g. `myapp.tasks:my_func1` for Python.

> **Settled:** moved to [workers-and-manager.md](workers-and-manager.md), "Handlers" and the startup steps.

## 3. Loops

### 3.1 Exit condition

- Is it a task inside the loop body whose result must be a boolean? In word_picker
  it is `enough_rounds`, which runs after `collect_words`; in word_picker_rounds it is `ran_enough`.

> **Answer:** yes, the loop definition should contain something like:

  name: my_loop
  max_cyles: 5
  exit_codition: collect_words


### 3.2 What the exit task receives

flows.md says `(loop_count, other_tasks_results)`, but `enough_rounds` only receives
`collect_words`'s output.

- Is `loop_count` passed automatically, or only when the config asks for it?

> **Answer:** exit task inputs are populated by config, same as regular tasks, they can equally request an optional neorc.loop_count as any of them.


### 3.3 Max cycles

The example is inconsistent: word_picker checks `> 10` before incrementing, word_picker_rounds
checks `>= 5` after.

- Canonical definition, e.g. "at most N complete iterations"?

> **Answer:** per loop config.

### 3.4 Accumulation

flows.md says results accumulate in "a single list", but `all_rounds` is a
list of lists: one entry per iteration, each holding one entry per fan-out
index. After the loop, `score_words` uses only the last `collect_words` result; inside the
loop, `collect_words` receives every iteration so far.

- Consumers need to choose between `last` and `all`. Which is the default, and
  is the other opted into per input?

> **Answer:** examples are an approximation. consumers will always receive "all". They can pick "last" inside their own code.
Iterations are provided to consumers in order.

> **Settled:** moved to [flows.md](flows.md), "Loops" and "Resolving references".

### 3.5 Exceeding max cycles

- Does the flow run fail? Are partial results kept?

> **Answer:** the entire flow fails.

## 4. Fan-out and fan-in

### 4.1 Scope

In the example a fan-out covers a chain of tasks (`pick_word → extract_word` per index), not a
single task, so a fan-out wraps a group sharing an index.

- Is the index passed to every task in the group?

> **Answer:** it is passed when the config requests it.
Inside a fan-out branch, a reference to a task in the same branch resolves to
that branch's single value.

> **Settled:** moved to [flows.md](flows.md), "Map / Fanout" and "Resolving references".


### 4.2 Range and list sources

- Can `range` bounds come from an upstream result, or only config literals?
- Can the iterated list (`for x in result_a`) be any task's output?

> **Answer:** range will always start at 1, the upper bound will be constant in the config.
Fan-out over an upstream task's list output is also allowed; instance *i* handles element *i*.

> **Settled:** moved to [flows.md](flows.md), "Map / Fanout".

### 4.3 Fan-in order and failures

- Are fan-in results ordered by index?
- If one branch fails: does the fan-in task fail, receive partial results, or
  are the other branches cancelled?

> **Answer:** ordered by index, not by completion. No per-result metadata: consumers receive plain values. if any task fail the fan-in task is not executed, the flow fails.

> **Settled:** moved to [flows.md](flows.md), "Map / Fanout".

### 4.4 Nesting and concurrency

- Can a fan-out contain another fan-out or a loop? The example only has a
  fan-out inside a loop.
- Is there a limit on how many branches run at once?

> **Answer:** 
- loops and fan-out should be nestable.
- no limits, that will implicitly managed by the amount of workers and the workers' concurrency setup. there is no cap per flow or task in this version.

## 5. Sub-flows

### 5.1 Sub-flow nodes

word_picker_rounds calls word_picker.

- Is a sub-flow a node referring to `name` plus `version`?
- Is the version pinned exactly, a semver range, or always the latest?

> **Answer:** 
They always call the latest version.
Only the latest version of each flow can be executed.
versions in flows are only for disambiguating during deployment and for querying historical executions.

> **Settled:** moved to [flows.md](flows.md), "Sub-flows and versions".


### 5.2 Flow output

word_picker returns `keep_matching_words`'s result. word_picker_rounds returns nothing, so `result` is
`None`.

- Is that a slip in the example, or can flows have no output?
- How is a flow's output declared?

> **Answer:** flow config points to a task that will be used for the final flow return value. It's optional. Flow only returns a value when all its tasks are finished no matter if they are used in the output or not.

### 5.3 Flow inputs

- Are flow parameters (`sentence`, `preferred_letter`) declared in the
  file? With types?

> **Answer:** yes, any json primitive plus datetime.

> **Settled:** value types moved to [workers-and-manager.md](workers-and-manager.md), "Values".

## 6. Scheduler architecture

### 6.1 Separate process or in the manager

core.md makes the scheduler a separate process that fetches flow definitions
and polls events. The alternative: the manager schedules downstream tasks in
the same transaction as `report_finished` — atomic, and no event log needed.

A separate scheduler needs:

- an events or outbox port in the store
- scheduling that is safe to repeat, e.g. deterministic task ids per
  (run, node, iteration, index)
- a plan for several schedulers at once: a leader, or a lock per flow run

Questions:

- Is it separate so it scales independently, or so it is optional?
- What happens to a flow run with dependencies when no scheduler is running:
  does it wait, or is it rejected?

> **Answer:** 
Scheduler should run as an independent service and not reference manager or worker code.
It will comunicate with the manager by http requests.
It will do long polling similar to the worker to detect events, such as a task finished.
When there is no scheduler running the events requiring scheduler actions just stay in the queue an in the tables.
The scheduler is always required, even for a single-task flow: it publishes a run's first tasks, publishes downstream tasks, and closes runs.

> **Settled:** moved to [core.md](core.md), "The scheduler".


### 6.2 Where run state lives

- Does the scheduler rebuild run state from the database on every event, or
  keep its own?

> **Answer:** the scheduler has no state, it will wait for a "run started" or "task finished" event, then pull the flow definition from the manager and emit the new tasks requests if needed.
The definition pulled is the one for the run's version, not the latest.

> **Settled:** moved to [core.md](core.md), "The scheduler".

## 7. Runs, failures, versions

### 7.1 Flow runs

A new "flow run" entity is needed, with a status, a start endpoint
(`name, inputs`) and a status query.

- Anything beyond that?

> **Answer:** yes, new table. that sounds good.
The start payload carries no version: a run always starts on the flow's latest version, which the manager records on the run.

### 7.2 Failures and cancellation

- If a task fails, does the whole run fail?
- Retries are already open in
  [postgres-implementation.md](postgres-implementation.md); flows make them
  more pressing. Do they come first?
- Can a run be cancelled?

> **Answer:** 
- yes, if any task is in failed status the flow will fail.
- we will do flows first, no retries yet.
- cancelling a run change the flow run status in the db. in flight tasks can finish, no new tasks will be triggered.
- the manager applies cancellation and failure to the whole run tree in one transaction. Manual cancelling uses the same mechanism as deploys. The scheduler asks for failures; the manager propagates them.

> **Settled:** moved to [core.md](core.md), "Cancellation and failure".

### 7.3 Versions

- Is a run pinned to the flow version it started with, even if a new version
  is uploaded mid-run?

> **Answer:** yes, each run can have only one version.
If a new version is uploaded mid-run, the run is marked as cancelled, in-flight tasks are allowed to finish, no new tasks will be triggered for that run.
The manager cancels the whole run tree, parents and children, in the same transaction as storing the new version.

> **Settled:** moved to [flows.md](flows.md), "Sub-flows and versions", and [core.md](core.md), "Cancellation and failure".

### 7.4 Queues

- Is the task → queue mapping part of the flow file? Queue names are not
  implemented yet.

> **Answer:** queue name will be field at the task level. that will allow to have specialized queues with specialized workers, that could be in different programming languages. Let's say queues: "python-gpu", "java-finance", "python-default"
