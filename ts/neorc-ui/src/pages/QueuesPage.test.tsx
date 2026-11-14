import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { QUEUES } from "../test/fixtures";
import { mockApi, renderWithClient } from "../test/render";
import { QueuesPage } from "./QueuesPage";

describe("QueuesPage", () => {
  it("lists the declared queues with their kinds", async () => {
    mockApi({ "/queues": { queues: QUEUES } });

    renderWithClient(<QueuesPage />);

    const rows = await screen.findAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(rows[1]).toHaveTextContent("default");
    expect(rows[1]).toHaveTextContent("worker");
    expect(rows[2]).toHaveTextContent("external");
    expect(screen.getByRole("link", { name: "editors" })).toHaveAttribute(
      "href",
      "#/queues/editors",
    );
    expect(screen.getByLabelText(/Kind/)).toHaveValue("");
  });

  it("keeps the queues of the kind the address names, and filters through it", async () => {
    mockApi({ "/queues": { queues: QUEUES } });

    renderWithClient(<QueuesPage kind="external" />);

    const rows = await screen.findAllByRole("row");
    expect(rows).toHaveLength(2);
    expect(rows[1]).toHaveTextContent("editors");
    expect(screen.getByText("1 external queue")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/Kind/), { target: { value: "worker" } });
    expect(location.hash).toBe("#/queues?kind=worker");
    fireEvent.change(screen.getByLabelText(/Kind/), { target: { value: "" } });
    expect(location.hash).toBe("#/queues");
  });

  it("says so when no queue is declared", async () => {
    mockApi({ "/queues": { queues: [] } });

    renderWithClient(<QueuesPage />);

    expect(await screen.findByText("No queue is declared yet.")).toBeInTheDocument();
  });
});
