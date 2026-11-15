import type { Session } from "../api";
import { rememberWhereFrom, signInHref } from "../session";

/** What each error code the manager's sign-in redirects with means, for a reader. */
export const SIGN_IN_ERRORS: Record<string, string> = {
  expired: "The sign-in took too long, or its link was used already. Try again.",
  state_mismatch:
    "The sign-in came back to a different browser, or another sign-in began since. Try again.",
  not_allowed:
    "The provider knows who you are, but this manager does not let that account in. Ask whoever runs it.",
  provider_unavailable: "The identity provider could not be reached. Try again shortly.",
  provider_refused: "The identity provider did not sign you in.",
  invalid_id_token: "The identity provider's answer could not be trusted, so you were not signed in.",
  unknown_provider: "That is not a provider this manager signs in with.",
  busy: "Too many sign-ins are under way. Try again in a few minutes.",
};

/** The page instead of every page while nobody is signed in. */
export function SignIn({ session, error }: { session: Session; error?: string }) {
  const providers = session.providers;
  const publicUrl = session.public_url;
  const message =
    error === undefined ? undefined : (SIGN_IN_ERRORS[error] ?? "Signing in failed. Try again.");
  return (
    <main className="sign-in">
      <h1>
        <span className="brand-mark" aria-hidden="true" />
        Sign in to neorc
      </h1>
      {message !== undefined && <p role="alert">{message}</p>}
      {publicUrl === null || providers.length === 0 ? (
        <p>
          Signing in to this manager is not configured: whoever runs it names an identity
          provider in its sign-in configuration. Until then it takes tokens alone.
        </p>
      ) : (
        <ul className="providers">
          {providers.map((provider) => (
            <li key={provider.id}>
              <a
                className="primary"
                href={signInHref(publicUrl, provider.id)}
                onClick={() => rememberWhereFrom(location.hash)}
              >
                Sign in with {provider.title}
              </a>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
