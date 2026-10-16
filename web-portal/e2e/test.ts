/**
 * The Playwright `test` every spec uses: the standard one plus a check that
 * the page logged no error or warning to the console. Ant Design reports a
 * deprecated prop there, and React a key or hook mistake, so such slips fail
 * the test instead of passing unnoticed.
 */
import { test as base } from "@playwright/test";

export { expect } from "@playwright/test";

export const test = base.extend<{ consoleProblems: string[] }>({
  consoleProblems: [
    async ({ page }, use) => {
      const consoleProblems: string[] = [];
      page.on("console", (message) => {
        // Two browser notices are not the page's problems: Chromium logs every
        // 4xx/5xx answer as "Failed to load resource" (the tests answer with
        // those statuses on purpose), and the software GL renderer of headless
        // Chromium reports a "GPU stall" when a screenshot reads the map canvas.
        const text = message.text();
        const isBrowserNotice =
          text.startsWith("Failed to load resource") ||
          text.includes("GL Driver Message");
        if (
          (message.type() === "error" || message.type() === "warning") &&
          !isBrowserNotice
        ) {
          consoleProblems.push(`${message.type()}: ${message.text()}`);
        }
      });
      page.on("pageerror", (error) => {
        consoleProblems.push(`pageerror: ${error.message}`);
      });
      await use(consoleProblems);
      if (consoleProblems.length > 0) {
        throw new Error(
          `The page logged problems:\n${consoleProblems.join("\n")}`,
        );
      }
    },
    { auto: true },
  ],
});
