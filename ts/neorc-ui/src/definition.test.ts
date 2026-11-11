import { describe, expect, it } from "vitest";

import { addressKey, instancesOf, referencedStep, stepsOf } from "./definition";
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

describe("referencedStep", () => {
  it("names the step of a tasks. or flows. reference, and nothing else", () => {
    expect(referencedStep("tasks.pick_word")).toBe("pick_word");
    expect(referencedStep("flows.picker")).toBe("picker");
    expect(referencedStep("tasks.pick_word.field")).toBe("pick_word");
    expect(referencedStep("inputs.sentence")).toBeUndefined();
    expect(referencedStep("neorc.item")).toBeUndefined();
    expect(referencedStep("tasks.")).toBeUndefined();
    expect(referencedStep("")).toBeUndefined();
  });
});
