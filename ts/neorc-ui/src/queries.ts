// Data fetching on TanStack Query, and the event poll that keeps it fresh.
import {
  useInfiniteQuery,
  useQuery,
  useQueryClient,
  type InvalidateQueryFilters,
  type QueryClient,
} from "@tanstack/react-query";
import { useEffect } from "react";

import {
  ApiError,
  PAGE_SIZE,
  type Api,
  type ApiEvent,
  type QueueTaskFilters,
  type RunFilters,
} from "./api";
import { useApi, useVisible } from "./context";

export const keys = {
  flows: ["flows"] as const,
  flow: (name: string, version?: string) =>
    ["flows", name, version ?? "latest"] as const,
  versions: (name: string) => ["flows", name, "versions"] as const,
  runs: (filters: RunFilters) => ["runs", filters] as const,
  recentRuns: (count: number) => ["runs", "recent", count] as const,
  run: (id: string) => ["run", id] as const,
  tasks: (id: string) => ["run", id, "tasks"] as const,
  subRuns: (id: string) => ["run", id, "sub-runs"] as const,
  queues: ["queues"] as const,
  queueTasks: (queue: string, filters: QueueTaskFilters) =>
    ["queue-tasks", queue, filters] as const,
  taskInputs: (id: string) => ["task", id, "inputs"] as const,
};

/**
 * How long a flow query is trusted. No event says a flow was uploaded, so
 * the flow pages ask again after a while, and whenever a run starts.
 */
export const FLOWS_STALE_MS = 30_000;

export function useFlows() {
  const api = useApi();
  return useQuery({
    queryKey: keys.flows,
    queryFn: api.listFlows,
    staleTime: FLOWS_STALE_MS,
  });
}

export function useFlow(name: string, version?: string) {
  const api = useApi();
  return useQuery({
    queryKey: keys.flow(name, version),
    queryFn: () => api.getFlow(name, version),
    // A stored version never changes; the latest may be replaced.
    staleTime: version ? Infinity : FLOWS_STALE_MS,
  });
}

export function useFlowVersions(name: string) {
  const api = useApi();
  return useQuery({
    queryKey: keys.versions(name),
    queryFn: () => api.flowVersions(name),
    staleTime: FLOWS_STALE_MS,
  });
}

export function useRuns(filters: RunFilters) {
  const api = useApi();
  return useInfiniteQuery({
    queryKey: keys.runs(filters),
    queryFn: ({ pageParam }) => api.listRuns(filters, pageParam),
    initialPageParam: undefined as string | undefined,
    // The cursor is the last run of the previous page; a short page is the end.
    getNextPageParam: (page) =>
      page.length < PAGE_SIZE ? undefined : page[page.length - 1]?.id,
  });
}

/**
 * The latest few runs, for the sidebar: a query of its own, small and under
 * the "runs" prefix the events refresh, rather than the list page's pages,
 * which the sidebar would otherwise keep alive and refetched on every event.
 */
export function useRecentRuns(count: number) {
  const api = useApi();
  return useQuery({
    queryKey: keys.recentRuns(count),
    queryFn: () => api.listRuns({}, undefined, count),
  });
}

export function useRun(id: string) {
  const api = useApi();
  return useQuery({ queryKey: keys.run(id), queryFn: () => api.getRun(id) });
}

/** How often an active run's tasks and sub-runs are re-read. */
export const ACTIVE_POLL_MS = 2000;

export interface LiveOptions {
  /** Whether the run is still active: what changes without an event. */
  active: boolean;
  everyMs?: number;
}

/**
 * The event log says when a task or run finished, and nothing else: a task
 * being published, received or claimed, or a sub-run starting, records no
 * event. So while a run is active its page asks again on its own, while
 * neorc is shown.
 */
function whileActive({ active, everyMs = ACTIVE_POLL_MS }: LiveOptions, visible: boolean) {
  return active && visible ? everyMs : false;
}

export function useRunTasks(id: string, live: LiveOptions) {
  const api = useApi();
  const visible = useVisible();
  return useQuery({
    queryKey: keys.tasks(id),
    queryFn: () => api.runTasks(id),
    refetchInterval: whileActive(live, visible),
  });
}

export function useSubRuns(id: string, live: LiveOptions) {
  const api = useApi();
  const visible = useVisible();
  return useQuery({
    queryKey: keys.subRuns(id),
    queryFn: () => api.subRuns(id),
    refetchInterval: whileActive(live, visible),
  });
}

/**
 * How long the list of queues is trusted. A queue is declared on the
 * database, with no event, and rarely.
 */
export const QUEUES_STALE_MS = 30_000;

export function useQueues() {
  const api = useApi();
  return useQuery({
    queryKey: keys.queues,
    queryFn: api.listQueues,
    staleTime: QUEUES_STALE_MS,
  });
}

/** How often a queue's page asks for its tasks again. */
export const QUEUE_POLL_MS = 5000;

/**
 * A queue's tasks. No event says a task was published or received, so the
 * page asks again on its own while neorc is shown; an event refreshes it at
 * once.
 */
export function useQueueTasks(queue: string, filters: QueueTaskFilters) {
  const api = useApi();
  const visible = useVisible();
  return useQuery({
    queryKey: keys.queueTasks(queue, filters),
    queryFn: () => api.queueTasks(queue, filters),
    refetchInterval: visible ? QUEUE_POLL_MS : false,
  });
}

export function useTaskInputs(id: string) {
  const api = useApi();
  return useQuery({
    queryKey: keys.taskInputs(id),
    queryFn: () => api.taskInputs(id),
  });
}

/** How long one poll waits on the manager, under its own deadline. */
export const POLL_SECONDS = 20;

/** How long to wait before polling again after a failure. */
export const RETRY_MS = 2000;

/**
 * Follow the manager's event log while neorc is shown and the tab is not
 * hidden.
 *
 * The poll starts after the log's latest sequence, asked for first, and then
 * refetches everything once: what a page loaded before that answer came may
 * already be behind. From then on each event invalidates the run it concerns
 * and every runs listing, and a run starting invalidates the flows, since a
 * flow may have been uploaded with no event of its own. No event kind is
 * needed beyond that: an event only says which run to look at again.
 */
/** How long a poll refused for its session waits before it tries again. */
export const REFUSED_RETRY_MS = 30_000;

export function useLiveEvents(): void {
  const client = useQueryClient();
  const api = useApi();
  const visible = useVisible();
  useEffect(() => {
    // A hidden tab, or neorc hidden by the shell, polls nothing: a browser
    // allows only a few connections to a host, shared by every tab, and a
    // long poll holds one. Shown again, the poll starts over, which
    // refetches everything it may have missed.
    if (!visible) return;
    let controller: AbortController | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const start = () => {
      clearTimeout(retry);
      if (controller === null && !document.hidden) {
        const mine = new AbortController();
        controller = mine;
        // A poll ends by itself only when its session was refused. If the
        // page is still here, the session check found someone signed in
        // after all: try again, slowly, so a refusal that persists costs a
        // request a while, not a loop.
        void follow(api, client, mine.signal).then(() => {
          if (controller !== mine) return;
          controller = null;
          if (!mine.signal.aborted) retry = setTimeout(start, REFUSED_RETRY_MS);
        });
      }
    };
    const stop = () => {
      clearTimeout(retry);
      controller?.abort();
      controller = null;
    };
    const onVisibility = () => (document.hidden ? stop() : start());
    document.addEventListener("visibilitychange", onVisibility);
    start();
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      stop();
    };
  }, [api, client, visible]);
}

export async function follow(
  api: Api,
  client: QueryClient,
  signal: AbortSignal,
  sleep: (ms: number) => Promise<void> = pause,
): Promise<void> {
  let after: number | undefined;
  while (!signal.aborted) {
    try {
      if (after === undefined) {
        after = await api.latestSequence(signal);
        void refresh(client, {});
        continue;
      }
      const events = await api.waitForEvents(after, {
        timeout: POLL_SECONDS,
        signal,
      });
      if (events.length > 0) {
        after = events[events.length - 1]?.sequence ?? after;
        invalidate(client, events);
      }
    } catch (error) {
      if (signal.aborted) return;
      // The session has ended: asking again would be refused again, and the
      // page is about to show the sign-in instead.
      if (error instanceof ApiError && error.status === 401) return;
      console.warn("event poll failed, retrying", error);
      // Start over from the log's end: a manager restarted on a fresh log
      // counts from 1 again, and a position past its end would read nothing
      // for ever. Everything is refetched then, which also clears queries
      // that failed during the outage.
      after = undefined;
      await sleep(RETRY_MS);
    }
  }
}

/**
 * A refetch already under way is left to finish rather than cancelled: the
 * poll returns as soon as an event exists, and cancelling the last refetch
 * on every batch would keep a busy manager's pages from ever settling, and
 * drop a page of older runs a reader asked for.
 */
const KEEP_GOING = { cancelRefetch: false } as const;

/**
 * Refetch what matches, and once more if a fetch was already in flight: that
 * one may have read the store before the event was recorded, and its answer
 * would otherwise clear the invalidation with stale data.
 */
export async function refresh(
  client: QueryClient,
  filters: InvalidateQueryFilters,
): Promise<void> {
  const inFlight = client.isFetching(filters) > 0;
  await client.invalidateQueries(filters, KEEP_GOING);
  if (inFlight) await client.invalidateQueries(filters, KEEP_GOING);
}

function invalidate(client: QueryClient, events: ApiEvent[]): void {
  void refresh(client, { queryKey: ["runs"] });
  // A task finished, or a run ended with tasks still waiting.
  void refresh(client, { queryKey: ["queue-tasks"] });
  void refresh(client, { queryKey: ["task"] });
  for (const id of new Set(events.map((event) => event.run_id))) {
    void refresh(client, { queryKey: keys.run(id) });
  }
  if (events.some((event) => event.kind !== "task_finished")) {
    // A sub-run's events carry its own id; its parent's page lists it.
    void refresh(client, { predicate: isSubRunsQuery });
  }
  if (events.some((event) => event.kind === "run_started")) {
    void refresh(client, { queryKey: keys.flows });
  }
}

export function isSubRunsQuery(query: { queryKey: readonly unknown[] }): boolean {
  return query.queryKey[0] === "run" && query.queryKey[2] === "sub-runs";
}

function pause(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
