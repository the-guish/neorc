import type { RunStatus, TaskStatus } from "../api";

/** One badge for runs and tasks: the status word, coloured by what it means. */
export function StatusBadge({ status }: { status: RunStatus | TaskStatus }) {
  return (
    <span className={`neorc-badge neorc-badge-${status}`} data-status={status}>
      {status}
    </span>
  );
}

/** The same status as a small coloured dot, its name for a screen reader. */
export function StatusDot({ status }: { status: RunStatus | TaskStatus | "none" }) {
  return (
    <span className={`neorc-dot neorc-dot-${status}`} data-status={status} role="img" aria-label={status} />
  );
}
