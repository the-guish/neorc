# @neorc/ui

Dependencies follow [contributing/js-dependencies.md](../../contributing/js-dependencies.md):
read it before adding, removing or updating a package, and
[AGENTS.md](AGENTS.md) if you are a coding agent. What ships here runs in a
browser with a signed-in person's session, able to do whatever they may.

neorc's interface: React, TypeScript and Vite, on oneberth's shell,
`@oneberth/ui`. It ships twice:

- as `@neorc/ui`, a node package of TypeScript source, for a host such as
  Robinauts that mounts neorc in its own shell;
- as standalone neorc, built into the `neorc-ui` Python distribution under
  `python/neorc-ui` and served by `neorc manager start` at `/ui/`. Standalone
  neorc is the shell with one application, neorc (`src/standalone/`).

Nothing here runs where neorc runs; only the built assets ship.

## In a host

The shell draws the rail, the sign-in page and the account pages. neorc
draws everything in its area: its own left panel of runs, flows and queues,
and its pages. Its routes are under `#/neorc/`.

```tsx
import { applyRememberedTheme, Shell, type ShellApp } from "@oneberth/ui";
import { neorcApp } from "@neorc/ui";

const apps: ShellApp[] = [
  { name: "robinauts", title: "Robinauts", icon: <RobinautsIcon />, load: () => import("./RobinautsApp") },
  neorcApp({ api: "/neorc/", forms: robinautsForms }),
];

applyRememberedTheme();
createRoot(root).render(<Shell apps={apps} title="Robinauts" />);
```

- `@neorc/ui` exports `neorcApp`, `NeorcIcon`, `formOf` and the types. Its root
  component loads the first time neorc's icon is chosen.
- `@neorc/ui/app` exports the root component, `NeorcApp`. It takes the
  shell's `AppProps`, `api` and `forms`.
- `api` is the address of neorc's API on the host's origin: `/neorc/`.
- `forms` is the host's source of forms to start a run. It answers a flow's
  form as A2UI v0.9 messages, as a flow's own form file holds them, or null.
  neorc draws its own form for a flow the source has no form for.
  [docs/architecture/forms.md](../../docs/architecture/forms.md) has the subset.
- `formOf` reads a form the way neorc draws it, and answers null for a form
  outside the subset. A host's test checks its form with
  `formOf({ form: messages })`. A host's bundle holds `formOf` only where the
  host calls it.
- While the shell hides neorc, neorc stops its long poll and its polls.
- The styles come with the root component. Every class starts with `neorc-`,
  and the colours are the tokens of `@oneberth/ui`.

React, React DOM and `@oneberth/ui` are peers: the host brings one copy of
each. The query library and the two graph libraries are dependencies of
`@neorc/ui`, and npm installs them with it.

No package is published yet. A host installs both packages from a checkout of
the neorc commit it pins:

```sh
scripts/neorc-ui-pack.sh <host>/vendor
# then, in the host, once: npm install --save-exact ./vendor/oneberth-ui-0.0.0.tgz ./vendor/neorc-ui-0.0.0.tgz
```

The script packs `@neorc/ui` and `@oneberth/ui` from the oneberth commit that
neorc's `pyproject.toml` pins. The host's lock file records the integrity of
each tarball.

## Working on it

Node.js at the version in `.nvmrc`, installed as
[contributing/dev-environment.md](../../contributing/dev-environment.md) says.
Then:

    ../../scripts/oneberth-ui-from-source.sh
    nvm install
    npm ci
    npm run dev

The script packs `@oneberth/ui` into `vendor/`, from the oneberth commit that
the root `pyproject.toml` pins. `package-lock.json` holds the tarball's
integrity, so `npm ci` installs that content or refuses.

`npm run dev` serves standalone neorc at the URL it prints and proxies the
API and `/auth` to a `neorc manager start --local --no-ui` on
`127.0.0.1:8420`: this dev server is the UI. The manager prints an address
that signs you in; open it with the dev server's host and port in place of
`127.0.0.1:8420`, then open the dev server's page.

The API types in `src/api/schema.d.ts` are generated from `openapi.json`,
the manager's committed schema, by `npm run generate`; every script runs it
first. When the routes change, `uv run python scripts/export_openapi.py` at
the repository root refreshes the snapshot.

Checks, as CI runs them: `npm run lint`, `npm run typecheck`, `npm test`,
`npm run build`. The type check uses the strictest flags of both hosts.
The build is standalone neorc. It fails on a bundled package outside the
license allowlist or over the size budget, and writes
`bundled-packages.txt`, which is committed. It prints the size of neorc's
own chunks, which is what a host's bundle gains. `scripts/check-package.mjs`
then checks what `npm pack` would ship.
