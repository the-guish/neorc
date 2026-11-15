# For coding agents working in ts/neorc-ui

This package ships to users inside the `neorc-ui` wheel, and to hosts as
`@neorc/ui`, and runs in a browser with a signed-in person's session cookie,
so any code in it can do whatever that person may. Its dependency rules are in
[contributing/js-dependencies.md](../../contributing/js-dependencies.md);
read them before touching `package.json`, `package-lock.json`, `.npmrc`,
`vite.config.ts` or `bundled-packages.txt`.

Do not:

- add, remove or bump a package as part of another change. A dependency
  change is its own pull request, vetted as the rules say, with the cooldown
  kept: no version published fewer than 10 days ago.
- loosen the license allowlist in `vite.config.ts`, the size budget, or the
  Content-Security-Policy, oneberth's `oneberth.web.CONTENT_SECURITY_POLICY`, to make
  a library fit. Choose another library, or write the code.
- run `npm install`. Installs are `npm ci`; `save-exact` and `ignore-scripts`
  stay on in `.npmrc`.
- commit `dist/`, `src/api/schema.d.ts`, `vendor/` or `node_modules/`. The
  assets are built by CI and by the wheel build, never committed.
  `scripts/oneberth-ui-from-source.sh` writes `vendor/`.
- add a class name without the `neorc-` prefix, or a rule on an element
  outside `:where(.neorc-app)`. A host's own styles share the page.
- edit `bundled-packages.txt` by hand. The build writes it; a change in it is
  what a reviewer looks at.

Do:

- run `npm run lint`, `npm run typecheck`, `npm test` and `npm run build`
  before declaring a change done; CI runs the same, plus `npm audit` and
  `npm audit signatures --omit=dev`.
- refresh `openapi.json` with `uv run python scripts/export_openapi.py` at
  the repository root when the manager's routes change, and
  `src/test/word_picker_rounds.json` with `scripts/record_ui_fixture.py`
  when what a run looks like changes.
