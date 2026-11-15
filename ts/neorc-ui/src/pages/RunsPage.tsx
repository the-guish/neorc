import { RUN_STATUSES, type Run, type RunStatus } from "../api";
import { Failed, Loading } from "../components/Layout";
import { StatusBadge } from "../components/StatusBadge";
import { formatDuration, formatTime, shortId } from "../format";
import { useFlows, useRuns } from "../queries";
import { useNav } from "../router";

function asStatus(value: string | undefined): RunStatus | undefined {
  return RUN_STATUSES.find((status) => status === value);
}

/** Runs newest first, a page at a time, filtered by flow and status. */
export function RunsPage({
  flow,
  status,
}: {
  flow?: string | undefined;
  status?: string | undefined;
}) {
  const { href, navigate } = useNav();
  const filters = { flow, status: asStatus(status) };
  const flows = useFlows();
  const runs = useRuns(filters);

  const listed = runs.data?.pages.flat() ?? [];
  return (
    <>
      <div className="neorc-page-head">
        <div>
          <h1>Latest runs</h1>
          <p className="neorc-muted neorc-subtitle">
            {runs.isSuccess &&
              `${listed.length}${runs.hasNextPage ? "+" : ""} run${listed.length === 1 ? "" : "s"}` +
                `${flow ? ` of ${flow}` : ""}${filters.status ? `, ${filters.status}` : ""}, newest first`}
          </p>
        </div>
        <form
          className="neorc-filters"
          onSubmit={(event) => event.preventDefault()}
          aria-label="Filters"
        >
          <label>
            Flow{" "}
            <select
              value={flow ?? ""}
              onChange={(event) =>
                navigate({
                  name: "runs",
                  flow: event.target.value || undefined,
                  status,
                })
              }
            >
              <option value="">all</option>
              {(flows.data ?? []).map((each) => (
                <option key={each.name} value={each.name}>
                  {each.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Status{" "}
            <select
              value={filters.status ?? ""}
              onChange={(event) =>
                navigate({
                  name: "runs",
                  flow,
                  status: event.target.value || undefined,
                })
              }
            >
              <option value="">all</option>
              {RUN_STATUSES.map((each) => (
                <option key={each} value={each}>
                  {each}
                </option>
              ))}
            </select>
          </label>
        </form>
      </div>
      {runs.isSuccess && listed.length > 0 && <Tiles runs={listed} partial={runs.hasNextPage} />}
      {runs.isPending && <Loading what="runs" />}
      {runs.isError && <Failed what="runs" error={runs.error} />}
      {runs.isSuccess && listed.length === 0 && (
        <p className="neorc-muted">No runs yet.</p>
      )}
      {listed.length > 0 && (
        <table className="neorc-runs">
          <thead>
            <tr>
              <th>Status</th>
              <th>Run</th>
              <th>Version</th>
              <th>Started</th>
              <th className="neorc-num">Took</th>
            </tr>
          </thead>
          <tbody>
            {listed.map((run) => (
              <tr key={run.id}>
                <td>
                  <StatusBadge status={run.status} />
                </td>
                <td>
                  <a className="neorc-run-name" href={href({ name: "run", id: run.id })}>
                    <strong>{run.flow}</strong>{" "}
                    <span className="neorc-mono neorc-muted">{shortId(run.id)}</span>
                  </a>
                </td>
                <td className="neorc-muted">{run.version}</td>
                <td>{formatTime(run.created_at)}</td>
                <td className="neorc-num neorc-mono">{formatDuration(run.created_at, run.finished_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {runs.hasNextPage && (
        <button
          type="button"
          // Not at the cost of a refetch an event started: see queries.ts.
          onClick={() => void runs.fetchNextPage({ cancelRefetch: false })}
          disabled={runs.isFetchingNextPage}
        >
          {runs.isFetchingNextPage ? "Loading…" : "Older runs"}
        </button>
      )}
    </>
  );
}

const TILES: { status: RunStatus; label: string }[] = [
  { status: "active", label: "Active" },
  { status: "succeeded", label: "Succeeded" },
  { status: "failed", label: "Failed" },
  { status: "cancelled", label: "Cancelled" },
];

/**
 * Counts by status among the runs loaded: the manager aggregates nothing,
 * so the tiles say what they count when more runs remain unloaded.
 */
function Tiles({ runs, partial }: { runs: Run[]; partial: boolean }) {
  return (
    <div className="neorc-tiles" aria-label={partial ? "Of the runs loaded" : "Of these runs"}>
      {TILES.map((tile) => (
        <div key={tile.status} className="neorc-tile">
          <span className="neorc-tile-label">
            {tile.label}
            {partial && <span className="neorc-muted"> of loaded</span>}
          </span>
          <span className="neorc-tile-figure neorc-mono">
            {runs.filter((run) => run.status === tile.status).length}
          </span>
        </div>
      ))}
    </div>
  );
}
