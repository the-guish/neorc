import { Fragment, type ReactNode } from "react";

import { textOf, type Answers, type Control, type Form, type Held } from "./subset";
import "./forms.css";

/** A choice of up to this many options is radio buttons; of more, a list. */
const RADIOS_UP_TO = 4;

const HEADINGS = { h1: "h3", h2: "h4", h3: "h5", h4: "h6", h5: "h6" } as const;

/** Where a component is drawn: in the item of a List's template, or not. */
interface Scope {
  index: number;
  item: unknown;
}

/**
 * A flow's form, or a task's, drawn: its controls, with what each holds in
 * `answers`. A flow's form has no button of its own; the page around it
 * starts the run (`answered`, in ./subset). A task's form may have buttons,
 * which complete the task: `onPress` is given the value the button sets.
 */
export function FlowForm({
  form,
  answers,
  onChange,
  onPress,
  pressing = false,
}: {
  form: Form;
  answers: Answers;
  onChange: (answers: Answers) => void;
  onPress?: (value: string) => void;
  /** While a press is being sent, the buttons wait. */
  pressing?: boolean;
}) {
  /** What a control holds, in its item when it is in a template. */
  function held(input: string, scope: Scope | undefined): Held | undefined {
    const value = answers[input];
    return Array.isArray(value) ? value[scope?.index ?? 0] : value;
  }
  function set(input: string, scope: Scope | undefined, value: Held) {
    if (scope === undefined) {
      onChange({ ...answers, [input]: value });
      return;
    }
    const before = answers[input];
    const each = Array.isArray(before) ? [...before] : [];
    each[scope.index] = value;
    onChange({ ...answers, [input]: each });
  }

  function draw(control: Control, scope?: Scope): ReactNode {
    const id = `form-${control.id}${scope === undefined ? "" : `-${scope.index}`}`;
    switch (control.kind) {
      case "column":
      case "row":
      case "list": {
        const look = {
          column: "neorc-fields",
          row: "neorc-flow-form-row",
          list: "neorc-flow-form-list",
        };
        return (
          <div className={look[control.kind]} key={control.id}>
            {control.children.map((child) => draw(child, scope))}
          </div>
        );
      }
      case "card":
        return (
          <article className="neorc-flow-form-card" key={control.id}>
            {draw(control.child, scope)}
          </article>
        );
      case "repeat":
        return (
          <div className="neorc-flow-form-list" key={control.id}>
            {control.items.map((item, index) => (
              <Fragment key={index}>{draw(control.template, { index, item })}</Fragment>
            ))}
          </div>
        );
      case "text": {
        const text = textOf(control.text, scope?.item);
        if (control.variant === "body" || control.variant === "caption") {
          const quiet = control.variant === "caption" ? "neorc-muted" : undefined;
          return (
            <p key={control.id} className={quiet}>
              {text}
            </p>
          );
        }
        const Heading = HEADINGS[control.variant];
        return <Heading key={control.id}>{text}</Heading>;
      }
      case "button":
        return (
          <button
            key={control.id}
            type="button"
            className={
              control.variant === "primary"
                ? "neorc-primary"
                : control.variant === "borderless"
                  ? "neorc-link"
                  : undefined
            }
            disabled={pressing}
            onClick={() => onPress?.(control.value)}
          >
            {control.label}
          </button>
        );
      case "field": {
        const value = String(held(control.input, scope) ?? "");
        const change = (text: string) => set(control.input, scope, text);
        return (
          <label key={control.id} htmlFor={id}>
            {control.label}
            {control.variant === "longText" ? (
              <textarea
                id={id}
                rows={4}
                value={value}
                onChange={(e) => change(e.target.value)}
              />
            ) : (
              <input
                id={id}
                type="text"
                inputMode={control.variant === "number" ? "decimal" : undefined}
                value={value}
                onChange={(e) => change(e.target.value)}
              />
            )}
          </label>
        );
      }
      case "checkbox":
        return (
          <label key={control.id} htmlFor={id} className="neorc-flow-form-inline">
            <input
              id={id}
              type="checkbox"
              checked={held(control.input, scope) === true}
              onChange={(e) => set(control.input, scope, e.target.checked)}
            />
            {control.label}
          </label>
        );
      case "choice": {
        const chosen = String(held(control.input, scope) ?? "");
        const choose = (value: string) => set(control.input, scope, value);
        if (control.display === "chips") {
          // A bar of buttons, one pressed at a time.
          return (
            <fieldset key={control.id} className="neorc-flow-form-chips">
              <legend>{control.label}</legend>
              {control.options.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  aria-pressed={chosen === option.value}
                  onClick={() => choose(option.value)}
                >
                  {option.label}
                </button>
              ))}
            </fieldset>
          );
        }
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
              <label key={option.value} className="neorc-flow-form-inline">
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
  return <div className="neorc-flow-form">{draw(form.root)}</div>;
}
