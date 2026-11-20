# neorc-mcp: the manager as an MCP server

`neorc-mcp` lets an agent use neorc. An agent reads flows and runs, finds
failed runs and reads why they failed, starts runs, completes the tasks that
wait for a person, and, with CI's token, uploads flows. In a host that shows
MCP Apps, such as Robinauts' side panel, the agent also draws a flow, or a
run on its flow, as neorc's UI draws it.

## Where it lives

- `python/neorc-mcp` is a distribution of its own, released from the same tag
  as the others. Nothing installs it but `pip install neorc-mcp`.
- It is a client of the manager's API over HTTP. It imports nothing of the
  `neorc` package, and the manager knows nothing of it.
- It runs as a process of its own. A host reaches it at its own address, where
  the manager's front listener may be closed to the host's workers, as in
  FA1's third isolation mode.

## Dependencies

- `mcp`, the MCP SDK, at the version Robinauts' lock pins: `mcp==1.30.0`.
  It brings Starlette, uvicorn, pydantic and httpx, which the workspace's one
  lock resolves beside oneberth's.
- `neorc-core`, which checks a flow before an upload sends it.
- `neorc-ui`, whose wheel carries the view.

## The protocol

- The server speaks MCP over streamable HTTP, at `/mcp`. It is stateless and
  answers in JSON.
- Every request carries `Authorization: Bearer <key>`. The key is
  `NEORC_MCP_KEY`, and the server refuses to start without one. A request
  without the key gets a 401. In Robinauts, the tool server's table names the
  variable that holds the key:

  ```toml
  [tool_servers.neorc]
  url = "http://neorc-mcp:8938/mcp"
  secret_env = "FA1_NEORC_MCP_KEY"
  ```

## Running it

| variable | what it holds |
|---|---|
| `NEORC_MCP_KEY` | the key a host sends; required |
| `NEORC_MANAGER_ADDRESS` | the backend serving the manager, as the `neorc` command takes it; required |
| `NEORC_API_TOKEN` | a person's own token, of the role `neorc/user` |
| `NEORC_CI_TOKEN` | a service token of the role `neorc/ci`; optional |
| `NEORC_ALLOW_INSECURE_HTTP` | `1` sends the tokens over plain `http` to another machine |
| `NEORC_MCP_HOST`, `NEORC_MCP_PORT` | where it listens; `127.0.0.1:8938` by default |

- The server sends a token over plain `http` only to a loopback address,
  unless `NEORC_ALLOW_INSECURE_HTTP=1`. A private network between containers
  is a place to allow it.
- The person's token acts as that person. A run the agent starts records the
  person as its starter, and the manager's roles bound what the agent may do.
  Service tokens have no `neorc/user` role, so the token is a person's own,
  made on the account page.
- What the agent sees of the manager is one person's view, for every
  conversation the server serves.

## The tools

Each API tool is one route of the manager, named after it. A host lets an
agent have one route and not another by its tool's name: Robinauts' `exclude`
lists the names an agent is not given. Each tool answers with the manager's
JSON twice, as text for the model and as structured content.

| tool | route | token | kind |
|---|---|---|---|
| `list_flows` | `GET /flows` | person | read |
| `get_flow` | `GET /flows/{name}` | person | read |
| `list_flow_versions` | `GET /flows/{name}/versions` | person | read |
| `get_flow_version` | `GET /flows/{name}/versions/{version}` | person | read |
| `upload_flows` | `POST /flows` | CI | destructive: cancels a flow's runs |
| `declare_queue` | `PUT /queues/{queue}` | CI | write |
| `start_run` | `POST /flows/{name}/runs` | person | write |
| `list_runs` | `GET /runs` | person | read |
| `get_run` | `GET /runs/{run_id}` | person | read |
| `list_run_tasks` | `GET /runs/{run_id}/tasks` | person | read |
| `list_sub_runs` | `GET /runs/{run_id}/sub-runs` | person | read |
| `get_run_state` | `GET /runs/{run_id}/state` | person | read |
| `cancel_run` | `POST /runs/{run_id}/cancel` | person | destructive |
| `list_queues` | `GET /queues` | person | read |
| `list_queue_tasks` | `GET /queues/{queue}/tasks/recent` | person | read |
| `get_task` | `GET /tasks/{task_id}` | person | read |
| `get_task_inputs` | `GET /tasks/{task_id}/inputs` | person | read |
| `complete_task` | `POST /tasks/{task_id}/complete` | person | destructive |

- A tool's annotations say what it does: `readOnlyHint` for a read,
  `destructiveHint` for a cancellation, a completion and an upload.
- `upload_flows` and `declare_queue` exist only with `NEORC_CI_TOKEN`.
- `upload_flows` takes each flow as the text of its file, YAML or JSON. A form
  is given inline, since the server reads no files. The server checks the set
  as `neorc flows upload` does, before it sends anything.
- A refusal reaches the model in the manager's words: the status, the error
  it names, its detail and the problems of an invalid flow set.

### Troubleshooting

neorc keeps no log of its own. A failed task's `error` holds the handler's
exit code, or its exception, and the end of its standard error, 16 KiB at most
([workers-and-manager.md](workers-and-manager.md)). That is the log an agent
reads:

1. `list_runs` with `status` `failed` finds the failed runs.
2. `get_run` gives a run's `reason`.
3. `list_run_tasks` gives each task's status, attempts and error.

A worker's own log stays on its host. Reaching it is the host's work.

### Not offered

- The scheduler's routes, which publish tasks and end runs, and the workers'
  routes, which receive, claim and finish tasks. An agent holding them would
  act as the scheduler or as a worker.
- The events, `GET /events` and `GET /events/latest`. They refresh the UI.
  The panel reads a run in progress again instead.
- A responder's pending list, `GET /queues/{queue}/tasks/pending`. A person's
  token lists the same tasks with `list_queue_tasks` and `status` `pending`.

## The panel

The panel is an MCP App (`io.modelcontextprotocol/ui`).

| tool | what it does | who calls it |
|---|---|---|
| `neorc_panel` | answers with what the panel shows; names the view | the host, with no arguments |
| `show_flow` | shows a flow, at its latest version or the one named | the model, and the view |
| `show_run` | shows a run | the model, and the view |

- `neorc_panel` carries `_meta.ui.resourceUri`, `ui://neorc/panel.html`. A
  host calls it with no arguments, so the server keeps what the panel shows.
  One server keeps one choice, for every conversation it serves.
- The three panel tools have `_meta.ui.visibility` `["model", "app"]`. Every
  other tool has `["model"]`, so a view cannot start, cancel or complete
  anything.
- Each answers with the panel's data, `shows` and what it shows:
  - `nothing`: the flows' names.
  - `flow`: the flow, and its versions.
  - `run`: the run, its flow at the run's version, its tasks and its
    sub-runs.
  - `missing`: the manager's detail, for a run or a flow it does not know.
- The text beside it describes the panel for the model: a run's status and
  reason, and each task's status with its error.

### The view

- The view is `ts/neorc-ui/src/mcp-view/`, built by `npm run build` with
  `vite.mcp-view.config.ts` into one HTML document, its script and style
  inline. The `neorc-ui` wheel carries it as `neorc_ui/mcp_view/`.
  `neorc_ui.mcp_view_html()` reads it.
- It draws with the UI's own `FlowGraph` and `RunGraph`. A failed run lists
  what failed, with each task's error, above the graph.
- It bundles no package that standalone neorc does not bundle. Its build
  fails on one outside `bundled-packages.txt`, and over 300 KB gzipped.
- It fetches nothing. It speaks JSON-RPC over `postMessage` with the window
  that frames it: `ui/initialize`, then `ui/notifications/initialized`. It
  reads `ui/notifications/tool-result` and
  `ui/notifications/host-context-changed`, and it asks `tools/call`.
- It takes the host's theme, light or dark, from the host's context.
- A link to a run or a flow calls `show_run` or `show_flow`, and the panel
  shows the answer.
- While a run is active, the view calls `neorc_panel` every 3 seconds.

## Tests

- `python/neorc-mcp/tests` start a manager composed by oneberth on a socket,
  with a scheduler and a worker over HTTP, and neorc-mcp from its variables.
  They speak to it as a host does, over streamable HTTP with its key, and with
  the SDK's own client.
- `ts/neorc-ui/src/mcp-view/App.test.tsx` tests the view's protocol, what it
  draws, its links and its refresh.
