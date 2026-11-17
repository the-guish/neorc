# Flows

## Example 1

Neorc should provide the wiring between tasks that allows to implement flows like flow_example.py


## Loops

Neorc only supports loops of this shape

```python
exit_condition = False
loop_count = 0
while not exit_condition:
    ... execute tasks
    if loop_count > max_configured_cyles:
        raise MaxCycles()
    exit_condition = selected_task_by_the_user(loop_count, other_tasks_results)

```

Neorc will accumulate the results of each task in a list, one entry per
iteration, in iteration order. A consumer always receives that list, never a
single iteration's result; picking the last one is up to the consumer's code.

Users cannot have the equivalent to

```python
exit_condition = False
loop_count = 0
my_custom_var = []
while not exit_condition:
    ... operate on my_custom_var

```

## Map / Fanout

Taking a task output and fanout to multiple copies of it with an index is supported:
```python
payload = compose_payload(sentence)
for i in range(1, 4):
    picked = pick_word(payload, i)
```

also

```python
words = ["red", "green", "blue"]
for word in words:
    padded = pad(word)
```

A range always starts at 1 and its upper bound is a constant in the config. A
list comes from an upstream task's output; instance *i* handles element *i*.

A fan-out can wrap a chain of tasks, like `pick_word → extract_word` in
[flow_example.py](flow_example.py); every task in the chain shares the
branch's index. The index reaches a handler only when the config requests it.

Fan-in results are ordered by index, not by the order branches finished. The
index is fixed when a branch's task is created and every branch has finished
before the fan-in task runs, so ordering costs the engine nothing. Results
carry no metadata: a consumer receives plain values, and for a list fan-out
result *i* lines up with input element *i*. If any branch fails, the fan-in
task is not executed and the flow fails.

## Switch

A switch runs one of several named cases. See [switch.md](switch.md).

## Resolving references

What a reference to another task's output resolves to depends on the loops and
fan-outs around the referenced task, relative to the consumer:

| Where the referenced task is                  | What the consumer receives                                  |
| --------------------------------------------- | ----------------------------------------------------------- |
| In the consumer's own fan-out branch          | That branch's single value                                  |
| In a fan-out the consumer is outside of       | A list of every branch's result, ordered by index           |
| In a loop the consumer is inside of           | The loop's results so far, in order, current iteration last |
| In a loop the consumer comes after            | Every iteration's result, in order                          |

Each enclosing fan-out the consumer is outside of, and each enclosing loop,
adds one list level, outermost first. A fan-out inside a loop, read from after
the loop, arrives as a list of iterations, each a list of branch results: the
`all_rounds` of the example.

## Sub-flows and versions

A flow can call another flow as a sub-flow, like `word_picker_rounds` calling
`word_picker` in [flow_example.py](flow_example.py). A sub-flow always runs the
latest version of the called flow: only the latest version of a flow can be
executed. Versions exist to disambiguate deployments and to query past runs.

Each run belongs to exactly one version. Uploading a new version of a flow
(see [workers-and-manager.md](workers-and-manager.md) for re-uploads of an
existing version) cancels that flow's runs in progress: tasks already in flight may finish, and no
new tasks are published for those runs.

Cancelling a run cancels its whole run tree: the top-level run and every
sub-flow run under it. Deploying a flow therefore cancels every run currently
inside it, including runs of flows that call it. Failures propagate across the
tree the same way. See [core.md](core.md), "Cancellation and failure".

