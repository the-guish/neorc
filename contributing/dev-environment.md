# Development environment

What to install on a machine before `uv sync`. The commands to build, test and
check are in [CONTRIBUTING.md](../CONTRIBUTING.md).

## Always

- [uv](https://docs.astral.sh/uv/getting-started/installation/). It fetches a
  Python on its own if the machine has none at 3.11 or newer.
- git.

## Postgres, for the `postgres` tests

- Python 3.11 or 3.12: nothing. `uv sync` installs `pgserver`, which bundles a
  server, and the tests start their own.
- Newer Pythons: a server of yours, named in `NEORC_TEST_DATABASE_URL`. On
  Ubuntu, `sudo apt install postgresql`; or a `postgres` container.
- Neither: the `postgres` tests skip.

## Playwright, for the browser tests

`uv sync` installs the `playwright` package; the browser it drives is a
separate download, with the system libraries it needs (Ubuntu: via `apt`, asks
for `sudo`):

    uv run playwright install --with-deps chromium

Without it, the browser tests skip. Repeat after `uv sync` bumps `playwright`:
each release wants its own browser build. `NEORC_TEST_CHROMIUM` names a
Chromium the tests launch instead, such as a headless shell already on the
machine.

## Node.js, only to work on `ts/`

Needed to build or change a package under `ts/`, never to run neorc. The
version is pinned in that package's `.nvmrc`; use it, not the distribution's
`nodejs` package (Ubuntu 24.04 ships 18, which is out of support).

Install [nvm](https://github.com/nvm-sh/nvm) from a tagged GitHub release,
not a piped install script. It downloads the official build from nodejs.org.
Then, in the package directory, after `scripts/oneberth-ui-from-source.sh`
for `ts/neorc-ui`:

    nvm install
    npm ci
