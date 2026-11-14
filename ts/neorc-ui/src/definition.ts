// A flow definition as uploaded, read into the steps the run page draws, and
// the addresses of a run's tasks and sub-runs read into loop iterations and
// fan-out branches.
import type { Address, Run, Task } from "./api";

export type StepNode =
  | {
      kind: "task";
      name: string;
      handler: string;
      queue: string;
      params: Record<string, string>;
      fixedParams: Record<string, unknown>;
    }
  | { kind: "flow"; name: string; flow: string; params: Record<string, string> }
  | {
      kind: "loop";
      name: string;
      maxCycles: number;
      exitCondition: string;
      children: StepNode[];
    }
  | {
      kind: "fan_out";
      name: string;
      /** What it iterates over, as the page says it: "over tasks.x", "range 3". */
      over: string;
      /** The reference the branches are made from, if `over` is one. */
      input?: string;
      children: StepNode[];
    };

type Json = Record<string, unknown>;

function record(value: unknown): Json {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Json)
    : {};
}

function strings(value: unknown): Record<string, string> {
  return Object.fromEntries(
    Object.entries(record(value)).map(([key, item]) => [key, String(item)]),
  );
}

/**
 * The steps of a definition's `steps` mapping, containers with their own, in
 * the order they run: a step after the ones it refers to. The manager stores
 * a definition with its keys sorted, so the order in the file is gone; the
 * references are what is left to say which step comes first.
 */
export function stepsOf(content: unknown): StepNode[] {
  return inRunOrder(
    Object.entries(record(record(content)["steps"])).map(([name, raw]) =>
      stepOf(name, record(raw)),
    ),
  );
}

/** The step a `tasks.` or `flows.` reference names; nothing for the rest. */
export function referencedStep(value: string): string | undefined {
  const match = /^(?:tasks|flows)\.([^.]+)/.exec(value);
  return match?.[1];
}

/** Every step a step refers to, from anywhere inside it. */
function references(step: StepNode, into = new Set<string>()): Set<string> {
  const values =
    step.kind === "task" || step.kind === "flow"
      ? Object.values(step.params)
      : step.kind === "fan_out" && step.input
        ? [step.input]
        : [];
  for (const value of values) {
    const name = referencedStep(value);
    if (name !== undefined) into.add(name);
  }
  if (step.kind === "loop" || step.kind === "fan_out") {
    for (const child of step.children) references(child, into);
  }
  return into;
}

/** The names of a step and of every step inside it. */
function names(step: StepNode, into = new Set<string>()): Set<string> {
  into.add(step.name);
  if (step.kind === "loop" || step.kind === "fan_out") {
    for (const child of step.children) names(child, into);
  }
  return into;
}

/**
 * Siblings sorted so each comes after the ones it waits on: those holding a
 * step it, or a step inside it, refers to. Steps that wait on nothing more
 * keep the order given. The core refuses a flow whose steps wait on each
 * other; if one arrives all the same, what is left keeps the order given.
 */
function inRunOrder(steps: StepNode[]): StepNode[] {
  const holder = new Map<string, string>();
  for (const step of steps) {
    for (const name of names(step)) holder.set(name, step.name);
  }
  const waitsOn = new Map<string, Set<string>>();
  for (const step of steps) {
    const siblings = new Set<string>();
    for (const name of references(step)) {
      const sibling = holder.get(name);
      if (sibling !== undefined && sibling !== step.name) siblings.add(sibling);
    }
    waitsOn.set(step.name, siblings);
  }
  const ordered: StepNode[] = [];
  const placed = new Set<string>();
  let left = steps;
  while (left.length > 0) {
    const ready = left.filter((step) =>
      [...(waitsOn.get(step.name) ?? [])].every((name) => placed.has(name)),
    );
    const next = ready.length > 0 ? ready : left;
    for (const step of next) {
      ordered.push(step);
      placed.add(step.name);
    }
    left = left.filter((step) => !placed.has(step.name));
  }
  return ordered;
}

function stepOf(name: string, step: Json): StepNode {
  if ("loop" in step) {
    const loop = record(step["loop"]);
    return {
      kind: "loop",
      name,
      maxCycles: Number(loop["max_cycles"] ?? 0),
      exitCondition: String(loop["exit_condition"] ?? ""),
      children: stepsOf(step),
    };
  }
  if ("fan_out" in step) {
    const fanOut = record(step["fan_out"]);
    if ("range" in fanOut) {
      return { kind: "fan_out", name, over: `range ${String(fanOut["range"])}`, children: stepsOf(step) };
    }
    const input = String(fanOut["over"] ?? "");
    return { kind: "fan_out", name, over: `over ${input}`, input, children: stepsOf(step) };
  }
  if ("flow" in step) {
    return {
      kind: "flow",
      name,
      flow: String(step["flow"]),
      params: strings(step["params"]),
    };
  }
  return {
    kind: "task",
    name,
    handler: String(step["handler"] ?? ""),
    queue: String(step["queue"] ?? "default"),
    params: strings(step["params"]),
    fixedParams: record(step["fixed_params"]),
  };
}

export type Scope = Address["scope"];

/** One text per address, to find a run's task or sub-run for a step instance. */
export function addressKey(step: string, scope: Scope): string {
  return `${step}@${scope.map(([name, n]) => `${name}:${n}`).join("/")}`;
}

/** What a run has done, by address: the outline and the graph both read it. */
export interface Published {
  /** The run's tasks by their address. */
  tasks: Map<string, Task>;
  /** The run's sub-flow runs by the address of the step that started them. */
  subRuns: Map<string, Run>;
  /** Every address above, for finding a container's iterations and branches. */
  addresses: Address[];
}

export function published(tasks: Task[], subRuns: Run[]): Published {
  const addresses: Address[] = [];
  const byTask = new Map<string, Task>();
  for (const task of tasks) {
    byTask.set(addressKey(task.address.step, task.address.scope), task);
    addresses.push(task.address);
  }
  const bySubRun = new Map<string, Run>();
  for (const run of subRuns) {
    if (run.parent_address) {
      bySubRun.set(addressKey(run.parent_address.step, run.parent_address.scope), run);
      addresses.push(run.parent_address);
    }
  }
  return { tasks: byTask, subRuns: bySubRun, addresses };
}

/**
 * The numbers a container at `scope` has been entered with: its iterations
 * or branches so far, from the addresses of what the run published.
 */
export function instancesOf(
  container: string,
  scope: Scope,
  addresses: Address[],
): number[] {
  const numbers = new Set<number>();
  for (const address of addresses) {
    const level = address.scope[scope.length];
    if (
      level !== undefined &&
      level[0] === container &&
      scope.every(([name, n], i) => address.scope[i]?.[0] === name && address.scope[i]?.[1] === n)
    ) {
      numbers.add(level[1]);
    }
  }
  return [...numbers].sort((a, b) => a - b);
}

/** The latest iteration a loop at `scope` has reached; 0 before the first. */
export function lastIteration(loop: string, scope: Scope, addresses: Address[]): number {
  const iterations = instancesOf(loop, scope, addresses);
  return iterations[iterations.length - 1] ?? 0;
}

/** What a loop's exit condition said at the end of one iteration. */
export interface IterationOutcome {
  text: string;
  /** The loop stops here without its exit condition met: the run fails on it. */
  failed: boolean;
}

/**
 * What iteration `n` of the loop at `scope` ended with, once its exit
 * condition has a result: the loop ended, went round again, or gave up. The
 * manager stores tasks, not loops, so a loop's fate is read from its exit
 * condition's results, as the planner decides it. Nothing while the exit
 * condition has not succeeded.
 */
export function iterationOutcome(
  loop: Extract<StepNode, { kind: "loop" }>,
  scope: Scope,
  n: number,
  done: Published,
): IterationOutcome | undefined {
  const exit = done.tasks.get(addressKey(loop.exitCondition, [...scope, [loop.name, n]]));
  if (exit?.status !== "succeeded") return undefined;
  if (exit.result === true) {
    return { text: `${loop.exitCondition} returned true: the loop ended here`, failed: false };
  }
  if (exit.result !== false) {
    return { text: `${loop.exitCondition} returned neither true nor false`, failed: true };
  }
  if (n < lastIteration(loop.name, scope, done.addresses)) {
    return {
      text: `${loop.exitCondition} returned false: iteration ${n + 1} followed`,
      failed: false,
    };
  }
  if (n >= loop.maxCycles) {
    return {
      text: `${loop.exitCondition} returned false on the last of ${loop.maxCycles} iterations`,
      failed: true,
    };
  }
  return { text: `${loop.exitCondition} returned false`, failed: false };
}

/**
 * Whether a loop stopped without its exit condition met, at its latest
 * iteration: what a run fails on with "still not done after max_cycles".
 */
export function loopFailed(
  loop: Extract<StepNode, { kind: "loop" }>,
  scope: Scope,
  done: Published,
): boolean {
  const last = lastIteration(loop.name, scope, done.addresses);
  return last > 0 && (iterationOutcome(loop, scope, last, done)?.failed ?? false);
}

/**
 * A step as the flow's file wrote it, by its name, from anywhere in the flow:
 * a step's name is its own within a flow. Undefined when there is none.
 */
export function rawStep(content: unknown, name: string): Json | undefined {
  const pending = [record(record(content)["steps"])];
  for (let steps = pending.pop(); steps !== undefined; steps = pending.pop()) {
    for (const [key, raw] of Object.entries(steps)) {
      const step = record(raw);
      if (key === name) return step;
      if ("steps" in step) pending.push(record(step["steps"]));
    }
  }
  return undefined;
}

/** Whether a loop of the flow exits on the step: its result is true or false. */
export function isExitCondition(content: unknown, name: string): boolean {
  const within = (steps: StepNode[]): boolean =>
    steps.some(
      (step) =>
        (step.kind === "loop" && step.exitCondition === name) ||
        ((step.kind === "loop" || step.kind === "fan_out") && within(step.children)),
    );
  return within(stepsOf(content));
}
