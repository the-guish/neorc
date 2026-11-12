import { describe, expect, it } from "vitest";

import example from "./code_task.form.json";
import { answered, formOf, type Answers, type Form } from "./subset";

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
