import { describe, expect, it } from "vitest";

import { href, parseHash, type Route } from "./router";

describe("routes", () => {
  it.each<[string, Route]>([
    ["", { name: "runs", flow: undefined, status: undefined }],
    ["#/", { name: "runs", flow: undefined, status: undefined }],
    ["#/runs?flow=a&status=failed", { name: "runs", flow: "a", status: "failed" }],
    ["#/flows", { name: "flows" }],
    ["#/flows/word_picker", { name: "flow", flow: "word_picker", version: undefined }],
    ["#/flows/a%2Fb?version=1.0.0", { name: "flow", flow: "a/b", version: "1.0.0" }],
    ["#/runs/abc-123", { name: "run", id: "abc-123", view: undefined }],
    ["#/runs/abc-123?view=graph", { name: "run", id: "abc-123", view: "graph" }],
    ["#/runs/abc-123?view=outline", { name: "run", id: "abc-123", view: "outline" }],
    ["#/runs/abc-123?view=sideways", { name: "run", id: "abc-123", view: undefined }],
    ["#/nothing/here", { name: "unknown", hash: "#/nothing/here" }],
    ["#/runs/%E0", { name: "unknown", hash: "#/runs/%E0" }],
    ["#/flows/100%", { name: "unknown", hash: "#/flows/100%" }],
    ["#/queues", { name: "queues", kind: undefined }],
    ["#/queues?kind=external", { name: "queues", kind: "external" }],
    [
      "#/queues/editors",
      {
        name: "queue",
        queue: "editors",
        status: undefined,
        flow: undefined,
        window: undefined,
        task: undefined,
      },
    ],
    [
      "#/queues/editors?status=all&flow=approval&window=7d&task=abc",
      {
        name: "queue",
        queue: "editors",
        status: "all",
        flow: "approval",
        window: "7d",
        task: "abc",
      },
    ],
    ["#/sign-in", { name: "sign-in", error: undefined }],
    ["#/sign-in?error=expired", { name: "sign-in", error: "expired" }],
  ])("parses %s", (hash, route) => {
    expect(parseHash(hash)).toEqual(route);
  });

  it("writes what it reads", () => {
    const routes: Route[] = [
      { name: "runs" },
      { name: "runs", flow: "a", status: "active" },
      { name: "flows" },
      { name: "flow", flow: "a/b", version: "2.0.0" },
      { name: "run", id: "abc" },
      { name: "run", id: "abc", view: "graph" },
      { name: "queues" },
      { name: "queues", kind: "worker" },
      { name: "queue", queue: "editors" },
      { name: "queue", queue: "a/b", status: "all", flow: "f", window: "1y", task: "t" },
      { name: "sign-in" },
      { name: "sign-in", error: "not allowed&more" },
    ];
    for (const route of routes) {
      expect(parseHash(href(route))).toMatchObject(route);
    }
  });
});
