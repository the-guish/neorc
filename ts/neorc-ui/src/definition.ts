// A flow definition as uploaded, read into the steps the run page draws, and
// the addresses of a run's tasks and sub-runs read into loop iterations,
// fan-out branches and the case a switch chose.
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
    }
  | SwitchNode;

export interface SwitchNode {
  kind: "switch";
  name: string;
  /** The reference whose value names the case that runs. */
  on: string;
  cases: SwitchCase[];
  /**
   * The switch `on` reads, when it reads one, with the number of loops and
   * fan-outs around that switch: its scope is the start of this one's.
   */
  onSwitch?: { step: SwitchNode; depth: number };
}

/** One case of a switch: its steps, in the switch's own iteration and branch. */
export interface SwitchCase {
  name: string;
  /** The step whose result is the case's output; none for `output: null`. */
  output?: string;
  children: StepNode[];
}

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
  return linked(parsed(content));
}

function parsed(content: unknown): StepNode[] {
  return inRunOrder(
    Object.entries(record(record(content)["steps"])).map(([name, raw]) =>
      stepOf(name, record(raw)),
    ),
  );
}

/**
 * The steps, each switch that chooses on another switch given that switch:
 * a step's name is its own within a flow, so it is found by its name.
 */
function linked(steps: StepNode[]): StepNode[] {
  const switches = new Map<string, { step: SwitchNode; depth: number }>();
  const walk = (level: StepNode[], depth: number) => {
    for (const step of level) {
      if (step.kind === "switch") switches.set(step.name, { step, depth });
      // A case adds no level to a scope; a loop and a fan-out add one.
      walk(inside(step), step.kind === "switch" ? depth : depth + 1);
    }
  };
  walk(steps, 0);
  for (const { step } of switches.values()) {
    const read = switches.get(/^switches\.([^.]+)$/.exec(step.on)?.[1] ?? "");
    if (read && read.step !== step) step.onSwitch = read;
  }
  return steps;
}

/**
 * The step a `tasks.`, `flows.` or `switches.` reference names; nothing for
 * the rest.
 */
export function referencedStep(value: string): string | undefined {
  const match = /^(?:tasks|flows|switches)\.([^.]+)/.exec(value);
  return match?.[1];
}

/** The steps directly inside a step: a container's, or those of every case. */
function inside(step: StepNode): StepNode[] {
  if (step.kind === "loop" || step.kind === "fan_out") return step.children;
  if (step.kind === "switch") return step.cases.flatMap((one) => one.children);
  return [];
}

/** Every step a step refers to, from anywhere inside it. */
function references(step: StepNode, into = new Set<string>()): Set<string> {
  const values =
    step.kind === "task" || step.kind === "flow"
      ? Object.values(step.params)
      : step.kind === "fan_out" && step.input
        ? [step.input]
        : step.kind === "switch"
          ? [step.on]
          : [];
  for (const value of values) {
    const name = referencedStep(value);
    if (name !== undefined) into.add(name);
  }
  for (const child of inside(step)) references(child, into);
  return into;
}

/** The names of a step and of every step inside it. */
function names(step: StepNode, into = new Set<string>()): Set<string> {
  into.add(step.name);
  for (const child of inside(step)) names(child, into);
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
  if ("switch" in step) {
    const config = record(step["switch"]);
    return {
      kind: "switch",
      name,
      on: String(config["on"] ?? ""),
      cases: Object.entries(record(config["cases"])).map(([caseName, raw]) => {
        const output = record(raw)["output"];
        return {
          name: caseName,
          ...(typeof output === "string" && { output }),
          children: parsed(raw),
        };
      }),
    };
  }
  if ("loop" in step) {
    const loop = record(step["loop"]);
    return {
      kind: "loop",
      name,
      maxCycles: Number(loop["max_cycles"] ?? 0),
      exitCondition: String(loop["exit_condition"] ?? ""),
      children: parsed(step),
    };
  }
  if ("fan_out" in step) {
    const fanOut = record(step["fan_out"]);
    if ("range" in fanOut) {
      return { kind: "fan_out", name, over: `range ${String(fanOut["range"])}`, children: parsed(step) };
    }
    const input = String(fanOut["over"] ?? "");
    return { kind: "fan_out", name, over: `over ${input}`, input, children: parsed(step) };
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
  /** The run's inputs, where a switch chooses its case on one. */
  inputs?: Record<string, unknown>;
}

export function published(
  tasks: Task[],
  subRuns: Run[],
  inputs?: Record<string, unknown>,
): Published {
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
  return { tasks: byTask, subRuns: bySubRun, addresses, ...(inputs && { inputs }) };
}

/**
 * The case the switch at `scope` chose, once the run says: the value its
 * `on` reads there, an input, a task's result, a sub-flow's output or
 * another switch's, or else the case a step of was published. The core
 * decides it, from `on` alone. The manager stores tasks and sub-runs, not
 * switches, so another switch is read as the core reads it: from the case
 * it chose. Nothing while the run does not say, and nothing in a loop or
 * fan-out the run has not entered.
 *
 * `seen` are the switches this one is being read for: a flow the core
 * refuses can have switches that choose on each other.
 */
export function chosenCase(
  step: SwitchNode,
  scope: Scope,
  done: Published,
  seen: ReadonlySet<string> = new Set(),
): string | undefined {
  // A loop or fan-out the run has not entered is drawn at instance 0:
  // nothing inside it has chosen anything yet.
  if (seen.has(step.name) || scope.some(([, n]) => n === 0)) return undefined;
  const value = onValue(step, scope, done, new Set([...seen, step.name]));
  if (typeof value === "string" && step.cases.some((one) => one.name === value)) return value;
  return step.cases.find((one) => {
    const within = new Set(one.children.flatMap((child) => [...names(child)]));
    return done.addresses.some(
      (address) =>
        within.has(address.step) &&
        scope.every(([name, n], i) => address.scope[i]?.[0] === name && address.scope[i]?.[1] === n),
    );
  })?.name;
}

/**
 * What `on` reads for a switch at `scope`: one value, in the switch's own
 * iteration and branch. The step it reads is around the switch, so its
 * address is the start of the switch's scope: the longest that has one.
 */
function onValue(
  step: SwitchNode,
  scope: Scope,
  done: Published,
  seen: ReadonlySet<string>,
): unknown {
  if (step.onSwitch) {
    const { step: read, depth } = step.onSwitch;
    return outputOf(read, scope.slice(0, depth), done, seen);
  }
  const input = /^inputs\.([^.]+)$/.exec(step.on)?.[1];
  if (input !== undefined) return done.inputs?.[input];
  const name = referencedStep(step.on);
  if (name === undefined) return undefined;
  for (let depth = scope.length; depth >= 0; depth -= 1) {
    const key = addressKey(name, scope.slice(0, depth));
    const task = done.tasks.get(key);
    if (task) return task.status === "succeeded" ? task.result : undefined;
    const run = done.subRuns.get(key);
    if (run) return run.status === "succeeded" ? run.output : undefined;
  }
  return undefined;
}

/**
 * The output of the switch at `scope`, `switches.<name>`, as the core reads
 * it: the result of the step the chosen case's output names, in the
 * switch's own iteration and branch. Nothing for a case with no output, and
 * for a step in a loop or fan-out of the case, whose results are a list:
 * neither names a case.
 */
function outputOf(
  step: SwitchNode,
  scope: Scope,
  done: Published,
  seen: ReadonlySet<string>,
): unknown {
  const chosen = chosenCase(step, scope, done, seen);
  const ran = step.cases.find((one) => one.name === chosen);
  const name = referencedStep(ran?.output ?? "");
  if (ran === undefined || name === undefined) return undefined;
  const inner = ran.children.find((child) => child.name === name);
  if (inner?.kind === "switch") return outputOf(inner, scope, done, seen);
  const key = addressKey(name, scope);
  const task = done.tasks.get(key);
  if (task) return task.status === "succeeded" ? task.result : undefined;
  const run = done.subRuns.get(key);
  return run?.status === "succeeded" ? run.output : undefined;
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
      // The steps of a switch are in its cases.
      for (const one of Object.values(record(record(step["switch"])["cases"]))) {
        pending.push(record(record(one)["steps"]));
      }
    }
  }
  return undefined;
}

/** Whether a loop of the flow exits on the step: its result is true or false. */
export function isExitCondition(content: unknown, name: string): boolean {
  const within = (steps: StepNode[]): boolean =>
    steps.some(
      (step) => (step.kind === "loop" && step.exitCondition === name) || within(inside(step)),
    );
  return within(stepsOf(content));
}
