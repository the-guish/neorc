import { useMemo } from "react";

import { Failed, Loading } from "../components/Layout";
import { FlowGraph } from "../components/RunGraph";
import { StartRunForm } from "../components/StartRunForm";
import { stepsOf } from "../definition";
import { formatValue } from "../format";
import { useFlow, useFlowVersions } from "../queries";
import { href, navigate } from "../router";

/**
 * One flow: its versions, and of one of them the steps as a graph, the form
 * to start a run, and the definition as it was uploaded, read-only.
 */
export function FlowPage({ flow, version }: { flow: string; version?: string }) {
  const versions = useFlowVersions(flow);
  const shown = useFlow(flow, version);
  const steps = useMemo(() => (shown.data ? stepsOf(shown.data.content) : []), [shown.data]);

  if (versions.isPending || shown.isPending) return <Loading what={flow} />;
  if (versions.isError) return <Failed what={flow} error={versions.error} />;
  if (shown.isError) return <Failed what={flow} error={shown.error} />;

  return (
    <>
      <h1>
        {flow} <small className="muted">{shown.data.version}</small>
      </h1>
      <p>
        <a href={href({ name: "runs", flow })}>Runs of this flow</a>
      </p>
      <label>
        Version{" "}
        <select
          value={shown.data.version}
          onChange={(event) =>
            navigate({ name: "flow", flow, version: event.target.value })
          }
        >
          {versions.data.map((each, index) => (
            <option key={each.version} value={each.version}>
              {each.version}
              {index === 0 ? " (latest)" : ""}
            </option>
          ))}
        </select>
      </label>
      {steps.length > 0 && (
        <>
          <h2>Steps</h2>
          <FlowGraph key={`${flow}@${shown.data.version}`} steps={steps} />
        </>
      )}
      {shown.data.version === versions.data[0]?.version ? (
        <StartRunForm key={`${flow}@${shown.data.version}`} flow={shown.data} />
      ) : (
        <p className="muted">
          A run always uses the latest version, {versions.data[0]?.version}:{" "}
          <a href={href({ name: "flow", flow })}>start one from there</a>.
        </p>
      )}
      <h2>Definition</h2>
      <pre className="definition" aria-label="Definition">
        {formatValue(shown.data.content)}
      </pre>
    </>
  );
}
