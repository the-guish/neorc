// The rules of oneberth's shell.md, "How a package reaches an application",
// checked on what `npm pack` would ship: React, React DOM and @oneberth/ui
// are the peers, the other runtime dependencies are pinned, no install
// script runs, and the TypeScript source ships without its tests and
// without standalone neorc's page.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

const here = new URL("..", import.meta.url);
const manifest = JSON.parse(readFileSync(new URL("package.json", here), "utf8"));
const failures = [];

const peers = Object.keys(manifest.peerDependencies ?? {}).sort();
if (peers.join(",") !== "@oneberth/ui,react,react-dom") {
  failures.push(`the peers are ${peers.join(", ")}; they must be @oneberth/ui, react and react-dom`);
}
for (const field of ["dependencies", "devDependencies"]) {
  for (const [name, range] of Object.entries(manifest[field] ?? {})) {
    if (name === "@oneberth/ui") continue;
    if (!/^\d+\.\d+\.\d+$/.test(range)) failures.push(`${name} is pinned to ${range}, not an exact version`);
  }
}
for (const name of peers) {
  if (name in (manifest.dependencies ?? {})) failures.push(`${name} is a peer, and must not be a dependency`);
}
const INSTALL_SCRIPTS = ["preinstall", "install", "postinstall", "prepare", "prepack", "postpack"];
for (const name of INSTALL_SCRIPTS) {
  if (manifest.scripts?.[name] !== undefined) failures.push(`the script ${name} must not exist`);
}
if (manifest.exports?.["."] !== "./src/index.tsx") failures.push("the entry point must be ./src/index.tsx");
if (manifest.exports?.["./app"] !== "./src/NeorcApp.tsx") failures.push("./app must be ./src/NeorcApp.tsx");

const [packed] = JSON.parse(
  execFileSync("npm", ["pack", "--dry-run", "--json", "--ignore-scripts"], {
    cwd: here,
    encoding: "utf8",
  }),
);
const files = packed.files.map((file) => file.path);
for (const path of files) {
  if (/\.test\.tsx?$/.test(path) || path.startsWith("src/test/") || path.startsWith("src/standalone/")) {
    failures.push(`${path} is shipped`);
  }
}
const NEEDED = ["src/index.tsx", "src/NeorcApp.tsx", "src/neorc.css", "src/api/schema.d.ts", "LICENSE"];
for (const needed of NEEDED) {
  if (!files.includes(needed)) failures.push(`${needed} is not shipped`);
}

if (failures.length > 0) {
  console.error(failures.map((line) => `check-package: ${line}`).join("\n"));
  process.exit(1);
}
console.log(`check-package: ${files.length} files; peers @oneberth/ui, react and react-dom`);
