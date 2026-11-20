// The view's side of MCP Apps (`io.modelcontextprotocol/ui`): JSON-RPC 2.0
// over `postMessage`, with the window that frames the view and nothing else.
//
// The view asks `ui/initialize` and is told the theme; it says
// `ui/notifications/initialized`; the host then calls the panel's tool and
// hands the view its result as `ui/notifications/tool-result`. The view may
// ask `tools/call`, which the host relays to neorc-mcp. A request of the host
// that the view does not serve is answered "method not found".
//
// Everything here but `connect` is pure: it reads a message, or builds one.
// docs/specs/mcp.md, "The view".

/** The protocol version the view speaks, as hosts of MCP Apps expect it. */
export const PROTOCOL_VERSION = "2025-06-18";

/** The panel's own tool: the one a host calls, with no arguments. */
export const PANEL_TOOL = "neorc_panel";

export type Theme = "light" | "dark";

type Id = string | number;

export type Inbound =
  | { kind: "response"; id: Id; result: unknown }
  | { kind: "error"; id: Id; message: string }
  | { kind: "request"; id: Id; method: string; params: unknown }
  | { kind: "notification"; method: string; params: unknown };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A JSON-RPC 2.0 message, read; anything else is `null`, and ignored. */
export function parse(data: unknown): Inbound | null {
  if (!isRecord(data) || data.jsonrpc !== "2.0") return null;
  const { id, method } = data;
  const hasId = typeof id === "string" || typeof id === "number";
  if (typeof method === "string" && method !== "") {
    if (!("id" in data)) return { kind: "notification", method, params: data.params };
    return hasId ? { kind: "request", id, method, params: data.params } : null;
  }
  if (!hasId) return null;
  if ("result" in data) return { kind: "response", id, result: data.result };
  if (isRecord(data.error)) {
    const message = typeof data.error.message === "string" ? data.error.message : "error";
    return { kind: "error", id, message };
  }
  return null;
}

/** The theme a host's context names, if it names one the view knows. */
export function themeOf(context: unknown): Theme | undefined {
  if (!isRecord(context)) return undefined;
  return context.theme === "light" || context.theme === "dark" ? context.theme : undefined;
}

/** What a tool answered, as the view reads it. */
export interface ToolAnswer {
  structured: Record<string, unknown> | undefined;
  text: string;
  isError: boolean;
}

/** A `CallToolResult`, as MCP writes it, read. */
export function toolAnswer(result: unknown): ToolAnswer {
  if (!isRecord(result)) return { structured: undefined, text: "", isError: true };
  const content = Array.isArray(result.content) ? result.content : [];
  const text = content
    .map((part) => (isRecord(part) && typeof part.text === "string" ? part.text : ""))
    .filter((part) => part !== "")
    .join("\n");
  return {
    structured: isRecord(result.structuredContent) ? result.structuredContent : undefined,
    text,
    isError: result.isError === true,
  };
}

/** How the view reaches its host, once `connect` has set it up. */
export interface Host {
  /** Call a tool of neorc-mcp through the host. */
  callTool: (name: string, args?: Record<string, unknown>) => Promise<ToolAnswer>;
}

export interface Handlers {
  /** The panel's tool answered: the host's own call, after each turn. */
  onPanel: (answer: ToolAnswer) => void;
  onTheme: (theme: Theme) => void;
  onFailure: (message: string) => void;
}

/**
 * Speak to the window that frames the view: the handshake, the host's
 * notifications and requests, and the view's own calls. The host's origin is
 * opaque to the view, which is sandboxed, so messages are posted to `"*"`;
 * only messages whose `source` is that window are read.
 */
export function connect(parent: Window, handlers: Handlers): Host {
  const pending = new Map<Id, { resolve: (value: unknown) => void; reject: (error: Error) => void }>();
  let next = 1;
  const send = (message: Record<string, unknown>) => parent.postMessage(message, "*");
  const request = (method: string, params: unknown) =>
    new Promise<unknown>((resolve, reject) => {
      const id = next++;
      pending.set(id, { resolve, reject });
      send({ jsonrpc: "2.0", id, method, params });
    });

  window.addEventListener("message", (event: MessageEvent) => {
    if (event.source !== parent) return;
    const message = parse(event.data);
    if (message === null) return;
    switch (message.kind) {
      case "response":
      case "error": {
        const waiting = pending.get(message.id);
        if (!waiting) return;
        pending.delete(message.id);
        if (message.kind === "response") waiting.resolve(message.result);
        else waiting.reject(new Error(message.message));
        return;
      }
      case "notification": {
        if (message.method === "ui/notifications/tool-result") {
          handlers.onPanel(toolAnswer(message.params));
        } else if (message.method === "ui/notifications/host-context-changed") {
          const theme = themeOf(message.params);
          if (theme) handlers.onTheme(theme);
        }
        return;
      }
      case "request": {
        if (message.method === "ping" || message.method === "ui/resource-teardown") {
          send({ jsonrpc: "2.0", id: message.id, result: {} });
        } else {
          send({
            jsonrpc: "2.0",
            id: message.id,
            error: { code: -32601, message: `Method not found: ${message.method}` },
          });
        }
      }
    }
  });

  request("ui/initialize", {
    protocolVersion: PROTOCOL_VERSION,
    appInfo: { name: "neorc", version: "0.0.0" },
    appCapabilities: {},
  }).then(
    (result) => {
      const theme = themeOf(isRecord(result) ? result.hostContext : undefined);
      if (theme) handlers.onTheme(theme);
      send({ jsonrpc: "2.0", method: "ui/notifications/initialized", params: {} });
    },
    (error: Error) => handlers.onFailure(`The host did not start this view: ${error.message}`),
  );

  return {
    callTool: async (name, args = {}) =>
      toolAnswer(await request("tools/call", { name, arguments: args })),
  };
}
