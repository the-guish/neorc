// The view's page: the panel's data from neorc-mcp, the links that show
// another run or flow, and a run in progress read again until it ends.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { NavContext, parseRoute, type Nav } from "../router";
import { connect, PANEL_TOOL, type Host, type Theme, type ToolAnswer } from "./bridge";
import { Panel, panelData, type PanelData } from "./Panel";

/** How often a run in progress is read again, in milliseconds. */
const REFRESH_MS = 3000;

/** Where the view's links point: the UI's own routes, never followed. */
const NAV: Nav = {
  href: (route) => {
    switch (route.name) {
      case "run":
        return `#/neorc/runs/${encodeURIComponent(route.id)}`;
      case "flow":
        return `#/neorc/flows/${encodeURIComponent(route.flow)}`;
      default:
        return "#";
    }
  },
  navigate: () => {},
};

type Shown =
  | { state: "waiting" }
  | { state: "shown"; data: PanelData }
  | { state: "failed"; message: string };

export function App({ connectTo = connect }: { connectTo?: typeof connect }) {
  const [shown, setShown] = useState<Shown>({ state: "waiting" });
  const host = useRef<Host | null>(null);

  const take = useCallback((answer: ToolAnswer) => {
    const data = panelData(answer.structured);
    if (data) setShown({ state: "shown", data });
    else setShown({ state: "failed", message: answer.text || "neorc-mcp answered nothing to show." });
  }, []);

  useEffect(() => {
    host.current = connectTo(window.parent, {
      onPanel: take,
      onTheme: (theme: Theme) => document.documentElement.setAttribute("data-theme", theme),
      onFailure: (message) => setShown({ state: "failed", message }),
    });
  }, [take, connectTo]);

  const call = useCallback(
    (name: string, args?: Record<string, unknown>) => {
      host.current?.callTool(name, args).then(take, (error: Error) =>
        setShown({ state: "failed", message: error.message }),
      );
    },
    [take],
  );

  // A link to a run or a flow shows it here, through neorc-mcp: the view
  // cannot leave its frame, and the panel follows what the agent shows next.
  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      const link = (event.target as Element | null)?.closest?.("a[href]");
      const target = link?.getAttribute("href");
      if (!target?.startsWith("#")) return;
      event.preventDefault();
      if (!target.startsWith("#/neorc/")) return;
      const route = parseRoute(target.slice("#/neorc".length));
      if (route.name === "run") call("show_run", { run_id: route.id });
      else if (route.name === "flow") call("show_flow", { name: route.flow });
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, [call]);

  // A run in progress is read again until it ends.
  const active = shown.state === "shown" && shown.data.shows === "run" && shown.data.run.status === "active";
  const runId = shown.state === "shown" && shown.data.shows === "run" ? shown.data.run.id : null;
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => call(PANEL_TOOL), REFRESH_MS);
    return () => clearInterval(timer);
  }, [active, runId, call]);

  const body = useMemo(() => {
    switch (shown.state) {
      case "waiting":
        return <p className="neorc-muted">Waiting for neorc…</p>;
      case "failed":
        return <p className="neorc-mcp-error">{shown.message}</p>;
      case "shown":
        return <Panel data={shown.data} />;
    }
  }, [shown]);

  return (
    <NavContext value={NAV}>
      <div className="neorc-app neorc-mcp-view">
        <main>{body}</main>
      </div>
    </NavContext>
  );
}

