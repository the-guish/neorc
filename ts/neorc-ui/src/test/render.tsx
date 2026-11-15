// Rendering with a query client, and the manager's API as a fetch stand-in.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi } from "vitest";

export type Reply =
  | unknown
  | ((url: URL, init: RequestInit | undefined) => unknown | Promise<unknown>);

export interface Refusal {
  status: number;
  error: string;
  detail: string;
}

export function refusal(status: number, error: string, detail: string): Refusal {
  return { status, error, detail };
}

/**
 * Serve `routes` in place of the manager: each key is a path with an optional
 * query string, matched exactly; a value is the JSON to answer with, a
 * function computing it, or a refusal. Requests are recorded for asserting.
 */
export function mockApi(routes: Record<string, Reply>) {
  const calls: { url: URL; init: RequestInit | undefined }[] = [];
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      // The request helper asks a path on this origin.
      const url = new URL(input instanceof Request ? input.url : String(input), location.href);
      calls.push({ url, init });
      // The manager's API is under /neorc/; a stand-in names its routes
      // without it, as the manager's own router does.
      const path = url.pathname.replace(/^\/neorc(?=\/)/, "");
      const key = path + url.search;
      const reply = key in routes ? routes[key] : routes[path];
      if (reply === undefined) {
        return Response.json(
          { error: "RouteNotFoundError", detail: `no stand-in for ${key}` },
          { status: 404 },
        );
      }
      const value = typeof reply === "function" ? await reply(url, init) : reply;
      if (isRefusal(value)) {
        return Response.json(
          { error: value.error, detail: value.detail },
          { status: value.status },
        );
      }
      if (value === undefined) return new Response(null, { status: 204 });
      return Response.json(value);
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

function isRefusal(value: unknown): value is Refusal {
  return (
    typeof value === "object" &&
    value !== null &&
    "status" in value &&
    "error" in value &&
    "detail" in value
  );
}

/** A poll that never answers: keeps the event loop quiet in a test. */
export function neverAnswers(): Promise<never> {
  return new Promise(() => {});
}

export function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  const view = render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
  return { ...view, client };
}
