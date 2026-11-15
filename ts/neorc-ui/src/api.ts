// The manager's API, typed from openapi.json (see `npm run generate`).
import { getJson, postJson } from "@oneberth/ui";

import type { components } from "./api/schema";

type Schemas = components["schemas"];

export type Flow = Schemas["FlowResponse"];
export type Run = Schemas["RunResponse"];
export type RunStatus = Schemas["RunStatus"];
export type Task = Schemas["TaskResponse"];
export type TaskStatus = Schemas["TaskStatus"];
export type Address = Schemas["AddressResponse"];
export type ApiEvent = Schemas["EventResponse"];
export type ErrorBody = Schemas["ErrorResponse"];
export type Queue = Schemas["QueueResponse"];
export type QueueKind = Schemas["QueueKind"];
export type QueueTask = Schemas["QueueTaskResponse"];
export type Delivery = Schemas["DeliveryResponse"];

export const RUN_STATUSES: readonly RunStatus[] = [
  "active",
  "succeeded",
  "failed",
  "cancelled",
];

export const TASK_STATUSES: readonly TaskStatus[] = [
  "pending",
  "received",
  "running",
  "succeeded",
  "failed",
];

export const QUEUE_KINDS: readonly QueueKind[] = ["worker", "external"];

/** A refusal of the manager: `error` names its exception, `detail` says why. */
export { ApiError } from "@oneberth/ui";

type Query = Record<string, string | number | boolean | undefined>;

/**
 * The manager's API at `base`, such as `/neorc/`: the address a host gives
 * neorc's root component. Requests go through `@oneberth/ui`'s request
 * helper, on this origin: a 401 signs the person out of the shell.
 */
export function createApi(base: string) {
  const root = base.replace(/\/+$/, "");
  if (!root.startsWith("/") || root.startsWith("//")) {
    throw new TypeError(`neorc's API is a path on this origin, such as /neorc/: ${base}`);
  }
  const get = <T>(path: string, query?: Query, signal?: AbortSignal) =>
    getJson<T>(`${root}${path}`, query, signal);
  const post = <T>(path: string, body: unknown) => postJson<T>(`${root}${path}`, body);

  return {
    // Flows.

    async listFlows(): Promise<Flow[]> {
      return (await get<{ flows: Flow[] }>("/flows")).flows;
    },

    getFlow(name: string, version?: string): Promise<Flow> {
      const path = version
        ? `/flows/${encodeURIComponent(name)}/versions/${encodeURIComponent(version)}`
        : `/flows/${encodeURIComponent(name)}`;
      return get<Flow>(path);
    },

    async flowVersions(name: string): Promise<Flow[]> {
      const path = `/flows/${encodeURIComponent(name)}/versions`;
      return (await get<{ versions: Flow[] }>(path)).versions;
    },

    // Runs.

    async listRuns(
      filters: RunFilters,
      before?: string,
      limit: number = PAGE_SIZE,
    ): Promise<Run[]> {
      const params = {
        flow: filters.flow,
        status: filters.status,
        root_only: filters.rootOnly ?? true,
        before,
        limit,
      };
      return (await get<{ runs: Run[] }>("/runs", params)).runs;
    },

    getRun(id: string): Promise<Run> {
      return get<Run>(`/runs/${encodeURIComponent(id)}`);
    },

    async runTasks(id: string): Promise<Task[]> {
      const path = `/runs/${encodeURIComponent(id)}/tasks`;
      return (await get<{ tasks: Task[] }>(path)).tasks;
    },

    async subRuns(id: string): Promise<Run[]> {
      const path = `/runs/${encodeURIComponent(id)}/sub-runs`;
      return (await get<{ runs: Run[] }>(path)).runs;
    },

    startRun(flow: string, inputs: Record<string, unknown>): Promise<Run> {
      return post<Run>(`/flows/${encodeURIComponent(flow)}/runs`, { inputs });
    },

    cancelRun(id: string): Promise<void> {
      return post<void>(`/runs/${encodeURIComponent(id)}/cancel`, {});
    },

    // Queues: the declared queues, a queue's tasks, and completing one.

    async listQueues(): Promise<Queue[]> {
      return (await get<{ queues: Queue[] }>("/queues")).queues;
    },

    async queueTasks(queue: string, filters: QueueTaskFilters): Promise<QueueTask[]> {
      const path = `/queues/${encodeURIComponent(queue)}/tasks/recent`;
      const params = {
        since: new Date(Date.now() - filters.windowMs).toISOString(),
        status: filters.status,
        flow: filters.flow,
        limit: QUEUE_PAGE,
      };
      return (await get<{ tasks: QueueTask[] }>(path, params)).tasks;
    },

    /** A task with its inputs filled in, as a worker would receive it. */
    taskInputs(id: string): Promise<Delivery> {
      return get<Delivery>(`/tasks/${encodeURIComponent(id)}/inputs`);
    },

    /** End a pending task: with `result`, or with its failure when `error` is set. */
    completeTask(id: string, body: { result: unknown } | { error: string }): Promise<void> {
      return post<void>(`/tasks/${encodeURIComponent(id)}/complete`, body);
    },

    // Events: the same long poll the scheduler uses, read, never consumed.

    /** The latest event's sequence, or 0: where a reader wanting only news starts. */
    async latestSequence(signal?: AbortSignal): Promise<number> {
      const latest = await get<{ sequence: number }>("/events/latest", {}, signal);
      return latest.sequence;
    },

    async waitForEvents(
      after: number,
      options: { timeout: number; limit?: number; signal?: AbortSignal },
    ): Promise<ApiEvent[]> {
      const params = {
        after,
        timeout: options.timeout,
        limit: options.limit ?? EVENT_PAGE,
      };
      const page = await get<{ events: ApiEvent[] }>("/events", params, options.signal);
      return page.events;
    },
  };
}

/** The manager's API, as `createApi` makes it. */
export type Api = ReturnType<typeof createApi>;

export interface RunFilters {
  flow?: string | undefined;
  status?: RunStatus | undefined;
  rootOnly?: boolean;
}

export const PAGE_SIZE = 50;

export interface QueueTaskFilters {
  status?: TaskStatus | undefined;
  flow?: string | undefined;
  /** How far back to list, in milliseconds before now. */
  windowMs: number;
}

/** The most tasks one listing of a queue asks for: the manager's own limit. */
export const QUEUE_PAGE = 500;

export const EVENT_PAGE = 1000;
