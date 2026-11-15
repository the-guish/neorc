import type { Task } from "../api";
import { formatDuration, formatTime, formatValue } from "../format";

/** Everything the manager knows about one task instance, folded away unless `open`. */
export function TaskDetails({ task, open = false }: { task: Task; open?: boolean }) {
  return (
    <details className="neorc-task-details" open={open}>
      <summary>details</summary>
      <dl className="neorc-facts">
        <dt>Handler</dt>
        <dd>
          <code>{task.handler}</code> on <code>{task.queue}</code>
        </dd>
        <dt>Attempts</dt>
        <dd>{task.attempts}</dd>
        {task.lease_expires_at && (
          <>
            <dt>Lease until</dt>
            <dd>{formatTime(task.lease_expires_at)}</dd>
          </>
        )}
        <dt>Published</dt>
        <dd>{formatTime(task.created_at)}</dd>
        <dt>Started</dt>
        <dd>{formatTime(task.started_at)}</dd>
        <dt>Finished</dt>
        <dd>
          {formatTime(task.finished_at)}
          {task.finished_at && (
            <span className="neorc-muted">
              {" "}
              took {formatDuration(task.started_at ?? task.created_at, task.finished_at)}
            </span>
          )}
        </dd>
        <dt>Id</dt>
        <dd>
          <code>{task.id}</code>
        </dd>
      </dl>
      {Object.keys(task.params).length > 0 && (
        <>
          <h4>Params</h4>
          <pre>{formatValue(task.params)}</pre>
        </>
      )}
      {Object.keys(task.fixed_params).length > 0 && (
        <>
          <h4>Fixed params</h4>
          <pre>{formatValue(task.fixed_params)}</pre>
        </>
      )}
      {task.status === "succeeded" && (
        <>
          <h4>Result</h4>
          <pre>{formatValue(task.result)}</pre>
        </>
      )}
      {task.error && (
        <>
          <h4>Error</h4>
          <pre className="neorc-error">{task.error}</pre>
        </>
      )}
    </details>
  );
}
