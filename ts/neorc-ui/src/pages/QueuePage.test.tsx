import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { APPROVAL, HELLO, QUEUES, REVIEW, TITLE, delivered, listed } from "../test/fixtures";
import { mockApi, renderWithClient } from "../test/render";
import { QueuePage } from "./QueuePage";

const NOW = new Date("2026-09-13T12:00:00Z");
const RECENT = "/queues/editors/tasks/recent";

function routes(tasks = [listed(TITLE), listed(REVIEW)]) {
  return {
    "/queues": { queues: QUEUES },
    "/flows": { flows: [APPROVAL, HELLO] },
    [RECENT]: { tasks },
  };
}

function asked(api: ReturnType<typeof mockApi>): URLSearchParams[] {
  return api.calls
    .filter((call) => call.url.pathname === RECENT)
    .map((call) => call.url.searchParams);
}

describe("QueuePage", () => {
  afterEach(() => vi.useRealTimers());

  it("lists the pending tasks that changed in the last 24 hours, unless told", async () => {
    vi.useFakeTimers({ now: NOW, toFake: ["Date"] });
    const api = mockApi(routes());

    renderWithClient(<QueuePage name="queue" queue="editors" />);

    const rows = await screen.findAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(rows[1]).toHaveTextContent("suggest_title");
    expect(rows[1]).toHaveTextContent("approval 1.0.0");
    expect(rows[1]?.querySelector("[data-status]")).toHaveAttribute(
      "data-status",
      "pending",
    );
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "editors external",
    );
    expect(
      screen.getByText(/2 pending tasks changed in the last 24 hours\./),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/State/)).toHaveValue("pending");
    expect(screen.getByLabelText(/^Flow/)).toHaveValue("");
    expect(screen.getByLabelText(/Changed in the last/)).toHaveValue("24h");
    const [params] = asked(api);
    expect(params?.get("status")).toBe("pending");
    expect(params?.get("flow")).toBeNull();
    expect(params?.get("since")).toBe("2026-09-12T12:00:00.000Z");
    expect(params?.get("limit")).toBe("500");
  });

  it("asks for every state, one flow and a year back when the address says so", async () => {
    vi.useFakeTimers({ now: NOW, toFake: ["Date"] });
    const api = mockApi(routes([]));

    renderWithClient(
      <QueuePage name="queue" queue="editors" status="all" flow="approval" window="1y" />,
    );

    expect(
      await screen.findByText("No task of approval changed in the last 1 year."),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/State/)).toHaveValue("all");
    const [params] = asked(api);
    expect(params?.get("status")).toBeNull();
    expect(params?.get("flow")).toBe("approval");
    expect(params?.get("since")).toBe("2025-09-13T12:00:00.000Z");
  });

  it.each([
    [/State/, "all", "#/queues/editors?status=all"],
    [/State/, "failed", "#/queues/editors?status=failed"],
    [/^Flow/, "approval", "#/queues/editors?flow=approval"],
    [/Changed in the last/, "7d", "#/queues/editors?window=7d"],
    [/Changed in the last/, "30d", "#/queues/editors?window=30d"],
  ])("filters through the address: %s to %s", async (label, value, hash) => {
    mockApi(routes());
    renderWithClient(<QueuePage name="queue" queue="editors" />);
    await screen.findByRole("option", { name: "approval" });

    fireEvent.change(screen.getByLabelText(label), { target: { value } });

    expect(location.hash).toBe(hash);
  });

  it("opens the task the address names beside the list", async () => {
    mockApi({
      ...routes(),
      [`/tasks/${TITLE.id}/inputs`]: delivered(TITLE),
    });

    renderWithClient(<QueuePage name="queue" queue="editors" task={TITLE.id} />);

    const panel = await screen.findByRole("complementary", { name: "Task" });
    await waitFor(() => expect(panel).toHaveTextContent("A first draft about tea."));
    expect(within(panel).getByRole("link", { name: "Close" })).toHaveAttribute(
      "href",
      "#/queues/editors",
    );
    const row = (await screen.findAllByRole("row"))[1];
    expect(row).toHaveAttribute("aria-selected", "true");
    expect(
      screen.getByRole("link", { name: /review_draft/ }),
    ).toHaveAttribute("href", `#/queues/editors?task=${REVIEW.id}`);
  });
});
