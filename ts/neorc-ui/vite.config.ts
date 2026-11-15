// Dependencies and the checks below follow contributing/js-dependencies.md.
//
// The build is standalone neorc (src/standalone/): oneberth's shell with one
// application, neorc, which the neorc-ui wheel ships. neorc's root component
// loads as chunks of its own, the part a host's bundle gains by mounting
// neorc; the build says how large they are.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { gzipSync } from "node:zlib";

import react from "@vitejs/plugin-react";
import license from "rollup-plugin-license";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

/**
 * Licenses a bundled package may carry; the build fails on any other.
 * Extending this list is a reviewed change with a stated reason:
 * contributing/js-dependencies.md, "Licenses".
 */
const ALLOWED_LICENSES = [
  "MIT",
  "ISC",
  "0BSD",
  "BSD-2-Clause",
  "BSD-3-Clause",
  "Apache-2.0",
  "CC0-1.0",
  "Unlicense",
];

/** Everything shipped, gzipped, must fit in this: contributing/js-dependencies.md. */
const SIZE_BUDGET_BYTES = 500 * 1024;

const here = import.meta.dirname;

/**
 * What ends up in the bundle, one line per package, committed and checked by
 * CI: a new line here is what a reviewer looks at. Never edited by hand.
 */
const BUNDLED_PACKAGES = resolve(here, "bundled-packages.txt");

function gzippedSize(dir: string, only: (name: string) => boolean = () => true): number {
  let total = 0;
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      total += gzippedSize(path, only);
    } else if (only(name)) {
      total += gzipSync(readFileSync(path)).length;
    }
  }
  return total;
}

function sizeBudget(): Plugin {
  return {
    name: "neorc-size-budget",
    apply: "build",
    closeBundle() {
      const size = gzippedSize(resolve(here, "dist"));
      const neorc = gzippedSize(resolve(here, "dist"), (name) => name.startsWith("NeorcApp-"));
      console.log(
        `standalone neorc is ${size} bytes gzipped (budget ${SIZE_BUDGET_BYTES}); ` +
          `neorc's own chunks, what a host gains, are ${neorc}`,
      );
      if (size > SIZE_BUDGET_BYTES) {
        throw new Error(
          `the built assets are ${size} bytes gzipped, over the budget of ` +
            `${SIZE_BUDGET_BYTES}: see contributing/js-dependencies.md`,
        );
      }
    },
  };
}

export default defineConfig({
  // Relative asset paths, so the app works under any prefix, such as /ui/.
  base: "./",
  plugins: [react(), sizeBudget()],
  test: {
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    css: false,
    // Role queries on a whole page are slow in jsdom: the slowest tests take
    // 3 s on an idle machine and over 5 s on a busy one.
    testTimeout: 20_000,
  },
  server: {
    // `npm run dev` against a local `neorc manager start --local`: the
    // manager's API under /neorc/, and oneberth's sign-in under /auth/.
    // oneberth takes a write with the session cookie from its own origin, and
    // the local mode a request to a loopback host: both are the manager's.
    proxy: {
      "^/(neorc|auth|health)([/?].*)?$": {
        target: "http://127.0.0.1:8420",
        changeOrigin: true,
        headers: { origin: "http://127.0.0.1:8420" },
      },
    },
  },
  build: {
    rollupOptions: {
      plugins: [
        license({
          thirdParty: {
            // @oneberth/ui is a private package, installed from a checkout.
            includePrivate: true,
            allow: {
              test: (dependency) =>
                ALLOWED_LICENSES.includes(dependency.license ?? ""),
              failOnUnlicensed: true,
              failOnViolation: true,
            },
            output: [
              { file: resolve(here, "dist/THIRD_PARTY_LICENSES.txt") },
              {
                file: BUNDLED_PACKAGES,
                template: (dependencies) =>
                  [
                    "# Packages in the built bundle, written by the build.",
                    "# A change here is reviewed: see contributing/js-dependencies.md.",
                    ...dependencies
                      .map((d) => `${d.name} ${d.version} ${d.license}`)
                      .sort(),
                  ].join("\n") + "\n",
              },
            ],
          },
        }),
      ],
    },
  },
});
