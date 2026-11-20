// neorc's MCP App: the view neorc-mcp serves as `ui://neorc/panel.html`, built
// into one HTML file with its script and style inline. A host such as
// Robinauts frames it sandboxed, with nothing to fetch: everything it shows
// comes from neorc-mcp's tools, through the host. docs/specs/mcp.md.
import "@oneberth/ui/tokens.css";
import "../neorc.css";
import "./mcp-view.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";

const root = document.getElementById("root");
if (root === null) throw new Error("mcp-view.html has no #root");
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
