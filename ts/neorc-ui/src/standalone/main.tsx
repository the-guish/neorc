// Standalone neorc: oneberth's shell with one application, neorc. This is
// the page `python/neorc-ui` ships and the manager serves under /ui/.
import { applyRememberedTheme, Shell } from "@oneberth/ui";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { neorcApp } from "../index";

const root = document.getElementById("root");
if (root === null) {
  throw new Error("index.html has no #root");
}

const apps = [neorcApp({ api: "/neorc/" })];

applyRememberedTheme();
createRoot(root).render(
  <StrictMode>
    <Shell apps={apps} title="neorc" />
  </StrictMode>,
);
