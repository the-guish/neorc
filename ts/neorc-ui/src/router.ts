// Hash routing: the page's path never changes, so the manager serves plain
// files under one prefix and needs no fallback route.
import { useSyncExternalStore } from "react";

/** How a run's steps are shown: the outline, or the graph. */
export type RunView = "outline" | "graph";

export type Route =
  | { name: "runs"; flow?: string; status?: string }
  | { name: "flows" }
  | { name: "flow"; flow: string; version?: string }
  | { name: "run"; id: string; view?: RunView }
  | { name: "queues"; kind?: string }
  | {
      name: "queue";
      queue: string;
      /** A task status, or "all"; pending when left out. */
      status?: string;
      flow?: string;
      /** How far back: one of the queue page's windows; 24 hours when left out. */
      window?: string;
      /** The task opened beside the list. */
      task?: string;
    }
  | { name: "sign-in"; error?: string }
  | { name: "unknown"; hash: string };

export function parseHash(hash: string): Route {
  const [path = "", query = ""] = hash.replace(/^#/, "").split("?", 2);
  const params = new URLSearchParams(query);
  const parts = path.split("/").filter((part) => part !== "");
  // The hash is the reader's to type: a bad escape is not a route, not a crash.
  if (parts.some((part) => safeDecode(part) === undefined)) {
    return { name: "unknown", hash };
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
  if (parts.length === 1 && parts[0] === "sign-in") {
    return { name: "sign-in", error: params.get("error") ?? undefined };
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
  return { name: "unknown", hash };
}

function safeDecode(part: string): string | undefined {
  try {
    return decodeURIComponent(part);
  } catch {
    return undefined;
  }
}

export function href(route: Route): string {
  switch (route.name) {
    case "runs": {
      const params = new URLSearchParams();
      if (route.flow) params.set("flow", route.flow);
      if (route.status) params.set("status", route.status);
      const query = params.toString();
      return query ? `#/runs?${query}` : "#/runs";
    }
    case "flows":
      return "#/flows";
    case "flow": {
      const base = `#/flows/${encodeURIComponent(route.flow)}`;
      return route.version
        ? `${base}?version=${encodeURIComponent(route.version)}`
        : base;
    }
    case "run": {
      const base = `#/runs/${encodeURIComponent(route.id)}`;
      return route.view ? `${base}?view=${route.view}` : base;
    }
    case "queues":
      return route.kind
        ? `#/queues?kind=${encodeURIComponent(route.kind)}`
        : "#/queues";
    case "queue": {
      const params = new URLSearchParams();
      if (route.status) params.set("status", route.status);
      if (route.flow) params.set("flow", route.flow);
      if (route.window) params.set("window", route.window);
      if (route.task) params.set("task", route.task);
      const query = params.toString();
      const base = `#/queues/${encodeURIComponent(route.queue)}`;
      return query ? `${base}?${query}` : base;
    }
    case "sign-in":
      return route.error
        ? `#/sign-in?error=${encodeURIComponent(route.error)}`
        : "#/sign-in";
    case "unknown":
      return route.hash;
  }
}

export function navigate(route: Route): void {
  location.hash = href(route);
}

/** Go to `route` in place of the page shown, so Back skips what it replaced. */
export function replaceRoute(route: Route): void {
  history.replaceState(history.state, "", href(route));
  dispatchEvent(new HashChangeEvent("hashchange"));
}

function subscribe(onChange: () => void): () => void {
  addEventListener("hashchange", onChange);
  return () => removeEventListener("hashchange", onChange);
}

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, () => location.hash);
  return parseHash(hash);
}
