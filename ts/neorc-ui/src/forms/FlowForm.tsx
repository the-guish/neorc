import type { ReactNode } from "react";

import type { Answers, Control, Form } from "./subset";
import "./forms.css";

/** A choice of up to this many options is radio buttons; of more, a list. */
const RADIOS_UP_TO = 4;

const HEADINGS = { h1: "h3", h2: "h4", h3: "h5", h4: "h6", h5: "h6" } as const;

/**
 * A flow's form, drawn: its controls, with what each holds in `answers`. It
 * has no button of its own; the page around it starts the run
 * (`answered`, in ./subset).
 */
export function FlowForm({
  form,
  answers,
  onChange,
}: {
  form: Form;
  answers: Answers;
  onChange: (answers: Answers) => void;
}) {
  function draw(control: Control): ReactNode {
    const id = `form-${control.id}`;
    switch (control.kind) {
      case "column":
        return (
          <div className="fields" key={control.id}>
            {control.children.map(draw)}
          </div>
        );
      case "text": {
        if (control.variant === "body" || control.variant === "caption") {
          const quiet = control.variant === "caption" ? "muted" : undefined;
          return (
            <p key={control.id} className={quiet}>
              {control.text}
            </p>
          );
        }
        const Heading = HEADINGS[control.variant];
        return <Heading key={control.id}>{control.text}</Heading>;
      }
      case "field": {
        const value = String(answers[control.input] ?? "");
        const set = (text: string) =>
          onChange({ ...answers, [control.input]: text });
        return (
          <label key={control.id} htmlFor={id}>
            {control.label}
            {control.variant === "longText" ? (
              <textarea
                id={id}
                rows={4}
                value={value}
                onChange={(e) => set(e.target.value)}
              />
            ) : (
              <input
                id={id}
                type="text"
                inputMode={control.variant === "number" ? "decimal" : undefined}
                value={value}
                onChange={(e) => set(e.target.value)}
              />
            )}
          </label>
        );
      }
      case "checkbox":
        return (
          <label key={control.id} htmlFor={id} className="flow-form-inline">
            <input
              id={id}
              type="checkbox"
              checked={answers[control.input] === true}
              onChange={(e) =>
                onChange({ ...answers, [control.input]: e.target.checked })
              }
            />
            {control.label}
          </label>
        );
      case "choice": {
        const chosen = String(answers[control.input] ?? "");
        const choose = (value: string) =>
          onChange({ ...answers, [control.input]: value });
        if (control.options.length > RADIOS_UP_TO) {
          return (
            <label key={control.id} htmlFor={id}>
              {control.label}
              <select id={id} value={chosen} onChange={(e) => choose(e.target.value)}>
                <option value="">Choose…</option>
                {control.options.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          );
        }
        return (
          <fieldset key={control.id}>
            <legend>{control.label}</legend>
            {control.options.map((option) => (
              <label key={option.value} className="flow-form-inline">
                <input
                  type="radio"
                  name={id}
                  checked={chosen === option.value}
                  onChange={() => choose(option.value)}
                />
                {option.label}
              </label>
            ))}
          </fieldset>
        );
      }
    }
  }
  return <div className="flow-form">{draw(form.root)}</div>;
}
