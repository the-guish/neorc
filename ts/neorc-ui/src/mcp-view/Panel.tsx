// What the panel shows: a flow, a run, or nothing yet, as neorc-mcp's
// `neorc_panel` answers. The graphs are the UI's own, FlowGraph and RunGraph.
import { useMemo } from "react";

import type { Flow, Run, Task } from "../api";
import { FlowGraph, RunGraph } from "../components/RunGraph";
import { StatusBadge } from "../components/StatusBadge";
import { published, stepsOf } from "../definition";
import { formatDuration, formatTime, formatValue, shortId } from "../format";
import { useNav } from "../router";

export type PanelData =
  | { shows: "nothing"; flows: string[] }
  | { shows: "flow"; flow: Flow; versions: string[] }
  | { shows: "run"; run: Run; flow: Flow; tasks: Task[]; sub_runs: Run[] }
  | { shows: "missing"; detail: string };

const SHOWN = new Set(["nothing", "flow", "run", "missing"]);

/** The structured content of the panel's tool, if it is the panel's. */
export function panelData(structured: Record<string, unknown> | undefined): PanelData | undefined {
  if (!structured || typeof structured.shows !== "string" || !SHOWN.has(structured.shows)) {
    return undefined;
  }
  return structured as unknown as PanelData;
}

export function Panel({ data }: { data: PanelData }) {
  switch (data.shows) {
    case "nothing":
      return <Nothing flows={data.flows} />;
    case "flow":
      return <FlowShown flow={data.flow} versions={data.versions} />;
    case "run":
      return <RunShown run={data.run} flow={data.flow} tasks={data.tasks} subRuns={data.sub_runs} />;
    case "missing":
      return <p className="neorc-muted">{data.detail}</p>;
  }
}

function Nothing({ flows }: { flows: string[] }) {
  const { href } = useNav();
  return (
    <>
      <p className="neorc-muted">
        Nothing shown yet. Ask the agent to show a flow or a run.
      </p>
      {flows.length > 0 && (
        <>
          <h2>Flows</h2>
          <ul className="neorc-mcp-list">
            {flows.map((name) => (
              <li key={name}>
                <a href={href({ name: "flow", flow: name })}>{name}</a>
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

function FlowShown({ flow, versions }: { flow: Flow; versions: string[] }) {
  const steps = useMemo(() => stepsOf(flow.content), [flow.content]);
  const latest = versions[0];
  return (
    <>
      <h1>
        {flow.name} <small className="neorc-muted">{flow.version}</small>
      </h1>
      {latest !== undefined && latest !== flow.version && (
        <p className="neorc-muted">The latest version is {latest}; a run always uses it.</p>
      )}
      {steps.length > 0 && <FlowGraph key={`${flow.name}@${flow.version}`} steps={steps} />}
      <details>
        <summary>Definition</summary>
        <pre className="neorc-definition" aria-label="Definition">
          {formatValue(flow.content)}
        </pre>
      </details>
    </>
  );
}

function RunShown({
  run,
  flow,
  tasks,
  subRuns,
}: {
  run: Run;
  flow: Flow;
  tasks: Task[];
  subRuns: Run[];
}) {
  const { href } = useNav();
  const steps = useMemo(() => stepsOf(flow.content), [flow.content]);
  const done = useMemo(() => published(tasks, subRuns, run.inputs), [tasks, subRuns, run.inputs]);
  const failed = tasks.filter((task) => task.status === "failed" || task.error);
  return (
    <>
      <h1 className="neorc-title-with-pill">
        <a href={href({ name: "flow", flow: run.flow })}>{run.flow}</a>{" "}
        <span className="neorc-mono neorc-muted">{shortId(run.id)}</span>{" "}
        <StatusBadge status={run.status} />
      </h1>
      <dl className="neorc-facts">
        <dt>Version</dt>
        <dd>{run.version}</dd>
        <dt>Started</dt>
        <dd>
          {formatTime(run.created_at)}
          {run.started_by && <span className="neorc-muted"> by {run.started_by.name}</span>}
        </dd>
        {run.finished_at && (
          <>
            <dt>Finished</dt>
            <dd>
              {formatTime(run.finished_at)}{" "}
              <span className="neorc-muted">
                took {formatDuration(run.created_at, run.finished_at)}
              </span>
            </dd>
          </>
        )}
        {run.parent_id && (
          <>
            <dt>Parent</dt>
            <dd>
              <a href={href({ name: "run", id: run.parent_id })}>run {shortId(run.parent_id)}</a>
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
      {failed.length > 0 && (
        <section aria-label="What failed">
          <h2>What failed</h2>
          {failed.map((task) => (
            <div key={task.id} className="neorc-mcp-failure">
              <strong>
                {task.address.step}
                {task.address.scope.map(([name, n]) => ` ${name} ${n}`).join("")}
              </strong>{" "}
              <span className="neorc-muted">
                {task.handler} on {task.queue}, attempt {task.attempts}
              </span>
              <pre>{task.error}</pre>
            </div>
          ))}
        </section>
      )}
      {steps.length > 0 && <RunGraph steps={steps} done={done} />}
    </>
  );
}
