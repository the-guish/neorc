# Forms

A flow can give the page that starts a run of it a form of its own: a list of
values to pick from, radio buttons, a check box, a text area, in the place of
one text box per input. The form is a file next to the flow's file, written in
a small subset of [A2UI](https://github.com/a2ui-project/a2ui) v0.9.

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

Five of A2UI's basic catalog, each with the properties listed and no other.

| component | properties | the input it fills |
|---|---|---|
| `Column` | `children`: the ids of the components under it | none |
| `Text` | `text`; `variant`: `h1` to `h5`, `caption`, `body` | none |
| `TextField` | `label`, `value`, `checks`; `variant`: `shortText`, `longText`, `number` | a `string`; a `number` when the variant is `number` |
| `CheckBox` | `label`, `value` | a `boolean` |
| `ChoicePicker` | `label`, `value`, `options`, `checks`; `variant`: `mutuallyExclusive` | a `string`: the value of the one option chosen |

- The component with the id `root` is a `Column`, and every other component
  is under it exactly once.
- `label`, `text` and an option's `label` and `value` are plain strings.
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

## What the page does

`StartRunForm` draws the flow's form when it has one.

- A choice of up to four options is radio buttons; of more, a list to pick
  from.
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

## What is ours and not A2UI's

A form file is A2UI v0.9: its messages, its components and their properties,
its bindings, its checks. Around it, four things are this project's:

- **The catalog.** A surface names
  `https://github.com/open-shipyard/neorc/forms/v1`, which is the subset
  above. A2UI's way of saying which components a surface may use is a
  catalog, and this one is smaller than the basic one.
- **How a form submits.** In A2UI a `Button` sends an event with the values
  it lists. A form here has no button: the data the controls wrote is what is
  submitted, by the page.
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
| `Button` and events | something to send an event to: a human task |
| `Row`, `Card`, `Divider`, `Tabs`, `Modal`, `List`, and a child template | the page drawing them; they submit nothing |
| a label or a text bound to data, `formatString` and the other functions | the page keeping a data model beyond the inputs |
| a theme, `sendDataModel`, `deleteSurface` | nothing a form of a flow needs |
| options from another service | a route of the manager's that asks it, and its configuration |

## Where it is

| | path |
|---|---|
| the rules, and what a form asks of inputs | `python/neorc-core/src/neorc_core/forms/` |
| drawing a form | `ts/neorc-ui/src/forms/` |
| the tests of the first | `python/neorc-core/tests/forms/` |
| the tests of the second | `ts/neorc-ui/src/forms/*.test.ts*` |
| an example | `examples/code_task/` |
| this page | `docs/architecture/forms.md` |

`neorc_core.forms` imports nothing of `neorc_core` but its errors. The rest of
neorc meets the feature in **four places**:

| where | what |
|---|---|
| `neorc_core/flows/_loader.py`, `_TOP_KEYS` and `_Parser.flow` | a flow may have a `form`, and `form_problems` says what is wrong with it |
| `neorc_core/flows/_loader.py`, `load_flow_yaml`, `load_flow_file`, `read_flows` | `inline_form` reads the file a flow file names |
| `neorc_core/_manager.py`, `_start_latest` | `check_form_inputs`, after `check_inputs` |
| `ts/neorc-ui/src/components/StartRunForm.tsx` | `formOf`, `answered` and `<FlowForm>`, in the place of the fields |

Nothing else knows what a form holds. The store keeps it as part of the flow's
content, the HTTP routes carry that content as they did, and the adapters in
`neorc` have no line about it.

`ts/neorc-ui/src/forms/code_task.form.json` is a copy of the example's form,
which the UI's tests read; a test in `python/neorc-core/tests/forms/` fails
when the two differ.

## Taking it out

1. Delete the six paths of the first table.
2. Undo the four places of the second: take `"form"` out of `_TOP_KEYS` and
   the two lines under it in `_Parser.flow`; make the three readers of
   `_loader.py` read a file's structure as it is; take the one line out of
   `_start_latest`; give `StartRunForm` back its fields alone.
3. Take the entry out of `CHANGELOG.md` and the paragraph out of `README.md`.

A flow stored with a `form` is then a flow with an unknown field: it has to be
uploaded again without one, under a new version.
