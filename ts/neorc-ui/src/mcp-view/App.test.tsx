import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Run, Task } from "../api";
import { ACTIVE, APPROVAL, HELLO, REVIEW, WAITING } from "../test/fixtures";
import { App } from "./App";
import { parse, PANEL_TOOL, themeOf, toolAnswer, type Handlers, type ToolAnswer } from "./bridge";
import { panelData, type PanelData } from "./Panel";

describe("the bridge", () => {
  it("reads JSON-RPC and nothing else", () => {
    expect(parse({ jsonrpc: "2.0", id: 1, result: { a: 1 } })).toEqual({
      kind: "response",
      id: 1,
      result: { a: 1 },
    });
    expect(parse({ jsonrpc: "2.0", id: "x", error: { code: 1, message: "no" } })).toEqual({
      kind: "error",
      id: "x",
      message: "no",
    });
    expect(parse({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: {} })).toEqual({
      kind: "notification",
      method: "ui/notifications/tool-result",
      params: {},
    });
    expect(parse({ jsonrpc: "2.0", id: 3, method: "ping" })).toMatchObject({ kind: "request" });
    expect(parse({ id: 1, result: {} })).toBeNull();
    expect(parse("hello")).toBeNull();
    expect(parse({ jsonrpc: "2.0", id: {}, result: {} })).toBeNull();
  });

  it("reads a theme it knows, and no other", () => {
    expect(themeOf({ theme: "dark" })).toBe("dark");
    expect(themeOf({ theme: "system" })).toBeUndefined();
    expect(themeOf(null)).toBeUndefined();
  });

  it("reads a tool's answer: its text, its structured content, and whether it failed", () => {
    expect(
      toolAnswer({
        content: [{ type: "text", text: "a" }, { type: "image" }, { type: "text", text: "b" }],
        structuredContent: { shows: "nothing", flows: [] },
      }),
    ).toEqual({ text: "a\nb", structured: { shows: "nothing", flows: [] }, isError: false });
    expect(toolAnswer({ content: [], isError: true })).toMatchObject({ isError: true });
    expect(toolAnswer(undefined)).toMatchObject({ isError: true });
  });

  it("takes only the panel's own data", () => {
    expect(panelData({ shows: "nothing", flows: [] })).toBeDefined();
    expect(panelData({ shows: "elsewhere" })).toBeUndefined();
    expect(panelData(undefined)).toBeUndefined();
  });
});

/** A host the test drives: what the view asked, and a way to answer for the host. */
function fakeHost(answers: Record<string, PanelData> = {}) {
  const calls: { name: string; args: Record<string, unknown> | undefined }[] = [];
  let handlers: Handlers | undefined;
  const connectTo = vi.fn((_parent: Window, given: Handlers) => {
    handlers = given;
    return {
      callTool: async (name: string, args?: Record<string, unknown>): Promise<ToolAnswer> => {
        calls.push({ name, args });
        const data = answers[name];
        return { structured: data as unknown as Record<string, unknown>, text: "", isError: !data };
      },
    };
  });
  const panel = (data: PanelData) =>
    act(() => handlers?.onPanel({ structured: data as never, text: "", isError: false }));
  const theme = (value: "light" | "dark") => act(() => handlers?.onTheme(value));
  return { connectTo, calls, panel, theme };
}

const FAILED_TASK: Task = {
  ...REVIEW,
  status: "failed",
  error: "exit code 1\nTraceback: KeyError: 'topic'",
  attempts: 2,
};

const FAILED_RUN: Run = { ...WAITING, status: "failed", reason: "task review_draft failed" };

describe("the view", () => {
  it("waits for the host, then shows the flows when nothing is shown yet", async () => {
    const host = fakeHost();
    render(<App connectTo={host.connectTo} />);
    expect(screen.getByText(/waiting for neorc/i)).toBeInTheDocument();
    await host.panel({ shows: "nothing", flows: ["approval", "hello"] });
    expect(screen.getByText(/nothing shown yet/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "hello" })).toHaveAttribute("href", "#/neorc/flows/hello");
  });

  it("draws a flow as the UI does", async () => {
    const host = fakeHost();
    render(<App connectTo={host.connectTo} />);
    await host.panel({ shows: "flow", flow: APPROVAL, versions: ["1.0.0"] });
    expect(screen.getByRole("heading", { name: /approval/ })).toBeInTheDocument();
    expect(screen.getByLabelText("Flow graph")).toBeInTheDocument();
    expect(await screen.findByText("review_draft")).toBeInTheDocument();
  });

  it("draws a failed run with what failed, its error first", async () => {
    const host = fakeHost();
    render(<App connectTo={host.connectTo} />);
    await host.panel({ shows: "run", run: FAILED_RUN, flow: APPROVAL, tasks: [FAILED_TASK], sub_runs: [] });
    const failed = screen.getByRole("region", { name: "What failed" });
    expect(failed).toHaveTextContent("review_draft");
    expect(failed).toHaveTextContent("KeyError: 'topic'");
    expect(failed).toHaveTextContent("attempt 2");
    expect(screen.getByText("task review_draft failed")).toBeInTheDocument();
    expect(screen.getByLabelText("Run graph")).toBeInTheDocument();
  });

  it("shows another run or flow through neorc-mcp when a link is followed", async () => {
    const shownFlow: PanelData = { shows: "flow", flow: HELLO, versions: ["1.0.0"] };
    const host = fakeHost({ show_flow: shownFlow });
    render(<App connectTo={host.connectTo} />);
    await host.panel({ shows: "nothing", flows: ["hello"] });
    fireEvent.click(screen.getByRole("link", { name: "hello" }));
    await waitFor(() => expect(host.calls).toEqual([{ name: "show_flow", args: { name: "hello" } }]));
    expect(await screen.findByRole("heading", { name: /hello/ })).toBeInTheDocument();
  });

  it("reads a run in progress again until it ends", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const done: PanelData = { shows: "run", run: { ...ACTIVE, status: "succeeded" }, flow: HELLO, tasks: [], sub_runs: [] };
      const host = fakeHost({ [PANEL_TOOL]: done });
      render(<App connectTo={host.connectTo} />);
      await host.panel({ shows: "run", run: ACTIVE, flow: HELLO, tasks: [], sub_runs: [] });
      await act(() => vi.advanceTimersByTimeAsync(3100));
      expect(host.calls).toEqual([{ name: PANEL_TOOL, args: undefined }]);
      await act(() => vi.advanceTimersByTimeAsync(9000));
      expect(host.calls).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("takes the host's theme", async () => {
    const host = fakeHost();
    render(<App connectTo={host.connectTo} />);
    await host.theme("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });
});
