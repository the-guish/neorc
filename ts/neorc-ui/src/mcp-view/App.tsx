// The view's page. neorc-mcp keeps nothing, so the view keeps its own choice:
// the run or the flow it draws. It reads that choice again while a run is in
// progress, and a link changes it. docs/specs/mcp.md, "The view".
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

/** The arguments of `neorc_panel` that draw `data` again: the view's own choice. */
export function choiceOf(data: PanelData): Record<string, string> | undefined {
  switch (data.shows) {
    case "run":
      return { run_id: data.run.id };
    case "flow":
      return { flow: data.flow.name, version: data.flow.version };
    default:
      return undefined;
  }
}

export function App({ connectTo = connect }: { connectTo?: typeof connect }) {
  const [shown, setShown] = useState<Shown>({ state: "waiting" });
  const host = useRef<Host | null>(null);
  // Read by the host's notifications, which outlive the render that made them.
  const choice = useRef<Record<string, string> | undefined>(undefined);

  const draw = useCallback((answer: ToolAnswer) => {
    const data = panelData(answer.structured);
    if (!data) {
      setShown({ state: "failed", message: answer.text || "neorc-mcp answered nothing to show." });
      return;
    }
    choice.current = choiceOf(data);
    setShown({ state: "shown", data });
  }, []);

  const call = useCallback(
    (args?: Record<string, unknown>) => {
      host.current?.callTool(PANEL_TOOL, args).then(draw, (error: Error) =>
        setShown({ state: "failed", message: error.message }),
      );
    },
    [draw],
  );

  // What the host hands over: the panel's own tool, which a host may call with
  // no arguments after each turn, or the answer of show_run or show_flow. The
  // choices, with no arguments, do not replace a run or a flow already drawn:
  // the view reads its own choice again instead.
  const fromHost = useCallback(
    (answer: ToolAnswer) => {
      const data = panelData(answer.structured);
      if (data?.shows === "choices" && choice.current) call(choice.current);
      else draw(answer);
    },
    [call, draw],
  );

  useEffect(() => {
    host.current = connectTo(window.parent, {
      onPanel: fromHost,
      onTheme: (theme: Theme) => document.documentElement.setAttribute("data-theme", theme),
      onFailure: (message) => setShown({ state: "failed", message }),
    });
  }, [fromHost, connectTo]);

  // A link to a run or a flow draws it here: the view cannot leave its frame.
  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      const link = (event.target as Element | null)?.closest?.("a[href]");
      const target = link?.getAttribute("href");
      if (!target?.startsWith("#")) return;
      event.preventDefault();
      if (!target.startsWith("#/neorc/")) return;
      const route = parseRoute(target.slice("#/neorc".length));
      if (route.name === "run") call({ run_id: route.id });
      else if (route.name === "flow") call({ flow: route.flow });
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, [call]);

  // A run in progress is read again until it ends.
  const runId =
    shown.state === "shown" && shown.data.shows === "run" && shown.data.run.status === "active"
      ? shown.data.run.id
      : null;
  useEffect(() => {
    if (runId === null) return;
    const timer = setInterval(() => call({ run_id: runId }), REFRESH_MS);
    return () => clearInterval(timer);
  }, [runId, call]);

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
