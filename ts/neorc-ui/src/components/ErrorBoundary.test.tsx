import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ErrorBoundary } from "./ErrorBoundary";

function Broken(): never {
  throw new Error("no such thing");
}

describe("ErrorBoundary", () => {
  it("shows a render error instead of a blank page", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});

    render(
      <ErrorBoundary>
        <Broken />
      </ErrorBoundary>,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("no such thing");
    expect(screen.getByRole("link", { name: "Runs" })).toHaveAttribute("href", "#/neorc/runs");
  });
});
