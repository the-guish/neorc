# Contributing to neorc

## Branching model

`main` is the only long-lived branch and must always be releasable. Direct
pushes to `main` are blocked.

1. Create a short-lived branch from `main` for each change, prefixed with
   `feature/` for new functionality or `fix/` for corrections:

       git switch main && git pull
       git switch -c feature/short-description

2. Open a pull request against `main`.
3. Once checks pass and review is complete, a maintainer merges it.

Releases are cut by maintainers by tagging a commit on `main` with a
[Semantic Versioning](https://semver.org/) tag such as `v0.2.0`. Fixes for a
release go through a regular pull request to `main` and ship in the next tag.

## Repository layout

This repository is a monorepo. Each distribution lives in its own directory and
is released from a single tag, so all packages share one version.

    python/neorc-core/    core primitives
    python/neorc/         top-level package, builds on neorc-core
    python/neorc-ui/      the built web UI as a distribution: assets, no code
    ts/neorc-ui/          the web UI's source, and @neorc/ui: React, TypeScript, Vite

The Python packages form a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/)
whose root is the `pyproject.toml` at the top of the repository: it holds the
shared ruff, mypy and pytest configuration and the single `uv.lock`. A package
depends on a sibling by naming it in its own `dependencies`; the root
`[tool.uv.sources]` resolves it to the workspace copy.

Packages in other languages get a sibling top-level directory (`rust/`, `ts/`)
with their own tooling. Dependencies under `ts/` follow
[contributing/js-dependencies.md](contributing/js-dependencies.md).

The web UI is built from `ts/neorc-ui` into `python/neorc-ui`, whose wheel
carries the assets and nothing else. CI and the release build it; a wheel
built from a checkout builds it too, which needs Node.js. Python work never
does: `uv sync` installs `neorc-ui` without assets, and the manager's tests
mount a stand-in directory.

Where code and tests go between `neorc-core` and the adapters in `neorc` is set
by [contributing/in-memory-first.md](contributing/in-memory-first.md): logic
and most tests live in core, running in memory; adapters add only persistence
and transport.

Adding a Python package:

1. Create `python/<name>/` with a `pyproject.toml` modelled on an existing one,
   `src/<module>/` (including `py.typed`), `tests/` and a `README.md`.
2. Copy `LICENSE`, `NOTICE` and `AUTHORS` from the root into it. Each
   distribution ships its own copies and CI checks they stay identical.
3. Add it to `[tool.uv.sources]` and the `dev` dependency group in the root
   `pyproject.toml`, to `testpaths` if it has tests, and to the `package`
   matrices in `.github/workflows/release.yml`.
4. Name test modules so they are unique across the repository, for example
   `tests/test_<name>_package.py`; pytest and mypy collect them all together,
   and two modules cannot share a name. Shared fixtures go in the `conftest.py`
   at the root, for the same reason.

## Development setup

The project uses [uv](https://docs.astral.sh/uv/); what to install on the
machine first is in [contributing/dev-environment.md](contributing/dev-environment.md).
Install the packages and the development tools into `.venv`:

    uv sync

That installs every workspace member in editable mode. The checks below run
across the whole repository from the root.

Run the same checks as CI before opening a pull request:

    uv run ruff check
    uv run ruff format --check
    uv run mypy
    uv run pytest

## Working on the web UI

Node.js at the version in `ts/neorc-ui/.nvmrc`, installed as
[contributing/dev-environment.md](contributing/dev-environment.md) says.
The interface is built on oneberth's shell, `@oneberth/ui`, which
`scripts/oneberth-ui-from-source.sh` packs from the oneberth commit the root
`pyproject.toml` pins. Then, in `ts/neorc-ui`:

    ../../scripts/oneberth-ui-from-source.sh
    nvm install
    npm ci
    npm run dev

`npm run dev` serves the app and proxies the API and `/auth` to a `neorc
manager start --local --no-ui` on `127.0.0.1:8420`: a checkout holds no built
UI, and the dev server is the UI here. The manager prints an address that
signs you in; open it with the dev server's host and port in place of
`127.0.0.1:8420`, then open the dev server's page. To have the manager serve it instead, as it does from the
wheel, `npm run build` and copy `dist/` to `python/neorc-ui/src/neorc_ui/static/`,
which is gitignored. The checks CI runs are `npm run lint`, `npm run
typecheck`, `npm test` and `npm run build`; the build also writes `bundled-packages.txt`,
which is committed, and fails on a license outside the allowlist or a bundle
over the size budget. When the manager's routes change, refresh the schema
the UI's types come from:

    uv run python scripts/export_openapi.py

## Tests that need Postgres

The Postgres tests run against a real server; they are marked `postgres`, and
`uv run pytest -m "not postgres"` leaves them out.

On Python 3.11 and 3.12 there is nothing to set up: `uv sync` installs
`pgserver`, which bundles a Postgres, and the test session starts one of its own
in a temporary directory. On newer Pythons, which `pgserver` has no wheels for,
point the tests at a server you supply and they will use that instead:

    export NEORC_TEST_DATABASE_URL=postgresql://postgres:secret@127.0.0.1:5432/postgres

That is what CI does, so those tests run on every supported Python. Without
either, they skip.

New source files start with the license header:

    # Copyright 2026 The neorc Authors
    # SPDX-License-Identifier: Apache-2.0

## Developer Certificate of Origin

All contributions must be signed off under the
[Developer Certificate of Origin 1.1](https://developercertificate.org/).
Add `-s` to your commit:

    git commit -s -m "component: short imperative summary"

which appends:

    Signed-off-by: Jane Doe <jane@example.com>

Use your real name and a reachable email address. Sign-off is a statement about
the provenance of your contribution, so pseudonymous sign-offs cannot be
accepted. If you are contributing on behalf of an employer, make sure you have
their authorization.
