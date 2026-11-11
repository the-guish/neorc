import { describe, expect, it } from "vitest";

import { published, stepsOf } from "./definition";
import {
  CONTAINER_HEADER,
  LOOP_HEADER,
  containerNodeId,
  loopKey,
  runGraph,
  stepNodeId,
  type Graph,
  type GraphNode,
  type Picked,
} from "./graph";
import type { Task } from "./api";
import { SPLIT } from "./test/fixtures";
import { RECORDED } from "./test/recorded";

function recordedGraph(picked?: Picked): Graph {
  const { flows, tasks, sub_runs } = RECORDED;
  return runGraph(
    stepsOf(flows["word_picker_rounds"]?.content),
    published(tasks, sub_runs),
    picked,
  );
}

/** What a container's node says of the loop it draws. */
function loopOf(graph: Graph, id: string) {
  const data = node(graph, id).data;
  return data.kind === "container" ? data.loop : undefined;
}

function node(graph: Graph, id: string): GraphNode {
  const found = graph.nodes.find((n) => n.id === id);
  if (!found) throw new Error(`no node ${id} among ${graph.nodes.map((n) => n.id).join(", ")}`);
  return found;
}

function edgeIds(graph: Graph): string[] {
  return graph.edges.map((edge) => edge.id).sort();
}

function overlap(a: GraphNode, b: GraphNode): boolean {
  return (
    a.position.x < b.position.x + b.width &&
    b.position.x < a.position.x + a.width &&
    a.position.y < b.position.y + b.height &&
    b.position.y < a.position.y + a.height
  );
}

describe("runGraph on the recorded word_picker_rounds run", () => {
  it("has one node per step, in each branch of a fan-out and in a loop's latest iteration", () => {
    const graph = recordedGraph();

    // The loop ran twice and is drawn once, as its second iteration.
    expect(graph.nodes).toHaveLength(13);
    expect(graph.nodes.some((n) => n.id === containerNodeId("runs", [], 1))).toBe(false);
    const runs = node(graph, containerNodeId("runs", [], 2));
    expect(runs.data).toMatchObject({
      kind: "container",
      container: "loop",
      instance: "iteration 2",
      entered: true,
      about: "loop, at most 4, until ran_enough",
      loop: {
        scope: [],
        iterations: [1, 2],
        shown: 2,
        outcome: { text: "ran_enough returned true: the loop ended here", failed: false },
        failed: false,
      },
    });
    expect(runs.parentId).toBeUndefined();
    const picker = node(graph, stepNodeId("picker", [["runs", 2]]));
    expect(picker).toMatchObject({ type: "flow", parentId: runs.id, extent: "parent" });
    expect(picker.data).toMatchObject({
      kind: "flow",
      flow: "word_picker",
      status: "succeeded",
      run: { id: RECORDED.sub_runs[1]?.id },
    });
    // Both branches of the fan-out, each with the loop inside it at its latest.
    const pad = node(graph, stepNodeId("pad", [["decorate", 2], ["padding", 2]]));
    expect(pad).toMatchObject({
      type: "task",
      parentId: containerNodeId("padding", [["decorate", 2]], 2),
    });
    expect(pad.data).toMatchObject({ kind: "task", handler: "tasks:pad", status: "succeeded" });
    expect(pad.data.kind === "task" && pad.data.task?.address).toEqual({
      step: "pad",
      scope: [["decorate", 2], ["padding", 2]],
    });
    expect(pad.data.kind === "task" && pad.data.duration).toMatch(/ms$/);
    expect(node(graph, containerNodeId("padding", [["decorate", 2]], 2)).parentId).toBe(
      containerNodeId("decorate", [], 2),
    );
    expect(loopOf(graph, containerNodeId("padding", [["decorate", 1]], 2))).toMatchObject({
      scope: [["decorate", 1]],
      iterations: [1, 2],
      shown: 2,
    });
    // A fan-out's branch has no iterations to pick from.
    expect(loopOf(graph, containerNodeId("decorate", [], 1))).toBeUndefined();
    expect(node(graph, stepNodeId("report", [])).parentId).toBeUndefined();
  });

  it("draws the iteration picked for a loop, each loop apart", () => {
    const graph = recordedGraph(
      new Map([
        [loopKey("runs", []), 1],
        [loopKey("padding", [["decorate", 2]]), 1],
      ]),
    );

    expect(graph.nodes).toHaveLength(13);
    expect(loopOf(graph, containerNodeId("runs", [], 1))).toMatchObject({
      iterations: [1, 2],
      shown: 1,
      outcome: { text: "ran_enough returned false: iteration 2 followed", failed: false },
      failed: false,
    });
    const picker = node(graph, stepNodeId("picker", [["runs", 1]]));
    expect(picker.data.kind === "flow" && picker.data.run?.id).toBe(RECORDED.sub_runs[0]?.id);
    // The loop in the second branch at its first iteration, the first branch's at its latest.
    expect(node(graph, stepNodeId("pad", [["decorate", 2], ["padding", 1]])).parentId).toBe(
      containerNodeId("padding", [["decorate", 2]], 1),
    );
    expect(node(graph, stepNodeId("pad", [["decorate", 1], ["padding", 2]])).parentId).toBe(
      containerNodeId("padding", [["decorate", 1]], 2),
    );
    // What is after a loop is joined to the iteration shown.
    expect(edgeIds(graph)).toContain(`${picker.id}->${stepNodeId("final_words", [])}`);
    expect(edgeIds(graph)).toContain(
      `${stepNodeId("pad", [["decorate", 2], ["padding", 1]])}->${stepNodeId("report", [])}`,
    );
  });

  it("draws a loop's latest iteration when the one picked was never reached", () => {
    const graph = recordedGraph(new Map([[loopKey("runs", []), 3]]));

    expect(loopOf(graph, containerNodeId("runs", [], 2))).toMatchObject({ shown: 2 });
  });

  it("lists parents before their children, as React Flow requires", () => {
    const graph = recordedGraph();
    const seen = new Set<string>();
    for (const n of graph.nodes) {
      if (n.parentId !== undefined) expect(seen.has(n.parentId)).toBe(true);
      seen.add(n.id);
    }
  });

  it("draws an edge from the step a param refers to, in the branches and the iteration drawn", () => {
    const graph = recordedGraph();
    const id = (source: string, target: string) => `${source}->${target}`;
    const runs: [string, number][] = [["runs", 2]];
    const padding = (branch: number): [string, number][] => [
      ["decorate", branch],
      ["padding", 2],
    ];

    expect(edgeIds(graph)).toEqual(
      [
        // Inside the loop: within the iteration drawn.
        id(stepNodeId("picker", runs), stepNodeId("ran_enough", runs)),
        // From after the loop: the iteration drawn stands for all of them.
        id(stepNodeId("picker", runs), stepNodeId("final_words", [])),
        // What the fan-out iterates over feeds each branch.
        id(stepNodeId("final_words", []), containerNodeId("decorate", [], 1)),
        id(stepNodeId("final_words", []), containerNodeId("decorate", [], 2)),
        // The same branch, the loop inside it.
        ...[1, 2].map((branch) =>
          id(stepNodeId("pad", padding(branch)), stepNodeId("long_enough", padding(branch))),
        ),
        // From outside the fan-out and the loop in it: every branch.
        ...[1, 2].map((branch) =>
          id(stepNodeId("pad", padding(branch)), stepNodeId("report", [])),
        ),
      ].sort(),
    );
    const report = graph.edges.filter((e) => e.target === stepNodeId("report", []));
    expect(report.map((e) => e.params)).toEqual([["padded"], ["padded"]]);
    const decorate = graph.edges.find((e) => e.target === containerNodeId("decorate", [], 1));
    expect(decorate?.params).toEqual(["over"]);
  });

  it("positions every node inside its parent, below its title, with no overlap", () => {
    const graph = recordedGraph();

    expect(graph.width).toBeGreaterThan(0);
    expect(graph.height).toBeGreaterThan(0);
    for (const n of graph.nodes) {
      expect(n.width).toBeGreaterThan(0);
      expect(n.height).toBeGreaterThan(0);
      const bounds = n.parentId ? node(graph, n.parentId) : { width: graph.width, height: graph.height };
      expect(n.position.x).toBeGreaterThanOrEqual(0);
      // A loop's title bar is taller: it holds the row an iteration is picked on.
      const header = !n.parentId
        ? 0
        : loopOf(graph, n.parentId)
          ? LOOP_HEADER
          : CONTAINER_HEADER;
      expect(n.position.y).toBeGreaterThanOrEqual(header);
      expect(n.position.x + n.width).toBeLessThanOrEqual(bounds.width);
      expect(n.position.y + n.height).toBeLessThanOrEqual(bounds.height);
    }
    for (const a of graph.nodes) {
      for (const b of graph.nodes) {
        if (a.id < b.id && a.parentId === b.parentId) {
          expect(overlap(a, b), `${a.id} overlaps ${b.id}`).toBe(false);
        }
      }
    }
  });

  it("draws a fan-out's branches side by side", () => {
    const graph = recordedGraph();
    const branch1 = node(graph, containerNodeId("decorate", [], 1));
    const branch2 = node(graph, containerNodeId("decorate", [], 2));
    expect(branch1.position.y).toBe(branch2.position.y);
    expect(branch2.position.x).toBeGreaterThanOrEqual(branch1.position.x + branch1.width);
  });

  it("makes a loop wide enough for its title bar, its steps in the middle", () => {
    const graph = recordedGraph();
    const runs = node(graph, containerNodeId("runs", [], 2));
    const inside = graph.nodes.filter((n) => n.parentId === runs.id);
    const left = Math.min(...inside.map((n) => n.position.x));
    const right = Math.max(...inside.map((n) => n.position.x + n.width));

    expect(runs.width).toBeGreaterThan(right - left + 64);
    expect(left).toBeCloseTo(runs.width - right);
  });

  it("draws a run that has published nothing yet with each container not entered", () => {
    const { flows } = RECORDED;
    const graph = runGraph(stepsOf(flows["word_picker_rounds"]?.content), published([], []));

    expect(graph.nodes.map((n) => n.id).sort()).toEqual(
      [
        containerNodeId("runs", [], 0),
        stepNodeId("picker", [["runs", 0]]),
        stepNodeId("ran_enough", [["runs", 0]]),
        stepNodeId("final_words", []),
        containerNodeId("decorate", [], 0),
        containerNodeId("padding", [["decorate", 0]], 0),
        stepNodeId("pad", [["decorate", 0], ["padding", 0]]),
        stepNodeId("long_enough", [["decorate", 0], ["padding", 0]]),
        stepNodeId("report", []),
      ].sort(),
    );
    const runs = node(graph, containerNodeId("runs", [], 0));
    expect(runs.data).toMatchObject({ instance: "not entered yet", entered: false });
    expect(loopOf(graph, runs.id)).toBeUndefined();
    const picker = node(graph, stepNodeId("picker", [["runs", 0]]));
    expect(picker.data).toMatchObject({ status: "none", duration: "" });
    expect(picker.data.kind === "flow" && picker.data.run).toBeUndefined();
    expect(edgeIds(graph)).toContain(
      `${stepNodeId("picker", [["runs", 0]])}->${stepNodeId("final_words", [])}`,
    );
    expect(edgeIds(graph)).toContain(
      `${stepNodeId("pad", [["decorate", 0], ["padding", 0]])}->${stepNodeId("report", [])}`,
    );
    expect(edgeIds(graph)).toContain(
      `${stepNodeId("final_words", [])}->${containerNodeId("decorate", [], 0)}`,
    );
  });
});

describe("runGraph on the recorded word_picker sub-run", () => {
  const subGraph = (picked?: Picked): Graph => {
    const { flows, sub_runs, sub_run_tasks } = RECORDED;
    const sub = sub_runs[0]!;
    return runGraph(
      stepsOf(flows["word_picker"]?.content),
      published(sub_run_tasks[sub.id] ?? [], []),
      picked,
    );
  };
  const into = (graph: Graph, target: string) =>
    graph.edges.filter((e) => e.target === target).map((e) => e.source).sort();

  it("joins every node a reference reaches", () => {
    const graph = subGraph();

    // The loop ran four times: its fourth iteration is the one drawn.
    expect(loopOf(graph, containerNodeId("rounds", [], 4))).toMatchObject({
      iterations: [1, 2, 3, 4],
      shown: 4,
      outcome: { text: "enough_rounds returned true: the loop ended here" },
    });
    // In the loop, outside the fan-out nested in it: every branch of the iteration.
    expect(into(graph, stepNodeId("collect_words", [["rounds", 4]]))).toEqual(
      [1, 2, 3].map((branch) => stepNodeId("extract_word", [["rounds", 4], ["picking", branch]])),
    );
    // In the loop, a sibling: in the same iteration.
    expect(into(graph, stepNodeId("enough_rounds", [["rounds", 4]]))).toEqual([
      stepNodeId("collect_words", [["rounds", 4]]),
    ]);
    // After the loop: the iteration drawn.
    expect(into(graph, stepNodeId("latest_round", []))).toEqual([
      stepNodeId("collect_words", [["rounds", 4]]),
    ]);
    // A fan-out over a range is fed by nothing.
    expect(into(graph, containerNodeId("grading", [], 2))).toEqual([]);
    // Outside a fan-out: every branch.
    expect(into(graph, stepNodeId("keep_matching_words", []))).toEqual(
      [1, 2, 3].map((branch) => stepNodeId("score_words", [["grading", branch]])),
    );
    // From deep inside, a step at the root.
    expect(into(graph, stepNodeId("pick_word", [["rounds", 4], ["picking", 2]]))).toEqual([
      stepNodeId("compose_payload", []),
    ]);
  });

  it("joins the iteration picked, and nothing of the others", () => {
    const graph = subGraph(new Map([[loopKey("rounds", []), 2]]));

    expect(loopOf(graph, containerNodeId("rounds", [], 2))).toMatchObject({
      shown: 2,
      outcome: { text: "enough_rounds returned false: iteration 3 followed", failed: false },
    });
    expect(into(graph, stepNodeId("collect_words", [["rounds", 2]]))).toEqual(
      [1, 2, 3].map((branch) => stepNodeId("extract_word", [["rounds", 2], ["picking", branch]])),
    );
    expect(into(graph, stepNodeId("latest_round", []))).toEqual([
      stepNodeId("collect_words", [["rounds", 2]]),
    ]);
    const ids = new Set(graph.nodes.map((n) => n.id));
    expect([...ids].filter((id) => /rounds:[134]/.test(id))).toEqual([]);
    for (const edge of graph.edges) {
      expect(ids.has(edge.source), edge.id).toBe(true);
      expect(ids.has(edge.target), edge.id).toBe(true);
    }
  });
});

describe("runGraph on nested containers", () => {
  const task = (step: string, scope: [string, number][]): Task => ({
    ...SPLIT,
    id: `${step}@${scope.map(([c, n]) => `${c}${n}`).join("/")}`,
    address: { step, scope },
  });

  it("draws a loop inside a loop at its latest iteration of the outer one drawn", () => {
    const steps = stepsOf({
      steps: {
        outer: {
          loop: { max_cycles: 5, exit_condition: "b" },
          steps: {
            inner: {
              loop: { max_cycles: 5, exit_condition: "b" },
              steps: { a: { handler: "t:a" }, b: { handler: "t:b", params: { x: "tasks.a" } } },
            },
          },
        },
      },
    });
    const done = published(
      [
        ...[1, 2, 3].flatMap((n) => [
          task("a", [["outer", 1], ["inner", n]]),
          task("b", [["outer", 1], ["inner", n]]),
        ]),
        task("a", [["outer", 2], ["inner", 1]]),
        task("b", [["outer", 2], ["inner", 1]]),
      ],
      [],
    );
    const ids = (picked?: Picked) => runGraph(steps, done, picked).nodes.map((n) => n.id).sort();
    const drawn = (outer: number, inner: number) =>
      [
        containerNodeId("outer", [], outer),
        containerNodeId("inner", [["outer", outer]], inner),
        stepNodeId("a", [["outer", outer], ["inner", inner]]),
        stepNodeId("b", [["outer", outer], ["inner", inner]]),
      ].sort();

    expect(ids()).toEqual(drawn(2, 1));
    // The inner loop went round three times in the outer loop's first iteration.
    expect(ids(new Map([[loopKey("outer", []), 1]]))).toEqual(drawn(1, 3));
    // A pick for the inner loop is for it in one iteration of the outer one.
    const inner2 = new Map([
      [loopKey("outer", []), 1],
      [loopKey("inner", [["outer", 1]]), 2],
    ]);
    expect(ids(inner2)).toEqual(drawn(1, 2));
    expect(ids(new Map([[loopKey("inner", [["outer", 1]]), 2]]))).toEqual(drawn(2, 1));
    const graph = runGraph(steps, done, inner2);
    expect(graph.edges.map((e) => e.id)).toEqual([
      `${stepNodeId("a", [["outer", 1], ["inner", 2]])}->${stepNodeId("b", [["outer", 1], ["inner", 2]])}`,
    ]);
  });

  it("draws the branches a fan-out had in the iteration drawn", () => {
    const steps = stepsOf({
      steps: {
        rounds: {
          loop: { max_cycles: 5, exit_condition: "b" },
          steps: {
            picking: {
              fan_out: { over: "inputs.words" },
              steps: { a: { handler: "t:a" }, b: { handler: "t:b", params: { x: "tasks.a" } } },
            },
          },
        },
      },
    });
    const done = published(
      [
        ...[1, 2].flatMap((branch) => [
          task("a", [["rounds", 1], ["picking", branch]]),
          task("b", [["rounds", 1], ["picking", branch]]),
        ]),
        ...[1, 2, 3].flatMap((branch) => [
          task("a", [["rounds", 2], ["picking", branch]]),
          task("b", [["rounds", 2], ["picking", branch]]),
        ]),
      ],
      [],
    );
    const branches = (graph: Graph) =>
      graph.nodes.filter((n) => n.data.kind === "container" && n.data.container === "fan_out");

    const latest = runGraph(steps, done);
    expect(branches(latest).map((n) => n.id)).toEqual(
      [1, 2, 3].map((branch) => containerNodeId("picking", [["rounds", 2]], branch)),
    );
    expect(latest.edges.map((e) => e.id).sort()).toEqual(
      [1, 2, 3].map(
        (branch) =>
          `${stepNodeId("a", [["rounds", 2], ["picking", branch]])}->${stepNodeId("b", [["rounds", 2], ["picking", branch]])}`,
      ),
    );
    const first = runGraph(steps, done, new Map([[loopKey("rounds", []), 1]]));
    expect(branches(first).map((n) => n.id)).toEqual(
      [1, 2].map((branch) => containerNodeId("picking", [["rounds", 1]], branch)),
    );
  });

  it("marks a loop that used its iterations up without its exit condition met", () => {
    const steps = stepsOf({
      steps: {
        trying: {
          loop: { max_cycles: 2, exit_condition: "done" },
          steps: { done: { handler: "t:done" } },
        },
      },
    });
    const exit = (n: number, result: unknown): Task => ({
      ...task("done", [["trying", n]]),
      status: "succeeded",
      result,
    });
    const loop = (tasks: Task[], picked?: Picked) => {
      const graph = runGraph(steps, published(tasks, []), picked);
      return graph.nodes.flatMap((n) => (n.data.kind === "container" && n.data.loop) || []);
    };

    expect(loop([exit(1, false), exit(2, false)])).toMatchObject([
      {
        shown: 2,
        outcome: { text: "done returned false on the last of 2 iterations", failed: true },
        failed: true,
      },
    ]);
    // The loop is marked whichever iteration is drawn; the iteration says its own end.
    expect(loop([exit(1, false), exit(2, false)], new Map([[loopKey("trying", []), 1]]))).toMatchObject([
      {
        shown: 1,
        outcome: { text: "done returned false: iteration 2 followed", failed: false },
        failed: true,
      },
    ]);
    // Going round still: the first iteration said no, the second is not there yet.
    expect(loop([exit(1, false)])).toMatchObject([
      { shown: 1, outcome: { text: "done returned false", failed: false }, failed: false },
    ]);
    // The run fails on an exit condition that is not a boolean.
    expect(loop([exit(1, "maybe")])).toMatchObject([
      { outcome: { text: "done returned neither true nor false", failed: true }, failed: true },
    ]);
    // Nothing to say while the exit condition has not finished.
    const waiting = loop([{ ...task("done", [["trying", 1]]), status: "running", result: null }]);
    expect(waiting).toMatchObject([{ shown: 1, failed: false }]);
    expect(waiting[0]?.outcome).toBeUndefined();
  });
});
