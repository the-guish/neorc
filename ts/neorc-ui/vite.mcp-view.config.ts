// The MCP App's view (src/mcp-view/): one HTML document with its script and
// style inline, which neorc-mcp serves as `ui://neorc/panel.html`. A host
// frames it sandboxed, with nothing to fetch, so nothing may stay a file of
// its own. docs/specs/mcp.md, "The view".
//
// It bundles the UI's own code and packages. It may bundle no package that
// standalone neorc does not: bundled-packages.txt is what a reviewer vetted.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gzipSync } from "node:zlib";

import react from "@vitejs/plugin-react";
import license from "rollup-plugin-license";
import { defineConfig, type Plugin } from "vite";

const here = import.meta.dirname;
const OUT = resolve(here, "dist-mcp-view");

/** The view, gzipped, fits in this: contributing/js-dependencies.md. */
const SIZE_BUDGET_BYTES = 300 * 1024;

/** The packages standalone neorc bundles, by name, as its build wrote them. */
function vetted(): Set<string> {
  const lines = readFileSync(resolve(here, "bundled-packages.txt"), "utf8").split("\n");
  return new Set(
    lines.filter((line) => line && !line.startsWith("#")).map((line) => line.split(" ")[0] ?? ""),
  );
}

/** `</script` inside a script would end it early: written so it cannot. */
function scriptText(code: string): string {
  return code.replace(/<\/(script)/gi, "<\\/$1");
}

/**
 * Puts the bundle's one script and one stylesheet inside the HTML, and drops
 * them as files. The build has a single chunk: dynamic imports are inlined.
 */
function singleFile(): Plugin {
  return {
    name: "neorc-mcp-view-single-file",
    apply: "build",
    enforce: "post",
    generateBundle(_options, bundle) {
      const html = Object.values(bundle).find(
        (item) => item.type === "asset" && item.fileName.endsWith(".html"),
      );
      if (!html || html.type !== "asset") throw new Error("the view built no HTML");
      let page = String(html.source);
      for (const [name, item] of Object.entries(bundle)) {
        if (item.type === "chunk") {
          const tag = new RegExp(`<script[^>]*src="[./]*${name}"[^>]*></script>`);
          if (!tag.test(page)) throw new Error(`the view's HTML does not load ${name}`);
          page = page.replace(tag, () => `<script type="module">${scriptText(item.code)}</script>`);
          delete bundle[name];
        } else if (name.endsWith(".css")) {
          const tag = new RegExp(`<link[^>]*href="[./]*${name}"[^>]*>`);
          if (!tag.test(page)) throw new Error(`the view's HTML does not load ${name}`);
          page = page.replace(tag, () => `<style>${String(item.source)}</style>`);
          delete bundle[name];
        }
      }
      const left = Object.keys(bundle).filter(
        (name) => name !== html.fileName && name !== "THIRD_PARTY_LICENSES.txt",
      );
      if (left.length > 0) throw new Error(`the view left files of its own: ${left.join(", ")}`);
      html.source = page;
      const size = gzipSync(page).length;
      console.log(`the MCP view is ${size} bytes gzipped (budget ${SIZE_BUDGET_BYTES})`);
      if (size > SIZE_BUDGET_BYTES) {
        throw new Error(`the MCP view is ${size} bytes gzipped, over ${SIZE_BUDGET_BYTES}`);
      }
    },
  };
}

export default defineConfig({
  base: "./",
  plugins: [react(), singleFile()],
  build: {
    outDir: OUT,
    emptyOutDir: true,
    cssCodeSplit: false,
    // Fonts and images, if any, become data: URLs, which the view may load.
    assetsInlineLimit: Number.MAX_SAFE_INTEGER,
    modulePreload: false,
    rollupOptions: {
      input: resolve(here, "mcp-view.html"),
      output: { codeSplitting: false },
      plugins: [
        license({
          thirdParty: {
            includePrivate: true,
            allow: {
              test: (dependency) => vetted().has(dependency.name ?? ""),
              failOnUnlicensed: true,
              failOnViolation: true,
            },
            output: { file: resolve(OUT, "THIRD_PARTY_LICENSES.txt") },
          },
        }),
      ],
    },
  },
});
