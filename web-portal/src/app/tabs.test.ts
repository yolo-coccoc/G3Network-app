/** Tabs follow the user's features; home is the first allowed page (§7). */
import { describe, expect, it } from "vitest";

import { activePagePathOf, resolveHomePath, visibleTabsOf } from "./tabs";

describe("visibleTabsOf", () => {
  it("shows a tab when any of its pages is allowed, with only the allowed pages", () => {
    const tabs = visibleTabsOf(["FLT-04"]);

    expect(tabs.map(({ tab }) => tab.id)).toEqual(["fleet"]);
    expect(tabs[0]?.pages.map((page) => page.path)).toEqual(["/fleet/live"]);
  });

  it("shows every tab to an organization administrator", () => {
    expect(
      visibleTabsOf(["VEH-01", "FLT-04", "ACC-23"]).map(({ tab }) => tab.id),
    ).toEqual(["fleet", "administration"]);
  });
});

describe("resolveHomePath", () => {
  it("lands on the first allowed page", () => {
    expect(resolveHomePath(["VEH-01", "ACC-23"])).toBe("/fleet/vehicles");
    expect(resolveHomePath(["ACC-23"])).toBe("/administration/organization");
  });

  it("lands on the no-access page when nothing is allowed", () => {
    expect(resolveHomePath(["ACC-04"])).toBe("/no-access");
  });
});

describe("activePagePathOf", () => {
  it("counts a vehicle's detail page as part of the vehicle list", () => {
    expect(activePagePathOf("/vehicles/123/edit")).toBe("/fleet/vehicles");
    expect(activePagePathOf("/fleet/live")).toBe("/fleet/live");
    expect(activePagePathOf("/no-access")).toBeUndefined();
  });
});
