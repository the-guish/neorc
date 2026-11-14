import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";

import { completeTask, type Delivery, type QueueKind } from "../api";
import { isExitCondition, rawStep } from "../definition";
import { answered, FlowForm, formOf, type Answers, type Form } from "../forms";
import { formatTime, formatValue, shortId } from "../format";
import { refresh, useFlow, useRun, useTaskInputs } from "../queries";
import { href } from "../router";
import { Failed, Loading } from "./Layout";
import { StatusBadge } from "./StatusBadge";

/**
 * One task of a queue, beside the queue's list: what it is, its inputs, and
 * how it ended. A pending task of an external queue has the form that
 * completes it.
 */
export function TaskPanel({
  id,
  kind,
  closeHref,
}: {
  id: string;
  /** The kind of the task's queue, once the page knows it. */
  kind: QueueKind | undefined;
  closeHref: string;
}) {
  const delivery = useTaskInputs(id);
  return (
    <aside className="task-panel card" aria-label="Task">
      <a className="task-panel-close" href={closeHref}>
        Close
      </a>
      {delivery.isPending && <Loading what="the task" />}
      {delivery.isError && <Failed what="the task" error={delivery.error} />}
      {delivery.isSuccess && <Opened delivery={delivery.data} kind={kind} />}
    </aside>
  );
}

function Opened({ delivery, kind }: { delivery: Delivery; kind: QueueKind | undefined }) {
  const { task, inputs } = delivery;
  const names = Object.keys(inputs);
  return (
    <>
      <h2>
        {task.address.step} <StatusBadge status={task.status} />
      </h2>
      <dl className="facts">
        <dt>Asks for</dt>
        <dd>
          <code>{task.handler}</code>
        </dd>
        <dt>Run</dt>
        <dd>
          <a className="mono" href={href({ name: "run", id: task.run_id })}>
            {shortId(task.run_id)}
          </a>
        </dd>
        <dt>Published</dt>
        <dd>{formatTime(task.created_at)}</dd>
        {task.finished_at && (
          <>
            <dt>Finished</dt>
            <dd>{formatTime(task.finished_at)}</dd>
          </>
        )}
      </dl>
      <h3>Inputs</h3>
      {names.length === 0 ? (
        <p className="muted">This task has no inputs.</p>
      ) : (
        <dl className="task-inputs">
          {names.map((name) => (
            <div key={name}>
              <dt>{name}</dt>
              <dd>
                <Value value={inputs[name]} />
              </dd>
            </div>
          ))}
        </dl>
      )}
      {task.status === "succeeded" && (
        <>
          <h3>Result</h3>
          <Value value={task.result} />
        </>
      )}
      {task.error && (
        <>
          <h3>Error</h3>
          <pre className="error">{task.error}</pre>
        </>
      )}
      {task.status === "pending" && kind === "external" && <Answer delivery={delivery} />}
      {task.status === "pending" && kind === "worker" && (
        <p className="muted">This task waits for a worker of its queue.</p>
      )}
      {(task.status === "received" || task.status === "running") && (
        <p className="muted">A worker has this task.</p>
      )}
    </>
  );
}

/** A value as a person reads it: a text as it is, anything else as JSON. */
function Value({ value }: { value: unknown }) {
  return typeof value === "string" ? (
    <p className="task-text">{value}</p>
  ) : (
    <pre>{formatValue(value)}</pre>
  );
}

/**
 * The form of the task's step, read from the version of the flow its run is
 * on; while that loads, and for a step with none, there is no form.
 */
function Answer({ delivery }: { delivery: Delivery }) {
  const { task } = delivery;
  const run = useRun(task.run_id);
  const flow = useFlow(run.data?.flow ?? "", run.data?.version, run.isSuccess);
  if (run.isError) return <Failed what="the task's run" error={run.error} />;
  if (flow.isError) return <Failed what="the task's flow" error={flow.error} />;
  if (!flow.isSuccess) return <Loading what="the form" />;
  const step = task.address.step;
  return (
    <AnswerForm
      taskId={task.id}
      step={step}
      form={formOf(rawStep(flow.data.content, step))}
      yesOrNo={isExitCondition(flow.data.content, step)}
    />
  );
}

type Mode = "form" | "json" | "fail";

/** The raw editor's text as a result, or what is wrong with it. */
export function jsonResultOf(text: string): { result: unknown } | { problem: string } {
  try {
    return { result: JSON.parse(text) as unknown };
  } catch (error) {
    return { problem: `not valid JSON: ${error instanceof Error ? error.message : error}` };
  }
}

/**
 * Complete a pending task: with the step's own form when it has one
 * (../forms), and its fields as the result; with a yes or no for a loop's
 * exit condition; with a text otherwise. A JSON editor is there for what
 * those cannot say, and a reason fails the task instead. The manager's
 * refusal is shown as it came.
 */
export function AnswerForm({
  taskId,
  step,
  form,
  yesOrNo,
}: {
  taskId: string;
  step: string;
  form: Form | null;
  yesOrNo: boolean;
}) {
  const client = useQueryClient();
  const [mode, setMode] = useState<Mode>("form");
  const start = useMemo(() => form?.start ?? {}, [form]);
  const [answers, setAnswers] = useState<Answers>(start);
  const [text, setText] = useState("");
  const [yes, setYes] = useState<boolean | null>(null);
  const [json, setJson] = useState("");
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const complete = useMutation({
    mutationFn: (body: { result: unknown } | { error: string }) =>
      completeTask(taskId, body),
    onSettled: () => {
      // Completed, or refused because someone else was first: read it again.
      void refresh(client, { queryKey: ["task", taskId] });
      void refresh(client, { queryKey: ["queue-tasks"] });
    },
  });

  function built(): { result: unknown } | { error: string } | { problem: string } {
    if (mode === "fail") {
      return reason.trim() === ""
        ? { problem: "Say why the task fails." }
        : { error: reason.trim() };
    }
    if (mode === "json") return jsonResultOf(json);
    if (form !== null) {
      const filled = answered(form, answers);
      return "problem" in filled ? filled : { result: filled.inputs };
    }
    if (yesOrNo) {
      return yes === null ? { problem: "Choose yes or no." } : { result: yes };
    }
    return text.trim() === "" ? { problem: "Write an answer." } : { result: text };
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const body = built();
    if ("problem" in body) {
      setProblem(body.problem);
      return;
    }
    setProblem(null);
    complete.mutate(body);
  }

  function show(next: Mode) {
    setMode(next);
    setProblem(null);
    complete.reset();
  }

  const refused = complete.error instanceof Error ? complete.error.message : null;
  return (
    <form className="start-run task-answer" onSubmit={submit} aria-label={`Complete ${step}`}>
      <h3>{mode === "fail" ? "Fail the task" : "Your answer"}</h3>
      {mode === "form" &&
        (form !== null ? (
          <FlowForm form={form} answers={answers} onChange={setAnswers} />
        ) : yesOrNo ? (
          <fieldset className="fields">
            <legend>The loop ends on a yes.</legend>
            <label className="flow-form-inline">
              <input
                type="radio"
                name={`answer-${taskId}`}
                checked={yes === true}
                onChange={() => setYes(true)}
              />
              Yes
            </label>
            <label className="flow-form-inline">
              <input
                type="radio"
                name={`answer-${taskId}`}
                checked={yes === false}
                onChange={() => setYes(false)}
              />
              No
            </label>
          </fieldset>
        ) : (
          <div className="fields">
            <label>
              Answer
              <textarea
                className="plain"
                rows={4}
                value={text}
                onChange={(event) => setText(event.target.value)}
              />
            </label>
          </div>
        ))}
      {mode === "json" && (
        <label className="json">
          Result as JSON
          <textarea
            rows={6}
            value={json}
            onChange={(event) => setJson(event.target.value)}
            spellCheck={false}
          />
        </label>
      )}
      {mode === "fail" && (
        <div className="fields">
          <label>
            Reason
            <textarea
              className="plain"
              rows={3}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <p className="muted">A failed task fails its run.</p>
        </div>
      )}
      {(problem ?? refused) && <p role="alert">{problem ?? refused}</p>}
      <div className="task-answer-actions">
        <button type="submit" className="primary" disabled={complete.isPending}>
          {complete.isPending
            ? "Sending…"
            : mode === "fail"
              ? "Fail task"
              : "Complete"}
        </button>
        {mode !== "form" && (
          <button type="button" className="link" onClick={() => show("form")}>
            Back to the form
          </button>
        )}
        {mode !== "json" && (
          <button type="button" className="link" onClick={() => show("json")}>
            Edit as JSON
          </button>
        )}
        {mode !== "fail" && (
          <button type="button" className="link" onClick={() => show("fail")}>
            Fail the task
          </button>
        )}
      </div>
    </form>
  );
}
