// What a form is, read out of a flow's content, and what a filled-in form
// submits. No React here.
//
// A form is the list of A2UI v0.9 messages the manager stored with the flow,
// and the manager has already refused what is outside the subset
// (neorc_core/forms/_subset.py, which is where the rules are written down).
// So this reads rather than checks: whatever it does not recognise makes
// `formOf` answer null, and the page falls back to one field per input.

/** A rule a control's value must keep, with what to say when it does not. */
export interface Check {
  call: (typeof CALLS)[number];
  min?: number;
  max?: number;
  message: string;
}

export interface Option {
  label: string;
  value: string;
}

const CALLS = ["required", "length", "numeric"] as const;
const TEXT_VARIANTS = ["h1", "h2", "h3", "h4", "h5", "caption", "body"] as const;
const FIELD_VARIANTS = ["shortText", "longText", "number"] as const;

/** A component of the form, with its children in the place of their ids. */
export type Control =
  | { kind: "column"; id: string; children: Control[] }
  | {
      kind: "text";
      id: string;
      text: string;
      variant: (typeof TEXT_VARIANTS)[number];
    }
  | {
      kind: "field";
      id: string;
      input: string;
      label: string;
      variant: (typeof FIELD_VARIANTS)[number];
      checks: Check[];
    }
  | { kind: "checkbox"; id: string; input: string; label: string }
  | {
      kind: "choice";
      id: string;
      input: string;
      label: string;
      options: Option[];
      checks: Check[];
    };

/** What each control holds while the form is filled in, by its input's name. */
export type Answers = Record<string, string | boolean>;

export interface Form {
  root: Control;
  /** What the form starts from: its own data, and empty where it gives none. */
  start: Answers;
}

type Json = Record<string, unknown>;

function isObject(value: unknown): value is Json {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** `value` if it is one of `allowed`, `otherwise` if it is left out, else null. */
function oneOf<T extends string>(
  value: unknown,
  allowed: readonly T[],
  otherwise: T,
): T | null {
  if (value === undefined) return otherwise;
  return allowed.find((each) => each === value) ?? null;
}

/** `repo` for `{"path": "/repo"}`. */
function inputOf(value: unknown): string | null {
  if (!isObject(value) || typeof value.path !== "string") return null;
  return /^\/[^/]+$/.test(value.path) ? value.path.slice(1) : null;
}

function checksOf(value: unknown): Check[] | null {
  if (value === undefined) return [];
  if (!Array.isArray(value)) return null;
  const checks: Check[] = [];
  for (const each of value) {
    if (!isObject(each) || typeof each.message !== "string") return null;
    const condition = each.condition;
    if (!isObject(condition) || !isObject(condition.args)) return null;
    const call = CALLS.find((known) => known === condition.call);
    if (call === undefined) return null;
    const { min, max } = condition.args;
    checks.push({
      call,
      message: each.message,
      ...(typeof min === "number" ? { min } : {}),
      ...(typeof max === "number" ? { max } : {}),
    });
  }
  return checks;
}

function optionsOf(value: unknown): Option[] | null {
  if (!Array.isArray(value) || value.length === 0) return null;
  const options: Option[] = [];
  for (const each of value) {
    if (!isObject(each)) return null;
    const { label, value: chosen } = each;
    if (typeof label !== "string" || typeof chosen !== "string") return null;
    options.push({ label, value: chosen });
  }
  return options;
}

/** The flow's form, as far as this page can draw it; null when it cannot. */
export function formOf(content: unknown): Form | null {
  const messages = isObject(content) ? content.form : undefined;
  if (!Array.isArray(messages)) return null;
  const components = new Map<string, Json>();
  let data: Json = {};
  for (const message of messages) {
    if (!isObject(message)) return null;
    const { updateComponents, updateDataModel } = message;
    if (isObject(updateComponents) && Array.isArray(updateComponents.components)) {
      for (const each of updateComponents.components) {
        if (!isObject(each) || typeof each.id !== "string") return null;
        components.set(each.id, each);
      }
    }
    if (isObject(updateDataModel) && isObject(updateDataModel.value)) {
      data = updateDataModel.value;
    }
  }

  const start: Answers = {};
  const placed = new Set<string>();
  function control(id: unknown): Control | null {
    const component = typeof id === "string" ? components.get(id) : undefined;
    if (typeof id !== "string" || component === undefined || placed.has(id)) return null;
    placed.add(id);
    const label = component.label;
    const input = inputOf(component.value);
    const given = input === null ? undefined : data[input];
    switch (component.component) {
      case "Column": {
        if (!Array.isArray(component.children)) return null;
        const children: Control[] = [];
        for (const child of component.children) {
          const drawn = control(child);
          if (drawn === null) return null;
          children.push(drawn);
        }
        return { kind: "column", id, children };
      }
      case "Text": {
        const variant = oneOf(component.variant, TEXT_VARIANTS, "body");
        if (typeof component.text !== "string" || variant === null) return null;
        return { kind: "text", id, text: component.text, variant };
      }
      case "TextField": {
        const variant = oneOf(component.variant, FIELD_VARIANTS, "shortText");
        const checks = checksOf(component.checks);
        if (typeof label !== "string" || input === null) return null;
        if (variant === null || checks === null) return null;
        const known = typeof given === "string" || typeof given === "number";
        start[input] = known ? String(given) : "";
        return { kind: "field", id, input, label, variant, checks };
      }
      case "CheckBox": {
        if (typeof label !== "string" || input === null) return null;
        start[input] = given === true;
        return { kind: "checkbox", id, input, label };
      }
      case "ChoicePicker": {
        const options = optionsOf(component.options);
        const checks = checksOf(component.checks);
        if (typeof label !== "string" || input === null) return null;
        if (options === null || checks === null) return null;
        const first: unknown = Array.isArray(given) ? given[0] : undefined;
        start[input] = options.find((option) => option.value === first)?.value ?? "";
        return { kind: "choice", id, input, label, options, checks };
      }
      default:
        return null;
    }
  }
  const root = control("root");
  return root === null || root.kind !== "column" ? null : { root, start };
}

/** Whether a text or a number keeps `check`. */
function keeps(check: Check, value: string | number): boolean {
  if (check.call === "required") return value !== "";
  // A length is a text's, in characters as the manager counts them rather
  // than UTF-16 units; a range is a number's.
  let measured: number;
  if (check.call === "length" && typeof value === "string") {
    measured = [...value].length;
  } else if (check.call === "numeric" && typeof value === "number") {
    measured = value;
  } else {
    return true;
  }
  return (
    (check.min === undefined || measured >= check.min) &&
    (check.max === undefined || measured <= check.max)
  );
}

/**
 * The inputs a run is started with, from a filled-in form, or the first thing
 * wrong with it. A field left without a number and a choice left unmade are
 * refused here: every input of a flow is needed. The rest is the form's own
 * checks, which the manager keeps as well.
 */
export function answered(
  form: Form,
  answers: Answers,
): { inputs: Record<string, unknown> } | { problem: string } {
  const inputs: Record<string, unknown> = {};
  function read(control: Control): string | null {
    if (control.kind === "column") {
      for (const child of control.children) {
        const problem = read(child);
        if (problem !== null) return problem;
      }
      return null;
    }
    if (control.kind === "text") return null;
    const answer = answers[control.input] ?? "";
    if (control.kind === "checkbox") {
      inputs[control.input] = answer === true;
      return null;
    }
    const text = String(answer);
    let value: string | number = text;
    if (control.kind === "choice" && text === "") {
      const required = control.checks.find((check) => check.call === "required");
      return `${control.label}: ${required?.message ?? "choose one"}`;
    }
    if (control.kind === "field" && control.variant === "number") {
      value = text.trim() === "" ? NaN : Number(text);
      if (!Number.isFinite(value)) return `${control.label} needs a number`;
    }
    const broken = control.checks.find((check) => !keeps(check, value));
    if (broken !== undefined) return `${control.label}: ${broken.message}`;
    inputs[control.input] = value;
    return null;
  }
  const problem = read(form.root);
  return problem === null ? { inputs } : { problem };
}
