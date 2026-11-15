// neorc's routes: what follows `#/neorc` in the hash. The shell owns the hash
// (oneberth's shell.md, "Routes"); neorc reads its own route from the shell
// and changes it through the shell's `navigate` and `href`.
import { createContext, useContext } from "react";

/** How a run's steps are shown: the outline, or the graph. */
export type RunView = "outline" | "graph";

export type Route =
  | { name: "runs"; flow?: string | undefined; status?: string | undefined }
  | { name: "flows" }
  | { name: "flow"; flow: string; version?: string | undefined }
  | { name: "run"; id: string; view?: RunView | undefined }
  | { name: "queues"; kind?: string | undefined }
  | {
      name: "queue";
      queue: string;
      /** A task status, or "all"; pending when left out. */
      status?: string | undefined;
      flow?: string | undefined;
      /** How far back: one of the queue page's windows; 24 hours when left out. */
      window?: string | undefined;
      /** The task opened beside the list. */
      task?: string | undefined;
    }
  | { name: "unknown"; route: string };

/** The route a shell route names: `""`, `/runs/7?view=graph` and so on. */
export function parseRoute(route: string): Route {
  const [path = "", query = ""] = route.split("?", 2);
  const params = new URLSearchParams(query);
  const parts = path.split("/").filter((part) => part !== "");
  // The hash is the reader's to type: a bad escape is not a route, not a crash.
  if (parts.some((part) => safeDecode(part) === undefined)) {
    return { name: "unknown", route };
  }
  if (parts.length === 0 || (parts.length === 1 && parts[0] === "runs")) {
    return {
      name: "runs",
      flow: params.get("flow") ?? undefined,
      status: params.get("status") ?? undefined,
    };
  }
  if (parts.length === 1 && parts[0] === "flows") return { name: "flows" };
  if (parts.length === 1 && parts[0] === "queues") {
    return { name: "queues", kind: params.get("kind") ?? undefined };
  }
  if (parts.length === 2 && parts[0] === "queues" && parts[1]) {
    return {
      name: "queue",
      queue: safeDecode(parts[1]) ?? parts[1],
      status: params.get("status") ?? undefined,
      flow: params.get("flow") ?? undefined,
      window: params.get("window") ?? undefined,
      task: params.get("task") ?? undefined,
    };
  }
  if (parts.length === 2 && parts[0] === "flows" && parts[1]) {
    return {
      name: "flow",
      flow: safeDecode(parts[1]) ?? parts[1],
      version: params.get("version") ?? undefined,
    };
  }
  if (parts.length === 2 && parts[0] === "runs" && parts[1]) {
    const view = params.get("view");
    return {
      name: "run",
      id: safeDecode(parts[1]) ?? parts[1],
      // Any other value is not a view: the page falls back to its own choice.
      view: view === "outline" || view === "graph" ? view : undefined,
    };
  }
  return { name: "unknown", route };
}

function safeDecode(part: string): string | undefined {
  try {
    return decodeURIComponent(part);
  } catch {
    return undefined;
  }
}

/** The shell route of `route`: `/runs/7?view=graph`, as `parseRoute` reads it. */
export function routeOf(route: Route): string {
  switch (route.name) {
    case "runs": {
      const params = new URLSearchParams();
      if (route.flow) params.set("flow", route.flow);
      if (route.status) params.set("status", route.status);
      const query = params.toString();
      return query ? `/runs?${query}` : "/runs";
    }
    case "flows":
      return "/flows";
    case "flow": {
      const base = `/flows/${encodeURIComponent(route.flow)}`;
      return route.version
        ? `${base}?version=${encodeURIComponent(route.version)}`
        : base;
    }
    case "run": {
      const base = `/runs/${encodeURIComponent(route.id)}`;
      return route.view ? `${base}?view=${route.view}` : base;
    }
    case "queues":
      return route.kind
        ? `/queues?kind=${encodeURIComponent(route.kind)}`
        : "/queues";
    case "queue": {
      const params = new URLSearchParams();
      if (route.status) params.set("status", route.status);
      if (route.flow) params.set("flow", route.flow);
      if (route.window) params.set("window", route.window);
      if (route.task) params.set("task", route.task);
      const query = params.toString();
      const base = `/queues/${encodeURIComponent(route.queue)}`;
      return query ? `${base}?${query}` : base;
    }
    case "unknown":
      return route.route;
  }
}

/** How neorc's pages link and move: through the shell. */
export interface Nav {
  /** The `href` of a route, for a plain link. */
  href: (route: Route) => string;
  /** Go to a route; `replace` takes the place of the page shown in the history. */
  navigate: (route: Route, options?: { replace?: boolean }) => void;
}

/** The shell's `href` and `navigate` of an application, as neorc's `Nav`. */
export function navOf(shell: {
  href: (route: string) => string;
  navigate: (route: string, options?: { replace?: boolean }) => void;
}): Nav {
  return {
    href: (route) => shell.href(routeOf(route)),
    navigate: (route, options) => shell.navigate(routeOf(route), options),
  };
}

/**
 * Outside the root component, as in a test of one page: the routes of an
 * application named `neorc`, on the window's hash.
 */
const STANDALONE: Nav = navOf({
  href: (route) => `#/neorc${route}`,
  navigate: (route, options) => {
    if (options?.replace) {
      history.replaceState(history.state, "", `#/neorc${route}`);
      dispatchEvent(new HashChangeEvent("hashchange"));
    } else {
      location.hash = `#/neorc${route}`;
    }
  },
});

export const NavContext = createContext<Nav>(STANDALONE);

export function useNav(): Nav {
  return useContext(NavContext);
}
