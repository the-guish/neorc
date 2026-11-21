// What a form is, read out of a flow's content, and what a filled-in form
// submits. No React here.
//
// A form is the list of A2UI v0.9 messages the manager stored with the flow,
// and the manager has already refused what is outside the subset
// (neorc_core/forms/_subset.py, which is where the rules are written down).
// So this reads rather than checks: whatever it does not recognise makes
// `formOf` answer null, and the page falls back to what it has without one.
//
// A task's form reads the task's inputs, at /inputs: a text shows one, and a
// List repeats a template once per item of a list in one. In the template, a
// path with no leading slash is relative to the item. A control there holds
// one value per item, and a Button completes the task, setting one field.
//
// A FlowPicker is the catalog's own: a choice of one of the manager's flows,
// which the page searches as a person types (./FlowPicker). It holds the name
// of the flow chosen.

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
const DISPLAY_STYLES = ["checkbox", "chips"] as const;
const BUTTON_VARIANTS = ["default", "primary", "borderless"] as const;

/**
 * What a text shows: a string, or the path of a value in the item of the
 * List's template it is in. A path from the root is read when the form is.
 */
export type Shown = string | { item: string[] };

/** What every control has: the field it fills, and whether once per item. */
interface Bound {
  id: string;
  input: string;
  label: string;
  /** In a List's template: the field holds one value per item. */
  perItem: boolean;
}

/** A component of the form, with its children in the place of their ids. */
export type Control =
  | { kind: "column" | "row" | "list"; id: string; children: Control[] }
  | { kind: "card"; id: string; child: Control }
  | { kind: "repeat"; id: string; items: unknown[]; template: Control }
  | {
      kind: "text";
      id: string;
      text: Shown;
      variant: (typeof TEXT_VARIANTS)[number];
    }
  | {
      kind: "button";
      id: string;
      label: string;
      value: string;
      variant: (typeof BUTTON_VARIANTS)[number];
    }
  | (Bound & {
      kind: "field";
      variant: (typeof FIELD_VARIANTS)[number];
      checks: Check[];
    })
  | (Bound & { kind: "checkbox" })
  | (Bound & {
      kind: "choice";
      options: Option[];
      display: (typeof DISPLAY_STYLES)[number];
      checks: Check[];
    })
  | (Bound & { kind: "flow"; checks: Check[] });

/** What one control holds: a text, or a check box's yes or no. */
export type Held = string | boolean;

/**
 * What each control holds while the form is filled in, by its field's name:
 * a list, one per item, for a control in a List's template.
 */
export type Answers = Record<string, Held | Held[]>;

export interface Form {
  root: Control;
  /** What the form starts from: its own data, and empty where it gives none. */
  start: Answers;
  /** The field the form's buttons set; null for a form with no button. */
  buttons: string | null;
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

/** A binding read: `{absolute: true, segments: ["inputs", "review"]}`. */
function pathOf(value: unknown): { absolute: boolean; segments: string[] } | null {
  if (!isObject(value) || typeof value.path !== "string") return null;
  const absolute = value.path.startsWith("/");
  const segments = (absolute ? value.path.slice(1) : value.path).split("/");
  return segments.every((each) => each !== "") ? { absolute, segments } : null;
}

/** `repo` for `{"path": "/repo"}`; in a template, `decision` for `{"path": "decision"}`. */
function inputOf(value: unknown, perItem: boolean): string | null {
  const read = pathOf(value);
  if (read === null || read.segments.length !== 1 || read.absolute === perItem) {
    return null;
  }
  return read.segments[0]!;
}

/** The value at `segments` below `from`; undefined where there is none. */
function at(from: unknown, segments: string[]): unknown {
  let value = from;
  for (const segment of segments) {
    value = isObject(value) ? value[segment] : undefined;
  }
  return value;
}

/** What a text shows, in the item of the List's template it is in, if any. */
export function textOf(text: Shown, item?: unknown): string {
  return typeof text === "string" ? text : shown(at(item, text.item));
}

/** A value as a text shows it, as A2UI converts one. */
function shown(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
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

/** What a Button's action sets: `["verdict", "ready"]`. */
function actionOf(value: unknown): [string, string] | null {
  const event = isObject(value) ? value.event : undefined;
  if (!isObject(event) || !isObject(event.context)) return null;
  const entries = Object.entries(event.context);
  if (entries.length !== 1) return null;
  const [name, set] = entries[0]!;
  return typeof set === "string" ? [name, set] : null;
}

/**
 * A flow's form, or a task's, as far as this page can draw it; null when it
 * cannot. `inputs` are the task's, which a task's form reads; a flow's form
 * is read without, and reads nothing.
 */
export function formOf(content: unknown, inputs?: Record<string, unknown>): Form | null {
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
  const root = inputs === undefined ? undefined : { inputs };

  const start: Answers = {};
  let buttons: string | null = null;
  const placed = new Set<string>();
  /** `items` are those of the List's template the component is in, if any. */
  function control(id: unknown, items: unknown[] | null): Control | null {
    const component = typeof id === "string" ? components.get(id) : undefined;
    if (typeof id !== "string" || component === undefined || placed.has(id)) return null;
    placed.add(id);
    const label = component.label;
    const perItem = items !== null;
    const input = inputOf(component.value, perItem);
    const given = input === null || perItem ? undefined : data[input];
    /** Start a field: once, or once per item. */
    function begin(name: string, held: Held) {
      start[name] = items !== null ? items.map(() => held) : held;
    }
    switch (component.component) {
      case "Column":
      case "Row":
      case "List": {
        const kind =
          component.component === "Column"
            ? "column"
            : component.component === "Row"
              ? "row"
              : "list";
        const template = component.children;
        if (kind === "list" && isObject(template)) {
          const path = pathOf({ path: template.path });
          if (root === undefined || perItem || path === null || !path.absolute)
            return null;
          const listed = at(root, path.segments);
          if (!Array.isArray(listed)) return null;
          const drawn = control(template.componentId, listed);
          if (drawn === null) return null;
          return { kind: "repeat", id, items: listed, template: drawn };
        }
        if (!Array.isArray(template)) return null;
        const children: Control[] = [];
        for (const child of template) {
          const drawn = control(child, items);
          if (drawn === null) return null;
          children.push(drawn);
        }
        return { kind, id, children };
      }
      case "Card": {
        const child = control(component.child, items);
        return child === null ? null : { kind: "card", id, child };
      }
      case "Text": {
        const variant = oneOf(component.variant, TEXT_VARIANTS, "body");
        if (variant === null) return null;
        if (typeof component.text === "string") {
          return { kind: "text", id, text: component.text, variant };
        }
        const path = pathOf(component.text);
        if (root === undefined || path === null) return null;
        if (path.absolute) {
          if (path.segments[0] !== "inputs") return null;
          return {
            kind: "text",
            id,
            text: shown(at(root, path.segments)),
            variant,
          };
        }
        if (!perItem) return null;
        return { kind: "text", id, text: { item: path.segments }, variant };
      }
      case "Button": {
        const variant = oneOf(component.variant, BUTTON_VARIANTS, "default");
        const sets = actionOf(component.action);
        const child =
          typeof component.child === "string"
            ? components.get(component.child)
            : undefined;
        if (inputs === undefined || perItem || variant === null || sets === null)
          return null;
        if (child?.component !== "Text" || typeof child.text !== "string") return null;
        if (buttons !== null && buttons !== sets[0]) return null;
        placed.add(String(component.child));
        buttons = sets[0];
        return {
          kind: "button",
          id,
          label: child.text,
          value: sets[1],
          variant,
        };
      }
      case "TextField": {
        const variant = oneOf(component.variant, FIELD_VARIANTS, "shortText");
        const checks = checksOf(component.checks);
        if (typeof label !== "string" || input === null) return null;
        if (variant === null || checks === null) return null;
        const known = typeof given === "string" || typeof given === "number";
        begin(input, known ? String(given) : "");
        return { kind: "field", id, input, label, perItem, variant, checks };
      }
      case "CheckBox": {
        if (typeof label !== "string" || input === null) return null;
        begin(input, given === true);
        return { kind: "checkbox", id, input, label, perItem };
      }
      case "ChoicePicker": {
        const options = optionsOf(component.options);
        const checks = checksOf(component.checks);
        const display = oneOf(component.displayStyle, DISPLAY_STYLES, "checkbox");
        if (typeof label !== "string" || input === null) return null;
        if (options === null || checks === null || display === null) return null;
        const first: unknown = Array.isArray(given) ? given[0] : undefined;
        begin(input, options.find((option) => option.value === first)?.value ?? "");
        return {
          kind: "choice",
          id,
          input,
          label,
          perItem,
          options,
          display,
          checks,
        };
      }
      case "FlowPicker": {
        const checks = checksOf(component.checks);
        if (typeof label !== "string" || input === null || checks === null) return null;
        begin(input, typeof given === "string" ? given : "");
        return { kind: "flow", id, input, label, perItem, checks };
      }
      default:
        return null;
    }
  }
  const drawn = control("root", null);
  if (drawn === null || drawn.kind !== "column") return null;
  return { root: drawn, start, buttons };
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

/** The fields of the controls in a List's template, which hold a list each. */
function perItemFields(control: Control): string[] {
  switch (control.kind) {
    case "column":
    case "row":
    case "list":
      return control.children.flatMap(perItemFields);
    case "card":
      return perItemFields(control.child);
    case "repeat":
      return perItemFields(control.template);
    case "text":
    case "button":
      return [];
    default:
      return control.perItem ? [control.input] : [];
  }
}

/**
 * The inputs a run is started with, or the result a task is completed with,
 * from a filled-in form; or the first thing wrong with it. A field left
 * without a number and a choice left unmade, of a flow too, are refused here:
 * every input of a flow is needed, and every field of a result. The rest is the form's own
 * checks, which the manager keeps as well. `pressed` is the value of the
 * button that completes a task, set in the field its form's buttons set.
 */
export function answered(
  form: Form,
  answers: Answers,
  pressed?: string,
): { inputs: Record<string, unknown> } | { problem: string } {
  const inputs: Record<string, unknown> = {};
  for (const name of perItemFields(form.root)) inputs[name] = [];
  /** `index` is the item's, for a control in a List's template. */
  function read(control: Control, index?: number): string | null {
    switch (control.kind) {
      case "column":
      case "row":
      case "list":
        for (const child of control.children) {
          const problem = read(child, index);
          if (problem !== null) return problem;
        }
        return null;
      case "card":
        return read(control.child, index);
      case "repeat":
        for (let item = 0; item < control.items.length; item++) {
          const problem = read(control.template, item);
          if (problem !== null) return problem;
        }
        return null;
      case "text":
      case "button":
        return null;
    }
    const input = control.input;
    const held = answers[input];
    const answer = Array.isArray(held) ? held[index ?? 0] : held;
    const label =
      index === undefined ? control.label : `${control.label}, item ${index + 1}`;
    function keep(value: unknown) {
      if (index === undefined) inputs[input] = value;
      else (inputs[input] as unknown[])[index] = value;
    }
    if (control.kind === "checkbox") {
      keep(answer === true);
      return null;
    }
    const text = String(answer ?? "");
    let value: string | number = text;
    if ((control.kind === "choice" || control.kind === "flow") && text === "") {
      const required = control.checks.find((check) => check.call === "required");
      const otherwise = control.kind === "flow" ? "choose a flow" : "choose one";
      return `${label}: ${required?.message ?? otherwise}`;
    }
    if (control.kind === "field" && control.variant === "number") {
      value = text.trim() === "" ? NaN : Number(text);
      if (!Number.isFinite(value)) return `${label} needs a number`;
    }
    const broken = control.checks.find((check) => !keeps(check, value));
    if (broken !== undefined) return `${label}: ${broken.message}`;
    keep(value);
    return null;
  }
  const problem = read(form.root);
  if (problem !== null) return { problem };
  if (form.buttons !== null && pressed !== undefined) inputs[form.buttons] = pressed;
  return { inputs };
}
