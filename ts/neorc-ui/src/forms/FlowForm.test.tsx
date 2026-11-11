import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Flow } from "../api";
import { StartRunForm } from "../components/StartRunForm";
import { SUCCEEDED } from "../test/fixtures";
import { mockApi, refusal, renderWithClient } from "../test/render";
import example from "./code_task.form.json";

// A flow's form is drawn by "Start a run", so that is what these tests open.

const CODE_TASK: Flow = {
  name: "code_task",
  version: "1.0.0",
  content: {
    name: "code_task",
    version: "1.0.0",
    inputs: {
      description: "string",
      repo: "string",
      model: "string",
      attempts: "number",
      draft: "boolean",
    },
    form: example,
    steps: { plan: { handler: "tasks:plan" } },
  },
};

function recordStarts() {
  const posted: unknown[] = [];
  mockApi({
    "/flows/code_task/runs": (_url: URL, init?: RequestInit) => {
      posted.push(JSON.parse(String(init?.body)));
      return { ...SUCCEEDED, id: "11111111-2222-4333-8444-555555555555" };
    },
  });
  return posted;
}

function fillIn() {
  fireEvent.change(screen.getByLabelText("Task description"), {
    target: { value: "Add a retry to the upload step." },
  });
  fireEvent.change(screen.getByLabelText("Repository"), {
    target: { value: "acme/search" },
  });
  fireEvent.click(screen.getByLabelText("Large"));
  fireEvent.change(screen.getByLabelText("Attempts"), { target: { value: "3" } });
  fireEvent.click(screen.getByLabelText("Open the pull request as a draft"));
}

describe("a flow's form", () => {
  it("is drawn in the place of one field per input", () => {
    renderWithClient(<StartRunForm flow={CODE_TASK} />);

    expect(
      screen.getByText("Describe a change, and where and how it should be made."),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Task description").tagName).toBe("TEXTAREA");
    // Six repositories are a list to pick from; three models are radio buttons.
    const repositories = screen.getByLabelText("Repository");
    expect(repositories.tagName).toBe("SELECT");
    expect(within(repositories).getAllByRole("option")).toHaveLength(7);
    const models = screen.getByRole("group", { name: "Model" });
    expect(within(models).getAllByRole("radio")).toHaveLength(3);
    expect(screen.getByLabelText("Open the pull request as a draft")).toHaveProperty(
      "type",
      "checkbox",
    );
  });

  it("starts from the form's own data", () => {
    renderWithClient(<StartRunForm flow={CODE_TASK} />);

    expect(screen.getByLabelText("Medium")).toBeChecked();
    expect(screen.getByLabelText("Attempts")).toHaveValue("2");
    expect(screen.getByLabelText("Open the pull request as a draft")).toBeChecked();
    expect(screen.getByLabelText("Repository")).toHaveValue("");
  });

  it("starts a run with what was filled in, each input of its declared type", async () => {
    const posted = recordStarts();

    renderWithClient(<StartRunForm flow={CODE_TASK} />);
    fillIn();
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));

    await waitFor(() =>
      expect(location.hash).toBe("#/runs/11111111-2222-4333-8444-555555555555"),
    );
    expect(posted).toEqual([
      {
        inputs: {
          description: "Add a retry to the upload step.",
          repo: "acme/search",
          model: "large",
          attempts: 3,
          draft: false,
        },
      },
    ]);
  });

  it("says what the form says, and starts nothing, when a check is broken", async () => {
    const posted = recordStarts();

    renderWithClient(<StartRunForm flow={CODE_TASK} />);
    fillIn();
    fireEvent.change(screen.getByLabelText("Task description"), {
      target: { value: "short" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Task description: Describe the task in at least 10 characters.",
    );
    expect(posted).toEqual([]);
  });

  it("shows the manager's refusal as it came", async () => {
    mockApi({
      "/flows/code_task/runs": refusal(
        422,
        "InvalidValueError",
        "code_task: input 'repo' is not one of acme/billing",
      ),
    });

    renderWithClient(<StartRunForm flow={CODE_TASK} />);
    fillIn();
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "input 'repo' is not one of acme/billing",
    );
  });

  it("leaves the JSON editor one click away", async () => {
    const posted = recordStarts();

    renderWithClient(<StartRunForm flow={CODE_TASK} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit as JSON" }));
    fireEvent.change(screen.getByLabelText("Inputs as JSON"), {
      target: { value: '{"repo": "acme/billing"}' },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));

    await waitFor(() => expect(posted).toEqual([{ inputs: { repo: "acme/billing" } }]));
  });

  it("falls back to one field per input for a form this page cannot read", () => {
    const content = structuredClone(CODE_TASK.content) as { form: typeof example };
    content.form[1]!.updateComponents!.components[1]!.component = "Video";

    renderWithClient(<StartRunForm flow={{ ...CODE_TASK, content }} />);

    expect(screen.getByLabelText(/^description/).tagName).toBe("INPUT");
    expect(screen.queryByText("Task description")).not.toBeInTheDocument();
  });
});
