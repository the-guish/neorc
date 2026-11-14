import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  ACTIVE,
  HELLO,
  OPEN,
  QUEUES,
  SIGNED_IN,
  SUCCEEDED,
  WORD_PICKER,
} from "../test/fixtures";
import { mockApi, refusal, renderWithClient } from "../test/render";
import { Layout, NAV_COLLAPSED_KEY, QUEUE_KIND_KEY, SIDEBAR_KEY } from "./Layout";

function routes() {
  return {
    "/flows": { flows: [HELLO, WORD_PICKER] },
    "/runs?root_only=true&limit=6": { runs: [ACTIVE, SUCCEEDED] },
  };
}

describe("Layout", () => {
  it("has the navigation, a way to a new run, the flows, the latest runs and no account", async () => {
    mockApi(routes());

    renderWithClient(
      <Layout route={{ name: "run", id: ACTIVE.id }} session={OPEN}>
        <p>the page</p>
      </Layout>,
    );

    const main = screen.getByRole("navigation", { name: "Main" });
    expect(within(main).getByRole("link", { name: "Runs" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(
      within(main).getByRole("link", { name: "Flows" }),
    ).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "New run" })).toHaveAttribute(
      "href",
      "#/flows",
    );
    expect(screen.getByRole("note")).toHaveTextContent("No authentication");
    expect(screen.getByText("the page")).toBeInTheDocument();

    const flows = await screen.findByRole("navigation", { name: "Flows" });
    expect(
      within(flows).getByRole("link", { name: "word_picker" }),
    ).toHaveAttribute("href", "#/flows/word_picker");
    const recent = await screen.findByRole("navigation", {
      name: "Recent runs",
    });
    const links = within(recent).getAllByRole("link");
    expect(links).toHaveLength(2);
    expect(links[0]).toHaveAttribute("href", `#/runs/${ACTIVE.id}`);
    expect(links[0]).toHaveAttribute("aria-current", "page");
    expect(links[0]?.querySelector("[data-status]")).toHaveAttribute(
      "data-status",
      "active",
    );
    expect(links[1]?.querySelector("[data-status]")).toHaveAttribute(
      "data-status",
      "succeeded",
    );

    const profile = screen.getByLabelText("Profile");
    expect(profile).toHaveTextContent("No account");
    expect(within(profile).queryByRole("link")).not.toBeInTheDocument();
  });

  it("embedded, draws the page and the strip with no sidebar of its own", () => {
    mockApi(routes());

    renderWithClient(
      <Layout route={{ name: "runs" }} session={OPEN} embedded>
        <p>the page</p>
      </Layout>,
    );

    expect(screen.getByText("the page")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("No authentication");
    expect(
      screen.queryByRole("complementary", { name: "Sidebar" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /sidebar/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "New run" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Profile")).not.toBeInTheDocument();
  });

  it("collapses to a rail and remembers it", () => {
    mockApi(routes());

    const first = renderWithClient(
      <Layout route={{ name: "flows" }} session={OPEN}>
        <p>the page</p>
      </Layout>,
    );
    const collapse = screen.getByRole("button", { name: "Collapse sidebar" });
    expect(collapse).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(collapse);

    expect(
      screen.getByRole("button", { name: "Expand sidebar" }),
    ).toHaveAttribute("aria-expanded", "false");
    // The links keep their names for a reader of the rail.
    expect(screen.getByRole("link", { name: "Flows" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(localStorage.getItem(SIDEBAR_KEY)).toBe("rail");
    first.unmount();

    renderWithClient(
      <Layout route={{ name: "flows" }} session={OPEN}>
        <p>the page</p>
      </Layout>,
    );
    expect(
      screen.getByRole("button", { name: "Expand sidebar" }),
    ).toBeInTheDocument();
  });

  it("stands without the flows or the runs", async () => {
    mockApi({});

    renderWithClient(
      <Layout route={{ name: "runs" }} session={OPEN}>
        <p>the page</p>
      </Layout>,
    );

    expect(await screen.findByText("the page")).toBeInTheDocument();
    expect(
      screen.queryByRole("navigation", { name: "Flows" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("navigation", { name: "Recent runs" }),
    ).not.toBeInTheDocument();
  });

  it("shows who is signed in, with no banner, and signs them out", async () => {
    let signedOut = false;
    const { calls } = mockApi({
      "/auth/logout": (_url: URL, init: RequestInit | undefined) => {
        signedOut = init?.method === "POST";
        return undefined;
      },
    });

    const { client } = renderWithClient(
      <Layout route={{ name: "runs" }} session={SIGNED_IN}>
        <p>the page</p>
      </Layout>,
    );
    const invalidated: unknown[] = [];
    const original = client.invalidateQueries.bind(client);
    client.invalidateQueries = ((filters: unknown) => {
      invalidated.push(filters);
      return original(filters as Parameters<typeof original>[0]);
    }) as typeof client.invalidateQueries;

    const profile = screen.getByLabelText("Profile");
    expect(profile).toHaveTextContent("Ada Lovelace");
    expect(profile).toHaveTextContent("ada@example.com");
    expect(screen.queryByRole("note")).not.toBeInTheDocument();

    fireEvent.click(within(profile).getByRole("button", { name: "Sign out" }));

    await waitFor(() =>
      expect(invalidated).toContainEqual({ queryKey: ["session"] }),
    );
    expect(signedOut).toBe(true);
    const logout = calls.find((call) => call.url.pathname === "/auth/logout");
    expect(new Headers(logout?.init?.headers).get("content-type")).toBe(
      "application/json",
    );
  });

  it.each([
    [
      403,
      "CrossSiteRequestError",
      "POST /auth/logout with a session must come from elsewhere",
    ],
    [503, "ManagerUnavailableError", "the manager is restarting"],
  ])(
    "says so when signing out fails with %i, and stays signed in",
    async (status, error, detail) => {
      mockApi({ "/auth/logout": refusal(status, error, detail) });

      const { client } = renderWithClient(
        <Layout route={{ name: "runs" }} session={SIGNED_IN}>
          <p>the page</p>
        </Layout>,
      );
      client.setQueryData(["session"], SIGNED_IN);
      const profile = screen.getByLabelText("Profile");

      fireEvent.click(
        within(profile).getByRole("button", { name: "Sign out" }),
      );

      expect(await within(profile).findByRole("alert")).toHaveTextContent(
        `Not signed out: ${detail}`,
      );
      expect(
        within(profile).getByRole("button", { name: "Sign out" }),
      ).toBeEnabled();
      expect(client.getQueryData(["session"])).toEqual(SIGNED_IN);
    },
  );

  it("counts a session already gone as signed out, whatever the next check says", async () => {
    mockApi({
      "/auth/logout": refusal(
        401,
        "AuthenticationError",
        "the session is unknown",
      ),
      "/auth/session": refusal(503, "ManagerUnavailableError", "restarting"),
    });

    const { client } = renderWithClient(
      <Layout route={{ name: "runs" }} session={SIGNED_IN}>
        <p>the page</p>
      </Layout>,
    );
    client.setQueryData(["session"], SIGNED_IN);

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() =>
      expect(client.getQueryData(["session"])).toMatchObject({
        principal: null,
      }),
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
  it("lists the queues under a filter by kind, which it keeps", async () => {
    localStorage.removeItem(QUEUE_KIND_KEY);
    mockApi({ ...routes(), "/queues": { queues: QUEUES } });

    const view = renderWithClient(
      <Layout route={{ name: "queue", queue: "editors" }} session={OPEN}>
        <p>the page</p>
      </Layout>,
    );

    const main = screen.getByRole("navigation", { name: "Main" });
    expect(within(main).getByRole("link", { name: "Queues" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    const queues = await screen.findByRole("navigation", { name: "Queues" });
    const names = () =>
      within(queues)
        .queryAllByRole("link")
        .map((link) => link.textContent);
    expect(names()).toEqual(["default", "editors"]);
    expect(within(queues).getByRole("link", { name: "editors" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    const kinds = within(queues).getByRole("group", { name: "Kind of queue" });
    expect(within(kinds).getByRole("button", { name: "all" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    fireEvent.click(within(kinds).getByRole("button", { name: "external" }));
    expect(names()).toEqual(["editors"]);
    fireEvent.click(within(kinds).getByRole("button", { name: "worker" }));
    expect(names()).toEqual(["default"]);
    expect(localStorage.getItem(QUEUE_KIND_KEY)).toBe("worker");

    // Kept for the next page load.
    view.unmount();
    renderWithClient(
      <Layout route={{ name: "queues" }} session={OPEN}>
        <p>the page</p>
      </Layout>,
    );
    const again = await screen.findByRole("navigation", { name: "Queues" });
    expect(within(again).getAllByRole("link").map((link) => link.textContent)).toEqual([
      "default",
    ]);
    localStorage.removeItem(QUEUE_KIND_KEY);
  });
  it("folds the list below an entry away with its arrow, and remembers it", async () => {
    localStorage.removeItem(NAV_COLLAPSED_KEY);
    mockApi({ ...routes(), "/queues": { queues: QUEUES } });

    const view = renderWithClient(
      <Layout route={{ name: "runs" }} session={OPEN}>
        <p>the page</p>
      </Layout>,
    );

    // Each list is below its own entry, inside the navigation.
    const main = screen.getByRole("navigation", { name: "Main" });
    const flows = await within(main).findByRole("navigation", { name: "Flows" });
    await within(main).findByRole("navigation", { name: "Recent runs" });
    await within(main).findByRole("navigation", { name: "Queues" });
    const entry = within(main).getByRole("link", { name: "Flows" });
    expect(
      entry.compareDocumentPosition(flows) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    const queuesEntry = within(main).getByRole("link", { name: "Queues" });
    expect(
      flows.compareDocumentPosition(queuesEntry) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    const arrow = within(main).getByRole("button", { name: "Collapse Flows" });
    expect(arrow).toHaveAttribute("aria-expanded", "true");
    const down = arrow.querySelector("path")?.getAttribute("d");
    fireEvent.click(arrow);

    const folded = within(main).getByRole("button", { name: "Expand Flows" });
    expect(folded).toHaveAttribute("aria-expanded", "false");
    expect(folded.querySelector("path")?.getAttribute("d")).not.toBe(down);
    expect(
      within(main).queryByRole("navigation", { name: "Flows" }),
    ).not.toBeInTheDocument();
    // The entry stays, and so do the other lists.
    expect(within(main).getByRole("link", { name: "Flows" })).toBeInTheDocument();
    expect(
      within(main).getByRole("navigation", { name: "Recent runs" }),
    ).toBeInTheDocument();
    expect(within(main).getByRole("navigation", { name: "Queues" })).toBeInTheDocument();
    expect(localStorage.getItem(NAV_COLLAPSED_KEY)).toBe('["flows"]');

    // Kept for the next page load, and unfolded again by the same arrow.
    view.unmount();
    renderWithClient(
      <Layout route={{ name: "runs" }} session={OPEN}>
        <p>the page</p>
      </Layout>,
    );
    await screen.findByRole("navigation", { name: "Recent runs" });
    expect(
      screen.queryByRole("navigation", { name: "Flows" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Expand Flows" }));
    expect(
      await screen.findByRole("navigation", { name: "Flows" }),
    ).toBeInTheDocument();
    expect(localStorage.getItem(NAV_COLLAPSED_KEY)).toBe("[]");
  });
});
