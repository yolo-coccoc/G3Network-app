/** Money and time formatters (web-portal-conventions.md §10, web-portal-ui.md §7). */
import { describe, expect, it } from "vitest";

import { formatVnd } from "./formatVnd";
import {
  EMPTY_VALUE,
  formatVietnamClock,
  formatVietnamDate,
  formatVietnamDateTime,
  vietnamDateToUtcIso,
} from "./vietnamTime";

describe("formatVnd", () => {
  it("groups whole dong with dots and adds the dong sign", () => {
    expect(formatVnd(1_250_000)).toBe("1.250.000 ₫");
  });

  it("rounds a fraction and keeps a negative balance", () => {
    expect(formatVnd(999.6)).toBe("1.000 ₫");
    expect(formatVnd(-50_000)).toBe("-50.000 ₫");
  });
});

describe("Vietnam time", () => {
  it("shows a UTC instant in Vietnam time, which is seven hours ahead", () => {
    expect(formatVietnamDateTime("2026-10-15T17:30:00Z")).toBe(
      "16/10/2026 00:30",
    );
    expect(formatVietnamDate("2026-10-15T17:30:00Z")).toBe("16/10/2026");
    expect(formatVietnamClock(Date.UTC(2026, 9, 16, 1, 2, 3))).toBe("08:02:03");
  });

  it("shows a missing value as a dash", () => {
    expect(formatVietnamDateTime(null)).toBe(EMPTY_VALUE);
    expect(formatVietnamDate(undefined)).toBe(EMPTY_VALUE);
  });

  it("sends a picked date as the start of that day in Vietnam, in UTC", () => {
    expect(vietnamDateToUtcIso("2026-10-16")).toBe("2026-10-15T17:00:00.000Z");
  });
});
