// A run as a graph: one node per step, a parent node per loop and per branch
// of a fan-out, a parent node per switch with one inside it per case, an edge
// from each step a param refers to, laid out by dagre one container level at
// a time. A loop is drawn once, as the definition has it, showing one of its
// iterations: the latest, or the one picked. No React here: the view draws
// what this gives.
import dagre from "@dagrejs/dagre";

import type { Run, RunStatus, Task, TaskStatus } from "./api";
import {
  addressKey,
  chosenCase,
  instancesOf,
  iterationOutcome,
  lastIteration,
  loopFailed,
  referencedStep,
  type IterationOutcome,
  type Published,
  type Scope,
  type StepNode,
} from "./definition";
import { formatDuration } from "./format";

/**
 * What a node shows. `task` and `run` are there for the details panel of a
 * run; the queue, the params and the fixed params for a definition's, which
 * has no run to show.
 */
export type NodeData =
  | {
      kind: "task";
      name: string;
      handler: string;
      queue: string;
      params: Record<string, string>;
      fixedParams: Record<string, unknown>;
      status: TaskStatus | "none";
      duration: string;
      task?: Task;
    }
  | {
      kind: "flow";
      name: string;
      flow: string;
      params: Record<string, string>;
      status: RunStatus | "none";
      duration: string;
      run?: Run;
    }
  | {
      kind: "container";
      name: string;
      /** A switch holds one container per case, and a case the case's steps. */
      container: "loop" | "fan_out" | "switch" | "case";
      about: string;
      /** "iteration 2", "branch 1", "case is_vip", "chosen", or "not entered yet". */
      instance: string;
      /** Of a switch, whether it chose a case; of a case, whether it is that one. */
      entered: boolean;
      /** For a loop that has been entered: its iterations, and the one drawn. */
      loop?: LoopShown;
    };

/** A loop's iterations so far, and the one its node shows. */
export interface LoopShown {
  /** The scope the loop itself is in: with its name, what a pick is kept by. */
  scope: Scope;
  iterations: number[];
  shown: number;
  /** What the iteration shown ended with, once its exit condition said. */
  outcome?: IterationOutcome;
  /** The loop stopped without its exit condition met, whichever iteration is shown. */
  failed: boolean;
}

/**
 * The iteration to show for each loop the reader picked one for, by
 * `loopKey`. A loop not in it shows its latest, and follows a live run.
 */
export type Picked = ReadonlyMap<string, number>;

/** What a pick is kept by: the loop, and the scope it is in. */
export function loopKey(loop: string, scope: Scope): string {
  return addressKey(loop, scope);
}

/** A node as React Flow takes it: a child's position is relative to its parent's. */
export interface GraphNode {
  id: string;
  type: NodeData["kind"];
  position: { x: number; y: number };
  width: number;
  height: number;
  data: NodeData;
  parentId?: string;
  extent?: "parent";
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  /** The params of the target that refer to the source. */
  params: string[];
}

export interface Graph {
  /** Parents before their children, as React Flow requires. */
  nodes: GraphNode[];
  edges: GraphEdge[];
  width: number;
  height: number;
}

// Sizes in pixels, agreed with the node components: the layout needs them
// before anything is drawn, and jsdom measures nothing.
export const LEAF_HEIGHT = 58;
export const LEAF_MIN_WIDTH = 200;
export const LEAF_MAX_WIDTH = 400;
const CHAR_WIDTH = 7.5;
/** Room beside the text: padding, the status dot, the duration. */
const LEAF_PADDING = 110;
/** A container's title bar, above its children. */
export const CONTAINER_HEADER = 32;
/** A loop's title bar once entered: the title, and a row to pick an iteration on. */
export const LOOP_HEADER = 68;
/** The widest a loop is made for its title bar alone; longer text is cut short. */
const LOOP_MAX_WIDTH = 680;
/** The controls of the row an iteration is picked on, before what it ended with. */
const PICKER_WIDTH = 230;
export const CONTAINER_PADDING = 16;
const NODE_SEP = 32;
const RANK_SEP = 40;

/** One id per step instance: the address of what the run publishes there. */
export function stepNodeId(step: string, scope: Scope): string {
  return addressKey(step, scope);
}

/** One id per container instance; `n` is 0 for a container not entered yet. */
export function containerNodeId(container: string, scope: Scope, n: number): string {
  return `${addressKey(container, scope)}#${n}`;
}

/** One id per case of a switch, in each iteration and branch the switch is in. */
export function caseNodeId(switchName: string, scope: Scope, caseName: string): string {
  return `${addressKey(switchName, scope)}/${caseName}`;
}

/**
 * The graph of `steps` for what the run `done` so far: a node for every
 * step, in every branch of a fan-out and in one iteration of a loop, the
 * containers around them, edges for the references between them, all
 * positioned. Each loop shows the iteration `picked` for it, or its latest.
 */
export function runGraph(
  steps: StepNode[],
  done: Published,
  picked: Picked = new Map(),
): Graph {
  const state: Build = {
    done,
    picked,
    paths: stepPaths(steps),
    nodes: [],
    byId: new Map(),
    edges: new Map(),
    levelEdges: new Map(),
    fanOuts: new Map(),
  };
  const level = build(state, steps, [], []);
  const { width, height } = layoutLevel(state, level, undefined);
  // A reference into a fan-out branch that an earlier iteration never had
  // names a node that was never built: the core would refuse it, the graph
  // leaves it out.
  const edges = [...state.edges.values()].filter(
    (edge) => state.byId.has(edge.source) && state.byId.has(edge.target),
  );
  return { nodes: state.nodes, edges, width, height };
}

interface Container {
  name: string;
  kind: "loop" | "fan_out";
}

/** A case of a switch, as what a step is inside of: it adds nothing to a scope. */
interface InCase {
  kind: "case";
  switch: string;
  name: string;
}

type Around = Container | InCase;

/**
 * One end of an edge: a node, and the nodes holding it, outermost first, a
 * container instance, or a switch and then its case.
 */
interface End {
  id: string;
  holders: string[];
}

interface Build {
  done: Published;
  picked: Picked;
  /**
   * What is around each step a reference can name, a task, a sub-flow or a
   * switch, outermost first.
   */
  paths: Map<string, Around[]>;
  nodes: GraphNode[];
  byId: Map<string, GraphNode>;
  edges: Map<string, GraphEdge>;
  /**
   * For each level, by the id of the node holding it, the edges it lays out:
   * each edge counted once, at the level of its ends' nearest shared
   * container, between the members holding its ends.
   */
  levelEdges: Map<string | undefined, [string, string][]>;
  /**
   * By level, each fan-out's branch nodes and each switch's case nodes in
   * order, to keep them left to right.
   */
  fanOuts: Map<string | undefined, string[][]>;
}

function stepPaths(
  steps: StepNode[],
  path: Around[] = [],
  into = new Map<string, Around[]>(),
): Map<string, Around[]> {
  for (const step of steps) {
    if (step.kind === "loop" || step.kind === "fan_out") {
      stepPaths(step.children, [...path, { name: step.name, kind: step.kind }], into);
      continue;
    }
    into.set(step.name, path);
    if (step.kind === "switch") {
      for (const one of step.cases) {
        stepPaths(one.children, [...path, { kind: "case", switch: step.name, name: one.name }], into);
      }
    }
  }
  return into;
}

/** The nodes holding a step under `path` at `scope`, outermost first. */
function holdersOf(path: Around[], scope: Scope): string[] {
  const holders: string[] = [];
  let depth = 0;
  for (const around of path) {
    const within = scope.slice(0, depth);
    if (around.kind === "case") {
      holders.push(stepNodeId(around.switch, within), caseNodeId(around.switch, within, around.name));
    } else {
      holders.push(containerNodeId(around.name, within, scope[depth]?.[1] ?? 0));
      depth += 1;
    }
  }
  return holders;
}

/** A node's size from the text it shows. */
function leafSize(name: string, sub: string): { width: number; height: number } {
  const longest = Math.max(name.length, sub.length);
  const width = Math.min(
    LEAF_MAX_WIDTH,
    Math.max(LEAF_MIN_WIDTH, LEAF_PADDING + CHAR_WIDTH * longest),
  );
  return { width, height: LEAF_HEIGHT };
}

/**
 * The iteration of the loop at `scope` to show: the one picked, if the loop
 * got that far, else its latest; 0 for a loop not entered yet.
 */
function shownIteration(state: Build, loop: string, scope: Scope): number {
  const iterations = instancesOf(loop, scope, state.done.addresses);
  const picked = state.picked.get(loopKey(loop, scope));
  if (picked !== undefined && iterations.includes(picked)) return picked;
  return lastIteration(loop, scope, state.done.addresses);
}

/**
 * The instances of a container at `scope` that get a node: every branch of
 * a fan-out, the one iteration shown of a loop; 0 alone for one not entered.
 */
function drawn(state: Build, { name, kind }: Container, scope: Scope): number[] {
  if (kind === "loop") return [shownIteration(state, name, scope)];
  const branches = instancesOf(name, scope, state.done.addresses);
  return branches.length === 0 ? [0] : branches;
}

/** Add a node to the graph, under `parentId` if it has a parent. */
function add(state: Build, node: GraphNode, parentId: string | undefined): void {
  if (parentId !== undefined) {
    node.parentId = parentId;
    node.extent = "parent";
  }
  state.nodes.push(node);
  state.byId.set(node.id, node);
}

/**
 * Size a container's node to the level laid out inside it, and to its title
 * bar if that is wider: its children then go in the middle.
 */
function fit(
  state: Build,
  node: GraphNode,
  inner: string[],
  size: { width: number; height: number },
  titleWidth: number,
): void {
  node.width = Math.max(size.width, titleWidth);
  node.height = size.height;
  for (const child of inner) {
    state.byId.get(child)!.position.x += (node.width - size.width) / 2;
  }
}

/**
 * Add the nodes of `steps` at `scope`, inside the nodes `holders`, with the
 * edges into them; gives the ids of the nodes at this level, for laying it
 * out. A fan-out becomes one node per branch, a loop one node, for the
 * iteration shown, and a switch one node holding one per case, each holding
 * a level of its own, laid out at once so the container's size is known here.
 */
function build(
  state: Build,
  steps: StepNode[],
  scope: Scope,
  holders: string[],
): string[] {
  const parentId = holders[holders.length - 1];
  const level: string[] = [];
  const place = (node: GraphNode) => {
    add(state, node, parentId);
    level.push(node.id);
  };
  for (const step of steps) {
    switch (step.kind) {
      case "task": {
        const id = stepNodeId(step.name, scope);
        const task = state.done.tasks.get(id);
        place({
          id,
          type: "task",
          position: { x: 0, y: 0 },
          ...leafSize(step.name, step.handler),
          data: {
            kind: "task",
            name: step.name,
            handler: step.handler,
            queue: step.queue,
            params: step.params,
            fixedParams: step.fixedParams,
            status: task?.status ?? "none",
            duration: task
              ? formatDuration(task.started_at ?? task.created_at, task.finished_at)
              : "",
            ...(task && { task }),
          },
        });
        refer(state, { id, holders }, step.params, scope);
        break;
      }
      case "flow": {
        const id = stepNodeId(step.name, scope);
        const run = state.done.subRuns.get(id);
        place({
          id,
          type: "flow",
          position: { x: 0, y: 0 },
          ...leafSize(step.name, `sub-flow ${step.flow}`),
          data: {
            kind: "flow",
            name: step.name,
            flow: step.flow,
            params: step.params,
            status: run?.status ?? "none",
            duration: run ? formatDuration(run.created_at, run.finished_at) : "",
            ...(run && { run }),
          },
        });
        refer(state, { id, holders }, step.params, scope);
        break;
      }
      case "loop":
      case "fan_out": {
        const what = step.kind === "loop" ? "iteration" : "branch";
        const about =
          step.kind === "loop"
            ? `loop, at most ${step.maxCycles}, until ${step.exitCondition}`
            : `fan-out ${step.over}`;
        const ids: string[] = [];
        for (const n of drawn(state, step, scope)) {
          const id = containerNodeId(step.name, scope, n);
          const instance = n === 0 ? "not entered yet" : `${what} ${n}`;
          const loop = step.kind === "loop" && n !== 0 ? loopShown(state, step, scope, n) : undefined;
          const node: GraphNode = {
            id,
            type: "container",
            position: { x: 0, y: 0 },
            width: 0,
            height: 0,
            data: {
              kind: "container",
              name: step.name,
              container: step.kind,
              about,
              instance,
              entered: n !== 0,
              ...(loop && { loop }),
            },
          };
          place(node);
          ids.push(id);
          // What a fan-out iterates over feeds each of its branches.
          if (step.kind === "fan_out" && step.input) {
            refer(state, { id, holders }, { over: step.input }, scope);
          }
          const inner = build(state, step.children, [...scope, [step.name, n]], [...holders, id]);
          const size = layoutLevel(state, inner, id);
          const title = Math.max(
            leafSize(`${step.name} ${instance}`, about).width,
            loop ? pickerWidth(loop) : 0,
          );
          fit(state, node, inner, size, title);
        }
        if (step.kind === "fan_out") {
          state.fanOuts.set(parentId, [...(state.fanOuts.get(parentId) ?? []), ids]);
        }
        break;
      }
      case "switch": {
        // A switch is no iteration or branch: its cases, and their steps,
        // are at the scope the switch is at.
        const id = stepNodeId(step.name, scope);
        const chosen = chosenCase(step, scope, state.done);
        const about = `switch on ${step.on}`;
        const instance = chosen === undefined ? "not chosen yet" : `case ${chosen}`;
        const node = containerNode(id, step.name, "switch", about, instance, chosen !== undefined);
        place(node);
        // What a switch chooses on feeds the switch.
        refer(state, { id, holders }, { on: step.on }, scope);
        const cases: string[] = [];
        for (const one of step.cases) {
          const caseId = caseNodeId(step.name, scope, one.name);
          const output = one.output === undefined ? "no output" : `output ${one.output}`;
          const ran =
            chosen === undefined ? "not chosen yet" : chosen === one.name ? "chosen" : "not chosen";
          const caseNode = containerNode(
            caseId,
            one.name,
            "case",
            `case, ${output}`,
            ran,
            chosen === one.name,
          );
          add(state, caseNode, id);
          cases.push(caseId);
          const inner = build(state, one.children, scope, [...holders, id, caseId]);
          const size = layoutLevel(state, inner, caseId);
          fit(state, caseNode, inner, size, leafSize(`${one.name} ${ran}`, `case, ${output}`).width);
        }
        state.fanOuts.set(id, [cases]);
        const size = layoutLevel(state, cases, id);
        // Cases differ in height, and the layout centres a row: line their
        // tops up, at the top of the tallest.
        const top = Math.min(...cases.map((caseId) => state.byId.get(caseId)!.position.y));
        for (const caseId of cases) state.byId.get(caseId)!.position.y = top;
        fit(state, node, cases, size, leafSize(`${step.name} ${instance}`, about).width);
        break;
      }
    }
  }
  return level;
}

/** A container's node, sized once what it holds is laid out. */
function containerNode(
  id: string,
  name: string,
  container: "switch" | "case",
  about: string,
  instance: string,
  entered: boolean,
): GraphNode {
  return {
    id,
    type: "container",
    position: { x: 0, y: 0 },
    width: 0,
    height: 0,
    data: { kind: "container", name, container, about, instance, entered },
  };
}

/** What a loop's node shows of the loop at `scope`, drawn at iteration `n`. */
function loopShown(
  state: Build,
  loop: Extract<StepNode, { kind: "loop" }>,
  scope: Scope,
  n: number,
): LoopShown {
  const outcome = iterationOutcome(loop, scope, n, state.done);
  return {
    scope,
    iterations: instancesOf(loop.name, scope, state.done.addresses),
    shown: n,
    ...(outcome && { outcome }),
    failed: loopFailed(loop, scope, state.done),
  };
}

/** The room a loop's title bar takes for picking an iteration and what it ended with. */
function pickerWidth(loop: LoopShown): number {
  const outcome = loop.outcome ? CHAR_WIDTH * loop.outcome.text.length : 0;
  return Math.min(LOOP_MAX_WIDTH, 2 * CONTAINER_PADDING + PICKER_WIDTH + outcome);
}

/** The height of the title bar of the container node `id`; none for the roots. */
function headerOf(state: Build, id: string | undefined): number {
  if (id === undefined) return 0;
  const data = state.byId.get(id)?.data;
  return data?.kind === "container" && data.loop ? LOOP_HEADER : CONTAINER_HEADER;
}

/**
 * Edges into `target` from the steps `params` refer to, `scope` being the
 * consumer's. The core resolves a reference to what the flows spec says,
 * "Resolving references": a list of every branch of a fan-out the consumer
 * is outside of, and of every iteration of a loop, so far or in all. This
 * draws one edge per node the reference reaches: every branch, and of a
 * loop the one iteration shown, which stands for the loop. What a step
 * received from the other iterations is in its details, in its params.
 */
function refer(
  state: Build,
  target: End,
  params: Record<string, string>,
  scope: Scope,
): void {
  for (const [param, value] of Object.entries(params)) {
    const name = referencedStep(value);
    const path = name === undefined ? undefined : state.paths.get(name);
    if (name === undefined || path === undefined) continue;
    const containers = path.filter((around): around is Container => around.kind !== "case");
    for (const sourceScope of sourceScopes(state, containers, scope)) {
      const source = { id: stepNodeId(name, sourceScope), holders: holdersOf(path, sourceScope) };
      if (source.id === target.id) continue;
      connect(state, source, target, param);
    }
  }
}

/** The scopes with a node that a reference from `scope` reaches, for a step under `path`. */
function sourceScopes(state: Build, path: Container[], scope: Scope): Scope[] {
  let reached: Scope[] = [[]];
  path.forEach((container, depth) => {
    const own = scope[depth];
    // Inside: the consumer is within this container too, and so in the same
    // branch or iteration of it, the only one of a loop that has nodes.
    const inside =
      own !== undefined &&
      own[0] === container.name &&
      scope.slice(0, depth).every(([c], i) => path[i]?.name === c);
    reached = reached.flatMap((so) =>
      // Its own branch or iteration; from outside, every branch, and the
      // iteration shown of a loop.
      (inside ? [own[1]] : drawn(state, container, so)).map(
        (n) => [...so, [container.name, n]] as Scope,
      ),
    );
  });
  return reached;
}

/** Record an edge, once per pair of ends, and at the level that lays it out. */
function connect(state: Build, source: End, target: End, param: string): void {
  const id = `${source.id}->${target.id}`;
  const edge = state.edges.get(id);
  if (edge) {
    edge.params.push(param);
    return;
  }
  state.edges.set(id, { id, source: source.id, target: target.id, params: [param] });
  // The nearest level holding both ends: the innermost node both are within.
  let shared = 0;
  while (
    shared < Math.min(source.holders.length, target.holders.length) &&
    source.holders[shared] === target.holders[shared]
  ) {
    shared += 1;
  }
  const level = shared === 0 ? undefined : source.holders[shared - 1];
  // At that level, an end is the node that holds it, or itself.
  const member = (end: End) => end.holders[shared] ?? end.id;
  const edges = state.levelEdges.get(level) ?? [];
  edges.push([member(source), member(target)]);
  state.levelEdges.set(level, edges);
}

/**
 * Position the nodes of one level, the children of `parentId` or the roots,
 * with dagre, top to bottom, on the edges between them. Gives the level's
 * size: what its container is sized to.
 */
function layoutLevel(
  state: Build,
  level: string[],
  parentId: string | undefined,
): { width: number; height: number } {
  const margin = parentId === undefined ? 0 : CONTAINER_PADDING;
  const graph = new dagre.graphlib.Graph();
  graph.setGraph({
    rankdir: "TB",
    nodesep: NODE_SEP,
    ranksep: RANK_SEP,
    marginx: margin,
    marginy: margin,
  });
  graph.setDefaultEdgeLabel(() => ({}));
  const members = new Set(level);
  for (const id of level) {
    const node = state.byId.get(id)!;
    graph.setNode(id, { width: node.width, height: node.height });
  }
  const between = (pairs: [string, string][]) =>
    pairs.filter(([a, b]) => a !== b && members.has(a) && members.has(b));
  for (const [source, target] of between(state.levelEdges.get(parentId) ?? [])) {
    graph.setEdge(source, target);
  }
  const constraints = between(pairs(state.fanOuts.get(parentId))).map(([left, right]) => ({
    left,
    right,
  }));
  dagre.layout(graph, { constraints });
  const header = headerOf(state, parentId);
  let width = 2 * margin;
  let height = 2 * margin + header;
  for (const id of level) {
    const node = state.byId.get(id)!;
    const laid = graph.node(id);
    node.position = {
      x: laid.x - node.width / 2,
      y: laid.y - node.height / 2 + header,
    };
    width = Math.max(width, node.position.x + node.width + margin);
    height = Math.max(height, node.position.y + node.height + margin);
  }
  return { width, height };
}

/** The consecutive pairs of each chain. */
function pairs(chains: string[][] = []): [string, string][] {
  return chains.flatMap((chain) =>
    chain.flatMap((id, i): [string, string][] => {
      const next = chain[i + 1];
      return next === undefined ? [] : [[id, next]];
    }),
  );
}
