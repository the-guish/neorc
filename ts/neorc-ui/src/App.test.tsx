import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { App } from "./App";
import type { Session } from "./api";
import { ACTIVE, answered, HELLO, SIGNED_IN, SIGNED_OUT } from "./test/fixtures";
import { mockApi, neverAnswers, refusal, renderWithClient } from "./test/render";
import { RETURN_KEY } from "./session";

const OTHER = { ...ACTIVE, id: "0ddddddd-0000-4000-8000-000000000002", root_id: "0ddddddd-0000-4000-8000-000000000002" };

/** oneberth's answer for a session the page shows, or a refusal as it is. */
function answer(value: unknown): unknown {
  return typeof value === "object" && value !== null && "providers" in value
    ? answered(value as Session)
    : value;
}

function routes(session: Session | (() => unknown) = SIGNED_IN) {
  const shared: Record<string, unknown> = {
    "/auth/session": typeof session === "function" ? () => answer(session()) : answer(session),
    "/events/latest": { sequence: 0 },
    "/events": () => neverAnswers(),
    "/flows": { flows: [HELLO] },
    "/flows/hello": HELLO,
    "/flows/hello/versions/0.1.0": HELLO,
  };
  for (const run of [ACTIVE, OTHER]) {
    shared[`/runs/${run.id}`] = run;
    shared[`/runs/${run.id}/tasks`] = { tasks: [] };
    shared[`/runs/${run.id}/sub-runs`] = { runs: [] };
  }
  return shared;
}

afterEach(() => {
  sessionStorage.clear();
});

describe("App", () => {
  it("leaves a half-confirmed cancellation behind when moving to another run", async () => {
    mockApi(routes());
    location.hash = `#/runs/${ACTIVE.id}`;

    renderWithClient(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Cancel run" }));
    expect(screen.getByRole("button", { name: "Cancel it" })).toBeInTheDocument();

    location.hash = `#/runs/${OTHER.id}`;
    dispatchEvent(new HashChangeEvent("hashchange"));

    expect(await screen.findByRole("button", { name: "Cancel run" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel it" })).not.toBeInTheDocument();
  });

  it("says where an unknown address leads", async () => {
    mockApi(routes());
    location.hash = "#/nowhere";

    renderWithClient(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Nothing at #/nowhere");
  });

  it("shows only the sign-in page while nobody is signed in, and asks nothing else", async () => {
    const { calls } = mockApi(routes(SIGNED_OUT));
    location.hash = `#/runs/${ACTIVE.id}`;

    renderWithClient(<App />);

    const google = await screen.findByRole("link", { name: "Sign in with Google" });
    expect(google).toHaveAttribute("href", `${location.origin}/auth/login/google`);
    expect(screen.getByRole("link", { name: "Sign in with Okta" })).toHaveAttribute(
      "href",
      `${location.origin}/auth/login/okta`,
    );
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    expect(calls.map((call) => call.url.pathname)).toEqual(["/auth/session"]);

    fireEvent.click(google);
    expect(sessionStorage.getItem(RETURN_KEY)).toBe(`#/runs/${ACTIVE.id}`);
  });

  it("says in a sentence why a sign-in failed", async () => {
    mockApi(routes(SIGNED_OUT));
    location.hash = "#/sign-in?error=not_allowed";

    renderWithClient(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "does not let that account in",
    );
    fireEvent.click(screen.getByRole("link", { name: "Sign in with Google" }));
    expect(sessionStorage.getItem(RETURN_KEY)).toBeNull();
  });

  it("says a code it does not know is a failure all the same", async () => {
    mockApi(routes(SIGNED_OUT));
    location.hash = "#/sign-in?error=something_new";

    renderWithClient(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Signing in failed");
  });

  it("names the fix when nobody can sign in", async () => {
    mockApi(routes({ ...SIGNED_OUT, providers: [] }));
    location.hash = "#/runs";

    renderWithClient(<App />);

    expect(await screen.findByText(/not configured/)).toHaveTextContent(
      "sign-in configuration",
    );
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("goes back to the page the sign-in began on, once", async () => {
    mockApi(routes(SIGNED_IN));
    sessionStorage.setItem(RETURN_KEY, `#/runs/${ACTIVE.id}`);
    location.hash = "";

    renderWithClient(<App />);

    await waitFor(() => expect(location.hash).toBe(`#/runs/${ACTIVE.id}`));
    expect(sessionStorage.getItem(RETURN_KEY)).toBeNull();
    const profile = await screen.findByLabelText("Profile");
    expect(within(profile).getByText("Ada Lovelace")).toBeInTheDocument();
  });

  it("does not follow a stored page that is not one of the UI's", async () => {
    mockApi(routes(SIGNED_IN));
    sessionStorage.setItem(RETURN_KEY, "javascript:alert(1)");
    location.hash = "";

    renderWithClient(<App />);

    await screen.findByLabelText("Profile");
    expect(location.hash).toBe("");
  });

  it("shows the sign-in page when a session ends while a page is open", async () => {
    let session: Session = SIGNED_IN;
    let refused = false;
    mockApi({
      ...routes(() => session),
      [`/runs/${ACTIVE.id}`]: () => (refused ? refusal(401, "AuthenticationError", "the session is unknown, ended or expired") : ACTIVE),
    });
    location.hash = `#/runs/${ACTIVE.id}`;

    const { client } = renderWithClient(<App />);
    expect(await screen.findByRole("button", { name: "Cancel run" })).toBeInTheDocument();

    session = SIGNED_OUT;
    refused = true;
    await client.invalidateQueries({ queryKey: ["run", ACTIVE.id] });

    expect(await screen.findByRole("link", { name: "Sign in with Google" })).toBeInTheDocument();
  });

  it("sends a sign-in link followed while signed in on to the runs", async () => {
    mockApi({ ...routes(SIGNED_IN), "/runs": { runs: [] } });
    location.hash = "#/flows";
    location.hash = "#/sign-in";
    const entries = history.length;

    renderWithClient(<App />);

    await waitFor(() => expect(location.hash).toBe("#/runs"));
    // In place of the sign-in link: Back goes where the reader was before it.
    expect(history.length).toBe(entries);
  });

  it("keeps the pages when asking for the session again fails", async () => {
    let failing = false;
    mockApi(
      routes(() =>
        failing ? refusal(503, "ManagerUnavailableError", "the manager is restarting") : SIGNED_IN,
      ),
    );
    location.hash = `#/runs/${ACTIVE.id}`;

    const { client } = renderWithClient(<App />);
    expect(await screen.findByRole("button", { name: "Cancel run" })).toBeInTheDocument();

    failing = true;
    await client.invalidateQueries({ queryKey: ["session"] });

    expect(screen.getByRole("button", { name: "Cancel run" })).toBeInTheDocument();
    expect(screen.getByLabelText("Profile")).toHaveTextContent("Ada Lovelace");
  });

  it("offers to ask again when there never was a session", async () => {
    let failing = true;
    mockApi(
      routes(() =>
        failing ? refusal(503, "ManagerUnavailableError", "the manager is restarting") : SIGNED_IN,
      ),
    );
    location.hash = "#/runs";

    renderWithClient(<App />);
    const again = await screen.findByRole("button", { name: "Try again" });
    expect(screen.getByRole("alert")).toHaveTextContent("the manager is restarting");

    failing = false;
    fireEvent.click(again);

    expect(await screen.findByLabelText("Profile")).toHaveTextContent("Ada Lovelace");
  });

  it("asks for the session once however many calls are refused, and not again for its own refusal", async () => {
    let sessions = 0;
    let signedIn = true;
    mockApi({
      ...routes(() => {
        sessions += 1;
        return signedIn
          ? SIGNED_IN
          : refusal(401, "AuthenticationError", "a gateway in front refuses everything");
      }),
      "/flows": refusal(401, "AuthenticationError", "no session"),
      "/runs?root_only=true&limit=6": refusal(401, "AuthenticationError", "no session"),
    });
    location.hash = "#/flows";

    const { client } = renderWithClient(<App />);
    await screen.findByLabelText("Profile");
    signedIn = false;
    await client.invalidateQueries({ queryKey: ["session"] });
    await new Promise((resolve) => setTimeout(resolve, 200));

    expect(sessions).toBeLessThan(6);
  });
});
