import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Task } from "../api";
import { published, stepsOf } from "../definition";
import { BOOKING, SPLIT } from "../test/fixtures";
import { RunTree } from "./RunTree";

const task = (step: string, result: unknown = null): Task => ({
  ...SPLIT,
  id: `7a7a7a7a-0000-4000-8000-${step.padStart(12, "0").slice(0, 12)}`,
  address: { step, scope: [] },
  handler: `tasks:${step}`,
  result,
});

function outline(...tasks: Task[]) {
  const steps = stepsOf(BOOKING.content);
  return render(<RunTree steps={steps} scope={[]} done={published(tasks, [])} />);
}

describe("RunTree on a switch", () => {
  it("lists every case of a switch with its steps, none chosen before the run says", () => {
    outline(task("load_booking"));

    const isVip = screen.getByRole("region", { name: "route case is_vip" });
    expect(within(isVip).getByRole("heading")).toHaveTextContent(
      "case is_vip · not chosen yet · output tasks.send_gift",
    );
    expect(within(isVip).getByText("compute_benefit")).toBeInTheDocument();
    expect(within(isVip).getByText("send_gift")).toBeInTheDocument();
    const notVip = screen.getByRole("region", { name: "route case not_vip" });
    expect(within(notVip).getByRole("heading")).toHaveTextContent(
      "case not_vip · not chosen yet · no output",
    );
    expect(within(notVip).getByText("no steps")).toBeInTheDocument();
    expect(screen.getByText("tasks.check_vip")).toBeInTheDocument();
  });

  it("says which case the run chose, with what its steps did", () => {
    outline(
      task("load_booking"),
      task("check_vip", "is_vip"),
      task("compute_benefit", "champagne"),
    );

    const isVip = screen.getByRole("region", { name: "route case is_vip" });
    // The first heading is the case's; a task's details have their own.
    expect(within(isVip).getAllByRole("heading")[0]).toHaveTextContent("case is_vip · chosen");
    expect(isVip).not.toHaveClass("neorc-not-yet");
    // A step of the case is at the switch's own scope: its task is found there.
    expect(within(isVip).getAllByText(/succeeded/).length).toBeGreaterThan(0);
    expect(within(isVip).getByText("not published")).toBeInTheDocument();
    const notVip = screen.getByRole("region", { name: "route case not_vip" });
    expect(within(notVip).getByRole("heading")).toHaveTextContent("case not_vip · not chosen");
    expect(notVip).toHaveClass("neorc-not-yet");
  });
});
