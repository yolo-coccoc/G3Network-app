/** Every mapped status has a label in both languages (web-portal-ui.md §2). */
import { describe, expect, it } from "vitest";

import { resources } from "../i18n/i18n";
import { STATUS_PRESETS, toOnlineStatus } from "./status";

describe("status map", () => {
  it("labels every status value in Vietnamese and English", () => {
    const statuses = Object.keys(STATUS_PRESETS);

    expect(
      statuses.filter((value) => !(value in resources.vi.common.status)),
    ).toEqual([]);
    expect(
      statuses.filter((value) => !(value in resources.en.common.status)),
    ).toEqual([]);
  });

  it("shows the derived online flag through the same map", () => {
    expect(STATUS_PRESETS[toOnlineStatus(true)]).toBe("success");
    expect(STATUS_PRESETS[toOnlineStatus(false)]).toBe("default");
  });
});
