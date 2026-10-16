/** The login page returns only to a path of the portal (no open redirect). */
import { describe, expect, it } from "vitest";

import { toSafeRedirect } from "./redirect";

describe("toSafeRedirect", () => {
  it("keeps a path of the portal, with its search", () => {
    expect(toSafeRedirect("/fleet/vehicles?page=2")).toBe(
      "/fleet/vehicles?page=2",
    );
  });

  it.each([
    "https://evil.example/",
    "//evil.example/",
    "/\\evil.example",
    "/login?redirect=/x",
    42,
    undefined,
  ])("drops %s", (value) => {
    expect(toSafeRedirect(value)).toBeUndefined();
  });
});
