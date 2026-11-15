import {
  addressKey,
  instancesOf,
  iterationOutcome,
  type Published,
  type Scope,
  type StepNode,
} from "../definition";
import { formatDuration, shortId } from "../format";
import { useNav } from "../router";
import { StatusDot } from "./StatusBadge";
import { TaskDetails } from "./TaskDetails";

/**
 * The run's steps as its definition lays them out, each with what the run
 * has done there: tasks with their status, loops with their iterations,
 * fan-outs with their branches, and sub-flows linking to their runs.
 */
export function RunTree({
  steps,
  scope,
  done,
}: {
  steps: StepNode[];
  scope: Scope;
  done: Published;
}) {
  return (
    <ul className="neorc-tree">
      {steps.map((step) => (
        <li key={step.name} className={`neorc-step neorc-step-${step.kind}`}>
          <Step step={step} scope={scope} done={done} />
        </li>
      ))}
    </ul>
  );
}

function Step({
  step,
  scope,
  done,
}: {
  step: StepNode;
  scope: Scope;
  done: Published;
}) {
  const { href } = useNav();
  switch (step.kind) {
    case "task": {
      const task = done.tasks.get(addressKey(step.name, scope));
      return (
        <div className={`neorc-card neorc-step-card${task ? "" : " neorc-not-yet"}`}>
          <div className="neorc-step-line">
            <StatusDot status={task?.status ?? "none"} />
            <span className="neorc-step-text">
              <strong>{step.name}</strong>
              <span className="neorc-muted">
                task · <code>{step.handler}</code>
              </span>
            </span>
            <span className="neorc-mono neorc-muted neorc-step-when">
              {task
                ? when(task.status, formatDuration(task.started_at ?? task.created_at, task.finished_at))
                : "not published"}
            </span>
          </div>
          {task && <TaskDetails task={task} />}
        </div>
      );
    }
    case "flow": {
      const run = done.subRuns.get(addressKey(step.name, scope));
      return (
        <div className={`neorc-card neorc-step-card${run ? "" : " neorc-not-yet"}`}>
          <div className="neorc-step-line">
            <StatusDot status={run?.status ?? "none"} />
            <span className="neorc-step-text">
              <strong>{step.name}</strong>
              <span className="neorc-muted">
                sub-flow <code>{step.flow}</code>
                {run && (
                  <>
                    {" · "}
                    <a href={href({ name: "run", id: run.id })}>
                      run <code>{shortId(run.id)}</code>
                    </a>
                  </>
                )}
              </span>
            </span>
            <span className="neorc-mono neorc-muted neorc-step-when">
              {run ? when(run.status, formatDuration(run.created_at, run.finished_at)) : "not published"}
            </span>
          </div>
        </div>
      );
    }
    case "loop":
    case "fan_out":
      return <Container step={step} scope={scope} done={done} />;
  }
}

/** The status in words, and how long it took once it has: colour alone says nothing to everyone. */
function when(status: string, duration: string): string {
  return duration ? `${status} · ${duration}` : status;
}

function Container({
  step,
  scope,
  done,
}: {
  step: Extract<StepNode, { kind: "loop" | "fan_out" }>;
  scope: Scope;
  done: Published;
}) {
  const instances = instancesOf(step.name, scope, done.addresses);
  const what = step.kind === "loop" ? "iteration" : "branch";
  const about =
    step.kind === "loop"
      ? `loop, at most ${step.maxCycles}, until ${step.exitCondition}`
      : `fan-out ${step.over}`;
  // What the loop came to, from its latest iteration: it ended, or gave up.
  const last = instances[instances.length - 1];
  const outcome =
    step.kind === "loop" && last !== undefined
      ? iterationOutcome(step, scope, last, done)
      : undefined;
  return (
    <div
      className={`neorc-card neorc-container-card${instances.length === 0 ? " neorc-not-yet" : ""}${outcome?.failed ? " neorc-loop-failed" : ""}`}
    >
      <div className="neorc-step-line">
        <span className="neorc-step-text">
          <strong>{step.name}</strong> <span className="neorc-muted">{about}</span>
        </span>
      </div>
      {outcome && (
        <p className={`neorc-loop-outcome ${outcome.failed ? "neorc-loop-outcome-failed" : "neorc-muted"}`}>
          iteration {last}: {outcome.text}
        </p>
      )}
      {instances.length === 0 ? (
        <section className="neorc-instance neorc-not-yet">
          <h4 className="neorc-muted">not entered yet</h4>
          <RunTree steps={step.children} scope={[...scope, [step.name, 0]]} done={done} />
        </section>
      ) : (
        instances.map((n) => (
          <section key={n} className="neorc-instance" aria-label={`${step.name} ${what} ${n}`}>
            <h4>
              {what} {n}
            </h4>
            <RunTree steps={step.children} scope={[...scope, [step.name, n]]} done={done} />
          </section>
        ))
      )}
    </div>
  );
}
