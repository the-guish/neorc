import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  ACTIVE,
  HELLO,
  QUEUES,
  SUCCEEDED,
  WORD_PICKER,
} from "../test/fixtures";
import { mockApi, renderWithClient } from "../test/render";
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
      <Layout route={{ name: "run", id: ACTIVE.id }}>
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
      "#/neorc/flows",
    );
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    expect(screen.getByText("the page")).toBeInTheDocument();

    const flows = await screen.findByRole("navigation", { name: "Flows" });
    expect(
      within(flows).getByRole("link", { name: "word_picker" }),
    ).toHaveAttribute("href", "#/neorc/flows/word_picker");
    const recent = await screen.findByRole("navigation", {
      name: "Recent runs",
    });
    const links = within(recent).getAllByRole("link");
    expect(links).toHaveLength(2);
    expect(links[0]).toHaveAttribute("href", `#/neorc/runs/${ACTIVE.id}`);
    expect(links[0]).toHaveAttribute("aria-current", "page");
    expect(links[0]?.querySelector("[data-status]")).toHaveAttribute(
      "data-status",
      "active",
    );
    expect(links[1]?.querySelector("[data-status]")).toHaveAttribute(
      "data-status",
      "succeeded",
    );

    // Who is signed in, and signing out, are the shell's.
    expect(screen.queryByLabelText("Profile")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });

  it("collapses to a rail and remembers it", () => {
    mockApi(routes());

    const first = renderWithClient(
      <Layout route={{ name: "flows" }}>
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
      <Layout route={{ name: "flows" }}>
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
      <Layout route={{ name: "runs" }}>
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

  it("lists the queues under a filter by kind, which it keeps", async () => {
    localStorage.removeItem(QUEUE_KIND_KEY);
    mockApi({ ...routes(), "/queues": { queues: QUEUES } });

    const view = renderWithClient(
      <Layout route={{ name: "queue", queue: "editors" }}>
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
      <Layout route={{ name: "queues" }}>
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
      <Layout route={{ name: "runs" }}>
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
      <Layout route={{ name: "runs" }}>
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
