import { QUEUE_KINDS, type QueueKind } from "../api";
import { Failed, Loading } from "../components/Layout";
import { useQueues } from "../queries";
import { href, navigate } from "../router";

export function asKind(value: string | undefined): QueueKind | undefined {
  return QUEUE_KINDS.find((kind) => kind === value);
}

/** What a queue's kind means, for a reader who has not met the word. */
export const KIND_NOTES: Record<QueueKind, string> = {
  worker: "Workers run its tasks.",
  external: "A person or another system completes its tasks.",
};

/** The declared queues, by name, filtered by kind. */
export function QueuesPage({ kind }: { kind?: string }) {
  const queues = useQueues();
  const chosen = asKind(kind);
  const listed = (queues.data ?? []).filter(
    (queue) => chosen === undefined || queue.kind === chosen,
  );
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Queues</h1>
          <p className="muted subtitle">
            {queues.isSuccess &&
              `${listed.length} ${chosen ? `${chosen} ` : ""}queue${listed.length === 1 ? "" : "s"}`}
          </p>
        </div>
        <form
          className="filters"
          onSubmit={(event) => event.preventDefault()}
          aria-label="Filters"
        >
          <label>
            Kind{" "}
            <select
              value={chosen ?? ""}
              onChange={(event) =>
                navigate({ name: "queues", kind: event.target.value || undefined })
              }
            >
              <option value="">all</option>
              {QUEUE_KINDS.map((each) => (
                <option key={each} value={each}>
                  {each}
                </option>
              ))}
            </select>
          </label>
        </form>
      </div>
      {queues.isPending && <Loading what="queues" />}
      {queues.isError && <Failed what="queues" error={queues.error} />}
      {queues.isSuccess && listed.length === 0 && (
        <p className="muted">
          {queues.data.length === 0
            ? "No queue is declared yet."
            : `No ${chosen} queue is declared.`}
        </p>
      )}
      {listed.length > 0 && (
        <table className="queues">
          <thead>
            <tr>
              <th>Queue</th>
              <th>Kind</th>
              <th>Its tasks</th>
            </tr>
          </thead>
          <tbody>
            {listed.map((queue) => (
              <tr key={queue.name}>
                <td>
                  <a href={href({ name: "queue", queue: queue.name })}>
                    <strong>{queue.name}</strong>
                  </a>
                </td>
                <td>
                  <span className={`kind kind-${queue.kind}`}>{queue.kind}</span>
                </td>
                <td className="muted">{KIND_NOTES[queue.kind]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
