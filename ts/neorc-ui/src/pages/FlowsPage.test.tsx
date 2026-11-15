import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HELLO, WORD_PICKER } from "../test/fixtures";
import { mockApi, refusal, renderWithClient } from "../test/render";
import { FlowsPage } from "./FlowsPage";

describe("FlowsPage", () => {
  it("lists the latest version of every flow, linking to each", async () => {
    mockApi({ "/flows": { flows: [HELLO, WORD_PICKER] } });

    renderWithClient(<FlowsPage />);

    const rows = await screen.findAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(screen.getByRole("link", { name: "hello" })).toHaveAttribute(
      "href",
      "#/neorc/flows/hello",
    );
    expect(screen.getByRole("link", { name: "word_picker" })).toHaveAttribute(
      "href",
      "#/neorc/flows/word_picker",
    );
    expect(screen.getByText("1.2.0")).toBeInTheDocument();
  });

  it("says when nothing is uploaded yet", async () => {
    mockApi({ "/flows": { flows: [] } });

    renderWithClient(<FlowsPage />);

    expect(await screen.findByText(/No flows uploaded yet/)).toBeInTheDocument();
  });

  it("shows the manager's refusal", async () => {
    mockApi({ "/flows": refusal(503, "ManagerUnavailableError", "down") });

    renderWithClient(<FlowsPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load flows: down",
    );
  });
});
