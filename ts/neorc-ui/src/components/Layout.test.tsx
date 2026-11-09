import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  ACTIVE,
  HELLO,
  OPEN,
  SIGNED_IN,
  SUCCEEDED,
  WORD_PICKER,
} from "../test/fixtures";
import { mockApi, refusal, renderWithClient } from "../test/render";
import { Layout, SIDEBAR_KEY } from "./Layout";

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
});
