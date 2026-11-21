# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The MCP server: a tool per route of the manager a person or CI may call,
and the panel, an MCP App that draws a flow or a run as neorc's UI does.

Each API tool sends one request and answers with the manager's JSON twice: as
text for the model, and as structured content. Its name is the route's, so a
host can let an agent have one route and not another.

The panel keeps no state. ``neorc_panel`` is the panel's own tool and names the
view; it answers for the run or the flow its arguments name, and with none, the
choices. ``show_flow`` and ``show_run`` answer the same, for the model and for a
host that hands their result to the view. The view keeps its own choice, and
may call those three tools and nothing else. docs/specs/mcp.md.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from neorc_core import FlowDefinitionError
from neorc_core.flows import parse_flow, read_flow_yaml, validate_flow_set
from neorc_mcp._client import As, ManagerApi, ManagerError, ManagerRefused, segment

VIEW_URI = "ui://neorc/panel.html"
VIEW_MIME_TYPE = "text/html;profile=mcp-app"
PANEL_TOOL = "neorc_panel"

MODEL_ONLY: dict[str, Any] = {"ui": {"visibility": ["model"]}}
"""A tool the model calls, and the view does not."""
MODEL_AND_VIEW: dict[str, Any] = {"ui": {"visibility": ["model", "app"]}}
"""A tool the view calls too: what the panel shows."""

READ = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
DESTROY = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)
RECENT_RUNS = 20
"""How many of the latest runs the panel offers when nothing is named."""

INSTRUCTIONS = """\
neorc runs flows: steps of tasks, which workers or people complete. These tools \
are the manager's API: list and read flows, start runs, read runs with their \
tasks, cancel runs, list queues and their tasks, complete a task that waits for \
a person, and, with CI's token, upload flows and declare queues.

To troubleshoot a run, read it (get_run: its status and reason), then its tasks \
(list_run_tasks): a failed task's `error` holds the exit code and the end of \
the handler's log. list_runs with status "failed" finds failed runs.

show_flow and show_run show the user a flow's steps as a graph, or a run on \
that graph: a host that shows MCP Apps draws them in a panel beside the chat."""

FlowName = Annotated[str, Field(description="The flow's name: letters, digits and _.")]
RunId = Annotated[str, Field(description="The run's id, a UUID.")]
TaskId = Annotated[str, Field(description="The task's id, a UUID.")]
QueueName = Annotated[
    str, Field(description="The queue's name: letters, digits, _ and -.")
]
Limit = Annotated[int, Field(ge=1, le=500, description="The most items to return.")]


def answer(value: Any) -> CallToolResult:
    """The manager's JSON for the model, and as structured content when an object."""
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(value, ensure_ascii=False))],
        structuredContent=value if isinstance(value, dict) else {"value": value},
    )


def build_server(
    api: ManagerApi,
    view_html: Callable[[], str],
    *,
    host: str = "127.0.0.1",
    port: int = 8938,
) -> FastMCP:
    """The server on ``api``; ``view_html`` reads the view when a host asks."""
    server = FastMCP(
        "neorc",
        instructions=INSTRUCTIONS,
        host=host,
        port=port,
        stateless_http=True,
        json_response=True,
        log_level="WARNING",
    )

    async def call(request: Awaitable[Any]) -> CallToolResult:
        try:
            return answer(await request)
        except ManagerError as exc:
            raise ToolError(str(exc)) from None

    def tool(
        name: str, title: str, description: str, annotations: ToolAnnotations
    ) -> Callable[[Callable[..., Awaitable[CallToolResult]]], Any]:
        return server.tool(
            name=name,
            title=title,
            description=description,
            annotations=annotations,
            meta=MODEL_ONLY,
        )

    # Flows.

    @tool(
        "list_flows",
        "List flows",
        "The latest version of every flow: name, version and content.",
        READ,
    )
    async def list_flows() -> CallToolResult:
        return await call(api.get("/flows"))

    @tool(
        "get_flow",
        "Get a flow",
        "A flow's latest version, with its content as uploaded.",
        READ,
    )
    async def get_flow(name: FlowName) -> CallToolResult:
        return await call(api.get(f"/flows/{segment(name)}"))

    @tool(
        "list_flow_versions",
        "List a flow's versions",
        "Every stored version of a flow, newest first.",
        READ,
    )
    async def list_flow_versions(name: FlowName) -> CallToolResult:
        return await call(api.get(f"/flows/{segment(name)}/versions"))

    @tool(
        "get_flow_version",
        "Get a flow version",
        "One version of a flow, such as 1.2.0.",
        READ,
    )
    async def get_flow_version(name: FlowName, version: str) -> CallToolResult:
        return await call(
            api.get(f"/flows/{segment(name)}/versions/{segment(version)}")
        )

    if api.has_token(As.CI):

        @tool(
            "upload_flows",
            "Upload flows",
            "Upload flows deployed together, each as the YAML (or JSON) of a flow "
            "file; per flow, whether a new version was stored. A flow names its "
            "queues, declared first (declare_queue). Uploading a new version of a "
            "flow CANCELS its runs in progress. A flow's form is given inline, "
            "not by file name.",
            DESTROY,
        )
        async def upload_flows(
            flows: Annotated[
                list[str], Field(min_length=1, description="Flow files' text.")
            ],
        ) -> CallToolResult:
            contents = []
            try:
                for index, text in enumerate(flows, 1):
                    try:
                        contents.append(read_flow_yaml(text))
                    except FlowDefinitionError as exc:
                        raise FlowDefinitionError(
                            [f"flow {index}: {p}" for p in exc.problems]
                        ) from None
                validate_flow_set([parse_flow(content) for content in contents])
            except FlowDefinitionError as exc:
                raise ToolError(str(exc)) from None
            return await call(
                api.request("POST", "/flows", body={"flows": contents}, who=As.CI)
            )

        @tool(
            "declare_queue",
            "Declare a queue",
            "Declare a queue with its kind: `worker`, served by workers, or "
            "`external`, "
            "whose tasks a person or another system completes. A declared queue keeps "
            "its kind; another kind is refused.",
            WRITE,
        )
        async def declare_queue(
            queue: QueueName, kind: Literal["worker", "external"]
        ) -> CallToolResult:
            return await call(
                api.request(
                    "PUT", f"/queues/{segment(queue)}", body={"kind": kind}, who=As.CI
                )
            )

    # Runs.

    @tool(
        "start_run",
        "Start a run",
        "Start a run of the flow's latest version with these inputs, as the person "
        'whose token this server holds. A datetime is {"$datetime": "<ISO 8601>"}.',
        WRITE,
    )
    async def start_run(
        flow: FlowName,
        inputs: Annotated[
            dict[str, Any] | None, Field(description="The flow's inputs, by name.")
        ] = None,
    ) -> CallToolResult:
        return await call(
            api.request(
                "POST", f"/flows/{segment(flow)}/runs", body={"inputs": inputs or {}}
            )
        )

    @tool(
        "list_runs",
        "List runs",
        'A page of runs, newest first. `status` "failed" finds failed runs; `before` '
        "is the id of the last run of the previous page.",
        READ,
    )
    async def list_runs(
        flow: FlowName | None = None,
        status: Literal["active", "succeeded", "failed", "cancelled"] | None = None,
        root_only: Annotated[
            bool, Field(description="Leave out sub-flow runs.")
        ] = True,
        before: RunId | None = None,
        limit: Limit = 50,
    ) -> CallToolResult:
        params = {
            "flow": flow,
            "status": status,
            "root_only": str(root_only).lower(),
            "before": before,
            "limit": limit,
        }
        return await call(api.get("/runs", params))

    @tool(
        "get_run",
        "Get a run",
        "A run: its status, inputs, output, and why it failed or was cancelled.",
        READ,
    )
    async def get_run(run_id: RunId) -> CallToolResult:
        return await call(api.get(f"/runs/{segment(run_id)}"))

    @tool(
        "list_run_tasks",
        "List a run's tasks",
        "A run's tasks, in the order they were published, with each one's status, "
        "attempts, result and error. A failed task's error is the handler's exit code "
        "and the end of its log: what to read to troubleshoot.",
        READ,
    )
    async def list_run_tasks(run_id: RunId) -> CallToolResult:
        return await call(api.get(f"/runs/{segment(run_id)}/tasks"))

    @tool(
        "list_sub_runs",
        "List a run's sub-runs",
        "A run's direct sub-flow runs, in the order they started.",
        READ,
    )
    async def list_sub_runs(run_id: RunId) -> CallToolResult:
        return await call(api.get(f"/runs/{segment(run_id)}/sub-runs"))

    @tool(
        "get_run_state",
        "Get a run's state",
        "What a run's tasks and sub-flow runs have produced so far.",
        READ,
    )
    async def get_run_state(run_id: RunId) -> CallToolResult:
        return await call(api.get(f"/runs/{segment(run_id)}/state"))

    @tool(
        "cancel_run",
        "Cancel a run",
        "Cancel a run, and with it every active run in its tree.",
        DESTROY,
    )
    async def cancel_run(run_id: RunId) -> CallToolResult:
        await call(api.request("POST", f"/runs/{segment(run_id)}/cancel"))
        return answer({"cancelled": run_id})

    # Queues and tasks.

    @tool(
        "list_queues",
        "List queues",
        "Every declared queue, by name, with its kind.",
        READ,
    )
    async def list_queues() -> CallToolResult:
        return await call(api.get("/queues"))

    @tool(
        "list_queue_tasks",
        "List a queue's tasks",
        "A queue's tasks that changed since `since` (24 hours ago when left out), in "
        'any status, the one published last first. `status` "pending" lists the '
        "tasks waiting, such as those a person completes.",
        READ,
    )
    async def list_queue_tasks(
        queue: QueueName,
        since: Annotated[str | None, Field(description="An ISO 8601 datetime.")] = None,
        status: Literal["pending", "received", "running", "succeeded", "failed"]
        | None = None,
        flow: FlowName | None = None,
        limit: Limit = 50,
    ) -> CallToolResult:
        params = {"since": since, "status": status, "flow": flow, "limit": limit}
        return await call(api.get(f"/queues/{segment(queue)}/tasks/recent", params))

    @tool(
        "get_task",
        "Get a task",
        "A task: its status, attempts, result and error.",
        READ,
    )
    async def get_task(task_id: TaskId) -> CallToolResult:
        return await call(api.get(f"/tasks/{segment(task_id)}"))

    @tool(
        "get_task_inputs",
        "Get a task's inputs",
        "A task with its inputs filled in, as a worker or a person receives it.",
        READ,
    )
    async def get_task_inputs(task_id: TaskId) -> CallToolResult:
        return await call(api.get(f"/tasks/{segment(task_id)}/inputs"))

    @tool(
        "complete_task",
        "Complete a task",
        "End a pending task with a result, or with an error, which fails the task and "
        "its run. A task with a form takes a mapping of the form's fields; one without "
        "takes a text. A task is completed once.",
        DESTROY,
    )
    async def complete_task(
        task_id: TaskId,
        result: Annotated[
            Any, Field(description="The task's result, a JSON value.")
        ] = None,
        error: Annotated[
            str | None, Field(description="Fail the task with this text.")
        ] = None,
    ) -> CallToolResult:
        body: dict[str, Any] = {"result": result}
        if error is not None:
            body["error"] = error
        await call(
            api.request("POST", f"/tasks/{segment(task_id)}/complete", body=body)
        )
        return answer({"completed": task_id})

    # The panel. It keeps nothing: each answer is what its arguments name, so
    # the panels of other conversations and other people never move with it.

    async def panel(
        run_id: str | None = None, flow: str | None = None, version: str | None = None
    ) -> dict[str, Any]:
        """What the panel draws for a run, a flow, or neither, read from the manager."""
        if run_id is not None and flow is not None:
            raise ToolError("name a run or a flow, not both")
        if version is not None and flow is None:
            raise ToolError("a version is a flow's: name the flow too")
        try:
            if run_id is None and flow is None:
                flows = (await api.get("/flows"))["flows"]
                runs = (await api.get("/runs", {"limit": RECENT_RUNS}))["runs"]
                return {
                    "shows": "choices",
                    "flows": [each["name"] for each in flows],
                    "runs": runs,
                }
            if flow is not None:
                name = segment(flow)
                path = (
                    f"/flows/{name}"
                    if version is None
                    else f"/flows/{name}/versions/{segment(version)}"
                )
                shown = await api.get(path)
                versions = (await api.get(f"/flows/{name}/versions"))["versions"]
                return {
                    "shows": "flow",
                    "flow": shown,
                    "versions": [v["version"] for v in versions],
                }
            assert run_id is not None
            run_path = f"/runs/{segment(run_id)}"
            run = await api.get(run_path)
            of_run = await api.get(
                f"/flows/{segment(run['flow'])}/versions/{segment(run['version'])}"
            )
            tasks = (await api.get(f"{run_path}/tasks"))["tasks"]
            sub_runs = (await api.get(f"{run_path}/sub-runs"))["runs"]
            return {
                "shows": "run",
                "run": run,
                "flow": of_run,
                "tasks": tasks,
                "sub_runs": sub_runs,
            }
        except ManagerRefused as exc:
            if exc.status != 404:
                raise ToolError(str(exc)) from None
            return {"shows": "missing", "detail": exc.detail}
        except ManagerError as exc:
            raise ToolError(str(exc)) from None

    def shown(data: dict[str, Any]) -> CallToolResult:
        return CallToolResult(
            content=[TextContent(type="text", text=describe(data))],
            structuredContent=data,
        )

    @server.resource(VIEW_URI, name="panel", title="neorc", mime_type=VIEW_MIME_TYPE)
    def view() -> str:
        return view_html()

    @server.tool(
        name=PANEL_TOOL,
        title="neorc panel",
        description="What the panel draws: a run, a flow at its latest version or "
        "the one named, or, with no arguments, the flows and the latest runs to "
        "choose from.",
        annotations=READ,
        meta={"ui": {"resourceUri": VIEW_URI, "visibility": ["model", "app"]}},
    )
    async def neorc_panel(
        run_id: RunId | None = None,
        flow: FlowName | None = None,
        version: str | None = None,
    ) -> CallToolResult:
        return shown(await panel(run_id, flow, version))

    @server.tool(
        name="show_flow",
        title="Show a flow",
        description="Show the user a flow's steps as a graph: its latest version, or "
        "the one named. A host that shows MCP Apps draws the answer in its panel.",
        annotations=READ,
        meta=MODEL_AND_VIEW,
    )
    async def show_flow(name: FlowName, version: str | None = None) -> CallToolResult:
        return shown(await panel(flow=name, version=version))

    @server.tool(
        name="show_run",
        title="Show a run",
        description="Show the user a run on its flow's graph, with what failed. A host "
        "that shows MCP Apps draws the answer in its panel, live while the run runs.",
        annotations=READ,
        meta=MODEL_AND_VIEW,
    )
    async def show_run(run_id: RunId) -> CallToolResult:
        return shown(await panel(run_id=run_id))

    return server


def describe(data: dict[str, Any]) -> str:
    """The panel in words, for the model: what it shows, and what failed."""
    match data["shows"]:
        case "choices":
            flows = ", ".join(data["flows"]) or "none"
            runs = "".join(
                f"\n- run {run['id']} of {run['flow']}: {run['status']}"
                for run in data["runs"]
            )
            return f"Flows: {flows}.\nLatest runs:{runs or ' none'}"
        case "missing":
            return f"The panel shows nothing: {data['detail']}"
        case "flow":
            flow = data["flow"]
            steps = ", ".join((flow["content"].get("steps") or {}).keys())
            name, version = flow["name"], flow["version"]
            return f"The panel shows flow {name} {version}. Steps: {steps}."
        case _:
            run = data["run"]
            lines = [
                f"The panel shows run {run['id']} of {run['flow']} {run['version']}: "
                f"{run['status']}"
                + (f", {run['reason']}" if run.get("reason") else "")
                + "."
            ]
            for task in data["tasks"]:
                scope = "".join(f" {name} {n}" for name, n in task["address"]["scope"])
                lines.append(f"- {task['address']['step']}{scope}: {task['status']}")
                if task.get("error"):
                    lines.append("  error: " + task["error"].replace("\n", "\n  "))
            return "\n".join(lines)
