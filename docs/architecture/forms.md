# Forms

A flow can give the page that starts a run of it a form of its own: a list of
values to pick from, radio buttons, a check box, a text area, in the place of
one text box per input. The form is a file next to the flow's file, written in
a small subset of [A2UI](https://github.com/a2ui-project/a2ui) v0.9.

A task of an external queue can have a form too, for the person who answers
it: [A task with a form](#a-task-with-a-form). Such a form can also show the
task's inputs, repeat a card for each item of a list in them, and end with
buttons that complete the task: [A form that reads the task's
inputs](#a-form-that-reads-the-tasks-inputs).

This is one feature in one place. Everything about it is listed under
[Where it is](#where-it-is), and [Taking it out](#taking-it-out) says how.

## A flow with a form

```yaml
# flows/code_task.yaml
name: code_task
version: 1.0.0

inputs:
  description: string
  repo: string
  draft: boolean

form: code_task.form.json
```

`form` names a file in the flow's own directory, by its name alone.
[examples/code_task](../../examples/code_task/README.md) is a whole one, to
run.

- The file is read in where the flow file is read: `neorc flows upload`,
  `neorc run`. What is uploaded and stored holds the form itself under
  `form`, so the manager reads no file a flow names.
- **A form is part of its flow's version.** Changing the form file is
  changing the flow, and needs a new version as any change does.
- A flow with no `form` is as it was: one field per input.

## The form file

The messages an A2UI agent would send to draw a surface, as a JSON list: one
`createSurface`, one or more `updateComponents`, and at most one
`updateDataModel` with what the form starts from.

```json
[
  { "version": "v0.9",
    "createSurface": { "surfaceId": "code_task",
                       "catalogId": "https://github.com/open-shipyard/neorc/forms/v1" } },
  { "version": "v0.9",
    "updateComponents": { "surfaceId": "code_task", "components": [
      { "id": "root", "component": "Column", "children": ["description", "repo", "draft"] },
      { "id": "description", "component": "TextField", "label": "Task description",
        "variant": "longText", "value": { "path": "/description" },
        "checks": [ { "condition": { "call": "length",
                                     "args": { "value": { "path": "/description" }, "min": 10 } },
                      "message": "Describe the task in at least 10 characters." } ] },
      { "id": "repo", "component": "ChoicePicker", "label": "Repository",
        "variant": "mutuallyExclusive", "value": { "path": "/repo" },
        "options": [ { "label": "billing", "value": "acme/billing" },
                     { "label": "search", "value": "acme/search" } ] },
      { "id": "draft", "component": "CheckBox", "label": "Open as a draft",
        "value": { "path": "/draft" } }
    ] } },
  { "version": "v0.9",
    "updateDataModel": { "surfaceId": "code_task", "path": "/",
                         "value": { "repo": ["acme/billing"], "draft": true } } }
]
```

### The components

Nine of A2UI's basic catalog, each with the properties listed and no other.

| component | properties | the input it fills |
|---|---|---|
| `Column` | `children`: the ids of the components under it | none |
| `Row` | `children` | none |
| `Card` | `child`: the id of the one component inside it | none |
| `List` | `children`, or in a task's form a template | none |
| `Text` | `text`; `variant`: `h1` to `h5`, `caption`, `body` | none |
| `TextField` | `label`, `value`, `checks`; `variant`: `shortText`, `longText`, `number` | a `string`; a `number` when the variant is `number` |
| `CheckBox` | `label`, `value` | a `boolean` |
| `ChoicePicker` | `label`, `value`, `options`, `checks`; `variant`: `mutuallyExclusive`; `displayStyle`: `checkbox`, `chips` | a `string`: the value of the one option chosen |
| `Button` | `child`, `action`; `variant`: `default`, `primary`, `borderless`; in a task's form only | none in a flow; in a task, the field its action sets |

- The component with the id `root` is a `Column`, and every other component
  is under it exactly once. A `Card` places its `child` under it, and a
  `Button` the `Text` that is its label.
- `label`, an option's `label` and `value` are plain strings. So is `text`,
  except in a task's form, where it may read the task's inputs.
- `value` is `{"path": "/<input>"}`: the input of the flow this control
  fills.
- **Every input of the flow has exactly one control**, of a kind that fills
  its declared type. Nothing in the subset fills a `datetime` yet, so a flow
  that declares one cannot have a form.

### The checks

A check is a rule and what to say when it is broken, as A2UI writes one. A
form may use those the manager can keep by itself:

| control | checks | arguments |
|---|---|---|
| a text field | `required`, `length` | `length`: `min`, `max` or both, in characters |
| a number field | `required`, `numeric` | `numeric`: `min`, `max` or both |
| a choice | `required` | |

Each is about the control's own value: its `args.value` is the control's
`value`.

### What a form starts from

`updateDataModel`, at the path `/`, gives controls a value to start from, in
the shape A2UI keeps each in: a string for a text field, a string or a number
for a number field, `true` or `false` for a check box, and for a choice a list
of at most one of its options' values.

It sets no value for a control in a List's template, and nothing at
`/inputs`: the task's inputs are there.

## What the page does

`StartRunForm` draws the flow's form when it has one.

- A choice of up to four options is radio buttons; of more, a list to pick
  from. A choice whose `displayStyle` is `chips` is a bar of buttons, one
  pressed at a time.
- A `Row` lays its children side by side, and a `Card` draws a box around
  its child.
- The form has no button. The page's own "Start run" starts the run, with
  what the form holds: an input named `repo` is what was written at `/repo`.
  A choice's list of one becomes the string, and a number field's text the
  number.
- A broken check is said with the form's own message, and nothing is sent.
  A choice left unmade and a number field without a number are refused too.
- "Edit as JSON" is still there, for what a form cannot say.
- A form the page cannot read is not drawn: the page falls back to one field
  per input. The manager has refused such a form already; this is for a page
  older than its manager.

## A host's form

A host application, such as Robinauts, mounts neorc's root component in its
own shell. It may give the root component `forms`, its source of forms to
start a run.

- The source gets a flow's name, latest version and definition. It answers
  the form's messages, in the same subset, or null.
- A form from the host takes the place of the flow's own form and of the
  fields. neorc draws its own for a flow the host has no form for.
- A host's form the page cannot read is not drawn. The page says so, and
  draws its own form.
- The form sends the flow's inputs, whoever described it. The manager holds
  every start to the flow's own form, if the flow has one.

## What the manager does

- **At upload**, a form outside the subset is refused, with every reason, and
  nothing is stored: `form.clip: component 'Video' is not in the subset`.
- **At every start of a run**, after the inputs' declared types are checked,
  the inputs must keep what the form says: a choice is one of its options,
  and each check holds. That is for every start, whoever sends it: the page, a
  script with a token, a webhook, a flow calling this one as a sub-flow. A
  calling flow's run fails with the reason, as it does for an input of the
  wrong type.

        code_task: input 'repo' is not one of acme/billing, acme/search

So a form is not only how a page looks. It narrows what the flow accepts.

## A task with a form

A task of an external queue may name a form too, for the person who answers
it on the queue's page ([task-completion.md](../specs/task-completion.md)).
[examples/approval](../../examples/approval/README.md) is a whole one, to run.

```yaml
review_draft:
  queue: editors
  handler: review-draft
  params:
    draft: tasks.write_draft
  form: review_draft.form.json
```

- **Each control is a field of the task's result**, named by its `value`: a
  check box at `/approved` makes the result `{"approved": true}`.
- **At upload**, a form on a task of a worker queue is refused, and so is a
  form on the exit condition of a loop.
- **At every completion**, the result must have the form's fields and keep
  what the form says, whoever sends it. A completion with an error has no
  result to hold. For a form that reads the task's inputs, see the next
  section.
- A task with no form gets a text field, and the exit condition of a loop a
  yes or no.

## A form that reads the task's inputs

A task's form can show what the task was given. Its data starts with the
task's inputs under `/inputs`, as a worker would receive them. They are read
only: no control is bound there, and the result does not hold them.
[examples/review_loop](../../examples/review_loop/README.md) is a whole one,
to run.

The task below triages the issues an AI reviewer found. Its input `review` is
the reviewer's result:

    {"pr_url": "https://github.com/o/r/pull/7", "summary": "...",
     "issues": [{"n": 1, "severity": "blocker", "title": "Crash on empty input",
                 "detail": "a.py:9"}, ...]}

The form shows one card per issue, a bar of Fix, Defer and Discard in each,
and two buttons at the end, Re-work and Ready:

```json
{ "id": "root", "component": "Column", "children": ["summary", "issues", "verdicts"] },
{ "id": "summary", "component": "Text", "text": { "path": "/inputs/review/summary" } },
{ "id": "issues", "component": "List",
  "children": { "componentId": "issue", "path": "/inputs/review/issues" } },
{ "id": "issue", "component": "Card", "child": "issue_body" },
{ "id": "issue_body", "component": "Column", "children": ["issue_head", "issue_detail", "decision"] },
{ "id": "issue_head", "component": "Row", "children": ["issue_severity", "issue_title"] },
{ "id": "issue_severity", "component": "Text", "variant": "caption", "text": { "path": "severity" } },
{ "id": "issue_title", "component": "Text", "variant": "h2", "text": { "path": "title" } },
{ "id": "issue_detail", "component": "Text", "text": { "path": "detail" } },
{ "id": "decision", "component": "ChoicePicker", "label": "Decision",
  "variant": "mutuallyExclusive", "displayStyle": "chips", "value": { "path": "decision" },
  "options": [ { "label": "Fix", "value": "fix" }, { "label": "Defer", "value": "defer" },
               { "label": "Discard", "value": "discard" } ] },
{ "id": "verdicts", "component": "Row", "children": ["rework", "ready"] },
{ "id": "rework", "component": "Button", "child": "rework_label",
  "action": { "event": { "name": "submit", "context": { "verdict": "rework" } } } },
{ "id": "rework_label", "component": "Text", "text": "Re-work" },
{ "id": "ready", "component": "Button", "variant": "primary", "child": "ready_label",
  "action": { "event": { "name": "submit", "context": { "verdict": "ready" } } } },
{ "id": "ready_label", "component": "Text", "text": "Ready" }
```

A person who decides Fix, Defer, Discard and presses Ready completes the task
with:

    {"decision": ["fix", "defer", "discard"], "verdict": "ready"}

- **A path from the root reads the inputs.** It is `/inputs/<input>`, or a
  path below it into an input that is a mapping: `/inputs/review/summary`.
  The input is one the task has, in `params` or `fixed_params`.
- **A `Text` may show a value**, with `{"path": ...}` in the place of its
  text. A value that is not a string is shown as JSON, and a missing one as
  nothing.
- **A `List` may repeat a template** over a list in the inputs:
  `{"componentId": ..., "path": "/inputs/..."}`. It draws the template once
  per item. A template in a template is left out.
- **Inside the template, a path without a leading slash is relative to the
  item.** `{"path": "title"}` is the title of the issue the card is for.
- **A control in the template submits a list**, one value per item, in the
  item's order. Its `value` is relative and names the field of the result:
  `{"path": "decision"}` fills `decision`. It starts with no value.
- **A `Button` completes the task.** Its `action` is an event whose
  `context` sets one field to a string: `{"verdict": "ready"}`. Every button
  of a form sets the same field, each to a value of its own. The result is
  what the controls hold, with that field. A button is not in a template.
- **A form with buttons has no other way to complete.** The page's own
  "Complete" is left out of it; "Edit as JSON" and "Fail the task" stay.
- A form of buttons alone is a form. Its result is the one field.

The manager holds every completion of such a task to its form, whoever sends
it. It reads the task's inputs as a worker would receive them:

| the result's | must be |
|---|---|
| field of a control in a template | a list with one value per item of the list in the inputs, each one the control takes |
| field of the buttons | one of the buttons' values |
| any other field | as for any task's form |

        triage: field 'decision' has 2 values, and /inputs/review/issues has 3 items
        triage: field 'decision', item 2, is not one of fix, defer, discard
        triage: field 'verdict' is not one of rework, ready

A task whose inputs hold no list where its form reads one cannot be
completed with a result: `the task's inputs have no list at
/inputs/review/issues`. The page cannot draw that form either, and falls
back to a text field. The task can still fail with a reason.

## What is ours and not A2UI's

A form file is A2UI v0.9: its messages, its components and their properties,
its bindings, its checks. Around it, six things are this project's:

- **The catalog.** A surface names
  `https://github.com/open-shipyard/neorc/forms/v1`, which is the subset
  above. A2UI's way of saying which components a surface may use is a
  catalog, and this one is smaller than the basic one.
- **How a form submits.** In A2UI a `Button` sends an event, with its
  context, to the agent that drew the surface. A flow's form has no button:
  the page submits the data the controls wrote. In a task's form a `Button`
  completes the task: the result is that data, with the field its context
  sets. The event's `name` is not read.
- **The inputs at `/inputs`.** In A2UI an agent sends the data a surface
  starts from. Here the page puts the task's inputs at `/inputs` before the
  form's own data, and keeps them out of the result. A form cannot write
  there, nor start a value there.
- **A control in a template.** In A2UI a control bound to `decision` in a
  template writes into the item, at `/inputs/review/issues/0/decision`. Here
  it writes into the result's `decision`, a list, at the item's index.
- **One value for one choice.** A2UI keeps a choice as a list. The input a
  run gets is the string.
- **A number from a text field.** A2UI keeps a text field's value as text
  whatever its variant.

## Left out

Each of these is refused by name when a form uses it. None is ruled out; each
is a piece of work that was not needed to start a run.

| left out | what it would take |
|---|---|
| a `datetime` input | A2UI's `DateTimeInput`, written as neorc's `$datetime` |
| a choice of several | an input type for a list, which flows do not have |
| `regex` checks | patterns that mean the same in Python and in a browser, and that cannot stall the manager |
| `and`, `or`, `not`, and a check about another control | the manager evaluating checks rather than reading limits out of them |
| a `Button` in a flow's form | a reason for one beside "Start run" |
| a `Button` with a `functionCall`, or a context bound to data | the page evaluating functions and paths for an event |
| `Divider`, `Tabs`, `Modal`, `Image`, `Icon` and the media | the page drawing them; they submit nothing |
| `justify`, `align`, `weight`, `direction`, `listStyle`, `filterable` | the page laying out and filtering as A2UI says |
| a template on a `Column` or `Row`, and a template in a template | a result of lists of lists, and checks for them |
| a text bound to data in a flow's form, a label bound to data, `formatString` and the other functions | a flow's form having data to read, and the page keeping a data model beyond the inputs |
| a list index in a path, and indexing from the end | a path that picks one item of a list in the inputs |
| a control or a check bound to another item | checks across items of a list |
| a theme, `sendDataModel`, `deleteSurface` | nothing a form of a flow needs |
| options from another service | a route of the manager's that asks it, and its configuration |

## Where it is

| | path |
|---|---|
| the rules, and what a form asks of inputs | `python/neorc-core/src/neorc_core/forms/` |
| drawing a form | `ts/neorc-ui/src/forms/` |
| the tests of the first | `python/neorc-core/tests/forms/` |
| the tests of the second | `ts/neorc-ui/src/forms/*.test.ts*` |
| three examples | `examples/code_task/`, `examples/approval/`, `examples/review_loop/` |
| this page | `docs/architecture/forms.md` |

`neorc_core.forms` imports nothing of `neorc_core` but its errors. The rest of
neorc meets the feature in **eight places**, four for a flow's form and four
for a task's:

| where | what |
|---|---|
| `neorc_core/flows/_loader.py`, `_TOP_KEYS` and `_Parser.flow` | a flow may have a `form`, and `form_problems` says what is wrong with it |
| `neorc_core/flows/_loader.py`, `load_flow_yaml`, `load_flow_file`, `read_flows` | `inline_form` reads the files a flow file names, the tasks' too |
| `neorc_core/_manager.py`, `_start_latest` | `check_form_inputs`, after `check_inputs` |
| `ts/neorc-ui/src/components/StartRunForm.tsx` | `formOf`, `answered` and `<FlowForm>`, in the place of the fields; `useHostForm`, the host's form, before the flow's own |
| `neorc_core/flows/_loader.py`, `_STEP_KEYS`, `_Parser.step` and `_Parser.loop` | a task may have a `form`, which reads only the task's params, and an exit condition may not |
| `neorc_core/_manager.py`, `upload_flows` | `step_forms`: a form is for a task of an external queue |
| `neorc_core/_manager.py`, `complete_task` | `check_form_result`, with the task's inputs, before the completion is stored |
| `ts/neorc-ui/src/components/TaskPanel.tsx` | `formOf` with the task's inputs, `answered` and `<FlowForm>` with its buttons, in the place of the text field |

Nothing else knows what a form holds. The store keeps it as part of the flow's
content, the HTTP routes carry that content as they did, and the adapters in
`neorc` have no line about it.

`ts/neorc-ui/src/forms/code_task.form.json`, `review_draft.form.json` and
`triage.form.json` are copies of the examples' forms, which the UI's tests
read; a test in `python/neorc-core/tests/forms/` fails when a copy differs.

## Taking it out

1. Delete the paths of the first table.
2. Undo the places of the second: take `"form"` out of `_TOP_KEYS` and
   `_STEP_KEYS`, and the lines about it in `_Parser.flow`, `_Parser.step` and
   `_Parser.loop`; make the three readers of `_loader.py` read a file's
   structure as it is; take the lines out of `_start_latest`, `upload_flows`
   and `complete_task`; give `StartRunForm` back its fields alone, and
   `TaskPanel` its text field. Take `forms` off the root component
   (`ts/neorc-ui/src/context.ts`, `NeorcApp.tsx`).
3. Take the journey on `examples/review_loop` out of
   `python/neorc/tests/test_ui_browser.py`.
4. Take the entries out of `CHANGELOG.md` and the paragraphs out of
   `README.md`.

A flow stored with a `form`, its own or a task's, is then a flow with an
unknown field: it has to be uploaded again without one, under a new version.
