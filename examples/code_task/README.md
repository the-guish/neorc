# code_task

A flow with a form. `flows/code_task.yaml` declares five inputs and names
`flows/code_task.form.json`, the file next to it, as the screen that asks for
them. What a form may hold is in
[docs/architecture/forms.md](../../docs/architecture/forms.md).

Run it in one process, with nothing to deploy, from the repository root after
`uv sync`:

    uv run neorc run examples/code_task --flow code_task --inputs '{
        "description": "Add a retry to the upload step.",
        "repo": "acme/billing", "model": "medium", "attempts": 2, "draft": true}'

It prints the run's output, a sentence saying back what was asked.

The form is not only how the page looks. It also says what the inputs may be,
and the manager keeps that for every run, whoever starts it. A repository the
form does not list is refused:

    uv run neorc run examples/code_task --flow code_task --inputs '{
        "description": "Add a retry to the upload step.",
        "repo": "acme/unknown", "model": "medium", "attempts": 2, "draft": true}'

To see the form drawn, serve the flows as [examples/hello](../hello/README.md)
does, with this directory in the place of `examples/hello`, and open the
flow's page in the web UI: "Start a run" shows a text area, a list of
repositories, three radio buttons, a number and a check box.
