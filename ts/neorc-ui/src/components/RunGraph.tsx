import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  type NodeTypes,
} from "@xyflow/react";
import { createContext, useCallback, useContext, useMemo, useState } from "react";
// React Flow's own styles, once, bundled with ours.
import "@xyflow/react/dist/style.css";

import type { Published, Scope, StepNode } from "../definition";
import { shortId } from "../format";
import {
  loopKey,
  runGraph,
  stepNodeId,
  type LoopShown,
  type NodeData,
  type Picked,
} from "../graph";
import { href } from "../router";
import { StatusDot } from "./StatusBadge";
import { TaskDetails } from "./TaskDetails";

type GraphFlowNode = Node<NodeData>;

/** Show iteration `n` of the loop `loop` at `scope`; `latest` when it is the last so far. */
type Pick = (loop: string, scope: Scope, n: number, latest: boolean) => void;

/** How a loop's node asks for another iteration: React Flow gives a node its data only. */
const PickContext = createContext<Pick>(() => {});

/** The task whose details are open: a step, in the branches and iterations of `scope`. */
interface Selected {
  step: string;
  scope: Scope;
}

/**
 * The run's steps as a graph: a node per step, loops and a fan-out's
 * branches as boxes around them, edges from each step to the ones reading
 * it. A loop is one box, showing its latest iteration until another is
 * picked on it. Read-only: pan and zoom, click a task for its details.
 */
export function RunGraph({ steps, done }: { steps: StepNode[]; done: Published }) {
  const [picked, setPicked] = useState<Picked>(() => new Map());
  // The poll gives new tasks and sub-runs only when something changed, so
  // the layout is redone only then, or when another iteration is picked.
  const graph = useMemo(() => runGraph(steps, done, picked), [steps, done, picked]);
  const [chosen, setChosen] = useState<Selected | null>(null);
  const selectedId = chosen && stepNodeId(chosen.step, chosen.scope);
  const selected = graph.nodes.find((node) => node.id === selectedId)?.data;
  const pick = useCallback<Pick>((loop, scope, n, latest) => {
    setPicked((before) => {
      const after = new Map(before);
      // The latest is not pinned: a live run's loop goes on to its next.
      if (latest) after.delete(loopKey(loop, scope));
      else after.set(loopKey(loop, scope), n);
      return after;
    });
    // Details open on a step of the loop follow it to the iteration picked.
    setChosen((before) => {
      const at = before?.scope[scope.length];
      const within =
        before &&
        at?.[0] === loop &&
        scope.every(([name, m], i) => before.scope[i]?.[0] === name && before.scope[i]?.[1] === m);
      if (!within) return before;
      const moved: Scope = [...scope, [loop, n], ...before.scope.slice(scope.length + 1)];
      return { step: before.step, scope: moved };
    });
  }, []);
  const nodes = useMemo(
    (): GraphFlowNode[] =>
      graph.nodes.map((node) => ({
        ...node,
        selected: node.id === selectedId,
        // The size the layout gave is the size drawn. Said here, React Flow
        // has nothing to measure: it measures a node once, when it first
        // draws it, and these are made anew whenever the graph changes, so
        // without this "fit view" would wait for a measure that never comes.
        measured: { width: node.width, height: node.height },
        // Where edges attach, known from the sizes: React Flow measures the
        // handles in the browser, and draws edges from these until then.
        handles: [
          { type: "target", position: Position.Top, x: node.width / 2, y: 0, width: 1, height: 1 },
          {
            type: "source",
            position: Position.Bottom,
            x: node.width / 2,
            y: node.height,
            width: 1,
            height: 1,
          },
        ],
        // A parent's box is drawn behind its children, not over them.
        ...(node.type === "container" && { zIndex: -1 }),
      })),
    [graph, selectedId],
  );
  const edges = useMemo(
    (): Edge[] =>
      graph.edges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        type: "smoothstep",
        markerEnd: { type: MarkerType.ArrowClosed },
        ariaLabel: `${edge.source} feeds ${edge.target}`,
      })),
    [graph],
  );

  return (
    <div className="run-graph">
      <div className="run-graph-canvas" aria-label="Run graph">
        <PickContext value={pick}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            fitView
            minZoom={0.05}
            colorMode="system"
            nodesDraggable={false}
            nodesConnectable={false}
            nodesFocusable
            edgesFocusable={false}
            onNodeClick={(_, node) => {
              // A click on a loop's controls picks an iteration, and leaves the details be.
              if (node.data.kind === "container") return;
              const task = node.data.kind === "task" ? node.data.task : undefined;
              setChosen(task ? { step: task.address.step, scope: task.address.scope } : null);
            }}
            onPaneClick={() => setChosen(null)}
          >
            <Background />
            <Controls showInteractive={false} />
            <MiniMap pannable zoomable />
          </ReactFlow>
        </PickContext>
      </div>
      {selected?.kind === "task" && selected.task && (
        <aside className="run-graph-panel" aria-label={`${selected.name} details`}>
          <div className="run-graph-panel-head">
            <strong>
              {selected.name}
              {selected.task.address.scope.map(([name, n]) => (
                <span key={name} className="muted">
                  {" "}
                  {name} {n}
                </span>
              ))}
            </strong>
            <button type="button" onClick={() => setChosen(null)}>
              Close
            </button>
          </div>
          <TaskDetails task={selected.task} open />
        </aside>
      )}
    </div>
  );
}

const NODE_TYPES: NodeTypes = {
  task: TaskNode,
  flow: FlowNode,
  container: ContainerNode,
};

function TaskNode({ data }: NodeProps<Node<Extract<NodeData, { kind: "task" }>>>) {
  return (
    <div className={`graph-node graph-task${data.task ? "" : " graph-none"}`}>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <StatusDot status={data.status} />
      <div className="graph-node-text">
        <strong>{data.name}</strong>
        <span className="muted">{data.handler}</span>
      </div>
      <span className="graph-node-when mono">{data.duration || data.status}</span>
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  );
}

function FlowNode({ data }: NodeProps<Node<Extract<NodeData, { kind: "flow" }>>>) {
  return (
    <div className={`graph-node graph-flow${data.run ? "" : " graph-none"}`}>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <StatusDot status={data.status} />
      <div className="graph-node-text">
        <strong>{data.name}</strong>
        <span className="muted">sub-flow {data.flow}</span>
        {data.run && (
          <a href={href({ name: "run", id: data.run.id })}>run {shortId(data.run.id)}</a>
        )}
      </div>
      <span className="graph-node-when mono">{data.duration || data.status}</span>
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  );
}

function ContainerNode({
  data,
  width,
  height,
}: NodeProps<Node<Extract<NodeData, { kind: "container" }>>>) {
  const { loop } = data;
  return (
    <div
      className={`graph-container${data.entered ? "" : " graph-none"}${loop?.failed ? " graph-failed" : ""}`}
      style={{ width, height }}
    >
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <div className="graph-container-title">
        {/* A loop says which iteration it shows where one is picked, below. */}
        <strong>{data.name}</strong> {!loop && <span>{data.instance} </span>}
        <span className="muted">{data.about}</span>
      </div>
      {loop && <IterationPicker name={data.name} loop={loop} />}
    </div>
  );
}

/**
 * Which iteration of a loop its node shows, with the controls to show
 * another, and what the iteration ended with. `nodrag`, `nopan` and
 * `nowheel` keep React Flow from taking the pointer over the controls.
 */
function IterationPicker({ name, loop }: { name: string; loop: LoopShown }) {
  const pick = useContext(PickContext);
  const { iterations, shown, outcome } = loop;
  const last = iterations[iterations.length - 1];
  const at = iterations.indexOf(shown);
  const show = (n: number | undefined) => {
    if (n !== undefined) pick(name, loop.scope, n, n === last);
  };
  return (
    <div className="graph-picker nodrag nopan nowheel">
      <button
        type="button"
        aria-label={`${name}: previous iteration`}
        disabled={at <= 0}
        onClick={() => show(iterations[at - 1])}
      >
        ‹
      </button>
      <label>
        iteration{" "}
        <select
          aria-label={`${name}: iteration shown`}
          value={shown}
          onChange={(event) => show(Number(event.target.value))}
        >
          {iterations.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>{" "}
        of {last}
      </label>
      <button
        type="button"
        aria-label={`${name}: next iteration`}
        disabled={at === iterations.length - 1}
        onClick={() => show(iterations[at + 1])}
      >
        ›
      </button>
      {outcome && (
        <span
          className={`graph-outcome ${outcome.failed ? "graph-outcome-failed" : "muted"}`}
          title={outcome.text}
        >
          {outcome.text}
        </span>
      )}
    </div>
  );
}
