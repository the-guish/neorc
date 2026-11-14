import { useEffect } from "react";

import type { Session } from "./api";
import { Failed, Layout, Loading } from "./components/Layout";
import { SignIn } from "./components/SignIn";
import { isEmbedded } from "./embedding";
import { FlowPage } from "./pages/FlowPage";
import { FlowsPage } from "./pages/FlowsPage";
import { QueuePage } from "./pages/QueuePage";
import { QueuesPage } from "./pages/QueuesPage";
import { RunPage } from "./pages/RunPage";
import { RunsPage } from "./pages/RunsPage";
import { useLiveEvents } from "./queries";
import { href, replaceRoute, useRoute, type Route } from "./router";
import {
  mayBrowse,
  takeWhereFrom,
  useSession,
  useSessionEndsOnRefusal,
} from "./session";

/**
 * The session first: with authentication on and nobody signed in, the
 * sign-in page stands in for every page, and nothing else is asked of the
 * manager, which would refuse it.
 */
export function App() {
  const route = useRoute();
  const session = useSession();
  useSessionEndsOnRefusal();
  // A refetch that fails keeps the session it had: only a page that never
  // had one shows the failure, with a way to ask again.
  if (session.data === undefined) {
    return (
      <main>
        {session.isError ? (
          <>
            <Failed what="the session" error={session.error} />
            <button type="button" onClick={() => void session.refetch()}>
              Try again
            </button>
          </>
        ) : (
          <Loading what="the session" />
        )}
      </main>
    );
  }
  if (!mayBrowse(session.data)) {
    return (
      <SignIn
        session={session.data}
        error={route.name === "sign-in" ? route.error : undefined}
      />
    );
  }
  return <Browse route={route} session={session.data} />;
}

function Browse({ route, session }: { route: Route; session: Session }) {
  useLiveEvents();
  useBackToWhereSignInBegan(session);
  return (
    <Layout route={route} session={session} embedded={isEmbedded()}>
      <Page route={route} />
    </Layout>
  );
}

/** Once signed in, the page the reader was on when they went to sign in. */
function useBackToWhereSignInBegan(session: Session) {
  const signedIn = session.principal !== null;
  useEffect(() => {
    if (!signedIn) return;
    const hash = takeWhereFrom();
    if (hash !== null && (location.hash === "" || location.hash === "#/")) {
      location.hash = hash;
    }
  }, [signedIn]);
}

function Page({ route }: { route: Route }) {
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
    case "sign-in":
      return <SignedInAlready />;
    case "unknown":
      return (
        <p role="alert">
          Nothing at <code>{route.hash}</code>.{" "}
          <a href={href({ name: "runs" })}>Runs</a>
        </p>
      );
  }
}

/** A sign-in link followed once signed in: on to the runs, in its place. */
function SignedInAlready() {
  useEffect(() => replaceRoute({ name: "runs" }), []);
  return <Loading what="the runs" />;
}
