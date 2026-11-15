import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Flow } from "../api";
import { SUCCEEDED, WORD_PICKER } from "../test/fixtures";
import { mockApi, refusal, renderWithClient } from "../test/render";
import { bodyOf, inputsOf, jsonBodyOf, StartRunForm } from "./StartRunForm";

const TYPED: Flow = {
  name: "typed",
  version: "1.0.0",
  content: {
    name: "typed",
    version: "1.0.0",
    inputs: { word: "string", count: "number", loud: "boolean", when: "datetime" },
    steps: { work: { handler: "tasks:work" } },
  },
};

describe("StartRunForm", () => {
  it("starts a run with the fields' values as their declared types", async () => {
    let posted: unknown;
    mockApi({
      "/flows/typed/runs": (_url: URL, init?: RequestInit) => {
        posted = JSON.parse(String(init?.body));
        return { ...SUCCEEDED, id: "11111111-2222-4333-8444-555555555555" };
      },
    });

    renderWithClient(<StartRunForm flow={TYPED} />);
    fireEvent.change(screen.getByLabelText(/word/), { target: { value: "hi" } });
    fireEvent.change(screen.getByLabelText(/count/), { target: { value: "2.5" } });
    fireEvent.change(screen.getByLabelText(/loud/), { target: { value: "true" } });
    fireEvent.change(screen.getByLabelText(/when/), {
      target: { value: "2026-09-13T10:30" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));

    await waitFor(() =>
      expect(location.hash).toBe("#/neorc/runs/11111111-2222-4333-8444-555555555555"),
    );
    expect(posted).toMatchObject({
      inputs: { word: "hi", count: 2.5, loud: true },
    });
    const when = (posted as { inputs: { when: { $datetime: string } } }).inputs.when;
    expect(when.$datetime).toMatch(/^2026-09-13T10:30:00[+-]\d\d:\d\d$/);
  });

  it("does not open the run once the reader has left the page", async () => {
    let answer: (run: unknown) => void = () => {};
    mockApi({
      "/flows/word_picker/runs": () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    });

    const view = renderWithClient(<StartRunForm flow={WORD_PICKER} />);
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));
    await screen.findByRole("button", { name: "Starting…" });
    view.unmount();
    answer(SUCCEEDED);
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(location.hash).toBe("");
  });

  it("shows the manager's refusal next to the form", async () => {
    mockApi({
      "/flows/word_picker/runs": refusal(
        422,
        "InvalidValueError",
        "word_picker: input 'sentence' is not a string: 1",
      ),
    });

    renderWithClient(<StartRunForm flow={WORD_PICKER} />);
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "input 'sentence' is not a string",
    );
    expect(location.hash).toBe("");
  });

  it("refuses what a field cannot send, before asking the manager", () => {
    const api = mockApi({});

    renderWithClient(<StartRunForm flow={TYPED} />);
    fireEvent.change(screen.getByLabelText(/count/), { target: { value: "many" } });
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));

    expect(screen.getByRole("alert")).toHaveTextContent("count needs a number");
    expect(api.calls).toHaveLength(0);
  });

  it("takes raw JSON for what the fields cannot say", async () => {
    let posted: unknown;
    mockApi({
      "/flows/word_picker/runs": (_url: URL, init?: RequestInit) => {
        posted = JSON.parse(String(init?.body));
        return SUCCEEDED;
      },
    });

    renderWithClient(<StartRunForm flow={WORD_PICKER} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit as JSON" }));
    const editor = screen.getByLabelText("Inputs as JSON");
    fireEvent.change(editor, { target: { value: "[1, 2" } });
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));
    expect(screen.getByRole("alert")).toHaveTextContent("not valid JSON");

    fireEvent.change(editor, { target: { value: '{"sentence": "a b", "extra": [1]}' } });
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));

    await waitFor(() => expect(posted).toEqual({ inputs: { sentence: "a b", extra: [1] } }));
  });
});

describe("the inputs a form builds", () => {
  it("reads the declared inputs, unknown types as strings", () => {
    expect(inputsOf(TYPED.content)).toEqual({
      word: "string",
      count: "number",
      loud: "boolean",
      when: "datetime",
    });
    expect(inputsOf({ inputs: { odd: "list" } })).toEqual({ odd: "string" });
    expect(inputsOf({})).toEqual({});
    expect(inputsOf(null)).toEqual({});
  });

  it("types each value, and names the first that cannot be typed", () => {
    const inputs = inputsOf(TYPED.content);
    expect(
      bodyOf(inputs, { word: "x", count: "3", loud: "false", when: "2026-01-01T00:00" }),
    ).toMatchObject({ inputs: { word: "x", count: 3, loud: false } });
    expect(bodyOf(inputs, { word: "x", count: "", loud: "", when: "" })).toEqual({
      problem: "count needs a number",
    });
    expect(bodyOf(inputs, { word: "x", count: "1", loud: "", when: "" })).toEqual({
      problem: "when needs a date and time",
    });
    expect(jsonBodyOf("[]")).toEqual({ problem: "the inputs must be a JSON object" });
    expect(jsonBodyOf('{"a": 1}')).toEqual({ inputs: { a: 1 } });
  });
});
