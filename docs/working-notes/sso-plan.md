# Plan: authentication, with sign-in through Google and Okta

> Superseded by oneberth's identity spec, `docs/specs/identity.md` in
> [oneberth](https://github.com/the-guish/oneberth): oneberth signs people in,
> keeps the tokens and checks the roles. This plan is kept as a record.

Goal: no request reaches a manager's data without an identity. People sign in
to the web UI with an OpenID Connect identity provider, Google or Okta.
Machines, the scheduler, workers, `neorc flows upload` and scripts, send an
API token. `neorc manager start` requires authentication unless told
`--no-auth`, and the "No authentication yet" banner shows only then.

Roles are deferred to a later plan: in this one, whoever is authenticated may
do everything the API allows, and who may sign in is decided by an allow list
in the configuration. Everything built here is shaped so roles go on top of
it; see "Choices made while planning".

[web-ui-plan.md](web-ui-plan.md) left authentication to this plan and said no
release is tagged before it lands. Follows
[contributing/in-memory-first.md](../../contributing/in-memory-first.md): what
a token or a session is, and who may sign in, are decided in core and tested
in memory; Postgres stores credentials, FastAPI reads them off a request, and
the identity providers are reached over HTTP by an adapter. Each step is one
branch stacked on the one before, roughly under 1000 lines including tests,
checked, reviewed with `review_my_changes.sh <repo> <previous branch>` until it
has no confirmed or plausible finding worth fixing, then one signed commit.

## What exists, for a reader with no memory of it

- `neorc_core.Manager` (`_manager.py`) holds every behaviour behind the
  `Store` port (`ports/_store.py`), one atomic method per operation, with
  `MemoryStore` in `local/` and `StoreContract` in `testing/contracts/`, which
  `PostgresStore` in `neorc.postgres` passes too.
- `neorc.manager`: `create_app(manager, *, long_poll_timeout, ui)` builds the
  FastAPI app; `_routes.py` is one `APIRouter` of thin translations onto
  `Manager`: flows and runs, the scheduler's requests, the workers'.
  `/health` is on the app. `build_app` in `_service.py` assembles it on
  Postgres from `NEORC_DATABASE_URL`; `run` serves it on uvicorn, bound to
  `0.0.0.0:8420`. `_ui.py` mounts the built UI at `/ui/` with a
  Content-Security-Policy that keeps scripts and connections same-origin and
  `form-action 'self'`.
- Errors cross HTTP as `{"error": "<core exception class>", "detail": ...}`
  with the status `neorc/_errors.py` gives the class; the HTTP clients raise
  the class the body names. Nothing answers 401 today.
- `neorc.http`: `HttpManagerClient` (scheduler, uploads) and `HttpQueueClient`
  (workers) on httpx; `NEORC_MANAGER_ADDRESS` names the manager in the CLI.
- `Scheduler.advance` fails a run when a request about it is refused with any
  `NeorcError` but `ManagerUnavailableError`; `Scheduler.run` and `Worker.run`
  log a `NeorcError` and go on after a second. A refused token must do neither.
- The UI (`ts/neorc-ui`) calls the API with same-origin `fetch`
  (`src/api.ts`), long-polls `GET /events`, and has hash routing; the profile
  at the bottom of the sidebar and the banner are placeholders
  (`src/components/Layout.tsx`). `npm run dev` proxies the API paths to
  `127.0.0.1:8420` (`vite.config.ts`). Its types come from `openapi.json`,
  refreshed by `scripts/export_openapi.py` and checked by a test.
- `python/neorc/tests/test_ui_browser.py` drives the built UI in Chromium
  against a manager on Postgres with a scheduler and workers.

## OpenID Connect, and what each provider sends

| Provider | What identifies a person | What groups them |
| -------- | ------------------------ | ---------------- |
| Google | `sub`; `email` with `email_verified` | `hd`, the Workspace domain; no groups claim |
| Okta | `sub`; `email` with `email_verified` | a `groups` claim, once the authorization server is set up to send one |

One OpenID Connect client, configured by issuer, serves both. Other providers
may work with it, but are not claimed to: they differ in what they send, such
as Entra ID, which sends no `email_verified` by default and puts group object
ids in its groups claim. The client signs in no machine: a process that cannot
follow a browser redirect sends an API token.

## Shape

    python/neorc-core/src/neorc_core/
      _access.py                  Principal, Identity, Allow, ApiToken,
                                  PendingLogin; Access, the service
      ports/_credentials.py       CredentialStore: tokens, sessions, pending logins
      local/_memory_credentials.py
      testing/contracts/_credentials.py
    python/neorc/src/neorc/
      postgres/_credentials.py    PostgresCredentialStore, and its three tables
      auth/                       reaching identity providers, and the config file
        _config.py                auth.toml read into providers and the allow list
        _oidc.py                  an OpenID Connect issuer
      manager/_access.py          who is asking: token or session; the Origin check
      manager/_auth_routes.py     /auth/session, /auth/login, /auth/callback,
                                  /auth/logout
      _cli.py                     neorc tokens, neorc sessions, manager start
                                  --auth-config and --no-auth
    ts/neorc-ui/src/
      session.ts                  the session query
      components/SignIn.tsx       the providers to sign in with

### What is public

With no identity: `/health`, `/`, `/ui/` and `/openapi.json`, which hold no
data, and `/auth/session`, `/auth/login/...`, `/auth/callback/...`. Every
other route needs a token or a session. With authentication on, the Swagger
and ReDoc pages at `/docs` and `/redoc` are not served at all: they load
scripts from a CDN, outside the UI's Content-Security-Policy, on the origin
where the session cookie works.

### Credentials

- An API token is `neorc_` and 32 random bytes in URL-safe base64, shown once
  when created. The store keeps its SHA-256, a name, when it was created and
  when it expires, if ever. A plain hash is enough: the secret has 256 bits
  and is never chosen by a person.
- A session is 32 random bytes in an `HttpOnly`, `SameSite=Lax` cookie,
  `Secure` and `__Host-` prefixed when the public URL is `https`. The store
  keeps its SHA-256, who signed in (provider, subject, name, email), and its
  expiry, 12 hours by default. Signing in makes a new one; signing out deletes
  it; `neorc sessions clear` deletes all.
- A pending login is the `state` of one sign-in, hashed, with its `nonce`,
  PKCE verifier and provider, for 10 minutes. The callback takes it once:
  read and deleted in one operation. A cookie holds the same `state`, so a
  callback is completed only in the browser that started it. Like the
  session's, it is `HttpOnly` and `SameSite=Lax` (`Strict` would drop it on
  the provider's redirect back), and `__Host-` prefixed with `Path=/` and
  `Secure` when the public URL is `https`: the prefix forbids a `Domain`
  attribute, so a sibling subdomain cannot plant a `state` of its own in the
  victim's browser and have them complete the attacker's sign-in. Over
  `http`, which only a loopback public URL allows, neither prefix nor
  `Secure` is possible, and browsers share cookies across every port of a
  host: any other server on the machine receives the session cookie and can
  plant a `state`. A loopback manager with sign-in is for a machine whose
  local servers are trusted, and says so in a warning at startup.
- The store keeps the clock for expiries, as it does for leases: it is given
  a duration, not a time. A find never returns an expired row, and
  `begin_login` deletes expired sessions and pending logins first, at most
  once a minute, so the tables are swept as people sign in, with no job of
  their own. Expired tokens stay listed until revoked.

### Configuration

Tokens need nothing configured. Sign-in is configured in a TOML file read with
the standard library, passed as `--auth-config` or `NEORC_AUTH_CONFIG`.
Secrets are never in it, only the name of the environment variable holding
each.

```toml
public_url = "https://neorc.example.com"   # redirect URIs, cookies, Origin
session_hours = 12

[providers.google]
title = "Google"
issuer = "https://accounts.google.com"
client_id = "1234.apps.googleusercontent.com"
client_secret_env = "NEORC_GOOGLE_CLIENT_SECRET"

[providers.okta]
title = "Okta"
issuer = "https://example.okta.com/oauth2/default"
client_id = "0oa..."
client_secret_env = "NEORC_OKTA_CLIENT_SECRET"
groups_claim = "groups"
scopes = ["openid", "email", "profile", "groups"]

[[allow]]
provider = "google"
hosted_domain = "example.com"

[[allow]]
provider = "okta"
group = "neorc-users"
```

An `[[allow]]` entry names a provider and one matcher: `everyone`, `subject`,
`email` (a verified address), `email_domain` (a verified address's domain),
`hosted_domain` (Google's `hd`; refused for any other provider, where a claim
of that name means nothing fixed), or `group` (a value of the provider's
groups claim). A person any entry matches may sign in; anyone else is refused. A
missing `email_verified` counts as unverified.

Google verifies the addresses of personal accounts registered with a work
address, so for Google `email_verified` alone says nothing about belonging to
a Workspace, and an address someone once held keeps signing them in after
their Workspace account is gone. For the Google issuer, the adapter counts an
address as verified only when `hd` is present, or when the address is a Gmail
one, `gmail.com` or `googlemail.com`. With `hd`, the account belongs to a
Workspace, and every domain its addresses are on, primary or secondary, was
verified by that Workspace's administrators; `hd` names the primary one, so
it is not compared with the address's domain. Google holds Gmail mailboxes
and never gives one to another person. So `email` entries match Workspace
accounts, on any of their domains, and personal Gmail accounts, and never a
personal account registered with a work address. `email_domain` is
refused at startup for Google, `hosted_domain` being the entry that says a
Workspace, and `email_domain = "gmail.com"` would let every Gmail user in,
which `everyone` says more plainly. A Gmail address is compared as Google
reports it, lower-cased: dots and `+` tags are not folded, so an entry names
the address as the person's account holds it.

## Steps

| # | Branch | Step | Status |
| - | ------ | ---- | ------ |
| 0 | `feature/sso-0-plan` | This plan | done |
| 1 | `feature/sso-1-access-core` | Access in core: tokens, sessions, the allow list, in memory | done |
| 2 | `feature/sso-2-postgres-credentials` | The credential store on Postgres | done |
| 3 | `feature/sso-3-manager-tokens` | The manager asks for a token; the clients send one | done |
| 4 | `feature/sso-4-cli` | The command line: tokens, sessions, `--no-auth` | done |
| 5 | `feature/sso-5-oidc` | Sign-in with OpenID Connect | done |
| 6 | `feature/sso-6-ui` | The UI: sign in, the profile, sign out | done |
| 7 | `feature/sso-7-browser-test-docs` | Browser test, docs and changelog | done |
| 8 | `feature/sso-8-review-fixes` | Fixes from the review of the whole | done |

### 1. Access in core: tokens, sessions, the allow list, in memory

Core only, and no new dependency: `secrets` and `hashlib`.

- `_errors.py`: `AuthenticationError`, no identity or a refused one, and
  `SignInRefusedError`, a person the provider vouched for whom no allow entry
  matches; exported from `neorc_core`. `neorc/_errors.py` maps them to 401
  and 403, so the clients raise them by name as they raise every other.
- `_access.py`:
  - `Principal`: kind (`token` or `session`), subject, display name, provider
    and email for a session.
  - `Identity`, what a provider says: provider, subject, name, email,
    `email_verified`, hosted domain, groups. `Allow`, one matcher and its
    value, checked when built; `allowed(identity, allow_list)`, pure.
  - `ApiToken` (id, name, created and expiry times) and `PendingLogin`
    (provider, nonce, PKCE verifier).
  - `Access(credentials, *, allow, session_seconds, login_seconds)`:
    `create_token(name, expires_seconds=None) -> (secret, ApiToken)`,
    `tokens()`, `revoke_token(name)`, `authenticate_token(secret)`,
    `begin_login(provider) -> (state, PendingLogin)`,
    `take_login(provider, state)`, `open_session(identity) -> (secret,
    Principal)`, `authenticate_session(secret)`, `end_session(secret)`,
    `end_sessions()`. A refusal says what kind of credential failed and never
    repeats it.
- `ports/_credentials.py`, `CredentialStore`, one atomic method each: add,
  find by hash, list and delete tokens; add, find by hash and delete one or
  all sessions; add and take a pending login; delete expired sessions and
  logins.
- `MemoryCredentialStore`, and `CredentialStoreContract` run on it: expiry
  with lives short enough to pass in a test, a token name taken twice, a
  login taken twice and by two takes at once, deleting, the sweep.
- Tests for `Access` and `allowed` in `neorc-core/tests`: a revoked or
  expired token, a secret that is no token, a login taken by the wrong
  provider or twice, a person no entry matches, an unverified email against
  `email` and `email_domain`, a missing hosted domain, sign-out, and that no
  secret is kept.

### 2. The credential store on Postgres

- `neorc/postgres/_credentials.py`: `PostgresCredentialStore(Pooled,
  CredentialStore)` on three tables, `neorc_api_tokens` (name unique, hash
  unique), `neorc_sessions` and `neorc_pending_logins` (hash primary keys,
  and an index on the expiry, which the sweep deletes by), times from
  `now()`. Taking a login is one `DELETE ... RETURNING`.
- `_schema.py`: the tables in `CREATE_SCHEMA`, `TABLES` and the drop; the
  schema test's list of tables. No foreign keys to the flow tables and no
  advisory locks: nothing here touches the flow store's lock order.
- `CredentialStoreContract` on Postgres.

### 3. The manager asks for a token; the clients send one

- `manager/_access.py`: one dependency on the router that reads
  `Authorization: Bearer` and authenticates it with `Access`. It runs before
  any body is read, because no route declares a body parameter: each reads
  its body itself through `read_body`, and new routes must keep doing so.
  401 carries `WWW-Authenticate: Bearer`. A test walks
  `app.routes` and checks every route is either behind the dependency or in
  the public list above, so a new route cannot be left open by accident.
- `create_app(manager, *, access)`: `access` is required, `None` for no
  authentication; every existing caller says which. Whoever builds an
  `Access` owns its credential store's opening and closing, as `build_app`
  owns the flow store's.
- `build_app(..., auth: bool)` and `run` take whether authentication is on,
  not an `Access`: `build_app` builds `PostgresCredentialStore` from the same
  database URL, opens it in the lifespan beside the flow store and the
  notifiers, after the schema step, and closes it with them. `run` keeps
  passing `auth=False` until step 4.
- Cross-site writes are refused in every mode, `--no-auth` included, where
  no credential stands between a web page and the manager. The router's
  dependency, for every request other than `GET`, `HEAD` or `OPTIONS`,
  whether or not the route reads a body: 403 for `Sec-Fetch-Site:
  cross-site` or `same-site`, and 415 unless `Content-Type` is
  `application/json`. A form, or a `fetch` a page may send without a
  preflight, cannot carry that type, and the manager answers no preflight,
  so a page elsewhere cannot write whether or not its browser sends Fetch
  Metadata. The HTTP clients send the type on every `POST`, with or without
  a body, and the UI's `postJson` already does. Tested with authentication
  off: a form-like `POST` to a route without a body, such as `cancel` or
  `tasks/receive`, with no `Sec-Fetch-Site`.
- DNS rebinding is not defended against with `--no-auth`: a page whose name
  is rebound to the manager's address is same-origin with it, and the
  manager does not check `Host`. The startup warning says so: a manager with
  no authentication is for a machine where nobody browses untrusted sites,
  or a network nobody else reaches. With authentication on, no credential
  is sent to a rebound name, so it gains nothing.
- The OpenAPI schema declares the bearer scheme; the snapshot is refreshed.
- `neorc.http`: both clients take `token=None` and send it on every request.
  They refuse to send one over `http` to anything but a loopback address,
  unless `allow_insecure=True`, which the CLI sets from
  `NEORC_ALLOW_INSECURE_HTTP=1`: a token can do everything, and a manager
  reached across a network is put behind TLS, a proxy or a load balancer
  that terminates it, or served by the manager itself with the TLS flags of
  step 4, and named with `https://`. The refusal names both ways out: an
  `https://` address, or the variable.
- `Scheduler` and `Worker`: `AuthenticationError` is neither a rejected
  request nor a passing failure. `advance` re-raises it instead of failing
  the run, and `run` stops by raising it, so a revoked token ends the process
  with the reason instead of failing runs or logging every second. The
  worker's heartbeat, which logs any failure and beats again, stops on it
  instead: without heartbeats the lease lapses and another worker may take
  the task, so the handler must not outlive the refusal. An async handler is
  cancelled at once, and the worker reports nothing and raises the refusal
  from `run`. A handler in a thread cannot be interrupted, so `Worker` raises
  the refusal from `run` without waiting for it, and `neorc worker start`
  exits the process at once, as `neorc run` already does when a handler
  thread is still busy, which ends the thread with it. A program that runs
  `Worker` itself with thread handlers is told, in `Worker`'s docstring, to
  do the same, or a refused token may let a task run twice. Tested in core
  with a client that refuses: the scheduler's requests, a poll, a heartbeat
  refused while a long async handler runs (cancelled before the lease
  lapses), and while a thread handler runs (`run` returns without it). The
  command's exit, and its test, come with the token in step 4, the first
  step in which the command can be refused.
- Route tests on the memory stores: no token, a wrong one, an expired one,
  401 before a malformed body, `/health` and `/ui/` open. The client contract
  suites keep running on an app with no authentication, plus a test that the
  token is sent and one that the contracts pass with a token required.

### 4. The command line: tokens, sessions, `--no-auth`

- `neorc tokens create NAME [--expires-days N]` prints the secret once;
  `neorc tokens list`; `neorc tokens revoke NAME`; `neorc sessions clear`. On
  the database directly, from `--database-url` or `NEORC_DATABASE_URL`, as
  `manager start` is: whoever holds the database holds the deployment, and
  the first token cannot come from an API that needs one. They create no
  tables: on a database without them they exit 1 naming `neorc manager start
  --create-schema`, which a manager already deployed needs once to add the
  credential tables.
- `neorc manager start` passes `auth=True` unless `--no-auth` is given,
  which logs a warning at startup saying that anyone who reaches the address
  it listens on, every interface unless `--host` says otherwise, can do
  everything, and that a web page can too through DNS rebinding. In the lifespan, after the schema step,
  the credential store checks its tables exist; when they do not, startup
  fails and the command exits 1 naming `--create-schema`, rather than answer
  every request with a 500 that workers retry forever.
- `neorc manager start --ssl-certfile PATH --ssl-keyfile PATH`, handed to
  uvicorn, so a manager on another host can take tokens over TLS with no
  proxy in front: one process stays enough to deploy.
- `flows upload`, `scheduler start` and `worker start` read `NEORC_API_TOKEN`,
  and `NEORC_ALLOW_INSECURE_HTTP` for a token over `http` beyond loopback.
  `neorc worker start` exits the process at once when the worker raises
  `AuthenticationError`, a handler thread still busy or not, and a test
  shows it does.
  Never a flag: a command line is visible to every user of the host. A 401
  exits 1 saying the token was refused and why, and a worker does not retry
  it as it retries an unreachable manager.
- README quick start, `examples/hello/README.md` and
  `examples/wordplay/README.md`: start the manager with `--create-schema`,
  then create a token, or `--no-auth` for trying it locally; a manager on
  another host served with the TLS flags, or behind a proxy terminating
  TLS, and named with `https://`. `python/neorc/README.md` and
  `docs/specs/core.md` too, whose examples name a manager on another host
  by a bare address; and that the UI needs `--no-auth` until sign-in is configured. The
  end-to-end test deploys the examples with a token.

### 5. Sign-in with OpenID Connect

- `neorc/auth/_config.py`: `auth.toml` read into providers and the allow
  list; every problem listed at once, as flow files are: a secret's variable
  unset, an entry naming an unknown provider or no matcher or two, an
  `email_domain` entry for the Google issuer, an issuer that is not `https`
  (loopback excepted), a public URL that is not a bare
  origin (scheme, host and port, no path), an `http` URL anywhere but
  loopback. The manager is served from the root of its origin: redirects go
  to `/ui/`, and `__Host-` cookies need `Path=/`. The issuer and the public
  URL are normalised once, as they are read: scheme and host lower-cased, a
  default port and a trailing slash dropped; that one value builds the
  discovery URL, is compared with the discovery `issuer` and the `iss` claim,
  builds the redirect URIs, and is what `Origin` must equal. Other forms of
  them are accepted and mean the same.
- `neorc/auth/_oidc.py`: discovery from `{issuer}/.well-known/openid-configuration`
  at a provider's first sign-in, not at startup, so a provider that is down
  keeps no manager from serving tokens; a success is kept for the life of
  the process, a failure is not, and ends that sign-in with an error code
  while the next one tries again. Its `issuer` checked against the configured one, and its
  `authorization_endpoint` and `token_endpoint` refused unless `https`, with
  the same loopback exception: skipping the ID token's signature is sound
  only when the token response comes over TLS; the
  authorization code flow with `state`, `nonce` and PKCE `S256`, asking for
  the provider's `scopes`, `openid email profile` unless configured, since a
  claim no scope asked for is not sent and a missing `email_verified` refuses
  every `email` entry (Okta's groups need a scope added there too); the code
  exchanged at the token endpoint with `client_secret_basic`, or `_post` if
  configured. The ID token comes from that response over TLS, so its claims
  are checked and its signature is not, as OpenID Connect Core 3.1.3.7 item 6
  allows for this flow: `iss`, `aud` holding the client id, `azp` when there
  are several audiences, `exp` and `iat` within a minute of skew, `nonce`.
  Google documents `iss` as `https://accounts.google.com` or
  `accounts.google.com`; for that issuer both are accepted, and the config
  recognises Google by its issuer with any trailing slash removed.
  The claims become an `Identity`. httpx, which the `manager` extra adds;
  the tests give it a transport.
- `manager/_auth_routes.py`:
  - `GET /auth/session`: whether authentication is on, the public URL, the
    providers to sign in with (id and title), and the principal, if any. Public, so the UI can
    draw its sign-in page.
  - `GET /auth/login/{provider}`: a pending login, the `state` cookie, a
    redirect to the provider. The UI links to it on the public URL, which
    `/auth/session` gives, never relative to the page: the cookie is then set
    on the host the provider sends the browser back to, whatever host the
    page was opened on, and the callback's redirect lands the reader on the
    public URL's `/ui/`. The manager never compares a request's `Host`, which
    a proxy in front may have rewritten.
  - `GET /auth/callback/{provider}`: the `state` in the query must equal the
    cookie's; the pending login is taken; the code exchanged; a session
    opened; a redirect to `/ui/`. Any failure redirects to
    `/ui/#/sign-in?error=<code>` with a fixed code, never the provider's
    text, and logs the detail.
  - `POST /auth/logout`: the session deleted, the cookie cleared.
- `manager/_access.py`: a session cookie is accepted where a token is, and a
  request carrying both is refused. A request authenticated by cookie with a
  method other than `GET`, `HEAD` or `OPTIONS` must carry an `Origin` equal to
  the public URL's origin, or, without `Origin`, `Sec-Fetch-Site:
  same-origin`; otherwise 403. Tokens are not ambient, so token requests are
  not checked.
- `neorc manager start --auth-config PATH`, or `NEORC_AUTH_CONFIG`. With
  authentication on and no providers, the manager logs at startup that the UI
  cannot be signed in to.
- Tests against a stand-in OpenID provider, an ASGI app on an httpx
  transport: a whole sign-in; a callback with no cookie, another state, a
  reused state, an expired login; ID tokens with the wrong issuer, audience,
  nonce, or expiry; a person no entry matches; cross-site `POST` by cookie
  refused; sign-out; the scopes asked for; Google's `iss` without a scheme;
  an `http` token endpoint on a host that is not loopback; a Google identity
  with a verified work address and no `hd` matching no `email` entry, and a
  Gmail or Googlemail address matching one; `email_domain = "gmail.com"`
  refused; an issuer and a public URL with a trailing slash, a default port
  and upper case working as their plain forms; discovery failing once, then
  working; a Workspace identity on a secondary domain, whose `hd` is the
  primary, matching its `email` entry; a session with a proxy's `Host`
  header; `/docs` and `/redoc` not served with authentication on.

### 6. The UI: sign in, the profile, sign out

- `src/session.ts`: `useSession()` on `GET /auth/session`. With
  authentication on and no principal, the app shows `SignIn` in place of the
  pages: one link per provider to `<public URL>/auth/login/<id>`, a plain
  navigation, so the CSP's `form-action` is not involved; the hash the reader
  was on is kept in `sessionStorage` and restored after, when the page was
  opened on the public URL, whose origin the storage belongs to. The error codes of step 5 as
  sentences. With no providers, it says sign-in is not configured and names
  `--auth-config`.
- A 401 from any call invalidates the session query, so a session that
  expires mid-page lands on the sign-in page; the event poll stops on it
  instead of retrying.
- The profile in the sidebar: name, email, and "Sign out", a `POST` to
  `/auth/logout`. The banner only when authentication is off.
- `vite.config.ts`: `/auth` proxied in `npm run dev`, which runs against a
  manager started with `--no-auth`, as `CONTRIBUTING.md` says: the dev
  server's origin is not the public URL, so the Origin check and the
  callback's redirect to `/ui/` cannot work through it. The signed-in and
  signed-out states are covered by component tests against stand-in
  sessions, and a real sign-in by the browser test of step 7. `openapi.json`
  refreshed. Component tests for signed out, signed in, each error code, no
  providers, a 401 mid-page.

### 7. Browser test, docs and changelog

- `test_ui_browser.py`: a stand-in OpenID provider on uvicorn beside the
  manager, an `auth.toml` naming it, a sign-in through its page, then the
  existing start, watch, graph and cancel with the session cookie; the
  scheduler and workers with a token.
- README: authentication, `neorc tokens`, and a short "Signing in with Google
  or Okta": the redirect URI to register,
  `{public_url}/auth/callback/<provider id>`, and the allow entry to write.
  `ts/neorc-ui/AGENTS.md` and the READMEs lose "no authentication yet".
- CHANGELOG: the credential store, `Access`, the new CLI commands and flags,
  sign-in, the UI.
- `web-ui-plan.md`: a line under its choice about authentication pointing
  here.

### 8. Fixes from the review of the whole

A review of steps 1 to 7 together found three inputs that ended in a 500
where the design promises a fixed failure, and three pieces of hardening:

- The callback compares its `state` with the cookie's, and the ID token's
  `nonce` with the sign-in's, as bytes: `hmac.compare_digest` refuses a `str`
  holding anything but ASCII, and both come from outside.
- An `iss` claim that is not a string is refused as an invalid ID token
  before it is looked up among the issuers.
- A provider's client secret is left out of its config's repr.
- The route test walks the app's routes rather than its schema, which the
  sign-in routes are not in, so a route kept out of the schema is covered.
- `neorc manager start` says in a line that the database cannot be reached,
  as the token commands do.

## Choices made while planning

Decided here to make progress. Revisit if they are wrong.

- Roles are deferred, not dropped; [roles-plan.md](roles-plan.md) adds them.
  Until they land, an authenticated
  principal may do everything, a worker's token included: a leaked token is
  as bad as a leaked admin session, and the defence is revoking it. They are
  left for their own plan because they add a permission per route, roles on
  tokens and in the allow list, and a UI that hides what a person cannot do.
  This plan keeps the way open: `Principal`, `ApiToken` and the allow entries
  are where roles go, and the one dependency of step 3 is where they are
  checked, so adding them changes no credential and no way of signing in.
- Authentication is built into the manager rather than left to a proxy such
  as oauth2-proxy in front of it. Machines would still need tokens, and a
  manager reachable around the proxy would trust a header anyone can send.
  `pip install` and one process stays the way to deploy.
- On by default. `--no-auth` is a flag someone types, and the banner stays
  for it. `create_app` has no default either.
- OpenID Connect only, tested against the two providers named. No GitHub,
  which offers OAuth 2.0 without OpenID Connect, and no SAML, which would
  bring an XML signature library.
- No JWT or crypto library. The ID token is read from the token endpoint's
  TLS response in the code flow, where OpenID Connect allows skipping its
  signature, and nothing else presents a JWT to the manager. If something
  ever does, such as a CI workload token, that feature verifies signatures
  and brings the library. httpx is already the HTTP client of `neorc`.
- Server-side sessions and pending logins in the database rather than signed
  cookies: no secret key to configure and rotate, sign-out and `sessions
  clear` really end a session, and several managers behind a load balancer
  share them.
- Who may sign in is checked at sign-in and not again for the session; a
  changed allow list applies at the next sign-in, or at once after `neorc
  sessions clear`. No user table: a principal is what its provider said.
- Tokens are managed on the database from the CLI, not through the API.
  Nothing to protect a token-issuing route with, and no UI for it yet.
- `GET /auth/login/{provider}` is public and writes a pending login, so
  anyone can add rows. Discovery comes before the row, so a provider that
  is down leaves none; at most 10,000 are in progress at once, counted and
  stored in one statement, past which a sign-in is refused with the `busy`
  code until some expire, 10 minutes each. A flood can so fill the ceiling
  and keep people from signing in for as long as it lasts; rate limits per
  client, which would stop that, are not in this plan.
- CSRF is refused by an `Origin` check on cookie-authenticated writes, on top
  of `SameSite=Lax`, which does not cover sibling subdomains. No CSRF token:
  every write the UI makes is a `fetch` that sends `Origin`.
- `/openapi.json` stays public: it describes the routes and holds no data.
  The Swagger and ReDoc pages go when authentication is on, rather than be
  served from bundled assets under a CSP: nobody depends on them, and the
  schema is there for any tool.
- Personal Gmail accounts sign in by `email` entries, trusted because Google
  owns Gmail mailboxes; a personal account on any other address does not.
- Not in this plan: roles (above), recording who started or cancelled a
  run, rate limits on sign-in, workload identity tokens, a UI to manage
  tokens, a `Host` allow list against DNS rebinding for `--no-auth`, and
  serving the manager under a path prefix, which would need the
  redirects and cookie paths built from the public URL.
