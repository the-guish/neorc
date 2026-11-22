import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Flow } from "../api";
import { StartRunForm } from "../components/StartRunForm";
import { FLOW_SEARCH_LIMIT } from "../queries";
import { SUCCEEDED } from "../test/fixtures";
import { mockApi, renderWithClient } from "../test/render";
import { answered, formOf, type Form } from "./subset";

// A choice of one of the manager's flows is drawn by a form, so these tests
// open "Start a run" on a flow whose form has one.

const CATALOG = "https://github.com/open-shipyard/neorc/forms/v1";

/** A form of one FlowPicker, bound to /target, with `changes` to it. */
function messages(changes: Record<string, unknown> = {}, start?: unknown): unknown[] {
  const form: unknown[] = [
    { version: "v0.9", createSurface: { surfaceId: "launcher", catalogId: CATALOG } },
    {
      version: "v0.9",
      updateComponents: {
        surfaceId: "launcher",
        components: [
          { id: "root", component: "Column", children: ["target"] },
          {
            id: "target",
            component: "FlowPicker",
            label: "Flow",
            value: { path: "/target" },
            checks: [
              {
                condition: { call: "required", args: { value: { path: "/target" } } },
                message: "Choose the flow to launch.",
              },
            ],
            ...changes,
          },
        ],
      },
    },
  ];
  if (start !== undefined) {
    form.push({
      version: "v0.9",
      updateDataModel: { surfaceId: "launcher", path: "/", value: start },
    });
  }
  return form;
}

function launcher(form: unknown[] = messages()): Flow {
  return {
    name: "launcher",
    version: "1.0.0",
    content: {
      name: "launcher",
      version: "1.0.0",
      inputs: { target: "string" },
      form,
      steps: { note: { handler: "tasks:note" } },
    },
  };
}

function flow(name: string, version = "1.0.0"): Flow {
  return { name, version, content: { name, version } };
}

const FLOWS = [flow("code_review"), flow("hello"), flow("review_loop", "2.1.0")];

/** The manager's listing, searched as the manager does; and the runs started. */
function serve(flows: Flow[] = FLOWS) {
  const posted: unknown[] = [];
  const api = mockApi({
    "/flows": (url: URL) => {
      const text = (url.searchParams.get("search") ?? "").toLowerCase();
      const limit = Number(url.searchParams.get("limit") ?? flows.length);
      const found = flows.filter((each) => each.name.toLowerCase().includes(text));
      const first = (each: Flow) => (each.name.toLowerCase().startsWith(text) ? 0 : 1);
      return { flows: found.sort((a, b) => first(a) - first(b)).slice(0, limit) };
    },
    "/flows/launcher/runs": (_url: URL, init?: RequestInit) => {
      posted.push(JSON.parse(String(init?.body)));
      return { ...SUCCEEDED, id: "11111111-2222-4333-8444-555555555555" };
    },
  });
  /** What the form asked the listing for, in order. */
  const searches = () =>
    api.calls
      .filter(({ url }) => url.pathname === "/neorc/flows")
      .map(({ url }) => url.search);
  return { posted, searches };
}

function box(): HTMLElement {
  return screen.getByRole("combobox", { name: "Flow" });
}

function type(text: string) {
  fireEvent.focus(box());
  fireEvent.change(box(), { target: { value: text } });
}

function start() {
  fireEvent.click(screen.getByRole("button", { name: "Start run" }));
}

function read(form: unknown[]): Form {
  const read = formOf({ form });
  if (read === null) throw new Error("the form was not read");
  return read;
}

describe("a form's choice of a flow, read", () => {
  it("is a control of its own, which starts with no flow chosen", () => {
    const form = read(messages());

    expect(form.root).toMatchObject({
      kind: "column",
      children: [{ kind: "flow", input: "target", label: "Flow", perItem: false }],
    });
    expect(form.start).toEqual({ target: "" });
  });

  it("starts from the flow the form's own data names", () => {
    expect(read(messages({}, { target: "hello" })).start).toEqual({ target: "hello" });
  });

  it("is not read without a label, a field to fill, or checks it knows", () => {
    expect(formOf({ form: messages({ label: 3 }) })).toBeNull();
    expect(formOf({ form: messages({ value: { path: "/a/b" } }) })).toBeNull();
    expect(formOf({ form: messages({ checks: [{ message: "No." }] }) })).toBeNull();
  });

  it("submits the name of the flow chosen, and refuses a choice left unmade", () => {
    const form = read(messages());

    expect(answered(form, { target: "hello" })).toEqual({ inputs: { target: "hello" } });
    expect(answered(form, { target: "" })).toEqual({
      problem: "Flow: Choose the flow to launch.",
    });
    expect(answered(read(messages({ checks: [] })), { target: "" })).toEqual({
      problem: "Flow: choose a flow",
    });
  });
});

describe("a form's choice of a flow, drawn", () => {
  it("lists the manager's flows once the box has the focus", async () => {
    const { searches } = serve();

    renderWithClient(<StartRunForm flow={launcher()} />);
    expect(searches()).toEqual([]);
    fireEvent.focus(box());

    const options = await screen.findAllByRole("option");
    expect(options.map((each) => each.textContent)).toEqual([
      "code_review 1.0.0",
      "hello 1.0.0",
      "review_loop 2.1.0",
    ]);
    expect(box()).toHaveAttribute("aria-expanded", "true");
    expect(searches()).toEqual([`?search=&limit=${FLOW_SEARCH_LIMIT}`]);
  });

  it("asks the manager for what is typed, once the typing rests", async () => {
    const { searches } = serve();

    renderWithClient(<StartRunForm flow={launcher()} />);
    type("r");
    type("re");
    type("rev");

    // Not once per letter: the list first shown, then the text as it rested.
    await waitFor(() =>
      expect(searches()).toEqual([
        `?search=&limit=${FLOW_SEARCH_LIMIT}`,
        `?search=rev&limit=${FLOW_SEARCH_LIMIT}`,
      ]),
    );
    // The manager's answer, as it lists them: those that start with the text
    // first.
    await waitFor(() =>
      expect(screen.getByRole("listbox")).toHaveAttribute("aria-busy", "false"),
    );
    expect(screen.getAllByRole("option").map((each) => each.textContent)).toEqual([
      "review_loop 2.1.0",
      "code_review 1.0.0",
    ]);
  });

  it("starts a run with the flow picked from the list", async () => {
    const { posted } = serve();

    renderWithClient(<StartRunForm flow={launcher()} />);
    type("rev");
    fireEvent.click(await screen.findByRole("option", { name: /code_review/ }));

    expect(box()).toHaveValue("code_review");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    start();
    await waitFor(() => expect(posted).toEqual([{ inputs: { target: "code_review" } }]));
  });

  it("picks with the keys: the first flow found, or the one the arrows are on", async () => {
    const { posted } = serve();

    renderWithClient(<StartRunForm flow={launcher()} />);
    type("rev");
    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(2));
    // Enter would pick the first flow found; the arrow moves to the next.
    expect(box()).toHaveAttribute(
      "aria-activedescendant",
      screen.getByRole("option", { name: /review_loop/ }).id,
    );
    fireEvent.keyDown(box(), { key: "ArrowDown" });
    expect(box()).toHaveAttribute(
      "aria-activedescendant",
      screen.getByRole("option", { name: /code_review/ }).id,
    );
    fireEvent.keyDown(box(), { key: "ArrowUp" });
    fireEvent.keyDown(box(), { key: "Enter" });

    expect(box()).toHaveValue("review_loop");
    expect(posted).toEqual([]);
    start();
    await waitFor(() => expect(posted).toEqual([{ inputs: { target: "review_loop" } }]));
  });

  it("narrows the list it has at once, before the manager answers for the text", async () => {
    const { posted, searches } = serve();

    renderWithClient(<StartRunForm flow={launcher()} />);
    fireEvent.focus(box());
    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(3));
    type("REV");

    // The manager was not asked yet: these are the flows it listed before
    // that hold the text, in its own order, those that start with it first.
    expect(searches()).toHaveLength(1);
    expect(screen.getAllByRole("option").map((each) => each.textContent)).toEqual([
      "review_loop 2.1.0",
      "code_review 1.0.0",
    ]);
    expect(screen.getByRole("listbox")).toHaveAttribute("aria-busy", "true");
    // So Enter picks a flow the person sees, and never one of another text.
    fireEvent.keyDown(box(), { key: "Enter" });
    expect(box()).toHaveValue("review_loop");
    expect(posted).toEqual([]);
  });

  it("keeps Enter from the form while the manager's answer is on its way", async () => {
    serve();

    renderWithClient(<StartRunForm flow={launcher()} />);
    fireEvent.focus(box());
    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(3));
    type("nothing like it");

    // No flow listed before holds the text: there is no list, and the form
    // is not submitted with a text the manager has yet to answer for.
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Searching…");
    expect(fireEvent.keyDown(box(), { key: "Enter" })).toBe(false);
    // Once the manager said that no flow matches, Enter is the form's again.
    expect(await screen.findByText("No flow matches.")).toBeInTheDocument();
    expect(fireEvent.keyDown(box(), { key: "Enter" })).toBe(true);
  });

  it("takes a name typed in full as the flow chosen", async () => {
    const { posted } = serve();

    renderWithClient(<StartRunForm flow={launcher()} />);
    type("hello");

    // No click and no key: the name is one of the flows the manager listed,
    // so nothing is left to say, and no list is left to pick from.
    expect(screen.getByRole("status")).toHaveTextContent("Searching…");
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    start();
    await waitFor(() => expect(posted).toEqual([{ inputs: { target: "hello" } }]));
  });

  it("looks up a name typed in full after the box is left", async () => {
    // More flows than one listing holds, so the first list lacks the last.
    const many = Array.from({ length: FLOW_SEARCH_LIMIT + 1 }, (_, n) => flow(`flow_${n}`));
    const last = many[many.length - 1]!.name;
    const { posted, searches } = serve(many);

    renderWithClient(<StartRunForm flow={launcher()} />);
    type(last);
    fireEvent.blur(box());

    // The box was left before the manager answered for the name: it is asked
    // all the same, and nothing is left to say once the name is a flow's.
    await waitFor(() =>
      expect(searches()).toContain(`?search=${last}&limit=${FLOW_SEARCH_LIMIT}`),
    );
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
    start();
    await waitFor(() => expect(posted).toEqual([{ inputs: { target: last } }]));
  });

  it("says that no flow is chosen once the box is left with part of a name", async () => {
    const { posted } = serve();

    renderWithClient(<StartRunForm flow={launcher()} />);
    type("hel");
    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(1));
    fireEvent.blur(box());

    expect(screen.getByRole("status")).toHaveTextContent(
      "No flow is chosen: pick one from the list.",
    );
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    start();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Flow: Choose the flow to launch.",
    );
    expect(posted).toEqual([]);
  });

  it("starts nothing with a text that names no flow", async () => {
    const { posted } = serve();

    renderWithClient(<StartRunForm flow={launcher()} />);
    type("nothing like it");
    expect(await screen.findByText("No flow matches.")).toBeInTheDocument();
    // Said under the box, and no list over what follows it.
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(box()).toHaveAttribute("aria-expanded", "false");
    start();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Flow: Choose the flow to launch.",
    );
    expect(posted).toEqual([]);
  });

  it("has no flow chosen once the name picked is typed over", async () => {
    const { posted } = serve();

    renderWithClient(<StartRunForm flow={launcher()} />);
    type("hel");
    fireEvent.click(await screen.findByRole("option", { name: /hello/ }));
    type("hello there");
    start();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Flow: Choose the flow to launch.",
    );
    expect(posted).toEqual([]);
  });

  it("starts from the flow the form names, and asks nothing for it", () => {
    const { searches } = serve();

    renderWithClient(<StartRunForm flow={launcher(messages({}, { target: "hello" }))} />);

    expect(box()).toHaveValue("hello");
    expect(searches()).toEqual([]);
  });

  it("closes the list on Escape and keeps what was typed", async () => {
    serve();

    renderWithClient(<StartRunForm flow={launcher()} />);
    type("rev");
    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(2));
    fireEvent.keyDown(box(), { key: "Escape" });

    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(box()).toHaveValue("rev");
    expect(box()).toHaveAttribute("aria-expanded", "false");
  });

  it("says so when the manager cannot be asked", async () => {
    mockApi({
      "/flows": { status: 503, error: "ManagerNotReady", detail: "the manager is starting" },
    });

    renderWithClient(<StartRunForm flow={launcher()} />);
    fireEvent.focus(box());

    expect(
      await screen.findByText("The flows could not be searched: the manager is starting"),
    ).toBeInTheDocument();
  });
});
