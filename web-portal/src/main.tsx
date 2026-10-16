/**
 * Entry point of the portal: mounts the shell (`app/App`) into `#root` of
 * `index.html`. Nothing else belongs here; providers and routing live in
 * `src/app/`.
 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";

const rootElement = document.getElementById("root");
if (rootElement === null) {
  throw new Error("index.html has no #root element");
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
