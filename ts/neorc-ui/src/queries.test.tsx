import { QueryClient, QueryClientProvider, QueryObserver } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { createApi, type ApiEvent } from "./api";
import {
  follow,
  isSubRunsQuery,
  keys,
  REFUSED_RETRY_MS,
  refresh,
  useLiveEvents,
  useRunTasks,
  useSubRuns,
} from "./queries";
import { ACTIVE, SUCCEEDED, event } from "./test/fixtures";
import { mockApi, neverAnswers, refusal } from "./test/render";

const API = createApi("/neorc/");

function pages(...pages: ApiEvent[][]) {
  let served = 0;
  return () => {
    const page = pages[served];
    served += 1;
    return page === undefined ? neverAnswers() : { events: page };
  };
}

/** The calls made, by their path below /neorc/, where the manager's API is. */
function asked(api: ReturnType<typeof mockApi>) {
  return api.calls.map((call) => ({
    path: call.url.pathname.replace(/^\/neorc/, ""),
    ...Object.fromEntries(call.url.searchParams),
  }));
}

describe("follow", () => {
  it("starts after the latest event, refetching everything once", async () => {
    const api = mockApi({
      "/events/latest": { sequence: 41 },
      "/events": pages([], [event(42, ACTIVE), event(43, SUCCEEDED)]),
    });
    const client = new QueryClient();
    const invalidated = vi.spyOn(client, "invalidateQueries");
    const controller = new AbortController();

    void follow(API, client, controller.signal);
    await vi.waitFor(() => expect(api.calls).toHaveLength(4));

    expect(asked(api)[0]).toEqual({ path: "/events/latest" });
    expect(asked(api)[1]).toMatchObject({ path: "/events", after: "41", timeout: "20" });
    expect(asked(api)[2]).toMatchObject({ after: "41" });
    expect(asked(api)[3]).toMatchObject({ after: "43" });
    // Once for everything at the start, then the runs and each run concerned.
    const keep = { cancelRefetch: false };
    expect(invalidated.mock.calls[0]).toEqual([{}, keep]);
    expect(invalidated).toHaveBeenCalledWith({ queryKey: ["runs"] }, keep);
    expect(invalidated).toHaveBeenCalledWith({ queryKey: keys.run(ACTIVE.id) }, keep);
    expect(invalidated).toHaveBeenCalledWith({ queryKey: keys.run(SUCCEEDED.id) }, keep);
    expect(invalidated).not.toHaveBeenCalledWith({ queryKey: keys.flows }, keep);
    // Run events reach every page listing sub-runs: they carry the sub-run's id.
    expect(invalidated).toHaveBeenCalledWith({ predicate: isSubRunsQuery }, keep);
    controller.abort();
  });

  it("asks for the flows again when a run starts", async () => {
    const started: ApiEvent = { sequence: 2, run_id: ACTIVE.id, kind: "run_started" };
    const api = mockApi({
      "/events/latest": { sequence: 1 },
      "/events": pages([started]),
    });
    const client = new QueryClient();
    const invalidated = vi.spyOn(client, "invalidateQueries");
    const controller = new AbortController();

    void follow(API, client, controller.signal);
    await vi.waitFor(() => expect(api.calls).toHaveLength(3));

    expect(invalidated).toHaveBeenCalledWith(
      { queryKey: keys.flows },
      { cancelRefetch: false },
    );
    controller.abort();
  });

  it("starts over from the log's end after a failure, and stops when told", async () => {
    let polls = 0;
    const api = mockApi({
      "/events/latest": { sequence: 200 },
      "/events": () => {
        polls += 1;
        if (polls === 1) throw new Error("connection reset");
        return neverAnswers();
      },
    });
    const controller = new AbortController();
    const slept: number[] = [];
    vi.spyOn(console, "warn").mockImplementation(() => {});

    const done = follow(API, new QueryClient(), controller.signal, async (ms) => {
      slept.push(ms);
    });
    await vi.waitFor(() => expect(api.calls).toHaveLength(4));
    controller.abort();

    expect(slept).toEqual([2000]);
    expect(asked(api).map((call) => call.path)).toEqual([
      "/events/latest",
      "/events",
      "/events/latest",
      "/events",
    ]);
    await expect(Promise.race([done, Promise.resolve("still waiting")])).resolves.toBe(
      "still waiting",
    );
  });

  it("stops, rather than retrying, once the session has ended", async () => {
    const api = mockApi({
      "/events/latest": refusal(401, "AuthenticationError", "the session is unknown"),
    });
    const slept: number[] = [];

    await follow(API, new QueryClient(), new AbortController().signal, async (ms) => {
      slept.push(ms);
    });

    expect(slept).toEqual([]);
    expect(api.calls).toHaveLength(1);
  });
});

describe("useLiveEvents, after a refusal", () => {
  it("tries again after a while, not at once, while the page is still shown", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      let refused = true;
      const api = mockApi({
        "/events/latest": () =>
          refused ? refusal(401, "AuthenticationError", "no session") : { sequence: 5 },
        "/events": () => neverAnswers(),
      });
      const client = new QueryClient();
      const wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      );

      const { unmount } = renderHook(() => useLiveEvents(), { wrapper });
      await vi.waitFor(() => expect(api.calls).toHaveLength(1));
      await vi.advanceTimersByTimeAsync(REFUSED_RETRY_MS - 1000);
      expect(api.calls).toHaveLength(1);
      refused = false;
      await vi.advanceTimersByTimeAsync(1000);

      await vi.waitFor(() =>
        expect(asked(api).map((call) => call.path)).toEqual([
          "/events/latest",
          "/events/latest",
          "/events",
        ]),
      );
      unmount();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("refresh", () => {
  it("fetches once more when a fetch was already in flight", async () => {
    const client = new QueryClient();
    let resolveFirst: (value: string) => void = () => {};
    const first = new Promise<string>((resolve) => {
      resolveFirst = resolve;
    });
    const answers: Promise<string>[] = [first, Promise.resolve("finished")];
    let calls = 0;
    const observer = new QueryObserver(client, {
      queryKey: keys.run(ACTIVE.id),
      queryFn: () => answers[calls++] ?? Promise.resolve("again"),
      staleTime: Infinity,
    });
    const unsubscribe = observer.subscribe(() => {});
    await vi.waitFor(() => expect(calls).toBe(1));

    // The event arrives while the first read, which predates it, is in flight.
    const refreshed = refresh(client, { queryKey: keys.run(ACTIVE.id) });
    resolveFirst("active");
    await refreshed;

    expect(calls).toBe(2);
    expect(observer.getCurrentResult().data).toBe("finished");
    unsubscribe();
  });

  it("fetches just once when nothing was in flight", async () => {
    const client = new QueryClient();
    let calls = 0;
    const observer = new QueryObserver(client, {
      queryKey: keys.run(ACTIVE.id),
      queryFn: () => Promise.resolve(`answer ${++calls}`),
      staleTime: Infinity,
    });
    const unsubscribe = observer.subscribe(() => {});
    await vi.waitFor(() => expect(observer.getCurrentResult().data).toBe("answer 1"));

    await refresh(client, { queryKey: keys.run(ACTIVE.id) });

    expect(calls).toBe(2);
    unsubscribe();
  });
});

describe("useLiveEvents", () => {
  it("polls only while the tab is shown, starting over when it is shown again", async () => {
    const api = mockApi({
      "/events/latest": { sequence: 5 },
      "/events": () => neverAnswers(),
    });
    let hidden = false;
    Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
    const client = new QueryClient();

    const { unmount } = renderHook(() => useLiveEvents(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    });
    await vi.waitFor(() => expect(api.calls).toHaveLength(2));
    const poll = api.calls[1]?.init?.signal;
    expect(poll?.aborted).toBe(false);

    hidden = true;
    document.dispatchEvent(new Event("visibilitychange"));
    expect(poll?.aborted).toBe(true);

    hidden = false;
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.waitFor(() => expect(api.calls).toHaveLength(4));
    expect(asked(api).map((call) => call.path)).toEqual([
      "/events/latest",
      "/events",
      "/events/latest",
      "/events",
    ]);

    unmount();
    expect(api.calls[3]?.init?.signal?.aborted).toBe(true);
  });
});

describe("an active run's page", () => {
  function wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
  }

  it("asks for the tasks and sub-runs again while the run is active", async () => {
    const api = mockApi({
      [`/runs/${ACTIVE.id}/tasks`]: { tasks: [] },
      [`/runs/${ACTIVE.id}/sub-runs`]: { runs: [] },
    });

    renderHook(
      () => {
        useRunTasks(ACTIVE.id, { active: true, everyMs: 20 });
        useSubRuns(ACTIVE.id, { active: true, everyMs: 20 });
      },
      { wrapper },
    );

    await vi.waitFor(() => expect(api.calls.length).toBeGreaterThanOrEqual(6));
    const paths = new Set(api.calls.map((call) => call.url.pathname));
    expect(paths).toEqual(
      new Set([`/neorc/runs/${ACTIVE.id}/tasks`, `/neorc/runs/${ACTIVE.id}/sub-runs`]),
    );
  });

  it("asks once when the run is over", async () => {
    const api = mockApi({
      [`/runs/${SUCCEEDED.id}/tasks`]: { tasks: [] },
      [`/runs/${SUCCEEDED.id}/sub-runs`]: { runs: [] },
    });

    renderHook(
      () => {
        useRunTasks(SUCCEEDED.id, { active: false, everyMs: 20 });
        useSubRuns(SUCCEEDED.id, { active: false, everyMs: 20 });
      },
      { wrapper },
    );

    await vi.waitFor(() => expect(api.calls).toHaveLength(2));
    await new Promise((resolve) => setTimeout(resolve, 80));
    expect(api.calls).toHaveLength(2);
  });

  it("tells a sub-runs query from the rest", () => {
    expect(isSubRunsQuery({ queryKey: keys.subRuns("x") })).toBe(true);
    expect(isSubRunsQuery({ queryKey: keys.tasks("x") })).toBe(false);
    expect(isSubRunsQuery({ queryKey: keys.run("x") })).toBe(false);
  });
});
