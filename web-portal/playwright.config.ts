/**
 * Playwright configuration (web-portal-scaffold.md D10, web-portal-ui.md §6):
 * Chromium only, a fixed viewport, Vietnamese locale and Vietnam time, and
 * the API mocked in every test (`e2e/mockApi.ts`), so screenshots are
 * deterministic and need no backend. Run by `make portal-e2e`, not by the
 * pre-commit hook. Its own Vite server on :5174 keeps a running `make
 * portal-dev` (:5173) out of the way.
 */
import { defineConfig, devices } from "@playwright/test";

const E2E_PORT = 5174;

export default defineConfig({
  testDir: "./e2e",
  snapshotPathTemplate: "{testDir}/__screenshots__/{testFilePath}/{arg}{ext}",
  fullyParallel: true,
  forbidOnly: process.env.CI !== undefined,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${String(E2E_PORT)}`,
    locale: "vi-VN",
    timezoneId: "Asia/Ho_Chi_Minh",
    trace: "retain-on-failure",
  },
  expect: {
    toHaveScreenshot: {
      maxDiffPixelRatio: 0.01,
      animations: "disabled",
      caret: "hide",
    },
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
      },
    },
  ],
  webServer: {
    command: `vite --port ${String(E2E_PORT)} --strictPort`,
    url: `http://localhost:${String(E2E_PORT)}`,
    reuseExistingServer: process.env.CI === undefined,
    timeout: 60_000,
  },
});
