import { QUEUE_PAGE, TASK_STATUSES, type QueueTask, type TaskStatus } from "../api";
import { Failed, Loading } from "../components/Layout";
import { StatusBadge } from "../components/StatusBadge";
import { TaskPanel } from "../components/TaskPanel";
import { formatTime, shortId } from "../format";
import { useFlows, useQueues, useQueueTasks } from "../queries";
import { href, navigate, type Route } from "../router";
import { KIND_NOTES } from "./QueuesPage";

type QueueRoute = Extract<Route, { name: "queue" }>;

const HOUR_MS = 60 * 60 * 1000;

/** How far back the page lists: the first is what it starts with. */
export const WINDOWS = [
  { value: "24h", label: "24 hours", ms: 24 * HOUR_MS },
  { value: "7d", label: "7 days", ms: 7 * 24 * HOUR_MS },
  { value: "30d", label: "30 days", ms: 30 * 24 * HOUR_MS },
  { value: "1y", label: "1 year", ms: 365 * 24 * HOUR_MS },
] as const;

type Window = (typeof WINDOWS)[number];

function asWindow(value: string | undefined): Window {
  return WINDOWS.find((each) => each.value === value) ?? WINDOWS[0];
}

/** The status the page keeps: pending unless the address says another, or all. */
function asStatus(value: string | undefined): TaskStatus | undefined {
  if (value === "all") return undefined;
  return TASK_STATUSES.find((status) => status === value) ?? "pending";
}

/** When a task last changed: it was published, started or finished. */
export function lastChange(task: QueueTask["task"]): string | null {
  return task.finished_at ?? task.started_at ?? task.created_at;
}

/**
 * A queue's tasks that changed lately, in any status, filtered by status and
 * flow, with the task the address names opened beside them: its inputs, and
 * for a pending task of an external queue the form that completes it.
 */
export function QueuePage(route: QueueRoute) {
  const { queue, flow, task: opened } = route;
  const status = asStatus(route.status);
  const window = asWindow(route.window);
  const queues = useQueues();
  const flows = useFlows();
  const tasks = useQueueTasks(queue, { status, flow, windowMs: window.ms });

  const declared = queues.data?.find((each) => each.name === queue);
  const listed = tasks.data ?? [];
  const go = (change: Partial<QueueRoute>) => navigate({ ...route, ...change });
  return (
    <>
      <div className="page-head">
        <div>
          <h1>
            {queue}{" "}
            {declared && (
              <span className={`kind kind-${declared.kind}`}>{declared.kind}</span>
            )}
          </h1>
          <p className="muted subtitle">
            {declared ? `${KIND_NOTES[declared.kind]} ` : ""}
            {queues.isSuccess && !declared && "This queue is not declared. "}
            {tasks.isSuccess &&
              `${listed.length} ${status ? `${status} ` : ""}task${listed.length === 1 ? "" : "s"}` +
                `${flow ? ` of ${flow}` : ""} changed in the last ${window.label}.`}
          </p>
        </div>
        <form
          className="filters"
          onSubmit={(event) => event.preventDefault()}
          aria-label="Filters"
        >
          <label>
            State{" "}
            <select
              value={status ?? "all"}
              onChange={(event) => go({ status: event.target.value, task: undefined })}
            >
              <option value="all">all</option>
              {TASK_STATUSES.map((each) => (
                <option key={each} value={each}>
                  {each}
                </option>
              ))}
            </select>
          </label>
          <label>
            Flow{" "}
            <select
              value={flow ?? ""}
              onChange={(event) =>
                go({ flow: event.target.value || undefined, task: undefined })
              }
            >
              <option value="">all</option>
              {(flows.data ?? []).map((each) => (
                <option key={each.name} value={each.name}>
                  {each.name}
                </option>
              ))}
              {flow && !flows.data?.some((each) => each.name === flow) && (
                <option value={flow}>{flow}</option>
              )}
            </select>
          </label>
          <label>
            Changed in the last{" "}
            <select
              value={window.value}
              onChange={(event) => go({ window: event.target.value })}
            >
              {WINDOWS.map((each) => (
                <option key={each.value} value={each.value}>
                  {each.label}
                </option>
              ))}
            </select>
          </label>
        </form>
      </div>
      <div className={`queue-body${opened ? " queue-body-open" : ""}`}>
        <div>
          {tasks.isPending && <Loading what="tasks" />}
          {tasks.isError && <Failed what="tasks" error={tasks.error} />}
          {tasks.isSuccess && listed.length === 0 && (
            <p className="muted">
              No {status ? `${status} ` : ""}task{flow ? ` of ${flow}` : ""} changed in
              the last {window.label}.
            </p>
          )}
          {listed.length > 0 && (
            <table className="queue-tasks">
              <thead>
                <tr>
                  <th>State</th>
                  <th>Task</th>
                  <th>Flow</th>
                  <th>Run</th>
                  <th>Changed</th>
                </tr>
              </thead>
              <tbody>
                {listed.map(({ task, flow: of, version }) => (
                  <tr key={task.id} aria-selected={task.id === opened}>
                    <td>
                      <StatusBadge status={task.status} />
                    </td>
                    <td>
                      <a
                        className="run-name"
                        href={href({ ...route, task: task.id })}
                        title={task.handler}
                      >
                        <strong>{task.address.step}</strong>{" "}
                        <span className="mono muted">{shortId(task.id)}</span>
                      </a>
                    </td>
                    <td>
                      {of} <span className="muted">{version}</span>
                    </td>
                    <td>
                      <a
                        className="mono"
                        href={href({ name: "run", id: task.run_id })}
                        aria-label={`Run ${shortId(task.run_id)}`}
                      >
                        {shortId(task.run_id)}
                      </a>
                    </td>
                    <td>{formatTime(lastChange(task))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {listed.length === QUEUE_PAGE && (
            <p className="muted">
              These are the {QUEUE_PAGE} tasks published last. Narrow the filters to
              see the rest.
            </p>
          )}
        </div>
        {opened && (
          <TaskPanel
            key={opened}
            id={opened}
            kind={declared?.kind}
            closeHref={href({ ...route, task: undefined })}
          />
        )}
      </div>
    </>
  );
}
