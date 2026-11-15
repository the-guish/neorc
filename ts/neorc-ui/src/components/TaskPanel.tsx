import { useMutation, useQueryClient } from "@tanstack/react-query";
import { detailOf } from "@oneberth/ui";
import { useMemo, useState, type FormEvent } from "react";

import { useApi } from "../context";
import {
  type Delivery,
  type QueueKind,
  type Run,
  type Task,
} from "../api";
import { isExitCondition, rawStep } from "../definition";
import { answered, FlowForm, formOf, type Answers, type Form } from "../forms";
import { formatTime, formatValue, shortId } from "../format";
import { refresh, useFlow, useRun, useTaskInputs } from "../queries";
import { useNav } from "../router";
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
    <aside className="neorc-task-panel neorc-card" aria-label="Task">
      <a className="neorc-task-panel-close" href={closeHref}>
        Close
      </a>
      {delivery.isPending && <Loading what="the task" />}
      {delivery.isError && <Failed what="the task" error={delivery.error} />}
      {delivery.isSuccess && <Opened delivery={delivery.data} kind={kind} />}
    </aside>
  );
}

function Opened({ delivery, kind }: { delivery: Delivery; kind: QueueKind | undefined }) {
  const { href } = useNav();
  const { task, inputs } = delivery;
  const names = Object.keys(inputs);
  return (
    <>
      <h2>
        {task.address.step} <StatusBadge status={task.status} />
      </h2>
      <dl className="neorc-facts">
        <dt>Asks for</dt>
        <dd>
          <code>{task.handler}</code>
        </dd>
        <dt>Run</dt>
        <dd>
          <a className="neorc-mono" href={href({ name: "run", id: task.run_id })}>
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
        <p className="neorc-muted">This task has no inputs.</p>
      ) : (
        <dl className="neorc-task-inputs">
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
          <pre className="neorc-error">{task.error}</pre>
        </>
      )}
      {task.finished_at === null && (
        <Unfinished task={task} kind={kind} inputs={inputs} />
      )}
    </>
  );
}

/**
 * What a task that has not finished waits for. Its run decides first: once
 * the run ended, nothing finishes the task, and it has no form.
 */
function Unfinished({
  task,
  kind,
  inputs,
}: {
  task: Task;
  kind: QueueKind | undefined;
  inputs: Record<string, unknown>;
}) {
  const run = useRun(task.run_id);
  if (run.isError) return <Failed what="the task's run" error={run.error} />;
  if (!run.isSuccess) return <Loading what="the task's run" />;
  const { status, reason } = run.data;
  if (status !== "active") {
    return (
      <p className="neorc-muted">
        Its run ended ({status}
        {reason ? `: ${reason}` : ""}). Nothing can complete this task.
      </p>
    );
  }
  if (task.status !== "pending") return <p className="neorc-muted">A worker has this task.</p>;
  if (kind === "external") return <Answer task={task} run={run.data} inputs={inputs} />;
  if (kind === "worker") {
    return <p className="neorc-muted">This task waits for a worker of its queue.</p>;
  }
  return null;
}

/** A value as a person reads it: a text as it is, anything else as JSON. */
function Value({ value }: { value: unknown }) {
  return typeof value === "string" ? (
    <p className="neorc-task-text">{value}</p>
  ) : (
    <pre>{formatValue(value)}</pre>
  );
}

/**
 * The form of the task's step, read from the version of the flow its run is
 * on. It may read the task's inputs.
 */
function Answer({
  task,
  run,
  inputs,
}: {
  task: Task;
  run: Run;
  inputs: Record<string, unknown>;
}) {
  const flow = useFlow(run.flow, run.version);
  if (flow.isError) return <Failed what="the task's flow" error={flow.error} />;
  if (!flow.isSuccess) return <Loading what="the form" />;
  const step = task.address.step;
  return (
    <AnswerForm
      taskId={task.id}
      step={step}
      form={formOf(rawStep(flow.data.content, step), inputs)}
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
 * (../forms), and its fields as the result; a form with buttons is completed
 * by one of them, which sets a field of its own. With a yes or no for a loop's
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
  const api = useApi();
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
      api.completeTask(taskId, body),
    onSettled: () => {
      // Completed, or refused because someone was first or the run ended:
      // read the task, its run and the list again.
      void refresh(client, { queryKey: ["task", taskId] });
      void refresh(client, { queryKey: ["run"] });
      void refresh(client, { queryKey: ["queue-tasks"] });
    },
  });

  function built(
    pressed?: string,
  ): { result: unknown } | { error: string } | { problem: string } {
    if (mode === "fail") {
      return reason.trim() === ""
        ? { problem: "Say why the task fails." }
        : { error: reason.trim() };
    }
    if (mode === "json") return jsonResultOf(json);
    if (form !== null) {
      const filled = answered(form, answers, pressed);
      return "problem" in filled ? filled : { result: filled.inputs };
    }
    if (yesOrNo) {
      return yes === null ? { problem: "Choose yes or no." } : { result: yes };
    }
    return text.trim() === "" ? { problem: "Write an answer." } : { result: text };
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    send();
  }

  /** Send the answer; `pressed` is the value of the form's button pressed. */
  function send(pressed?: string) {
    const body = built(pressed);
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

  const refused = complete.error === null ? null : detailOf(complete.error);
  return (
    <form className="neorc-start-run neorc-task-answer" onSubmit={submit} aria-label={`Complete ${step}`}>
      <h3>{mode === "fail" ? "Fail the task" : "Your answer"}</h3>
      {mode === "form" &&
        (form !== null ? (
          <FlowForm
            form={form}
            answers={answers}
            onChange={setAnswers}
            onPress={send}
            pressing={complete.isPending}
          />
        ) : yesOrNo ? (
          <fieldset className="neorc-fields">
            <legend>The loop ends on a yes.</legend>
            <label className="neorc-flow-form-inline">
              <input
                type="radio"
                name={`answer-${taskId}`}
                checked={yes === true}
                onChange={() => setYes(true)}
              />
              Yes
            </label>
            <label className="neorc-flow-form-inline">
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
          <div className="neorc-fields">
            <label>
              Answer
              <textarea
                className="neorc-plain"
                rows={4}
                value={text}
                onChange={(event) => setText(event.target.value)}
              />
            </label>
          </div>
        ))}
      {mode === "json" && (
        <label className="neorc-json">
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
        <div className="neorc-fields">
          <label>
            Reason
            <textarea
              className="neorc-plain"
              rows={3}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <p className="neorc-muted">A failed task fails its run.</p>
        </div>
      )}
      {(problem ?? refused) && <p role="alert">{problem ?? refused}</p>}
      <div className="neorc-task-answer-actions">
        {/* A form with buttons of its own is completed by one of them. */}
        {!(mode === "form" && form?.buttons) && (
          <button type="submit" className="neorc-primary" disabled={complete.isPending}>
            {complete.isPending ? "Sending…" : mode === "fail" ? "Fail task" : "Complete"}
          </button>
        )}
        {mode !== "form" && (
          <button type="button" className="neorc-link" onClick={() => show("form")}>
            Back to the form
          </button>
        )}
        {mode !== "json" && (
          <button type="button" className="neorc-link" onClick={() => show("json")}>
            Edit as JSON
          </button>
        )}
        {mode !== "fail" && (
          <button type="button" className="neorc-link" onClick={() => show("fail")}>
            Fail the task
          </button>
        )}
      </div>
    </form>
  );
}
