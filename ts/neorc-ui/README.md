# neorc-ui

Dependencies follow [contributing/js-dependencies.md](../../contributing/js-dependencies.md):
read it before adding, removing or updating a package, and
[AGENTS.md](AGENTS.md) if you are a coding agent. What ships here runs in a
browser with a signed-in person's session, able to do whatever they may.

The manager's web UI: React, TypeScript and Vite. It is built into the
`neorc-ui` Python distribution under `python/neorc-ui`, and served by
`neorc manager start` at `/ui/`. Nothing here runs where neorc runs; only the
built assets ship.

## Working on it

Node.js at the version in `.nvmrc`, installed as
[contributing/dev-environment.md](../../contributing/dev-environment.md) says.
Then:

    nvm install
    npm ci
    npm run dev

`npm run dev` serves the app at the URL it prints and proxies the API and
`/auth` to a `neorc manager start --local --no-ui` on `127.0.0.1:8420`: this
dev server is the UI. The manager prints an address that signs you in; open
it with the dev server's host and port in place of `127.0.0.1:8420`, then
open the dev server's page.

The page reads the manager's API under `/neorc/`, and who is signed in from
oneberth's `/auth/session`.

The API types in `src/api/schema.d.ts` are generated from `openapi.json`,
the manager's committed schema, by `npm run generate`; every script runs it
first. When the routes change, `uv run python scripts/export_openapi.py` at
the repository root refreshes the snapshot.

Checks, as CI runs them: `npm run lint`, `npm run typecheck`, `npm test`,
`npm run build`.
The build fails on a bundled package outside the license allowlist or over
the size budget, and writes `bundled-packages.txt`, which is committed.
