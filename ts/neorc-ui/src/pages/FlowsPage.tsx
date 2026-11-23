import { Failed, Loading } from "../components/Layout";
import type { Flow } from "../api";
import { useFlows } from "../queries";
import { useNav } from "../router";

/**
 * The latest version of every flow the manager holds. The inactive flows,
 * which start no run, are folded away below the others.
 */
export function FlowsPage() {
  const flows = useFlows();
  if (flows.isPending) return <Loading what="flows" />;
  if (flows.isError) return <Failed what="flows" error={flows.error} />;
  const active = flows.data.filter((flow) => flow.active);
  const inactive = flows.data.filter((flow) => !flow.active);

  return (
    <>
      <h1>Flows</h1>
      {active.length === 0 ? (
        <p className="neorc-muted">
          No flows uploaded yet: <code>neorc flows upload</code> puts them here.
        </p>
      ) : (
        <FlowTable flows={active} label="Flows" />
      )}
      {inactive.length > 0 && (
        <details className="neorc-inactive-flows">
          <summary>Inactive flows ({inactive.length})</summary>
          <p className="neorc-muted">
            An inactive flow keeps its runs and starts no new one. Uploading
            it again makes it active.
          </p>
          <FlowTable flows={inactive} label="Inactive flows" />
        </details>
      )}
    </>
  );
}

function FlowTable({ flows, label }: Readonly<{ flows: Flow[]; label: string }>) {
  const { href } = useNav();
  return (
    <table aria-label={label}>
      <thead>
        <tr>
          <th>Flow</th>
          <th>Latest version</th>
          <th>Runs</th>
        </tr>
      </thead>
      <tbody>
        {flows.map((flow) => (
          <tr key={flow.name}>
            <td>
              <a href={href({ name: "flow", flow: flow.name })}>{flow.name}</a>
            </td>
            <td>{flow.version}</td>
            <td>
              <a href={href({ name: "runs", flow: flow.name })}>runs</a>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
