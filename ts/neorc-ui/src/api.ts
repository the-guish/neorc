// The manager's API, typed from openapi.json (see `npm run generate`).
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
export type Session = Schemas["SessionResponse"];
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

/** A refusal: `error` names the manager's exception, `detail` says why. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly body: ErrorBody,
  ) {
    super(body.detail);
    this.name = body.error;
  }
}

/**
 * The API lives one level above the page: the manager serves the UI at /ui/
 * and its routes at /, and `npm run dev` serves the page at / and proxies
 * the routes. Hash routing keeps the page's path fixed either way.
 */
export function apiUrl(
  path: string,
  params: Record<string, string | number | boolean | undefined> = {},
): URL {
  const url = new URL(path.replace(/^\//, ""), new URL("../", location.href));
  for (const [name, value] of Object.entries(params)) {
    if (value !== undefined) url.searchParams.set(name, String(value));
  }
  return url;
}

const unauthorizedListeners = new Set<() => void>();

/**
 * Call `listener` whenever the manager answers 401: the session this page
 * signed in with has ended. Returns the function that stops it.
 */
export function onUnauthorized(listener: () => void): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

async function request<T>(url: URL, init: RequestInit = {}): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { accept: "application/json", ...init.headers },
  });
  // The session's own route is what the listeners ask again: its refusal
  // telling them would only ask again, and again.
  if (response.status === 401 && !url.pathname.endsWith("/auth/session")) {
    for (const listener of unauthorizedListeners) listener();
  }
  if (!response.ok) {
    let body: ErrorBody;
    try {
      body = (await response.json()) as ErrorBody;
    } catch {
      body = { error: "HTTPError", detail: `${response.status} from ${url}` };
    }
    throw new ApiError(response.status, body);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function getJson<T>(
  path: string,
  params?: Record<string, string | number | boolean | undefined>,
  signal?: AbortSignal,
): Promise<T> {
  return request<T>(apiUrl(path, params), { signal });
}

export function postJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(apiUrl(path), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

// Flows.

export async function listFlows(): Promise<Flow[]> {
  return (await getJson<{ flows: Flow[] }>("/flows")).flows;
}

export function getFlow(name: string, version?: string): Promise<Flow> {
  const path = version
    ? `/flows/${encodeURIComponent(name)}/versions/${encodeURIComponent(version)}`
    : `/flows/${encodeURIComponent(name)}`;
  return getJson<Flow>(path);
}

export async function flowVersions(name: string): Promise<Flow[]> {
  const path = `/flows/${encodeURIComponent(name)}/versions`;
  return (await getJson<{ versions: Flow[] }>(path)).versions;
}

// Runs.

export interface RunFilters {
  flow?: string;
  status?: RunStatus;
  rootOnly?: boolean;
}

export const PAGE_SIZE = 50;

export async function listRuns(
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
  return (await getJson<{ runs: Run[] }>("/runs", params)).runs;
}

export function getRun(id: string): Promise<Run> {
  return getJson<Run>(`/runs/${encodeURIComponent(id)}`);
}

export async function runTasks(id: string): Promise<Task[]> {
  const path = `/runs/${encodeURIComponent(id)}/tasks`;
  return (await getJson<{ tasks: Task[] }>(path)).tasks;
}

export async function subRuns(id: string): Promise<Run[]> {
  const path = `/runs/${encodeURIComponent(id)}/sub-runs`;
  return (await getJson<{ runs: Run[] }>(path)).runs;
}

export function startRun(
  flow: string,
  inputs: Record<string, unknown>,
): Promise<Run> {
  return postJson<Run>(`/flows/${encodeURIComponent(flow)}/runs`, { inputs });
}

export function cancelRun(id: string): Promise<void> {
  return postJson<void>(`/runs/${encodeURIComponent(id)}/cancel`, {});
}

// Queues: the declared queues, a queue's tasks, and completing one.

export async function listQueues(): Promise<Queue[]> {
  return (await getJson<{ queues: Queue[] }>("/queues")).queues;
}

export interface QueueTaskFilters {
  status?: TaskStatus;
  flow?: string;
  /** How far back to list, in milliseconds before now. */
  windowMs: number;
}

/** The most tasks one listing of a queue asks for: the manager's own limit. */
export const QUEUE_PAGE = 500;

export async function queueTasks(
  queue: string,
  filters: QueueTaskFilters,
): Promise<QueueTask[]> {
  const path = `/queues/${encodeURIComponent(queue)}/tasks/recent`;
  const params = {
    since: new Date(Date.now() - filters.windowMs).toISOString(),
    status: filters.status,
    flow: filters.flow,
    limit: QUEUE_PAGE,
  };
  return (await getJson<{ tasks: QueueTask[] }>(path, params)).tasks;
}

/** A task with its inputs filled in, as a worker would receive it. */
export function taskInputs(id: string): Promise<Delivery> {
  return getJson<Delivery>(`/tasks/${encodeURIComponent(id)}/inputs`);
}

/** End a pending task: with `result`, or with its failure when `error` is set. */
export function completeTask(
  id: string,
  body: { result: unknown } | { error: string },
): Promise<void> {
  return postJson<void>(`/tasks/${encodeURIComponent(id)}/complete`, body);
}

// Events: the same long poll the scheduler uses, read, never consumed.

export const EVENT_PAGE = 1000;

/** The latest event's sequence, or 0: where a reader wanting only news starts. */
export async function latestSequence(signal?: AbortSignal): Promise<number> {
  const latest = await getJson<{ sequence: number }>(
    "/events/latest",
    {},
    signal,
  );
  return latest.sequence;
}

export async function waitForEvents(
  after: number,
  options: { timeout: number; limit?: number; signal?: AbortSignal },
): Promise<ApiEvent[]> {
  const params = {
    after,
    timeout: options.timeout,
    limit: options.limit ?? EVENT_PAGE,
  };
  const page = await getJson<{ events: ApiEvent[] }>(
    "/events",
    params,
    options.signal,
  );
  return page.events;
}
