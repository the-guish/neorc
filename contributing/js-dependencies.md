# JavaScript dependencies

Rules for the packages under `ts/`. Their code ships to users inside a wheel,
compiled into a bundle nobody reads, and runs in a browser beside a person's
session on the manager, able to do whatever they may. `ts/neorc-ui` is also
`@neorc/ui`, a node package of TypeScript source that a host compiles into
its own bundle. Every dependency is code the project vouches for, at every
version it takes.

## Keep them few

- Add a runtime dependency only when writing the code would cost more than
  vetting the package, and its transitive tree, now and at every update.
- `dependencies` is what the bundle ships; `devDependencies` is build tooling.
  Keep the first list as short as it can be.
- React, React DOM and `@oneberth/ui` are `peerDependencies`: a host brings
  one copy of each, and standalone neorc has them as development
  dependencies. A host gets the other dependencies of `@neorc/ui` from npm
  with it.
- `ts/neorc-ui/bundled-packages.txt` lists every package that ends up in the
  bundle. The build writes it and CI fails when it differs, so a new bundled
  package is a line in the pull request diff. Never edit it by hand.
- Standalone neorc, the shell with neorc, fits in 500 KB gzipped; the build
  fails past that. The build also prints the size of neorc's own chunks,
  which is what a host's bundle gains.

## Vet before adding

- Maintained: recent releases, issues answered, more than one maintainer.
- Small: `npm view <pkg> dependencies` is short, and the size fits the budget.
- Licensed on the allowlist below.
- Published with provenance, `npm view <pkg> dist.attestations`.
- Needs no install script. A package that does is a reason to pick another.
- Read what npm serves, `npm pack <pkg>` and look inside; the GitHub
  repository is not necessarily what was published.

## Licenses

Bundled packages may be licensed MIT, ISC, 0BSD, BSD-2-Clause, BSD-3-Clause,
Apache-2.0, CC0-1.0 or Unlicense. The build fails on anything else, and on a
package with no recognised license (`rollup-plugin-license` in
`vite.config.ts`). The wheel is Apache-2.0; a copyleft library in the bundle
would put that in question. Extending the list is a reviewed change with a
stated reason. Build tooling is not distributed and is not checked.

## Installing and updating

- Exact versions in `package.json`, no ranges. `package-lock.json` is
  committed and installs are `npm ci`, never `npm install`. The peers take a
  range, which every host on React 19 meets.
- `@oneberth/ui` comes from no registry. `scripts/oneberth-ui-from-source.sh`
  packs it from the oneberth commit that the root `pyproject.toml` pins, into
  `ts/neorc-ui/vendor/`, which is not committed. CI and a developer run it
  before `npm ci`. `package-lock.json` holds the tarball's integrity, so
  `npm ci` installs the content of that commit or refuses. Moving the pin
  moves both packages: run the script, then `npm install` the tarball again.
- A host installs `@neorc/ui` the same way, from a checkout of the neorc
  commit it pins: `scripts/neorc-ui-pack.sh` packs it, with `@oneberth/ui`.
- `.npmrc` sets `ignore-scripts=true`; install scripts do not run on any
  machine or in CI.
- Updates come from Dependabot, with a cooldown: no version published fewer
  than 10 days ago. Dependabot leaves `@oneberth/ui` alone. Do not update by hand to a version younger than that;
  compromised releases are usually withdrawn within days.
- One dependency change per pull request, never as a side effect of a feature.
- CI runs `npm audit` for known advisories on everything installed, and
  `npm audit signatures --omit=dev` for registry signatures and provenance
  on what ships: the registry does not hold attestations for every
  dev-only package.
- Node.js at the version in `.nvmrc`, installed as
  [dev-environment.md](dev-environment.md) says.

## In the browser

- The backend serves `/ui/` with oneberth's Content-Security-Policy, which allows
  scripts and connections from its own origin only, and styles from it or
  inline, which React needs for `style` attributes. Do not loosen it for a
  library; choose another library.
- No CDN and no request outside the manager's API. Every asset is in the
  wheel, so the UI works on air-gapped hosts.

## Releases

Bundles are built in CI on the release tag, from the committed lockfile and
`.nvmrc`. Built assets are never committed, and a bundle built on a laptop is
never released.

## Where this is enforced

- `ts/neorc-ui/vite.config.ts`: the license allowlist, the size budget and
  the bundled-package list.
- `ts/neorc-ui/scripts/check-package.mjs`: the peers, exact versions, no
  install script, and what `npm pack` ships.
- `ts/neorc-ui/.npmrc`: exact versions and no install scripts.
- `.github/dependabot.yml`: the updates and their cooldown.
- `.github/workflows/ci.yml`, the `ui` job: audits, signatures, the build
  and the committed list; a red step there names this file.
- oneberth's web part, `oneberth.web.CONTENT_SECURITY_POLICY`: the
  Content-Security-Policy.
- `ts/neorc-ui/AGENTS.md`: the same rules, as do-nots for coding agents.
