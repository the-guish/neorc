# neorc-mcp

neorc's manager as an MCP server, over streamable HTTP at `/mcp`. An agent
reads flows and runs, troubleshoots failed runs from their tasks' errors,
starts runs, completes the tasks that wait for a person, and, with CI's
token, uploads flows. Each tool is one route of the manager's API, so a host
can let an agent have one route and not another.

It is an MCP App too: in a host that shows MCP Apps, such as Robinauts' side
panel, the agent draws a flow, or a run on its flow, as neorc's UI draws it.

```sh
pip install neorc-mcp

export NEORC_MCP_KEY=...                       # what the host sends as a bearer
export NEORC_MANAGER_ADDRESS=https://neorc.example.com
export NEORC_API_TOKEN=...                     # a person's own token
export NEORC_CI_TOKEN=...                      # optional: upload_flows, declare_queue
neorc-mcp --host 127.0.0.1 --port 8938
```

The tools, the tokens and the view are in
[docs/specs/mcp.md](https://github.com/open-shipyard/neorc/blob/main/docs/specs/mcp.md).
