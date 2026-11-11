import { useMutation } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";

import { startRun, type Flow } from "../api";
import { answered, FlowForm, formOf, type Answers } from "../forms";
import { localToIso } from "../format";
import { navigate } from "../router";

const INPUT_TYPES = ["string", "number", "boolean", "datetime"] as const;
type InputType = (typeof INPUT_TYPES)[number];

/** The inputs a flow declares, by name, as far as the page can read them. */
export function inputsOf(content: unknown): Record<string, InputType> {
  const declared =
    typeof content === "object" && content !== null && "inputs" in content
      ? (content as { inputs?: unknown }).inputs
      : undefined;
  if (typeof declared !== "object" || declared === null) return {};
  return Object.fromEntries(
    Object.entries(declared as Record<string, unknown>).map(([name, type]) => [
      name,
      INPUT_TYPES.find((t) => t === type) ?? "string",
    ]),
  );
}

type Values = Record<string, string>;

/**
 * The typed values a run is started with, from the form's fields, or the
 * first thing wrong with them.
 */
export function bodyOf(
  inputs: Record<string, InputType>,
  values: Values,
): { inputs: Record<string, unknown> } | { problem: string } {
  const body: Record<string, unknown> = {};
  for (const [name, type] of Object.entries(inputs)) {
    const value = values[name] ?? "";
    switch (type) {
      case "string":
        body[name] = value;
        break;
      case "boolean":
        body[name] = value === "true";
        break;
      case "number": {
        const number = value.trim() === "" ? NaN : Number(value);
        if (!Number.isFinite(number)) return { problem: `${name} needs a number` };
        body[name] = number;
        break;
      }
      case "datetime": {
        const iso = localToIso(value);
        if (iso === undefined) return { problem: `${name} needs a date and time` };
        body[name] = { $datetime: iso };
        break;
      }
    }
  }
  return { inputs: body };
}

/** The raw editor's text as inputs, or what is wrong with it. */
export function jsonBodyOf(
  text: string,
): { inputs: Record<string, unknown> } | { problem: string } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    return { problem: `not valid JSON: ${error instanceof Error ? error.message : error}` };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { problem: "the inputs must be a JSON object" };
  }
  return { inputs: parsed as Record<string, unknown> };
}

/**
 * Start a run of a flow's latest version: the flow's own form when it gives
 * one (../forms), one field per declared input when it does not, or a JSON
 * editor for what neither can say. The manager's refusal is shown as it
 * came; a started run is opened.
 */
export function StartRunForm({ flow }: { flow: Flow }) {
  const inputs = inputsOf(flow.content);
  const names = Object.keys(inputs);
  const [mode, setMode] = useState<"form" | "json">(names.length ? "form" : "json");
  const [values, setValues] = useState<Values>({});
  const form = useMemo(() => formOf(flow.content), [flow.content]);
  const [answers, setAnswers] = useState<Answers>(form?.start ?? {});
  const [json, setJson] = useState("{}");
  const [problem, setProblem] = useState<string | null>(null);
  const start = useMutation({
    mutationFn: (body: Record<string, unknown>) => startRun(flow.name, body),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    const built =
      mode === "json"
        ? jsonBodyOf(json)
        : form
          ? answered(form, answers)
          : bodyOf(inputs, values);
    if ("problem" in built) {
      setProblem(built.problem);
      return;
    }
    setProblem(null);
    // Per call, not in the hook's options: a callback there would still run
    // after the form unmounted, and pull a reader away from wherever they went.
    start.mutate(built.inputs, {
      onSuccess: (run) => navigate({ name: "run", id: run.id }),
    });
  }

  const refused = start.error instanceof Error ? start.error.message : null;
  return (
    <form className="start-run" onSubmit={submit} aria-label={`Start ${flow.name}`}>
      <h2>Start a run</h2>
      <p className="muted">
        Of {flow.name} {flow.version}, the latest version.{" "}
        {names.length > 0 && (
          <button type="button" className="link" onClick={() => setMode(mode === "form" ? "json" : "form")}>
            {mode === "form" ? "Edit as JSON" : "Back to the fields"}
          </button>
        )}
      </p>
      {mode === "form" ? (
        form ? (
          <FlowForm form={form} answers={answers} onChange={setAnswers} />
        ) : names.length === 0 ? (
          <p className="muted">This flow declares no inputs.</p>
        ) : (
          <div className="fields">
            {names.map((name) => (
              <Field
                key={name}
                name={name}
                type={inputs[name] ?? "string"}
                value={values[name] ?? ""}
                onChange={(value) => setValues({ ...values, [name]: value })}
              />
            ))}
          </div>
        )
      ) : (
        <label className="json">
          Inputs as JSON
          <textarea
            rows={6}
            value={json}
            onChange={(event) => setJson(event.target.value)}
            spellCheck={false}
          />
        </label>
      )}
      {(problem ?? refused) && <p role="alert">{problem ?? refused}</p>}
      <button type="submit" className="primary" disabled={start.isPending}>
        {start.isPending ? "Starting…" : "Start run"}
      </button>
    </form>
  );
}

function Field({
  name,
  type,
  value,
  onChange,
}: {
  name: string;
  type: InputType;
  value: string;
  onChange: (value: string) => void;
}) {
  const id = `input-${name}`;
  return (
    <label htmlFor={id}>
      {name} <small className="muted">{type}</small>
      {type === "boolean" ? (
        <select id={id} value={value || "false"} onChange={(e) => onChange(e.target.value)}>
          <option value="false">false</option>
          <option value="true">true</option>
        </select>
      ) : (
        <input
          id={id}
          type={type === "number" ? "text" : type === "datetime" ? "datetime-local" : "text"}
          inputMode={type === "number" ? "decimal" : undefined}
          step={type === "datetime" ? 1 : undefined}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </label>
  );
}
