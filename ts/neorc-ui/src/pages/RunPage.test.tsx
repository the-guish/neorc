import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { shortId } from "../format";
import { RECORDED, recordedRoutes } from "../test/recorded";
import { ACTIVE, CANCELLED } from "../test/fixtures";
import { mockApi, refusal, renderWithClient } from "../test/render";
import { RunPage, VIEW_KEY } from "./RunPage";

describe("RunPage on a recorded word_picker_rounds run", () => {
  it("shows the run, and every step with what the run did there", async () => {
    mockApi(recordedRoutes());
    const { run, sub_runs } = RECORDED;

    renderWithClient(<RunPage id={run.id} />);

    const heading = await screen.findByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent(`${run.flow} ${shortId(run.id)}`);
    expect(within(heading).getByText("succeeded")).toBeInTheDocument();

    // The loop ran twice: each iteration holds the sub-flow and its exit task.
    const first = await screen.findByRole("region", { name: "runs iteration 1" });
    const second = screen.getByRole("region", { name: "runs iteration 2" });
    for (const [iteration, sub] of [first, second].map((r, i) => [r, sub_runs[i]] as const)) {
      expect(within(iteration).getByText("picker")).toBeInTheDocument();
      expect(within(iteration).getByText("ran_enough")).toBeInTheDocument();
      expect(within(iteration).getByRole("link", { name: `run ${shortId(sub!.id)}` })).toHaveAttribute(
        "href",
        `#/runs/${sub!.id}`,
      );
    }
    expect(screen.queryByRole("region", { name: "runs iteration 3" })).not.toBeInTheDocument();
    // The steps in the order they run, not the sorted order of the stored keys.
    expect(
      [...(document.querySelector(".tree")?.children ?? [])].map(
        (step) => step.querySelector("strong")?.textContent,
      ),
    ).toEqual(["runs", "final_words", "decorate", "report"]);
    // What the loop came to, read from its exit condition.
    expect(
      screen.getByText("iteration 2: ran_enough returned true: the loop ended here"),
    ).toBeInTheDocument();

    // The fan-out has a branch per word, each with the nested loop's iterations.
    for (const branch of [1, 2]) {
      const region = screen.getByRole("region", { name: `decorate branch ${branch}` });
      expect(within(region).getByRole("region", { name: "padding iteration 1" })).toBeInTheDocument();
      expect(within(region).getByRole("region", { name: "padding iteration 2" })).toBeInTheDocument();
    }
    expect(screen.queryByText("not published")).not.toBeInTheDocument();
    // A finished step says its status in words, with how long it took.
    const report = screen.getByText("report").closest("li");
    expect(report).toHaveTextContent(/succeeded · \d+ ms/);

    // Sub-runs are listed too, with the step that started them.
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(within(table).getByText("picker runs 1")).toBeInTheDocument();
  });

  it("opens a task's details: handler, attempts, times, params and result", async () => {
    mockApi(recordedRoutes());
    const { run, tasks } = RECORDED;
    const report = tasks.find((task) => task.address.step === "report");

    renderWithClient(<RunPage id={run.id} />);
    await screen.findByRole("region", { name: "runs iteration 1" });
    const step = screen.getByText("report").closest("li");
    expect(step).not.toBeNull();
    fireEvent.click(within(step!).getByText("details"));

    const details = within(step!.querySelector("details")!);
    expect(details.getByText("tasks:report")).toBeInTheDocument();
    expect(details.getByText(String(report?.attempts))).toBeInTheDocument();
    expect(details.getByText(/potato\*\*/)).toBeInTheDocument();
    expect(details.getByText(/neorc\.flow_run_id/)).toBeInTheDocument();
  });

  it("links a sub-run to its parent and the step it was started at", async () => {
    mockApi(recordedRoutes());
    const { run, sub_runs } = RECORDED;
    const sub = sub_runs[0]!;

    renderWithClient(<RunPage id={sub.id} />);

    await screen.findByRole("heading", { level: 1 });
    expect(screen.getByRole("link", { name: `run ${shortId(run.id)}` })).toHaveAttribute(
      "href",
      `#/runs/${run.id}`,
    );
    expect(screen.getByText(/at picker runs 1/)).toBeInTheDocument();
    // word_picker's own loop and fan-outs are drawn from its definition.
    expect(await screen.findByRole("region", { name: "rounds iteration 1" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "grading branch 3" })).toBeInTheDocument();
  });

  it("draws the steps of a run that has published nothing yet as not started", async () => {
    const { run, flows } = RECORDED;
    const fresh = { ...run, status: "active" as const, finished_at: null, output: null };
    mockApi({
      ...recordedRoutes(),
      [`/runs/${run.id}`]: fresh,
      [`/runs/${run.id}/tasks`]: { tasks: [] },
      [`/runs/${run.id}/sub-runs`]: { runs: [] },
      [`/flows/word_picker_rounds/versions/${flows["word_picker_rounds"]?.version}`]:
        flows["word_picker_rounds"],
    });

    renderWithClient(<RunPage id={run.id} />);

    expect(await screen.findAllByText("not published")).not.toHaveLength(0);
    expect(screen.getAllByText("not entered yet")).toHaveLength(3);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("reports a run the manager does not have", async () => {
    mockApi({ "/runs/nothing": refusal(404, "RunNotFoundError", "nothing") });

    renderWithClient(<RunPage id="nothing" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("nothing");
  });
});

describe("cancelling a run", () => {
  function activeRoutes(cancel: unknown) {
    const { flows } = RECORDED;
    const child = { ...ACTIVE, id: "c0c0c0c0-0000-4000-8000-000000000001", parent_id: ACTIVE.id };
    return {
      "/flows/hello": flows["word_picker_rounds"],
      "/flows/hello/versions/0.1.0": {
        ...flows["word_picker_rounds"],
        name: "hello",
        version: "0.1.0",
      },
      [`/runs/${ACTIVE.id}`]: ACTIVE,
      [`/runs/${ACTIVE.id}/tasks`]: { tasks: [] },
      [`/runs/${ACTIVE.id}/sub-runs`]: { runs: [child, { ...child, id: "c0c0c0c0-0000-4000-8000-000000000002", status: "succeeded" as const }] },
      [`/runs/${ACTIVE.id}/cancel`]: cancel,
    };
  }

  it("asks first, naming the active sub-runs, then cancels", async () => {
    let cancelled = 0;
    mockApi(activeRoutes(() => { cancelled += 1; return undefined; }));

    renderWithClient(<RunPage id={ACTIVE.id} />);
    fireEvent.click(await screen.findByRole("button", { name: "Cancel run" }));
    const asking = await screen.findByRole("group", { name: "Confirm cancelling" });
    expect(asking).toHaveTextContent("every active run in its tree: 1 active sub-run");

    fireEvent.click(within(asking).getByRole("button", { name: "Cancel it" }));

    await screen.findByRole("button", { name: "Cancel run" });
    expect(cancelled).toBe(1);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("can be thought better of", async () => {
    let cancelled = 0;
    mockApi(activeRoutes(() => { cancelled += 1; return undefined; }));

    renderWithClient(<RunPage id={ACTIVE.id} />);
    fireEvent.click(await screen.findByRole("button", { name: "Cancel run" }));
    fireEvent.click(await screen.findByRole("button", { name: "Keep it" }));

    expect(screen.getByRole("button", { name: "Cancel run" })).toBeInTheDocument();
    expect(cancelled).toBe(0);
  });

  it("shows the manager's refusal", async () => {
    mockApi(activeRoutes(refusal(409, "RunStateError", `run ${ACTIVE.id} is already cancelled`)));

    renderWithClient(<RunPage id={ACTIVE.id} />);
    fireEvent.click(await screen.findByRole("button", { name: "Cancel run" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel it" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("already cancelled");
  });

  it("is not offered on a sub-run, which points at its root instead", async () => {
    const sub = { ...ACTIVE, id: "c0c0c0c0-0000-4000-8000-000000000009", parent_id: "root-id", root_id: "aaaaaaaa-0000-4000-8000-000000000000", parent_address: { step: "picker", scope: [] as [string, number][] } };
    mockApi({
      ...activeRoutes(undefined),
      [`/runs/${sub.id}`]: sub,
      [`/runs/${sub.id}/tasks`]: { tasks: [] },
      [`/runs/${sub.id}/sub-runs`]: { runs: [] },
    });

    renderWithClient(<RunPage id={sub.id} />);

    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("button", { name: "Cancel run" })).not.toBeInTheDocument();
    expect(screen.getByText(/cancelled from its root/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "aaaaaaaa" })).toHaveAttribute(
      "href",
      `#/runs/${sub.root_id}`,
    );
  });

  it("is not offered for a run that is over", async () => {
    mockApi({ ...activeRoutes(undefined), [`/runs/${ACTIVE.id}`]: CANCELLED });

    renderWithClient(<RunPage id={ACTIVE.id} />);

    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("button", { name: "Cancel run" })).not.toBeInTheDocument();
  });
});

describe("the graph view", () => {
  it("draws a node per step, a loop once at its latest iteration, edges between them", async () => {
    mockApi(recordedRoutes());
    const { run, sub_runs } = RECORDED;

    renderWithClient(<RunPage id={run.id} view="graph" />);

    const canvas = await screen.findByLabelText("Run graph");
    await waitFor(() => expect(canvas.querySelectorAll(".react-flow__node")).toHaveLength(13));
    await waitFor(() => expect(canvas.querySelectorAll(".react-flow__edge")).toHaveLength(8));
    // Each loop went round twice and is one box: runs, and padding in each branch.
    expect(within(canvas).getAllByText("picker")).toHaveLength(1);
    expect(within(canvas).getAllByText("pad")).toHaveLength(2);
    expect(within(canvas).getByLabelText("runs: iteration shown")).toHaveValue("2");
    for (const shown of within(canvas).getAllByLabelText("padding: iteration shown")) {
      expect(shown).toHaveValue("2");
    }
    expect(within(canvas).getByText("ran_enough returned true: the loop ended here")).toBeInTheDocument();
    expect(within(canvas).getAllByText("branch 2")).toHaveLength(1);
    expect(within(canvas).getAllByText("fan-out over tasks.final_words")).toHaveLength(2);
    expect(within(canvas).getAllByLabelText("succeeded")).toHaveLength(8);
    expect(within(canvas).getByRole("link", { name: `run ${shortId(sub_runs[1]!.id)}` })).toHaveAttribute(
      "href",
      `#/runs/${sub_runs[1]!.id}`,
    );
    // The outline is not there as well: one view at a time.
    expect(screen.queryByRole("region", { name: "runs iteration 1" })).not.toBeInTheDocument();
    const switcher = screen.getByRole("navigation", { name: "View" });
    expect(within(switcher).getByRole("link", { name: "Graph" })).toHaveAttribute("aria-current", "true");
    expect(within(switcher).getByRole("link", { name: "Outline" })).toHaveAttribute(
      "href",
      `#/runs/${run.id}?view=outline`,
    );
  });

  it("opens a task's details beside the graph when its node is clicked", async () => {
    mockApi(recordedRoutes());
    const { run } = RECORDED;

    renderWithClient(<RunPage id={run.id} view="graph" />);
    const canvas = await screen.findByLabelText("Run graph");
    fireEvent.click(await within(canvas).findByText("report"));

    const panel = await screen.findByRole("complementary", { name: "report details" });
    expect(within(panel).getByText("tasks:report")).toBeInTheDocument();
    expect(within(panel).getByText(/potato\*\*/)).toBeInTheDocument();

    fireEvent.click(within(panel).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  });

  it("shows another iteration of a loop when it is picked", async () => {
    mockApi(recordedRoutes());
    const { run, sub_runs } = RECORDED;

    renderWithClient(<RunPage id={run.id} view="graph" />);
    const canvas = await screen.findByLabelText("Run graph");
    await within(canvas).findByLabelText("runs: iteration shown");
    const previous = () => within(canvas).getByRole("button", { name: "runs: previous iteration" });
    const next = () => within(canvas).getByRole("button", { name: "runs: next iteration" });
    const subRun = (n: number) =>
      within(canvas).queryByRole("link", { name: `run ${shortId(sub_runs[n]!.id)}` });

    // The latest of the two: nothing after it.
    expect(next()).toBeDisabled();
    expect(subRun(1)).toBeInTheDocument();

    fireEvent.click(previous());

    await waitFor(() => expect(subRun(0)).toBeInTheDocument());
    expect(subRun(1)).not.toBeInTheDocument();
    expect(within(canvas).getByLabelText("runs: iteration shown")).toHaveValue("1");
    expect(previous()).toBeDisabled();
    expect(
      within(canvas).getByText("ran_enough returned false: iteration 2 followed"),
    ).toBeInTheDocument();
    // The other loops stay where they were, and the graph has as many nodes.
    for (const padding of within(canvas).getAllByLabelText("padding: iteration shown")) {
      expect(padding).toHaveValue("2");
    }
    expect(canvas.querySelectorAll(".react-flow__node")).toHaveLength(13);

    // By its number, too.
    fireEvent.change(within(canvas).getByLabelText("runs: iteration shown"), {
      target: { value: "2" },
    });
    await waitFor(() => expect(subRun(1)).toBeInTheDocument());
    expect(within(canvas).getByLabelText("runs: iteration shown")).toHaveValue("2");
    expect(next()).toBeDisabled();
  });

  it("keeps a task's details open on the iteration picked", async () => {
    mockApi(recordedRoutes());
    const { run } = RECORDED;

    renderWithClient(<RunPage id={run.id} view="graph" />);
    const canvas = await screen.findByLabelText("Run graph");
    fireEvent.click(await within(canvas).findByText("ran_enough"));

    const panel = await screen.findByRole("complementary", { name: "ran_enough details" });
    expect(within(panel).getByText("runs 2")).toBeInTheDocument();
    expect(within(panel).getByText("true")).toBeInTheDocument();

    fireEvent.click(within(canvas).getByRole("button", { name: "runs: previous iteration" }));

    const moved = await screen.findByRole("complementary", { name: "ran_enough details" });
    await waitFor(() => expect(within(moved).getByText("runs 1")).toBeInTheDocument());
    expect(within(moved).getByText("false")).toBeInTheDocument();
  });

  it("marks a loop that used up its iterations", async () => {
    const { run, tasks } = RECORDED;
    // The loop of at most 4 never told to stop: four iterations, the exit condition false.
    const exit = tasks.find((task) => task.address.step === "ran_enough")!;
    const never = [1, 2, 3, 4].map((n) => ({
      ...exit,
      id: `${exit.id}-${n}`,
      address: { step: "ran_enough", scope: [["runs", n]] as [string, number][] },
      result: false,
    }));
    mockApi({
      ...recordedRoutes(),
      [`/runs/${run.id}`]: {
        ...run,
        status: "failed" as const,
        output: null,
        reason: "runs: still not done after max_cycles, 4 iterations",
      },
      [`/runs/${run.id}/tasks`]: { tasks: never },
      [`/runs/${run.id}/sub-runs`]: { runs: [] },
    });

    const graph = renderWithClient(<RunPage id={run.id} view="graph" />);
    const canvas = await screen.findByLabelText("Run graph");
    const outcome = await within(canvas).findByText(
      "ran_enough returned false on the last of 4 iterations",
    );
    expect(outcome.closest(".graph-container")).toHaveClass("graph-failed");
    expect(within(canvas).getByLabelText("runs: iteration shown")).toHaveValue("4");
    graph.unmount();

    renderWithClient(<RunPage id={run.id} view="outline" />);
    const said = await screen.findByText(
      "iteration 4: ran_enough returned false on the last of 4 iterations",
    );
    expect(said.closest(".card")).toHaveClass("loop-failed");
  });

  it("draws a run that has published nothing yet, containers not entered", async () => {
    const { run, flows } = RECORDED;
    mockApi({
      ...recordedRoutes(),
      [`/runs/${run.id}`]: { ...run, status: "active" as const, finished_at: null, output: null },
      [`/runs/${run.id}/tasks`]: { tasks: [] },
      [`/runs/${run.id}/sub-runs`]: { runs: [] },
      [`/flows/word_picker_rounds/versions/${flows["word_picker_rounds"]?.version}`]:
        flows["word_picker_rounds"],
    });

    renderWithClient(<RunPage id={run.id} view="graph" />);

    const canvas = await screen.findByLabelText("Run graph");
    await waitFor(() => expect(canvas.querySelectorAll(".react-flow__node")).toHaveLength(9));
    expect(within(canvas).getAllByText("not entered yet")).toHaveLength(3);
    expect(within(canvas).getAllByLabelText("none")).toHaveLength(6);
  });

  it("is remembered for the next run opened, and the address wins", async () => {
    mockApi(recordedRoutes());
    const { run } = RECORDED;

    const first = renderWithClient(<RunPage id={run.id} view="graph" />);
    await screen.findByLabelText("Run graph");
    expect(localStorage.getItem(VIEW_KEY)).toBe("graph");
    first.unmount();

    const second = renderWithClient(<RunPage id={run.id} />);
    expect(await screen.findByLabelText("Run graph")).toBeInTheDocument();
    second.unmount();

    renderWithClient(<RunPage id={run.id} view="outline" />);
    expect(await screen.findByRole("region", { name: "runs iteration 1" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Run graph")).not.toBeInTheDocument();
    expect(localStorage.getItem(VIEW_KEY)).toBe("outline");
  });
});
