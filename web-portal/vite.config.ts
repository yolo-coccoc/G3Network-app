/**
 * Vite and Vitest configuration of the portal (web-portal-scaffold.md D9).
 *
 * Development: the portal is served on :5173 and `/api` is proxied to the
 * backend on :8000, so the browser sees one origin and the refresh-token
 * cookie needs no CORS (IS-13). The production serving (same origin behind
 * the reverse proxy) is deferred (deferred.md item 106).
 *
 * TanStack Router's plugin builds the route tree from `src/routes/` into
 * `src/app/routeTree.gen.ts` (generated, committed so `tsc` sees it) and
 * splits each route into its own chunk.
 */
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [
    // Must run before the React plugin (it rewrites route files).
    tanstackRouter({
      target: "react",
      routesDirectory: "./src/routes",
      generatedRouteTree: "./src/app/routeTree.gen.ts",
      autoCodeSplitting: true,
    }),
    react(),
  ],
  // Scan every source file at start-up, so dependencies used only by lazily
  // loaded routes are pre-bundled at once instead of reloading the page
  // ("Outdated Optimize Dep") the first time such a route opens.
  optimizeDeps: {
    entries: ["index.html", "src/**/*.tsx"],
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
  test: {
    include: ["src/**/*.test.{ts,tsx}"],
    environment: "jsdom",
  },
});
