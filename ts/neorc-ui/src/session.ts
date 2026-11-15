// Who is signed in, and signing in and out.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import {
  getJson,
  onUnauthorized,
  postJson,
  type OneberthSession,
  type Session,
} from "./api";

export const SESSION_KEY = ["session"] as const;

/** Where the hash the reader was on is kept while they sign in. */
export const RETURN_KEY = "neorc-ui.return-to";

/** oneberth's session, as the page shows it: sign-in starts on this origin. */
export function sessionOf(answer: OneberthSession): Session {
  const principal = answer.principal;
  return {
    public_url: location.origin,
    providers: answer.providers,
    principal:
      principal === null
        ? null
        : principal.kind === "user"
          ? {
              kind: "user",
              name: principal.name,
              email: principal.email,
              provider: principal.provider,
            }
          : { kind: "service", name: principal.name, email: null, provider: null },
  };
}

export function useSession() {
  return useQuery({
    queryKey: SESSION_KEY,
    queryFn: async () => sessionOf(await getJson<OneberthSession>("/auth/session")),
    // Asked again when a call answers 401, not on a timer.
    staleTime: Infinity,
  });
}

/**
 * Whether the pages may be shown: someone is signed in. A session that has
 * ended shows the sign-in page instead.
 */
export function mayBrowse(session: Session): boolean {
  return session.principal !== null;
}

/**
 * Any call answering 401 means the session is gone: ask again who is in,
 * which shows the sign-in page if nobody is.
 */
export function useSessionEndsOnRefusal(): void {
  const client = useQueryClient();
  useEffect(
    () =>
      onUnauthorized(() => {
        // Joined, not restarted, when a check is already under way: many
        // calls refused at once ask once.
        void client.invalidateQueries({ queryKey: SESSION_KEY }, { cancelRefetch: false });
      }),
    [client],
  );
}

/** Where a provider's sign-in starts: on the public URL, whatever host this page is on. */
export function signInHref(publicUrl: string, provider: string): string {
  return `${publicUrl}/auth/login/${encodeURIComponent(provider)}`;
}

/** Keep the page the reader was on, to come back to once signed in. */
export function rememberWhereFrom(hash: string): void {
  if (hash === "" || hash === "#" || hash === "#/" || hash.startsWith("#/sign-in")) {
    return;
  }
  try {
    sessionStorage.setItem(RETURN_KEY, hash);
  } catch {
    // Storage may be off; the reader lands on the runs instead.
  }
}

/** The page to go back to after signing in, taken once. */
export function takeWhereFrom(): string | null {
  try {
    const hash = sessionStorage.getItem(RETURN_KEY);
    sessionStorage.removeItem(RETURN_KEY);
    return hash !== null && hash.startsWith("#/") ? hash : null;
  } catch {
    return null;
  }
}

export async function signOut(): Promise<void> {
  await postJson<void>("/auth/logout", {});
}
