import { useMutation, useQueryClient } from "@tanstack/react-query";
import { detailOf } from "@oneberth/ui";
import { useState } from "react";

import type { Run } from "../api";
import { useApi } from "../context";
import { keys, refresh } from "../queries";

/**
 * Cancel an active root run, and with it every active run in its tree, after
 * saying how many of its sub-runs that reaches. Only a root: the manager
 * cancels a whole tree from any run in it, so a sub-run's page sends the
 * reader to the root rather than offer a button that does more than it says.
 */
export function CancelRun({ run, subRuns }: { run: Run; subRuns: Run[] }) {
  const client = useQueryClient();
  const api = useApi();
  const [confirming, setConfirming] = useState(false);
  const cancel = useMutation({
    mutationFn: () => api.cancelRun(run.id),
    onSettled: () => {
      setConfirming(false);
      void refresh(client, { queryKey: keys.run(run.id) });
      void refresh(client, { queryKey: ["runs"] });
    },
  });
  const active = subRuns.filter((sub) => sub.status === "active").length;
  const refused = cancel.error === null ? null : detailOf(cancel.error);

  return (
    <div className="neorc-cancel-run">
      {confirming ? (
        <p role="group" aria-label="Confirm cancelling">
          This cancels the run and every active run in its tree
          {active > 0 && `: ${active} active sub-run${active === 1 ? "" : "s"} and what is below them`}
          . Tasks already running finish on their own.{" "}
          <button type="button" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
            {cancel.isPending ? "Cancelling…" : "Cancel it"}
          </button>{" "}
          <button type="button" onClick={() => setConfirming(false)} disabled={cancel.isPending}>
            Keep it
          </button>
        </p>
      ) : (
        <button type="button" onClick={() => setConfirming(true)}>
          Cancel run
        </button>
      )}
      {refused && <p role="alert">Could not cancel: {refused}</p>}
    </div>
  );
}
