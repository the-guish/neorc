import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PAGE_SIZE, type Run } from "../api";
import { ACTIVE, CANCELLED, HELLO, SUCCEEDED, WORD_PICKER } from "../test/fixtures";
import { mockApi, renderWithClient } from "../test/render";
import { RunsPage } from "./RunsPage";

const FLOWS = { flows: [HELLO, WORD_PICKER] };

describe("RunsPage", () => {
  it("lists runs newest first with their status and times", async () => {
    mockApi({
      "/flows": FLOWS,
      "/runs?root_only=true&limit=50": { runs: [ACTIVE, SUCCEEDED] },
    });

    renderWithClient(<RunsPage />);

    const rows = await screen.findAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(rows[1]).toHaveTextContent("hello");
    expect(rows[1]?.querySelector("[data-status]")).toHaveAttribute(
      "data-status",
      "active",
    );
    expect(rows[2]).toHaveTextContent("word_picker");
    expect(rows[2]).toHaveTextContent("2.5 s");
    expect(screen.getByRole("link", { name: "word_picker 5f3a1c2e" })).toHaveAttribute(
      "href",
      `#/neorc/runs/${SUCCEEDED.id}`,
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("filters by flow and status through the address", async () => {
    const api = mockApi({
      "/flows": FLOWS,
      "/runs?flow=hello&status=cancelled&root_only=true&limit=50": {
        runs: [CANCELLED],
      },
    });

    renderWithClient(<RunsPage flow="hello" status="cancelled" />);

    await screen.findByText("cancelled");
    await screen.findByRole("option", { name: "hello" });
    expect(screen.getByLabelText(/Flow/)).toHaveValue("hello");
    expect(screen.getByLabelText(/Status/)).toHaveValue("cancelled");
    expect(api.calls.map((call) => call.url.pathname + call.url.search)).toContain(
      "/neorc/runs?flow=hello&status=cancelled&root_only=true&limit=50",
    );

    fireEvent.change(screen.getByLabelText(/Status/), { target: { value: "" } });
    expect(location.hash).toBe("#/neorc/runs?flow=hello");
  });

  it("pages with the last run as the cursor", async () => {
    const page: Run[] = Array.from({ length: PAGE_SIZE }, (_, index) => ({
      ...ACTIVE,
      id: `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
    }));
    const last = page[page.length - 1]?.id;
    mockApi({
      "/flows": FLOWS,
      "/runs?root_only=true&limit=50": { runs: page },
      [`/runs?root_only=true&before=${last}&limit=50`]: { runs: [SUCCEEDED] },
    });

    renderWithClient(<RunsPage />);

    const older = await screen.findByRole("button", { name: "Older runs" });
    fireEvent.click(older);

    await waitFor(() =>
      expect(screen.getAllByRole("row")).toHaveLength(PAGE_SIZE + 2),
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("says when there are no runs", async () => {
    mockApi({ "/flows": FLOWS, "/runs?root_only=true&limit=50": { runs: [] } });

    renderWithClient(<RunsPage />);

    expect(await screen.findByText("No runs yet.")).toBeInTheDocument();
  });
});
