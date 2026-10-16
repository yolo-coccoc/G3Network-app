/** List search params parse leniently: a bad value falls back to its default. */
import { describe, expect, it } from "vitest";

import {
  parseOptionalBoolean,
  parseOptionalEnum,
  parseOptionalText,
  parsePageSearch,
  resolvePage,
} from "./listSearch";

describe("parsePageSearch", () => {
  it("leaves page and size out when the URL names none, and resolvePage fills the defaults", () => {
    expect(parsePageSearch({})).toEqual({
      page: undefined,
      page_size: undefined,
    });
    expect(resolvePage(parsePageSearch({}))).toEqual({
      page: 1,
      page_size: 20,
    });
  });

  it("keeps valid values and clamps the page size to the backend maximum", () => {
    expect(parsePageSearch({ page: 3, page_size: 500 })).toEqual({
      page: 3,
      page_size: 100,
    });
    expect(parsePageSearch({ page: "2", page_size: "50" })).toEqual({
      page: 2,
      page_size: 50,
    });
  });

  it("drops a negative, fractional or non-numeric page", () => {
    expect(resolvePage(parsePageSearch({ page: -1, page_size: 2.5 }))).toEqual({
      page: 1,
      page_size: 20,
    });
    expect(parsePageSearch({ page: "abc" }).page).toBeUndefined();
  });
});

describe("filters", () => {
  it("trims text and drops a blank one", () => {
    expect(parseOptionalText("  51C-123  ")).toBe("51C-123");
    expect(parseOptionalText("   ")).toBeUndefined();
    expect(parseOptionalText({})).toBeUndefined();
  });

  it("accepts only a listed enum value", () => {
    expect(parseOptionalEnum("ACTIVE", ["ACTIVE", "INACTIVE"] as const)).toBe(
      "ACTIVE",
    );
    expect(
      parseOptionalEnum("BROKEN", ["ACTIVE", "INACTIVE"] as const),
    ).toBeUndefined();
  });

  it("reads booleans from JSON or text", () => {
    expect(parseOptionalBoolean(true)).toBe(true);
    expect(parseOptionalBoolean("false")).toBe(false);
    expect(parseOptionalBoolean("yes")).toBeUndefined();
  });
});
