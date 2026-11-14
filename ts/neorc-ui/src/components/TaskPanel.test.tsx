import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Flow, QueueKind, Task } from "../api";
import { APPROVAL, REVIEW, TITLE, WAITING, delivered } from "../test/fixtures";
import { mockApi, refusal, renderWithClient, type Reply } from "../test/render";
import { TaskPanel } from "./TaskPanel";

// A flow whose loop ends on a person's yes: its exit condition has no form.
const ROUNDS: Flow = {
  name: "approval",
  version: "1.0.0",
  content: {
    name: "approval",
    version: "1.0.0",
    steps: {
      rounds: {
        loop: { max_cycles: 3, exit_condition: "good_enough" },
        steps: { good_enough: { queue: "editors", handler: "good-enough" } },
      },
    },
  },
};

const ENOUGH: Task = {
  ...TITLE,
  address: { step: "good_enough", scope: [["rounds", 1]] },
  handler: "good-enough",
};

/** The manager, for one task; what was posted to complete it is recorded. */
function serve(task: Task, flow: Flow = APPROVAL, complete?: Reply) {
  const posted: unknown[] = [];
  const api = mockApi({
    [`/tasks/${task.id}/inputs`]: () =>
      posted.length > 0 && complete === undefined
        ? delivered({ ...task, status: "succeeded", result: resultOf(posted[0]) })
        : delivered(task),
    [`/runs/${WAITING.id}`]: WAITING,
    "/flows/approval/versions/1.0.0": flow,
    [`/tasks/${task.id}/complete`]: (_url: URL, init: RequestInit | undefined) => {
      posted.push(JSON.parse(String(init?.body)));
      return complete;
    },
  });
  return { api, posted };
}

function resultOf(body: unknown): unknown {
  return (body as { result?: unknown }).result ?? null;
}

function open(task: Task, kind: QueueKind | undefined = "external") {
  return renderWithClient(
    <TaskPanel id={task.id} kind={kind} closeHref="#/queues/editors" />,
  );
}

describe("TaskPanel", () => {
  it("shows a task with what it asks for and its inputs", async () => {
    serve(REVIEW);

    open(REVIEW);

    const panel = screen.getByRole("complementary", { name: "Task" });
    await waitFor(() => expect(panel).toHaveTextContent("review-draft"));
    expect(within(panel).getByRole("heading", { level: 2 })).toHaveTextContent(
      "review_draft pending",
    );
    expect(panel).toHaveTextContent("A first draft about tea.");
    expect(within(panel).getByRole("link", { name: "ed170000" })).toHaveAttribute(
      "href",
      `#/runs/${WAITING.id}`,
    );
  });

  it("completes a task with the fields of its step's form", async () => {
    const { posted } = serve(REVIEW);
    open(REVIEW);

    const form = await screen.findByRole("form", { name: "Complete review_draft" });
    expect(within(form).getByText("Read the draft above, then decide.")).toBeInTheDocument();
    expect(within(form).getByLabelText("Approve")).toBeChecked();
    fireEvent.click(within(form).getByLabelText("Ask for changes"));
    fireEvent.change(within(form).getByLabelText("Comment for the author"), {
      target: { value: "Shorter, please." },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Complete" }));

    await waitFor(() =>
      expect(posted).toEqual([
        { result: { decision: "changes", comment: "Shorter, please." } },
      ]),
    );
    // Read again, the task has ended: its result, and no form.
    const panel = screen.getByRole("complementary", { name: "Task" });
    await waitFor(() => expect(panel).toHaveTextContent("succeeded"));
    expect(screen.queryByRole("form")).not.toBeInTheDocument();
  });

  it("says a broken check of the form and sends nothing", async () => {
    const { posted } = serve(REVIEW);
    open(REVIEW);

    const form = await screen.findByRole("form", { name: "Complete review_draft" });
    fireEvent.change(within(form).getByLabelText("Comment for the author"), {
      target: { value: "x".repeat(501) },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Complete" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "Comment for the author: Keep the comment under 500 characters.",
    );
    expect(posted).toEqual([]);
  });

  it("completes a task with no form with a text", async () => {
    const { posted } = serve(TITLE);
    open(TITLE);

    const form = await screen.findByRole("form", { name: "Complete suggest_title" });
    fireEvent.click(within(form).getByRole("button", { name: "Complete" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("Write an answer.");
    fireEvent.change(within(form).getByLabelText("Answer"), {
      target: { value: "On tea" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Complete" }));

    await waitFor(() => expect(posted).toEqual([{ result: "On tea" }]));
  });

  it("asks a loop's exit condition for a yes or a no", async () => {
    const { posted } = serve(ENOUGH, ROUNDS);
    open(ENOUGH);

    const form = await screen.findByRole("form", { name: "Complete good_enough" });
    fireEvent.click(within(form).getByRole("button", { name: "Complete" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("Choose yes or no.");
    fireEvent.click(within(form).getByLabelText("No"));
    fireEvent.click(within(form).getByRole("button", { name: "Complete" }));

    await waitFor(() => expect(posted).toEqual([{ result: false }]));
  });

  it("takes any JSON in the place of the form", async () => {
    const { posted } = serve(TITLE);
    open(TITLE);

    const form = await screen.findByRole("form", { name: "Complete suggest_title" });
    fireEvent.click(within(form).getByRole("button", { name: "Edit as JSON" }));
    fireEvent.change(within(form).getByLabelText("Result as JSON"), {
      target: { value: "{not json" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Complete" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("not valid JSON");
    fireEvent.change(within(form).getByLabelText("Result as JSON"), {
      target: { value: '{"titles": ["On tea", "Tea"]}' },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Complete" }));

    await waitFor(() =>
      expect(posted).toEqual([{ result: { titles: ["On tea", "Tea"] } }]),
    );
  });

  it("fails a task with a reason", async () => {
    const { posted } = serve(TITLE);
    open(TITLE);

    const form = await screen.findByRole("form", { name: "Complete suggest_title" });
    fireEvent.click(within(form).getByRole("button", { name: "Fail the task" }));
    fireEvent.click(within(form).getByRole("button", { name: "Fail task" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "Say why the task fails.",
    );
    fireEvent.change(within(form).getByLabelText("Reason"), {
      target: { value: "No title fits." },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Fail task" }));

    await waitFor(() => expect(posted).toEqual([{ error: "No title fits." }]));
  });

  it("shows the manager's refusal as it came", async () => {
    serve(
      TITLE,
      APPROVAL,
      refusal(409, "TaskStateError", "a succeeded task cannot be completed"),
    );
    open(TITLE);

    const form = await screen.findByRole("form", { name: "Complete suggest_title" });
    fireEvent.change(within(form).getByLabelText("Answer"), {
      target: { value: "On tea" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Complete" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "a succeeded task cannot be completed",
    );
  });

  it("has no form for a task whose run has ended, and says how it ended", async () => {
    mockApi({
      [`/tasks/${REVIEW.id}/inputs`]: delivered(REVIEW),
      [`/runs/${WAITING.id}`]: {
        ...WAITING,
        status: "cancelled",
        reason: "approval 1.1.0 was uploaded",
      },
    });

    open(REVIEW);

    expect(
      await screen.findByText(
        "Its run ended (cancelled: approval 1.1.0 was uploaded). Nothing can complete this task.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("form")).not.toBeInTheDocument();
  });

  it("has no form for a task of a worker queue, or one that has ended", async () => {
    serve(TITLE);
    const first = open(TITLE, "worker");
    expect(
      await screen.findByText("This task waits for a worker of its queue."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("form")).not.toBeInTheDocument();
    first.unmount();

    const ended: Task = { ...TITLE, status: "failed", error: "No title fits." };
    serve(ended);
    open(ended);
    expect(await screen.findByText("No title fits.")).toBeInTheDocument();
    expect(screen.queryByRole("form")).not.toBeInTheDocument();
  });
});
