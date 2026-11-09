import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";

import { ApiError, type Session } from "../api";
import { useFlows, useRecentRuns } from "../queries";
import { SESSION_KEY, signOut } from "../session";
import { href, type Route } from "../router";
import { shortId } from "../format";

const NAV: { label: string; route: Route; icon: ReactNode }[] = [
  { label: "Runs", route: { name: "runs" }, icon: <ListIcon /> },
  { label: "Flows", route: { name: "flows" }, icon: <FlowIcon /> },
];

/** How many of the latest runs the sidebar lists. */
export const RECENT_RUNS = 6;

/** Where the sidebar's open or collapsed state is kept. */
export const SIDEBAR_KEY = "neorc-ui.sidebar";

/**
 * The shell: a sidebar with the brand, a way to a new run, the navigation,
 * the flows, the latest runs and who is signed in, collapsible to a rail of
 * icons; the page beside it, under a strip when authentication is off.
 *
 * `embedded`, for a page framed by an application with a navigation of its
 * own (`../embedding.ts`): the page and the strip alone, with no sidebar.
 */
export function Layout({
  route,
  session,
  embedded = false,
  children,
}: {
  route: Route;
  session: Session;
  embedded?: boolean;
  children: ReactNode;
}) {
  if (embedded) {
    return (
      <div className="shell shell-embedded">
        <Content session={session}>{children}</Content>
      </div>
    );
  }
  return (
    <WithSidebar route={route} session={session}>
      {children}
    </WithSidebar>
  );
}

function WithSidebar({
  route,
  session,
  children,
}: {
  route: Route;
  session: Session;
  children: ReactNode;
}) {
  const [collapsed, setCollapsed] = useSidebarCollapsed();
  return (
    <div className={`shell${collapsed ? " shell-rail" : ""}`}>
      <aside className="sidebar" aria-label="Sidebar">
        <div className="sidebar-top">
          <button
            type="button"
            className="icon-button"
            onClick={() => setCollapsed(!collapsed)}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-expanded={!collapsed}
          >
            <MenuIcon />
          </button>
          <a
            className="brand"
            href={href({ name: "runs" })}
            aria-label="neorc, runs"
          >
            <span className="brand-mark" aria-hidden="true" />
            <span className="label">neorc</span>
          </a>
        </div>
        <a
          className="primary new-run"
          href={href({ name: "flows" })}
          title="New run"
        >
          <PlusIcon />
          <span className="label">New run</span>
        </a>
        <nav aria-label="Main">
          {NAV.map((item) => (
            <a
              key={item.label}
              href={href(item.route)}
              aria-current={current(route, item.route) ? "page" : undefined}
              title={item.label}
            >
              {item.icon}
              <span className="label">{item.label}</span>
            </a>
          ))}
        </nav>
        <PinnedFlows route={route} />
        <RecentRuns route={route} />
        <Profile session={session} />
      </aside>
      <Content session={session}>{children}</Content>
    </div>
  );
}

/** The page, under a strip when authentication is off. */
function Content({
  session,
  children,
}: {
  session: Session;
  children: ReactNode;
}) {
  return (
    <div className="content">
      {!session.authentication && (
        <p className="banner" role="note">
          No authentication: anyone who reaches this manager can start and
          cancel runs. For development and testing only.
        </p>
      )}
      <main>{children}</main>
    </div>
  );
}

/** Who is signed in, and signing out; or that nobody need be. */
function Profile({ session }: { session: Session }) {
  const client = useQueryClient();
  const [leaving, setLeaving] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);
  const principal = session.principal;
  if (principal === null) {
    return (
      <div className="profile" aria-label="Profile">
        <span className="avatar" aria-hidden="true" />
        <span className="label">
          <span>No account</span>
          <span className="muted">
            {session.authentication ? "Not signed in" : "No authentication"}
          </span>
        </span>
      </div>
    );
  }
  const leave = async () => {
    setLeaving(true);
    setRefused(null);
    try {
      await signOut();
    } catch (error) {
      // A 401 is a session already gone: signed out all the same. Anything
      // else leaves the session as it was, and the reader must know.
      if (!(error instanceof ApiError && error.status === 401)) {
        setRefused(error instanceof Error ? error.message : String(error));
        setLeaving(false);
        return;
      }
    }
    // Signed out whatever the next check of the session says or fails to.
    client.setQueryData<Session>(SESSION_KEY, { ...session, principal: null });
    void client.invalidateQueries({ queryKey: SESSION_KEY });
  };
  return (
    <div className="profile" aria-label="Profile">
      <span className="avatar avatar-signed-in" aria-hidden="true">
        {principal.name.slice(0, 1).toUpperCase()}
      </span>
      <span className="label">
        <span title={principal.name}>{principal.name}</span>
        {principal.email !== null && (
          <span className="muted" title={principal.email}>
            {principal.email}
          </span>
        )}
        <button
          type="button"
          className="link sign-out"
          onClick={leave}
          disabled={leaving}
        >
          Sign out
        </button>
        {refused !== null && (
          <span role="alert" className="sign-out-refused">
            Not signed out: {refused}
          </span>
        )}
      </span>
    </div>
  );
}

function useSidebarCollapsed(): [boolean, (collapsed: boolean) => void] {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(SIDEBAR_KEY) === "rail";
    } catch {
      return false;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_KEY, collapsed ? "rail" : "open");
    } catch {
      // Storage may be off; the sidebar just opens again next time.
    }
  }, [collapsed]);
  return [collapsed, setCollapsed];
}

function current(route: Route, item: Route): boolean {
  if (item.name === "runs")
    return route.name === "runs" || route.name === "run";
  if (item.name === "flows")
    return route.name === "flows" || route.name === "flow";
  return false;
}

/** The flows the manager holds, latest version each, as the pinned workflows. */
function PinnedFlows({ route }: { route: Route }) {
  const flows = useFlows();
  if (!flows.isSuccess || flows.data.length === 0) return null;
  return (
    <nav className="sidebar-section" aria-label="Flows">
      <h3 className="label">Flows</h3>
      {flows.data.map((flow) => (
        <a
          key={flow.name}
          href={href({ name: "flow", flow: flow.name })}
          aria-current={
            route.name === "flow" && route.flow === flow.name
              ? "page"
              : undefined
          }
          title={flow.name}
        >
          <span className="dot dot-flow" aria-hidden="true" />
          <span className="label">{flow.name}</span>
        </a>
      ))}
    </nav>
  );
}

/** The latest runs, each with its status as a dot. */
function RecentRuns({ route }: { route: Route }) {
  const runs = useRecentRuns(RECENT_RUNS);
  const recent = runs.data ?? [];
  if (recent.length === 0) return null;
  return (
    <nav className="sidebar-section" aria-label="Recent runs">
      <h3 className="label">Recent runs</h3>
      {recent.map((run) => (
        <a
          key={run.id}
          href={href({ name: "run", id: run.id })}
          aria-current={
            route.name === "run" && route.id === run.id ? "page" : undefined
          }
          title={`${run.flow} ${shortId(run.id)}, ${run.status}`}
        >
          <span
            className={`dot dot-${run.status}`}
            data-status={run.status}
            aria-hidden="true"
          />
          <span className="label">
            {run.flow} <span className="mono muted">{shortId(run.id)}</span>
          </span>
        </a>
      ))}
    </nav>
  );
}

export function Loading({ what }: { what: string }) {
  return <p className="muted">Loading {what}…</p>;
}

export function Failed({ what, error }: { what: string; error: unknown }) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <p role="alert">
      Could not load {what}: {message}
    </p>
  );
}

// Icons: 16 px outlines in the current colour, no font or file behind them.

function Icon({ children }: { children: ReactNode }) {
  return (
    <svg
      className="icon"
      viewBox="0 0 16 16"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

function MenuIcon() {
  return (
    <Icon>
      <path d="M2 4h12M2 8h12M2 12h12" />
    </Icon>
  );
}

function PlusIcon() {
  return (
    <Icon>
      <path d="M8 3v10M3 8h10" />
    </Icon>
  );
}

function ListIcon() {
  return (
    <Icon>
      <path d="M5 4h9M5 8h9M5 12h9" />
      <circle cx="2.5" cy="4" r="0.75" fill="currentColor" />
      <circle cx="2.5" cy="8" r="0.75" fill="currentColor" />
      <circle cx="2.5" cy="12" r="0.75" fill="currentColor" />
    </Icon>
  );
}

function FlowIcon() {
  return (
    <Icon>
      <circle cx="4" cy="3.5" r="1.5" />
      <circle cx="4" cy="12.5" r="1.5" />
      <circle cx="12" cy="6" r="1.5" />
      <path d="M4 5v6M12 7.5c0 2.5-3 2-8 3.5" />
    </Icon>
  );
}
