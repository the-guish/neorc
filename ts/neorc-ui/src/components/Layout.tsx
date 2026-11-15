import { detailOf, ShellPanel } from "@oneberth/ui";
import { useEffect, useState, type ReactNode } from "react";

import { QUEUE_KINDS, type QueueKind } from "../api";
import { useFlows, useQueues, useRecentRuns } from "../queries";
import { useNav, type Route } from "../router";
import { shortId } from "../format";

type NavKey = "runs" | "flows" | "queues";

const NAV: { key: NavKey; label: string; route: Route; icon: ReactNode }[] = [
  { key: "runs", label: "Runs", route: { name: "runs" }, icon: <ListIcon /> },
  { key: "flows", label: "Flows", route: { name: "flows" }, icon: <FlowIcon /> },
  { key: "queues", label: "Queues", route: { name: "queues" }, icon: <QueueIcon /> },
];

/** How many of the latest runs the sidebar lists. */
export const RECENT_RUNS = 6;

/** Where the sidebar's open or collapsed state is kept. */
export const SIDEBAR_KEY = "neorc-ui.sidebar";

/** Where the navigation entries whose lists are folded away are kept. */
export const NAV_COLLAPSED_KEY = "neorc-ui.nav-collapsed";

/** Where the kind of queue the sidebar lists is kept. */
export const QUEUE_KIND_KEY = "neorc-ui.queue-kind";

/**
 * neorc's area of the shell: its own left panel and the page beside it. The
 * panel has the brand, a way to a new run and the navigation, and collapses
 * to a column of icons. Each entry of the navigation has its list below it,
 * the latest runs, the flows and the queues, which its arrow folds away.
 *
 * The shell draws the rail, the sign-in page and the account; on a small
 * screen it draws the panel in its drawer (`ShellPanel`).
 */
export function Layout({ route, children }: Readonly<{ route: Route; children: ReactNode }>) {
  const [collapsed, setCollapsed] = useSidebarCollapsed();
  const [folded, fold] = useFoldedEntries();
  const { href } = useNav();
  return (
    <div className={`neorc-shell${collapsed ? " neorc-shell-rail" : ""}`}>
      <ShellPanel>
        <aside className="neorc-sidebar" aria-label="Sidebar">
          <div className="neorc-sidebar-top">
            <button
              type="button"
              className="neorc-icon-button"
              onClick={() => setCollapsed(!collapsed)}
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              aria-expanded={!collapsed}
            >
              <MenuIcon />
            </button>
            <a
              className="neorc-brand"
              href={href({ name: "runs" })}
              aria-label="neorc, runs"
            >
              <span className="neorc-brand-mark" aria-hidden="true" />
              <span className="neorc-label">neorc</span>
            </a>
          </div>
          <a
            className="neorc-primary neorc-new-run"
            href={href({ name: "flows" })}
            title="New run"
          >
            <PlusIcon />
            <span className="neorc-label">New run</span>
          </a>
          <nav aria-label="Main">
            {NAV.map((item) => {
              const open = !folded.has(item.key);
              return (
                <div key={item.key} className="neorc-nav-group">
                  <div className="neorc-nav-entry">
                    <a
                      href={href(item.route)}
                      aria-current={current(route, item.route) ? "page" : undefined}
                      title={item.label}
                    >
                      {item.icon}
                      <span className="neorc-label">{item.label}</span>
                    </a>
                    <button
                      type="button"
                      className="neorc-icon-button neorc-nav-toggle neorc-label"
                      onClick={() => fold(item.key, open)}
                      aria-label={`${open ? "Collapse" : "Expand"} ${item.label}`}
                      aria-expanded={open}
                    >
                      <ArrowIcon open={open} />
                    </button>
                  </div>
                  {open && item.key === "runs" && <RecentRuns route={route} />}
                  {open && item.key === "flows" && <PinnedFlows route={route} />}
                  {open && item.key === "queues" && <Queues route={route} />}
                </div>
              );
            })}
          </nav>
        </aside>
      </ShellPanel>
      <Content>{children}</Content>
    </div>
  );
}

/** The page. */
function Content({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <div className="neorc-content">
      <main>{children}</main>
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

/**
 * The entries of the navigation whose lists are folded away, kept for the
 * next page load; and folding or unfolding one.
 */
function useFoldedEntries(): [Set<NavKey>, (key: NavKey, fold: boolean) => void] {
  const [folded, setFolded] = useState<Set<NavKey>>(() => {
    try {
      const kept: unknown = JSON.parse(localStorage.getItem(NAV_COLLAPSED_KEY) ?? "[]");
      const keys = Array.isArray(kept) ? kept : [];
      return new Set(NAV.map((item) => item.key).filter((key) => keys.includes(key)));
    } catch {
      return new Set();
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(NAV_COLLAPSED_KEY, JSON.stringify([...folded]));
    } catch {
      // Storage may be off; every list is shown again next time.
    }
  }, [folded]);
  const fold = (key: NavKey, away: boolean) =>
    setFolded((before) => {
      const after = new Set(before);
      if (away) after.add(key);
      else after.delete(key);
      return after;
    });
  return [folded, fold];
}

function current(route: Route, item: Route): boolean {
  if (item.name === "runs")
    return route.name === "runs" || route.name === "run";
  if (item.name === "flows")
    return route.name === "flows" || route.name === "flow";
  if (item.name === "queues")
    return route.name === "queues" || route.name === "queue";
  return false;
}

type KindFilter = QueueKind | "all";

const KIND_FILTERS: readonly KindFilter[] = [...QUEUE_KINDS, "all"];

function useQueueKind(): [KindFilter, (kind: KindFilter) => void] {
  const [kind, setKind] = useState<KindFilter>(() => {
    try {
      const kept = localStorage.getItem(QUEUE_KIND_KEY);
      return KIND_FILTERS.find((each) => each === kept) ?? "all";
    } catch {
      return "all";
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(QUEUE_KIND_KEY, kind);
    } catch {
      // Storage may be off; the sidebar lists every queue again next time.
    }
  }, [kind]);
  return [kind, setKind];
}

/** The declared queues, of the kind chosen, below "Queues": worker, external, or all. */
function Queues({ route }: { route: Route }) {
  const { href } = useNav();
  const queues = useQueues();
  const [kind, setKind] = useQueueKind();
  if (!queues.isSuccess || queues.data.length === 0) return null;
  const listed = queues.data.filter((queue) => kind === "all" || queue.kind === kind);
  return (
    <nav className="neorc-sidebar-section" aria-label="Queues">
      <fieldset className="neorc-segmented neorc-label" aria-label="Kind of queue">
        {KIND_FILTERS.map((each) => (
          <button
            key={each}
            type="button"
            aria-pressed={kind === each}
            onClick={() => setKind(each)}
          >
            {each}
          </button>
        ))}
      </fieldset>
      {listed.map((queue) => (
        <a
          key={queue.name}
          href={href({ name: "queue", queue: queue.name })}
          aria-current={
            route.name === "queue" && route.queue === queue.name ? "page" : undefined
          }
          title={`${queue.name}, ${queue.kind}`}
        >
          <span className={`neorc-dot neorc-dot-queue-${queue.kind}`} aria-hidden="true" />
          <span className="neorc-label">{queue.name}</span>
        </a>
      ))}
      {listed.length === 0 && <p className="neorc-label neorc-muted neorc-sidebar-note">No {kind} queue.</p>}
    </nav>
  );
}

/** The flows the manager holds, latest version each, below "Flows". */
function PinnedFlows({ route }: { route: Route }) {
  const { href } = useNav();
  const flows = useFlows();
  if (!flows.isSuccess || flows.data.length === 0) return null;
  return (
    <nav className="neorc-sidebar-section" aria-label="Flows">
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
          <span className="neorc-dot neorc-dot-flow" aria-hidden="true" />
          <span className="neorc-label">{flow.name}</span>
        </a>
      ))}
    </nav>
  );
}

/** The latest runs, each with its status as a dot, below "Runs". */
function RecentRuns({ route }: { route: Route }) {
  const { href } = useNav();
  const runs = useRecentRuns(RECENT_RUNS);
  const recent = runs.data ?? [];
  if (recent.length === 0) return null;
  return (
    <nav className="neorc-sidebar-section" aria-label="Recent runs">
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
            className={`neorc-dot neorc-dot-${run.status}`}
            data-status={run.status}
            aria-hidden="true"
          />
          <span className="neorc-label">
            {run.flow} <span className="neorc-mono neorc-muted">{shortId(run.id)}</span>
          </span>
        </a>
      ))}
    </nav>
  );
}

export function Loading({ what }: { what: string }) {
  return <p className="neorc-muted">Loading {what}…</p>;
}

export function Failed({ what, error }: { what: string; error: unknown }) {
  const message = detailOf(error);
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
      className="neorc-icon"
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

/** Down while the list below its entry is shown, right while it is folded away. */
function ArrowIcon({ open }: { open: boolean }) {
  return (
    <Icon>
      <path d={open ? "M4 6l4 4 4-4" : "M6 4l4 4-4 4"} />
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

function QueueIcon() {
  return (
    <Icon>
      <rect x="2.5" y="2.5" width="11" height="3" rx="1" />
      <rect x="2.5" y="6.5" width="11" height="3" rx="1" />
      <rect x="2.5" y="10.5" width="11" height="3" rx="1" />
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
