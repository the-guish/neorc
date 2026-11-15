import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { loadSession, Shell, type ShellApp } from "@oneberth/ui";
import { afterEach, describe, expect, it } from "vitest";

import type { Flow } from "./api";
import type { FormSource } from "./context";
import { neorcApp } from "./index";
import { ACTIVE, HELLO, SIGNED_IN, SUCCEEDED, WORD_PICKER } from "./test/fixtures";
import { mockApi, neverAnswers, refusal } from "./test/render";

const OTHER = {
  ...ACTIVE,
  id: "0ddddddd-0000-4000-8000-000000000002",
  root_id: "0ddddddd-0000-4000-8000-000000000002",
};

/** A flow with one input, which the host has a form for. */
const SHIP: Flow = {
  name: "ship",
  version: "1.0.0",
  content: {
    name: "ship",
    version: "1.0.0",
    inputs: { repo: "string" },
    steps: { work: { handler: "tasks:work" } },
  },
};

/** The host's form for `ship`, in A2UI v0.9: a choice of two repositories. */
const SHIP_FORM = [
  {
    version: "v0.9",
    createSurface: { surfaceId: "ship", catalogId: "https://github.com/open-shipyard/neorc/forms/v1" },
  },
  {
    version: "v0.9",
    updateComponents: {
      surfaceId: "ship",
      components: [
        { id: "root", component: "Column", children: ["repo"] },
        {
          id: "repo",
          component: "ChoicePicker",
          label: "Repository",
          variant: "mutuallyExclusive",
          value: { path: "/repo" },
          options: [
            { label: "billing", value: "acme/billing" },
            { label: "search", value: "acme/search" },
          ],
        },
      ],
    },
  },
];

function routes(session: unknown = SIGNED_IN) {
  const shared: Record<string, unknown> = {
    "/auth/session": session,
    "/events/latest": { sequence: 0 },
    "/events": () => neverAnswers(),
    "/flows": { flows: [HELLO, SHIP] },
    "/runs?root_only=true&limit=6": { runs: [] },
    "/runs?root_only=true&limit=50": { runs: [ACTIVE] },
    "/flows/hello": HELLO,
    "/flows/hello/versions": { versions: [HELLO] },
    "/flows/hello/versions/0.1.0": HELLO,
    "/flows/ship": SHIP,
    "/flows/ship/versions": { versions: [SHIP] },
    "/flows/ship/versions/1.0.0": SHIP,
    "/flows/word_picker": WORD_PICKER,
    "/flows/word_picker/versions": { versions: [WORD_PICKER] },
    [`/flows/word_picker/versions/${WORD_PICKER.version}`]: WORD_PICKER,
  };
  for (const run of [ACTIVE, OTHER]) {
    shared[`/runs/${run.id}`] = run;
    shared[`/runs/${run.id}/tasks`] = { tasks: [] };
    shared[`/runs/${run.id}/sub-runs`] = { runs: [] };
  }
  return shared;
}

/** An application of the shell beside neorc, which draws one line. */
const NOTES: ShellApp = {
  name: "notes",
  title: "Notes",
  icon: <span />,
  load: async () => ({ default: () => <p>the notes</p> }),
};

/** neorc in oneberth's shell, as a host mounts it, once the session is read. */
async function renderInShell(options: { forms?: FormSource; beside?: ShellApp[] } = {}) {
  await loadSession();
  const apps = [neorcApp({ api: "/neorc/", forms: options.forms }), ...(options.beside ?? [])];
  return render(<Shell apps={apps} title="neorc" />);
}

afterEach(() => {
  history.replaceState(null, "", "/");
});

describe("neorc in the shell", () => {
  it("leaves a half-confirmed cancellation behind when moving to another run", async () => {
    mockApi(routes());
    location.hash = `#/neorc/runs/${ACTIVE.id}`;

    await renderInShell();
    fireEvent.click(await screen.findByRole("button", { name: "Cancel run" }));
    expect(screen.getByRole("button", { name: "Cancel it" })).toBeInTheDocument();

    location.hash = `#/neorc/runs/${OTHER.id}`;
    dispatchEvent(new HashChangeEvent("hashchange"));

    expect(await screen.findByRole("button", { name: "Cancel run" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel it" })).not.toBeInTheDocument();
  });

  it("says where an unknown address leads", async () => {
    mockApi(routes());
    location.hash = "#/neorc/nowhere";

    await renderInShell();

    expect(await screen.findByRole("alert")).toHaveTextContent("Nothing at #/neorc/nowhere");
  });

  it("moves through the shell: its links and its navigate change neorc's route", async () => {
    mockApi(routes());
    location.hash = "#/neorc/runs";

    await renderInShell();
    const rail = screen.getByRole("navigation", { name: "Applications" });
    expect(within(rail).getByRole("link", { name: "neorc" })).toHaveAttribute("aria-current", "page");

    const main = await screen.findByRole("navigation", { name: "Main" });
    expect(within(main).getByRole("link", { name: "Flows" })).toHaveAttribute("href", "#/neorc/flows");
    const filters = await screen.findByRole("form", { name: "Filters" });
    fireEvent.change(within(filters).getByLabelText(/Status/), { target: { value: "failed" } });

    await waitFor(() => expect(location.hash).toBe("#/neorc/runs?status=failed"));
    expect(within(filters).getByLabelText(/Status/)).toHaveValue("failed");
  });

  it("stops its long poll while the shell shows another application, and reads again when shown", async () => {
    const api = mockApi(routes());
    location.hash = "#/neorc/runs";
    const polls = () => api.calls.filter((call) => call.url.pathname === "/neorc/events");

    await renderInShell({ beside: [NOTES] });
    await waitFor(() => expect(polls()).toHaveLength(1));
    expect(polls()[0]?.init?.signal?.aborted).toBe(false);

    location.hash = "#/notes";
    dispatchEvent(new HashChangeEvent("hashchange"));
    expect(await screen.findByText("the notes")).toBeInTheDocument();
    await waitFor(() => expect(polls()[0]?.init?.signal?.aborted).toBe(true));
    expect(polls()).toHaveLength(1);

    location.hash = "#/neorc/runs";
    dispatchEvent(new HashChangeEvent("hashchange"));
    await waitFor(() => expect(polls()).toHaveLength(2));
    expect(polls()[1]?.init?.signal?.aborted).toBe(false);
  });

  it("shows the shell's sign-in page when the manager refuses the session of an open page", async () => {
    mockApi({
      ...routes(),
      [`/runs/${ACTIVE.id}/cancel`]: refusal(401, "Unauthorized", "the session is unknown, ended or expired"),
    });
    location.hash = `#/neorc/runs/${ACTIVE.id}`;

    await renderInShell();
    fireEvent.click(await screen.findByRole("button", { name: "Cancel run" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel it" }));

    expect(await screen.findByRole("link", { name: /Google/ })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
  });

  it("draws the host's form in the place of its own, and starts the run with its inputs", async () => {
    let posted: unknown;
    const asked: string[] = [];
    mockApi({
      ...routes(),
      "/flows/ship/runs": (_url: URL, init?: RequestInit) => {
        posted = JSON.parse(String(init?.body));
        return { ...SUCCEEDED, flow: "ship" };
      },
    });
    const forms: FormSource = async (flow) => {
      asked.push(`${flow.name}@${flow.version}`);
      return flow.name === "ship" ? SHIP_FORM : null;
    };
    location.hash = "#/neorc/flows/ship";

    await renderInShell({ forms });
    const start = await screen.findByRole("form", { name: "Start ship" });
    expect(await within(start).findByRole("radio", { name: "search" })).toBeInTheDocument();
    expect(within(start).queryByRole("textbox", { name: /repo/ })).not.toBeInTheDocument();
    fireEvent.click(within(start).getByRole("radio", { name: "search" }));
    fireEvent.click(within(start).getByRole("button", { name: "Start run" }));

    await waitFor(() => expect(location.hash).toBe(`#/neorc/runs/${SUCCEEDED.id}`));
    expect(posted).toEqual({ inputs: { repo: "acme/search" } });
    expect(asked).toEqual(["ship@1.0.0"]);
  });

  it("draws its own form for a flow the host has no form for", async () => {
    mockApi(routes());
    const forms: FormSource = (flow) => (flow.name === "ship" ? SHIP_FORM : null);
    location.hash = "#/neorc/flows/word_picker";

    await renderInShell({ forms });

    const start = await screen.findByRole("form", { name: "Start word_picker" });
    expect(within(start).getByLabelText(/sentence/)).toBeInTheDocument();
    expect(within(start).queryByRole("radio")).not.toBeInTheDocument();
  });

  it("says why it draws its own form when the host's cannot be drawn", async () => {
    mockApi(routes());
    const forms: FormSource = () => [{ version: "v0.9", updateComponents: { components: [{}] } }];
    location.hash = "#/neorc/flows/ship";

    await renderInShell({ forms });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The host's form for ship could not be drawn",
    );
    const start = screen.getByRole("form", { name: "Start ship" });
    expect(within(start).getByLabelText(/repo/)).toBeInTheDocument();
  });
});
