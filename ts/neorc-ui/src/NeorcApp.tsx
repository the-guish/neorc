// neorc's root component: the whole neorc application, with its own left
// panel, as an application of oneberth's shell (oneberth's shell.md, "The
// neorc interface as a package").
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { AppProps } from "@oneberth/ui";
import { useMemo, useState } from "react";

import { createApi } from "./api";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Layout } from "./components/Layout";
import { NeorcContext, type FormSource, type Neorc } from "./context";
import { FlowPage } from "./pages/FlowPage";
import { FlowsPage } from "./pages/FlowsPage";
import { QueuePage } from "./pages/QueuePage";
import { QueuesPage } from "./pages/QueuesPage";
import { RunPage } from "./pages/RunPage";
import { RunsPage } from "./pages/RunsPage";
import { useLiveEvents } from "./queries";
import { NavContext, navOf, parseRoute, useNav, type Route } from "./router";
import "./neorc.css";

/** What a host gives neorc's root component, besides what the shell gives it. */
export interface NeorcOptions {
  /** The address of neorc's API on this origin: `/neorc/`. */
  api: string;
  /**
   * The host's source of forms to start a run. neorc draws its own form for
   * a flow the source has no form for, and for every flow without a source.
   */
  forms?: FormSource | undefined;
}

export type NeorcAppProps = AppProps & NeorcOptions;

/** The query client's defaults: the event poll says what to fetch again. */
function newQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // The event poll says when a run's data is to be refetched; the flow
        // queries, which no event covers, set a stale time of their own.
        staleTime: Infinity,
        refetchOnWindowFocus: false,
        retry: 1,
      },
    },
  });
}

/**
 * The whole neorc application: its left panel of runs, flows and queues, and
 * its pages. The shell gives it the route, `navigate`, `href` and `visible`;
 * the host gives it the address of neorc's API and, if it likes, its forms.
 */
export function NeorcApp({ route, navigate, href, visible, api, forms }: NeorcAppProps) {
  const [client] = useState(newQueryClient);
  const neorc = useMemo<Neorc>(
    () => ({ api: createApi(api), visible, forms }),
    [api, visible, forms],
  );
  const nav = useMemo(() => navOf({ href, navigate }), [href, navigate]);
  const shown = parseRoute(route);
  return (
    <div className="neorc-app">
      <NavContext.Provider value={nav}>
        <NeorcContext.Provider value={neorc}>
          <QueryClientProvider client={client}>
            <ErrorBoundary>
              <Live />
              <Layout route={shown}>
                <Page route={shown} />
              </Layout>
            </ErrorBoundary>
          </QueryClientProvider>
        </NeorcContext.Provider>
      </NavContext.Provider>
    </div>
  );
}

export default NeorcApp;

/** The event poll, while neorc is shown. */
function Live() {
  useLiveEvents();
  return null;
}

function Page({ route }: Readonly<{ route: Route }>) {
  const { href } = useNav();
  switch (route.name) {
    case "runs":
      return <RunsPage flow={route.flow} status={route.status} />;
    case "flows":
      return <FlowsPage />;
    // Keyed, so nothing typed or half-confirmed on one page carries to another.
    case "flow":
      return (
        <FlowPage
          key={`${route.flow}@${route.version ?? "latest"}`}
          flow={route.flow}
          version={route.version}
        />
      );
    case "run":
      return <RunPage key={route.id} id={route.id} view={route.view} />;
    case "queues":
      return <QueuesPage kind={route.kind} />;
    case "queue":
      return <QueuePage {...route} />;
    case "unknown":
      return (
        <p role="alert">
          Nothing at <code>{href(route)}</code>.{" "}
          <a href={href({ name: "runs" })}>Runs</a>
        </p>
      );
  }
}
