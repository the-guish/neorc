import { Failed, Loading } from "../components/Layout";
import { useFlows } from "../queries";
import { useNav } from "../router";

/** The latest version of every flow the manager holds. */
export function FlowsPage() {
  const { href } = useNav();
  const flows = useFlows();
  if (flows.isPending) return <Loading what="flows" />;
  if (flows.isError) return <Failed what="flows" error={flows.error} />;

  return (
    <>
      <h1>Flows</h1>
      {flows.data.length === 0 ? (
        <p className="neorc-muted">
          No flows uploaded yet: <code>neorc flows upload</code> puts them here.
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Flow</th>
              <th>Latest version</th>
              <th>Runs</th>
            </tr>
          </thead>
          <tbody>
            {flows.data.map((flow) => (
              <tr key={flow.name}>
                <td>
                  <a href={href({ name: "flow", flow: flow.name })}>
                    {flow.name}
                  </a>
                </td>
                <td>{flow.version}</td>
                <td>
                  <a href={href({ name: "runs", flow: flow.name })}>runs</a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
