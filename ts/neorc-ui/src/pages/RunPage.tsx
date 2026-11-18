import { useEffect, useMemo } from "react";

import { CancelRun } from "../components/CancelRun";
import { Failed, Loading } from "../components/Layout";
import { RunGraph } from "../components/RunGraph";
import { RunTree } from "../components/RunTree";
import { StatusBadge } from "../components/StatusBadge";
import { published, stepsOf } from "../definition";
import { formatDuration, formatTime, formatValue, shortId } from "../format";
import { useFlow, useRun, useRunTasks, useSubRuns } from "../queries";
import { useNav, type RunView } from "../router";

/** One run: what it is, where it stands, and each step of its flow, live. */
export function RunPage({ id, view }: { id: string; view?: RunView | undefined }) {
  const run = useRun(id);
  const chosen = useRunView(view);
  if (run.isPending) return <Loading what={`run ${shortId(id)}`} />;
  if (run.isError) return <Failed what={`run ${shortId(id)}`} error={run.error} />;
  return <Loaded run={run.data} view={chosen} />;
}

/** Where the last choice of view is kept, for the next run opened. */
export const VIEW_KEY = "neorc-ui.run-view";

/**
 * The view to show: the route's if it names one, else the one last chosen,
 * else the outline. A choice in the route is remembered.
 */
function useRunView(view: RunView | undefined): RunView {
  useEffect(() => {
    if (view) {
      try {
        localStorage.setItem(VIEW_KEY, view);
      } catch {
        // Storage may be off; the route still carries the choice.
      }
    }
  }, [view]);
  if (view) return view;
  try {
    return localStorage.getItem(VIEW_KEY) === "graph" ? "graph" : "outline";
  } catch {
    return "outline";
  }
}

function Loaded({
  run,
  view,
}: {
  run: ReturnType<typeof useRun>["data"] & object;
  view: RunView;
}) {
  const { href } = useNav();
  const flow = useFlow(run.flow, run.version);
  const live = { active: run.status === "active" };
  const tasks = useRunTasks(run.id, live);
  const subRuns = useSubRuns(run.id, live);
  // The queries keep the same data while nothing changed, so the steps and
  // what the run did there are read again, and the graph laid out again,
  // only when the poll brings news.
  const steps = useMemo(() => flow.data && stepsOf(flow.data.content), [flow.data]);
  // The inputs with them: a switch may choose its case on one.
  const done = useMemo(
    () => tasks.data && subRuns.data && published(tasks.data, subRuns.data, run.inputs),
    [tasks.data, subRuns.data, run.inputs],
  );
  return (
    <>
      <p className="neorc-crumbs">
        <a href={href({ name: "runs" })}>Runs</a> /{" "}
        <a href={href({ name: "flow", flow: run.flow, version: run.version })}>{run.flow}</a> /{" "}
        <span className="neorc-mono">{shortId(run.id)}</span>
      </p>
      <div className="neorc-page-head">
        <div>
          <h1 className="neorc-title-with-pill">
            {run.flow} <span className="neorc-mono neorc-muted">{shortId(run.id)}</span>{" "}
            <StatusBadge status={run.status} />
          </h1>
          <p className="neorc-mono-line neorc-subtitle">
            {run.id} · version {run.version} · started {formatTime(run.created_at)}
            {run.started_by && ` by ${run.started_by.name ?? run.started_by.id}`}
            {run.finished_at &&
              ` · finished ${formatTime(run.finished_at)}, took ${formatDuration(run.created_at, run.finished_at)}`}
          </p>
        </div>
        {run.status === "active" && run.parent_id === null && (
          <div className="neorc-actions">
            <CancelRun key={run.id} run={run} subRuns={subRuns.data ?? []} />
          </div>
        )}
      </div>
      {(run.parent_id || run.reason) && (
        <dl className="neorc-facts">
          {run.parent_id && (
            <>
              <dt>Parent</dt>
              <dd>
                <a href={href({ name: "run", id: run.parent_id })}>
                  run <code>{shortId(run.parent_id)}</code>
                </a>
                {run.parent_address && (
                  <span className="neorc-muted">
                    {" "}
                    at {run.parent_address.step}
                    {run.parent_address.scope.map(([name, n]) => ` ${name} ${n}`).join("")}
                  </span>
                )}
              </dd>
            </>
          )}
          {run.reason && (
            <>
              <dt>Reason</dt>
              <dd>{run.reason}</dd>
            </>
          )}
        </dl>
      )}

      {run.status === "active" && run.parent_id !== null && (
        <p className="neorc-muted">
          Part of run{" "}
          <a href={href({ name: "run", id: run.root_id })}>
            <code>{shortId(run.root_id)}</code>
          </a>
          : a run is cancelled from its root, with its whole tree.
        </p>
      )}

      <h2>Steps</h2>
      <ViewSwitch id={run.id} view={view} />
      {(flow.isPending || tasks.isPending || subRuns.isPending) && (
        <Loading what="the steps" />
      )}
      {flow.isError && <Failed what="the flow's definition" error={flow.error} />}
      {tasks.isError && <Failed what="the tasks" error={tasks.error} />}
      {subRuns.isError && <Failed what="the sub-runs" error={subRuns.error} />}
      {steps &&
        done &&
        (view === "graph" ? (
          <RunGraph steps={steps} done={done} />
        ) : (
          <RunTree steps={steps} scope={[]} done={done} />
        ))}

      {subRuns.isSuccess && subRuns.data.length > 0 && (
        <>
          <h2>Sub-runs</h2>
          <table>
            <thead>
              <tr>
                <th>Run</th>
                <th>Flow</th>
                <th>Step</th>
                <th>Status</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {subRuns.data.map((sub) => (
                <tr key={sub.id}>
                  <td>
                    <a href={href({ name: "run", id: sub.id })}>
                      <code>{shortId(sub.id)}</code>
                    </a>
                  </td>
                  <td>
                    {sub.flow} <span className="neorc-muted">{sub.version}</span>
                  </td>
                  <td>
                    {sub.parent_address?.step}
                    {sub.parent_address?.scope.map(([name, n]) => ` ${name} ${n}`).join("")}
                  </td>
                  <td>
                    <StatusBadge status={sub.status} />
                  </td>
                  <td>{sub.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h2>Inputs</h2>
      <pre>{formatValue(run.inputs)}</pre>
      {run.status === "succeeded" && (
        <>
          <h2>Output</h2>
          <pre>{formatValue(run.output)}</pre>
        </>
      )}
    </>
  );
}

const VIEWS: { view: RunView; label: string }[] = [
  { view: "outline", label: "Outline" },
  { view: "graph", label: "Graph" },
];

/** Outline or graph: links, so the choice is in the address and can be shared. */
function ViewSwitch({ id, view }: { id: string; view: RunView }) {
  const { href } = useNav();
  return (
    <nav className="neorc-segmented" aria-label="View">
      {VIEWS.map((item) => (
        <a
          key={item.view}
          href={href({ name: "run", id, view: item.view })}
          aria-current={item.view === view ? "true" : undefined}
        >
          {item.label}
        </a>
      ))}
    </nav>
  );
}
