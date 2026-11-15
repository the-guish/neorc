import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Flow, QueueKind, Task } from "../api";
import {
  APPROVAL,
  FOUND,
  REVIEW,
  REVIEW_ROUND,
  TITLE,
  TRIAGE,
  WAITING,
  delivered,
} from "../test/fixtures";
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

/**
 * The manager, for one task of a run of `flow`; what was posted to complete
 * it is recorded. `inputs` are the task's.
 */
function serve(
  task: Task,
  flow: Flow = APPROVAL,
  complete?: Reply,
  inputs?: Record<string, unknown>,
) {
  const posted: unknown[] = [];
  const deliver = (given: Task) =>
    inputs === undefined ? delivered(given) : { task: given, inputs };
  const api = mockApi({
    [`/tasks/${task.id}/inputs`]: () =>
      posted.length > 0 && complete === undefined
        ? deliver({ ...task, status: "succeeded", result: resultOf(posted[0]) })
        : deliver(task),
    [`/runs/${WAITING.id}`]: { ...WAITING, flow: flow.name, version: flow.version },
    [`/flows/${flow.name}/versions/${flow.version}`]: flow,
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
    <TaskPanel id={task.id} kind={kind} closeHref="#/neorc/queues/editors" />,
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
      `#/neorc/runs/${WAITING.id}`,
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

  it("draws a card per item of a list in the task's inputs", async () => {
    serve(TRIAGE, REVIEW_ROUND, undefined, { review: FOUND });
    open(TRIAGE);

    const form = await screen.findByRole("form", { name: "Complete triage" });
    expect(form).toHaveTextContent("The review found 2 issues.");
    const cards = within(form).getAllByRole("article");
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveTextContent("blocker");
    expect(within(cards[0]!).getByRole("heading")).toHaveTextContent(
      "Crash on empty input",
    );
    expect(cards[1]).toHaveTextContent("a.py:2");
    // A bar of three buttons in each card, none pressed to start with.
    const bar = within(cards[1]!).getByRole("group", { name: "Decision" });
    expect(within(bar).getAllByRole("button").map((each) => each.textContent)).toEqual([
      "Fix",
      "Defer",
      "Discard",
    ]);
    for (const each of within(bar).getAllByRole("button")) {
      expect(each).toHaveAttribute("aria-pressed", "false");
    }
    // The form's own buttons complete the task: there is no other.
    expect(within(form).queryByRole("button", { name: "Complete" })).toBeNull();
    expect(within(form).getByRole("button", { name: "Re-work" })).toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Ready" })).toHaveClass("neorc-primary");
  });

  it.each([
    ["Ready", "ready"],
    ["Re-work", "rework"],
  ])(
    "completes a triage with a decision per item and %s",
    async (button, verdict) => {
      const { posted } = serve(TRIAGE, REVIEW_ROUND, undefined, { review: FOUND });
      open(TRIAGE);

      const form = await screen.findByRole("form", { name: "Complete triage" });
      const cards = within(form).getAllByRole("article");
      for (const [card, decision] of [
        [0, "Fix"],
        [1, "Discard"],
      ] as const) {
        const bar = within(cards[card]!).getByRole("group", { name: "Decision" });
        fireEvent.click(within(bar).getByRole("button", { name: decision }));
        expect(within(bar).getByRole("button", { name: decision })).toHaveAttribute(
          "aria-pressed",
          "true",
        );
      }
      fireEvent.click(within(form).getByRole("button", { name: button }));

      await waitFor(() =>
        expect(posted).toEqual([{ result: { decision: ["fix", "discard"], verdict } }]),
      );
    },
  );

  it("needs a decision for every item, and sends nothing without", async () => {
    const { posted } = serve(TRIAGE, REVIEW_ROUND, undefined, { review: FOUND });
    open(TRIAGE);

    const form = await screen.findByRole("form", { name: "Complete triage" });
    const first = within(form).getAllByRole("group", { name: "Decision" })[0]!;
    fireEvent.click(within(first).getByRole("button", { name: "Defer" }));
    fireEvent.click(within(form).getByRole("button", { name: "Ready" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "Decision, item 2: choose one",
    );
    expect(posted).toEqual([]);
  });

  it("keeps the JSON editor, with its own Complete, beside a form's buttons", async () => {
    const { posted } = serve(TRIAGE, REVIEW_ROUND, undefined, { review: FOUND });
    open(TRIAGE);

    const form = await screen.findByRole("form", { name: "Complete triage" });
    fireEvent.click(within(form).getByRole("button", { name: "Edit as JSON" }));
    fireEvent.change(within(form).getByLabelText("Result as JSON"), {
      target: { value: '{"decision": ["fix", "fix"], "verdict": "ready"}' },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Complete" }));

    await waitFor(() =>
      expect(posted).toEqual([{ result: { decision: ["fix", "fix"], verdict: "ready" } }]),
    );
  });

  it("falls back to a text for a form whose list is not in the inputs", async () => {
    serve(TRIAGE, REVIEW_ROUND, undefined, { review: "no issues" });
    open(TRIAGE);

    const form = await screen.findByRole("form", { name: "Complete triage" });
    expect(within(form).getByLabelText("Answer")).toBeInTheDocument();
    expect(within(form).queryByRole("article")).toBeNull();
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
