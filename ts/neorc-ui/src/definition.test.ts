import { describe, expect, it } from "vitest";

import type { Run, Task } from "./api";
import {
  addressKey,
  chosenCase,
  instancesOf,
  isExitCondition,
  published,
  rawStep,
  referencedStep,
  stepsOf,
  type Scope,
  type StepNode,
} from "./definition";
import { BOOKING, SPLIT, SUCCEEDED } from "./test/fixtures";
import { RECORDED } from "./test/recorded";

describe("stepsOf", () => {
  it("reads tasks, sub-flows, loops and fan-outs, containers with their own", () => {
    const steps = stepsOf(RECORDED.flows["word_picker_rounds"]?.content);

    // In the order they run, each after what it refers to: the manager
    // stores the keys sorted, which would put the loop last.
    expect(steps.map((step) => [step.kind, step.name])).toEqual([
      ["loop", "runs"],
      ["task", "final_words"],
      ["fan_out", "decorate"],
      ["task", "report"],
    ]);
    const runs = steps.find((step) => step.name === "runs");
    expect(runs).toMatchObject({ kind: "loop", maxCycles: 4, exitCondition: "ran_enough" });
    expect(runs?.kind === "loop" && runs.children.map((c) => [c.kind, c.name])).toEqual([
      ["flow", "picker"],
      ["task", "ran_enough"],
    ]);
    const decorate = steps.find((step) => step.name === "decorate");
    expect(decorate).toMatchObject({ kind: "fan_out", over: "over tasks.final_words" });
    const padding = decorate?.kind === "fan_out" ? decorate.children[0] : undefined;
    expect(padding).toMatchObject({ kind: "loop", name: "padding", maxCycles: 5 });
    const pad = padding?.kind === "loop" ? padding.children.find((c) => c.name === "pad") : undefined;
    expect(pad).toMatchObject({
      kind: "task",
      handler: "tasks:pad",
      queue: "default",
      params: { word: "neorc.item", loop_count: "neorc.loop_count" },
      fixedParams: { pad_char: "*" },
    });
  });

  it("puts a step after the ones it refers to, at every level", () => {
    const steps = stepsOf(RECORDED.flows["word_picker"]?.content);

    expect(steps.map((step) => step.name)).toEqual([
      "compose_payload",
      "rounds",
      "latest_round",
      "grading",
      "keep_matching_words",
    ]);
    const rounds = steps.find((step) => step.name === "rounds");
    expect(rounds?.kind === "loop" && rounds.children.map((c) => c.name)).toEqual([
      "picking",
      "collect_words",
      "enough_rounds",
    ]);
    const picking = rounds?.kind === "loop" ? rounds.children[0] : undefined;
    expect(picking?.kind === "fan_out" && picking.children.map((c) => c.name)).toEqual([
      "pick_word",
      "extract_word",
    ]);
  });

  it("keeps the order given among steps that do not wait on each other", () => {
    const names = (steps: Record<string, unknown>) => stepsOf({ steps }).map((step) => step.name);

    expect(names({ b: { handler: "t:b" }, a: { handler: "t:a" } })).toEqual(["b", "a"]);
    // A reference to no step, to itself, or round in a circle: nothing is lost.
    expect(
      names({
        a: { handler: "t:a", params: { x: "tasks.b", y: "tasks.nowhere", z: "tasks.a" } },
        b: { handler: "t:b", params: { x: "tasks.a" } },
        c: { handler: "t:c" },
      }),
    ).toEqual(["c", "a", "b"]);
  });

  it("makes something of a definition it does not understand", () => {
    expect(stepsOf(null)).toEqual([]);
    expect(stepsOf({ steps: { odd: 1 } })).toMatchObject([{ kind: "task", name: "odd" }]);
  });
});

describe("instancesOf", () => {
  const addresses = RECORDED.tasks.map((task) => task.address);

  it("finds a container's iterations and branches at its scope", () => {
    expect(instancesOf("runs", [], addresses)).toEqual([1, 2]);
    expect(instancesOf("decorate", [], addresses)).toEqual([1, 2]);
    expect(instancesOf("padding", [["decorate", 1]], addresses)).toEqual([1, 2]);
    expect(instancesOf("padding", [], addresses)).toEqual([]);
    expect(instancesOf("padding", [["decorate", 9]], addresses)).toEqual([]);
  });

  it("keys an address by its step and scope", () => {
    expect(addressKey("pad", [["decorate", 1], ["padding", 2]])).toBe(
      "pad@decorate:1/padding:2",
    );
    expect(addressKey("report", [])).toBe("report@");
  });
});

describe("stepsOf on a switch", () => {
  it("reads a switch with its cases, each with its steps and its output", () => {
    const steps = stepsOf(BOOKING.content);

    // The switch after what it chooses on and what its cases read outside it.
    expect(steps.map((step) => [step.kind, step.name])).toEqual([
      ["task", "load_booking"],
      ["task", "check_vip"],
      ["switch", "route"],
      ["task", "confirm"],
    ]);
    const route = steps.find((step) => step.name === "route");
    expect(route).toMatchObject({ kind: "switch", on: "tasks.check_vip" });
    const cases = route?.kind === "switch" ? route.cases : [];
    expect(cases.map((one) => [one.name, one.output])).toEqual([
      ["is_vip", "tasks.send_gift"],
      ["not_vip", undefined],
    ]);
    // Inside a case too, a step comes after the ones it refers to.
    expect(cases[0]?.children.map((child) => child.name)).toEqual([
      "compute_benefit",
      "send_gift",
    ]);
    expect(cases[1]?.children).toEqual([]);
  });

  it("makes something of a switch it does not understand", () => {
    expect(stepsOf({ steps: { s: { switch: "tasks.t" } } })).toEqual([
      { kind: "switch", name: "s", on: "", cases: [] },
    ]);
    expect(stepsOf({ steps: { s: { switch: { on: "inputs.x", cases: { a: null } } } } })).toEqual([
      { kind: "switch", name: "s", on: "inputs.x", cases: [{ name: "a", children: [] }] },
    ]);
  });
});

describe("chosenCase", () => {
  const route = (content: unknown = BOOKING.content, name = "route") => {
    const find = (steps: StepNode[]): StepNode | undefined =>
      steps
        .flatMap((step) => [
          step,
          ...(step.kind === "loop" || step.kind === "fan_out" ? step.children : []),
        ])
        .find((step) => step.name === name);
    const step = find(stepsOf(content));
    if (step?.kind !== "switch") throw new Error(`no switch ${name}`);
    return step;
  };
  const task = (step: string, result: unknown, scope: Scope = [], status = "succeeded"): Task => ({
    ...SPLIT,
    id: `${step}@${scope.map(([c, n]) => `${c}${n}`).join("/")}`,
    address: { step, scope },
    status: status as Task["status"],
    result,
  });

  it("reads the task the switch chooses on, once it succeeded with the name of a case", () => {
    const chosen = (...tasks: Task[]) => chosenCase(route(), [], published(tasks, []));

    expect(chosen()).toBeUndefined();
    expect(chosen(task("check_vip", null, [], "running"))).toBeUndefined();
    expect(chosen(task("check_vip", "is_vip"))).toBe("is_vip");
    expect(chosen(task("check_vip", "not_vip"))).toBe("not_vip");
    // What the core fails the run on: a value that names no case.
    expect(chosen(task("check_vip", "gold"))).toBeUndefined();
    expect(chosen(task("check_vip", true))).toBeUndefined();
  });

  it("reads the switch's own iteration, and a step outside the loop the switch is in", () => {
    const inLoop = (on: string) => ({
      steps: {
        tier: { handler: "t:tier" },
        rounds: {
          loop: { max_cycles: 3, exit_condition: "check" },
          steps: {
            check: { handler: "t:check" },
            route: {
              switch: {
                on,
                cases: {
                  a: { steps: { in_a: { handler: "t:a" } }, output: "tasks.in_a" },
                  b: { steps: {}, output: null },
                },
              },
            },
          },
        },
      },
    });
    const done = published(
      [
        task("tier", "a"),
        task("check", "b", [["rounds", 1]]),
        task("check", "a", [["rounds", 2]]),
      ],
      [],
    );

    expect(chosenCase(route(inLoop("tasks.check")), [["rounds", 1]], done)).toBe("b");
    expect(chosenCase(route(inLoop("tasks.check")), [["rounds", 2]], done)).toBe("a");
    expect(chosenCase(route(inLoop("tasks.check")), [["rounds", 3]], done)).toBeUndefined();
    expect(chosenCase(route(inLoop("tasks.tier")), [["rounds", 2]], done)).toBe("a");
  });

  it("reads an input of the run, and a sub-flow's output", () => {
    const by = (on: string) =>
      route({
        steps: {
          classify: { flow: "classifier" },
          route: {
            switch: { on, cases: { paid: { steps: {}, output: null }, free: { steps: {}, output: null } } },
          },
        },
      });
    const subRun: Run = {
      ...SUCCEEDED,
      parent_address: { step: "classify", scope: [] },
      output: "paid",
    };

    expect(chosenCase(by("inputs.plan"), [], published([], [], { plan: "free" }))).toBe("free");
    expect(chosenCase(by("inputs.plan"), [], published([], []))).toBeUndefined();
    expect(chosenCase(by("flows.classify"), [], published([], [subRun]))).toBe("paid");
    const active: Run = { ...subRun, status: "active", output: null };
    expect(chosenCase(by("flows.classify"), [], published([], [active]))).toBeUndefined();
  });

  it("falls back on the case a step of was published, for a switch on a switch", () => {
    const second = {
      steps: {
        first: { switch: { on: "inputs.x", cases: { a: { steps: {}, output: null } } } },
        route: {
          switch: {
            on: "switches.first",
            cases: {
              left: { steps: { go_left: { handler: "t:l" } }, output: "tasks.go_left" },
              right: {
                steps: {
                  fan: { fan_out: { range: 2 }, steps: { go_right: { handler: "t:r" } } },
                },
                output: "tasks.go_right",
              },
            },
          },
        },
      },
    };

    expect(chosenCase(route(second), [], published([], []))).toBeUndefined();
    expect(chosenCase(route(second), [], published([task("go_left", 1)], []))).toBe("left");
    const inBranch = task("go_right", 1, [["fan", 2]]);
    expect(chosenCase(route(second), [], published([inBranch], []))).toBe("right");
  });
});

describe("referencedStep", () => {
  it("names the step of a tasks., flows. or switches. reference, and nothing else", () => {
    expect(referencedStep("tasks.pick_word")).toBe("pick_word");
    expect(referencedStep("flows.picker")).toBe("picker");
    expect(referencedStep("switches.route")).toBe("route");
    expect(referencedStep("tasks.pick_word.field")).toBe("pick_word");
    expect(referencedStep("inputs.sentence")).toBeUndefined();
    expect(referencedStep("neorc.item")).toBeUndefined();
    expect(referencedStep("tasks.")).toBeUndefined();
    expect(referencedStep("")).toBeUndefined();
  });
});

describe("rawStep", () => {
  const content = {
    steps: {
      write: { handler: "tasks:write" },
      rounds: {
        loop: { max_cycles: 3, exit_condition: "enough" },
        steps: {
          each: {
            fan_out: { range: 2 },
            steps: { ask: { queue: "editors", handler: "ask", form: [] } },
          },
          enough: { queue: "editors", handler: "enough" },
        },
      },
    },
  };

  it("finds a step by its name, inside loops and fan-outs too", () => {
    expect(rawStep(content, "write")).toEqual({ handler: "tasks:write" });
    expect(rawStep(content, "ask")).toEqual({ queue: "editors", handler: "ask", form: [] });
    expect(rawStep(content, "nobody")).toBeUndefined();
    expect(rawStep(null, "write")).toBeUndefined();
  });

  it("says which step a loop exits on", () => {
    expect(isExitCondition(content, "enough")).toBe(true);
    expect(isExitCondition(content, "ask")).toBe(false);
    expect(isExitCondition(content, "write")).toBe(false);
  });

  it("finds a step, and a loop's exit, inside a case of a switch", () => {
    const routed = {
      steps: {
        route: {
          switch: {
            on: "inputs.kind",
            cases: {
              asked: {
                steps: {
                  rounds: {
                    loop: { max_cycles: 3, exit_condition: "enough" },
                    steps: {
                      ask: { queue: "editors", handler: "ask", form: [] },
                      enough: { queue: "editors", handler: "enough" },
                    },
                  },
                },
                output: "tasks.ask",
              },
              skipped: { steps: {}, output: null },
            },
          },
        },
      },
    };

    expect(rawStep(routed, "ask")).toEqual({ queue: "editors", handler: "ask", form: [] });
    expect(rawStep(routed, "skipped")).toBeUndefined();
    expect(isExitCondition(routed, "enough")).toBe(true);
    expect(isExitCondition(routed, "ask")).toBe(false);
  });
});
