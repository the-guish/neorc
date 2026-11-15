import { useMutation, useQuery } from "@tanstack/react-query";
import { detailOf } from "@oneberth/ui";
import { useMemo, useState, type FormEvent } from "react";

import type { Flow } from "../api";
import { useApi, useFormSource, type FormMessages } from "../context";
import { answered, FlowForm, formOf, type Answers, type Form } from "../forms";
import { localToIso } from "../format";
import { useNav } from "../router";

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
 * The host's form for a flow, when the host gives neorc a source of forms:
 * `form` is null for a flow it has none for, and `failed` says why a form it
 * gave cannot be drawn.
 */
function useHostForm(flow: Flow): { pending: boolean; form: Form | null; failed: string | null } {
  const source = useFormSource();
  const asked = useQuery({
    queryKey: ["host-form", flow.name, flow.version],
    queryFn: async (): Promise<FormMessages | null> =>
      (await source?.({ name: flow.name, version: flow.version, content: flow.content })) ?? null,
    enabled: source !== undefined,
    retry: false,
  });
  return useMemo(() => {
    if (source === undefined || asked.data === null) return { pending: false, form: null, failed: null };
    if (asked.isPending) return { pending: true, form: null, failed: null };
    if (asked.isError) return { pending: false, form: null, failed: detailOf(asked.error) };
    const form = formOf({ form: asked.data });
    return form === null
      ? { pending: false, form: null, failed: "it is outside what neorc draws" }
      : { pending: false, form, failed: null };
  }, [source, asked.data, asked.isPending, asked.isError, asked.error]);
}

/**
 * Start a run of a flow's latest version. The host's form comes first, when
 * the host gives one for the flow (`forms` on the root component); then the
 * flow's own form (../forms); then one field per declared input; and a JSON
 * editor for what none can say. The manager's refusal is shown as it came;
 * a started run is opened.
 */
export function StartRunForm({ flow }: { flow: Flow }) {
  const host = useHostForm(flow);
  if (host.pending) return <p className="neorc-muted">Loading the form…</p>;
  return (
    <>
      {host.failed !== null && (
        <p role="alert">The host's form for {flow.name} could not be drawn: {host.failed}.</p>
      )}
      <StartRun key={host.form === null ? "own" : "host"} flow={flow} hostForm={host.form} />
    </>
  );
}

function StartRun({ flow, hostForm }: { flow: Flow; hostForm: Form | null }) {
  const api = useApi();
  const { navigate } = useNav();
  const inputs = inputsOf(flow.content);
  const names = Object.keys(inputs);
  const form = useMemo(() => hostForm ?? formOf(flow.content), [hostForm, flow.content]);
  const [mode, setMode] = useState<"form" | "json">(names.length || form ? "form" : "json");
  const [values, setValues] = useState<Values>({});
  const [answers, setAnswers] = useState<Answers>(form?.start ?? {});
  const [json, setJson] = useState("{}");
  const [problem, setProblem] = useState<string | null>(null);
  const start = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.startRun(flow.name, body),
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

  const refused = start.error === null ? null : detailOf(start.error);
  return (
    <form className="neorc-start-run" onSubmit={submit} aria-label={`Start ${flow.name}`}>
      <h2>Start a run</h2>
      <p className="neorc-muted">
        Of {flow.name} {flow.version}, the latest version.{" "}
        {(names.length > 0 || form !== null) && (
          <button type="button" className="neorc-link" onClick={() => setMode(mode === "form" ? "json" : "form")}>
            {mode === "form" ? "Edit as JSON" : "Back to the fields"}
          </button>
        )}
      </p>
      {mode === "form" ? (
        form ? (
          <FlowForm form={form} answers={answers} onChange={setAnswers} />
        ) : names.length === 0 ? (
          <p className="neorc-muted">This flow declares no inputs.</p>
        ) : (
          <div className="neorc-fields">
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
        <label className="neorc-json">
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
      <button type="submit" className="neorc-primary" disabled={start.isPending}>
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
      {name} <small className="neorc-muted">{type}</small>
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
