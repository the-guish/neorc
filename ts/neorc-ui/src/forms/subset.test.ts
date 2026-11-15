import { describe, expect, it } from "vitest";

import example from "./code_task.form.json";
import triage from "./triage.form.json";
import { answered, formOf, textOf, type Answers, type Form } from "./subset";

/** The example's form, with one change to the messages before it is read. */
function changed(change: (messages: typeof example) => void): unknown {
  const messages = structuredClone(example);
  change(messages);
  return { form: messages };
}

function components(messages: typeof example) {
  return messages[1]!.updateComponents!.components;
}

function read(content: unknown): Form {
  const form = formOf(content);
  if (form === null) throw new Error("the form was not read");
  return form;
}

const FILLED: Answers = {
  description: "Add a retry to the upload step.",
  repo: "acme/billing",
  model: "medium",
  attempts: "2",
  draft: true,
};

describe("formOf", () => {
  it("reads the example's form as a tree of controls", () => {
    const form = read({ form: example });

    expect(form.root.kind).toBe("column");
    const kinds =
      form.root.kind === "column" ? form.root.children.map((each) => each.kind) : [];
    expect(kinds).toEqual(["text", "field", "choice", "choice", "field", "checkbox"]);
  });

  it("starts from the form's own data, and from nothing where it gives none", () => {
    expect(read({ form: example }).start).toEqual({
      description: "",
      repo: "",
      model: "medium",
      attempts: "2",
      draft: true,
    });
  });

  it("is what the package's entry gives a host to check its form", async () => {
    const entry = await import("../index");

    expect(entry.formOf({ form: example })).toEqual(read({ form: example }));
    expect(entry.formOf({ form: [{ updateComponents: { components: [{}] } }] })).toBeNull();
  });

  it("answers null for a flow with no form", () => {
    expect(formOf({ name: "a", version: "1.0.0" })).toBeNull();
    expect(formOf(null)).toBeNull();
    expect(formOf({ form: "code_task.form.json" })).toBeNull();
  });

  it.each([
    [
      "a component it does not draw",
      (messages: typeof example) => {
        components(messages)[1]!.component = "Video";
      },
    ],
    [
      "a child that is not there",
      (messages: typeof example) => {
        components(messages)[0]!.children!.push("nowhere");
      },
    ],
    [
      "a component placed twice",
      (messages: typeof example) => {
        components(messages)[0]!.children!.push("intro");
      },
    ],
    [
      "a check it does not know",
      (messages: typeof example) => {
        components(messages)[2]!.checks![0]!.condition.call = "regex";
      },
    ],
    [
      "a control bound below an input",
      (messages: typeof example) => {
        components(messages)[6]!.value!.path = "/options/draft";
      },
    ],
  ])("answers null for %s, so the page can fall back", (_name, change) => {
    expect(formOf(changed(change))).toBeNull();
  });
});

describe("answered", () => {
  const form = read({ form: example });

  it("gives each input the type its control submits", () => {
    expect(answered(form, FILLED)).toEqual({
      inputs: {
        description: "Add a retry to the upload step.",
        repo: "acme/billing",
        model: "medium",
        attempts: 2,
        draft: true,
      },
    });
  });

  it("says what the form says when a check is broken", () => {
    expect(answered(form, { ...FILLED, description: "short" })).toEqual({
      problem: "Task description: Describe the task in at least 10 characters.",
    });
    expect(answered(form, { ...FILLED, attempts: "9" })).toEqual({
      problem: "Attempts: Between 1 and 5 attempts.",
    });
  });

  it("needs a number where the control submits one", () => {
    for (const attempts of ["", " ", "two"]) {
      expect(answered(form, { ...FILLED, attempts })).toEqual({
        problem: "Attempts needs a number",
      });
    }
  });

  it("needs every choice made", () => {
    expect(answered(form, { ...FILLED, repo: "" })).toEqual({
      problem: "Repository: choose one",
    });
  });

  it("counts a length in characters, as the manager does", () => {
    const tenFaces = "😀".repeat(10);
    const nineFaces = "😀".repeat(9);

    expect(answered(form, { ...FILLED, description: tenFaces })).toHaveProperty("inputs");
    expect(answered(form, { ...FILLED, description: nineFaces })).toHaveProperty(
      "problem",
    );
  });

  it("refuses an empty text only where the form requires one", () => {
    const required = read(
      changed((messages) => {
        components(messages)[2]!.checks = [
          {
            condition: { call: "required", args: { value: { path: "/description" } } },
            message: "Say what to do.",
          },
        ] as never;
      }),
    );

    expect(answered(required, { ...FILLED, description: "" })).toEqual({
      problem: "Task description: Say what to do.",
    });
    expect(answered(required, { ...FILLED, description: "x" })).toHaveProperty("inputs");
  });
});

// A task's form reads the task's inputs: examples/review_loop's triage.

const FOUND = {
  summary: "The review found 2 issues.",
  issues: [
    { severity: "blocker", title: "Crash on empty input", detail: "a.py:9" },
    { severity: "minor", title: "Typo in a docstring", detail: { line: 2 } },
  ],
};

function triageForm(inputs: Record<string, unknown> = { review: FOUND }): Form {
  const form = formOf({ form: triage }, inputs);
  if (form === null) throw new Error("the form was not read");
  return form;
}

/** The triage form, with one change to its messages. */
function triageChanged(change: (messages: typeof triage) => void): unknown {
  const messages = structuredClone(triage);
  change(messages);
  return { form: messages };
}

function triageComponent(messages: typeof triage, id: string) {
  const found = messages[1]!.updateComponents!.components.find((each) => each.id === id);
  if (found === undefined) throw new Error(`no component ${id}`);
  return found as Record<string, unknown>;
}

describe("a task's form", () => {
  it("repeats its template once per item of the list in the inputs", () => {
    const form = triageForm();

    const root = form.root.kind === "column" ? form.root.children : [];
    expect(root.map((each) => each.kind)).toEqual(["text", "repeat", "row"]);
    expect(root[0]).toMatchObject({ text: "The review found 2 issues." });
    expect(root[1]).toMatchObject({ items: FOUND.issues, template: { kind: "card" } });
  });

  it("starts each control in the template with one value per item", () => {
    expect(triageForm().start).toEqual({ decision: ["", ""] });
    expect(triageForm({ review: { issues: [] } }).start).toEqual({ decision: [] });
  });

  it("knows the field its buttons set", () => {
    expect(triageForm().buttons).toBe("verdict");
    expect(read({ form: example }).buttons).toBeNull();
  });

  it.each([
    ["a string as it is", "title", 0, "Crash on empty input"],
    ["anything else as JSON", "detail", 1, '{"line":2}'],
    ["nothing as an empty text", "missing", 0, ""],
  ])("shows %s", (_name, field, index, text) => {
    expect(textOf({ item: [field] }, FOUND.issues[index])).toBe(text);
  });

  it.each([
    ["no inputs: a flow's form", undefined],
    ["no list where the template reads one", { review: { issues: "none" } }],
    ["no input there at all", {}],
  ])("answers null with %s", (_name, inputs) => {
    expect(formOf({ form: triage }, inputs)).toBeNull();
  });

  it.each([
    [
      "a relative text outside a template",
      (messages: typeof triage) => {
        triageComponent(messages, "summary").text = { path: "summary" };
      },
    ],
    [
      "a path from the root outside the inputs",
      (messages: typeof triage) => {
        triageComponent(messages, "summary").text = { path: "/verdict" };
      },
    ],
    [
      "a control in the template bound from the root",
      (messages: typeof triage) => {
        triageComponent(messages, "decision").value = { path: "/decision" };
      },
    ],
    [
      "buttons that set two fields",
      (messages: typeof triage) => {
        triageComponent(messages, "ready").action = {
          event: { name: "submit", context: { done: "yes" } },
        };
      },
    ],
    [
      "a display style it does not know",
      (messages: typeof triage) => {
        triageComponent(messages, "decision").displayStyle = "dial";
      },
    ],
  ])("answers null for %s", (_name, change) => {
    expect(formOf(triageChanged(change), { review: FOUND })).toBeNull();
  });

  it.each([
    ["Ready", "ready"],
    ["Re-work", "rework"],
  ])("submits a decision per item, and the value of %s", (_name, pressed) => {
    expect(answered(triageForm(), { decision: ["fix", "defer"] }, pressed)).toEqual({
      inputs: { decision: ["fix", "defer"], verdict: pressed },
    });
  });

  it("names the item whose choice is not made", () => {
    expect(answered(triageForm(), { decision: ["fix", ""] }, "ready")).toEqual({
      problem: "Decision, item 2: choose one",
    });
  });

  it("submits an empty list for a list with no items", () => {
    const empty = triageForm({ review: { summary: "", issues: [] } });

    expect(answered(empty, empty.start, "ready")).toEqual({
      inputs: { decision: [], verdict: "ready" },
    });
  });
});
