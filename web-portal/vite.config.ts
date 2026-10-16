/**
 * Vite and Vitest configuration of the portal (web-portal-scaffold.md D9).
 *
 * Development: the portal is served on :5173 and `/api` is proxied to the
 * backend on :8000, so the browser sees one origin and the refresh-token
 * cookie needs no CORS (IS-13). The production serving (same origin behind
 * the reverse proxy) is deferred (deferred.md item 106).
 */
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
  test: {
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
