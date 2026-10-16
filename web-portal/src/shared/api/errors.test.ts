/** Error mapping per status and of 422 field errors (conventions §8, D15). */
import { describe, expect, it } from "vitest";

import {
  ApiError,
  asApiError,
  describeFieldError,
  toApiError,
  toApiErrorKind,
} from "./errors";

describe("toApiErrorKind", () => {
  it.each([
    [400, "badRequest"],
    [401, "unauthenticated"],
    [403, "forbidden"],
    [404, "notFound"],
    [409, "conflict"],
    [418, "badRequest"],
    [422, "validation"],
    [423, "locked"],
    [429, "tooManyRequests"],
    [500, "server"],
    [503, "server"],
  ])("maps %i to %s", (status, kind) => {
    expect(toApiErrorKind(status)).toBe(kind);
  });
});

describe("toApiError", () => {
  it("keeps the backend's text of a domain error as the detail", () => {
    const error = toApiError(409, { detail: "License plate already exists" });

    expect(error.kind).toBe("conflict");
    expect(error.detail).toBe("License plate already exists");
    expect(error.fieldErrors).toEqual([]);
  });

  it("puts each validation error on its field, without the body prefix", () => {
    const error = toApiError(422, {
      detail: [
        {
          loc: ["body", "license_plate"],
          msg: "String should have at most 20 characters",
          type: "string_too_long",
          ctx: { max_length: 20 },
        },
        {
          loc: ["query", "page_size"],
          msg: "bad",
          type: "less_than_equal",
          ctx: { le: 100 },
        },
      ],
    });

    expect(error.kind).toBe("validation");
    expect(error.detail).toBeUndefined();
    expect(error.fieldErrors.map((fieldError) => fieldError.name)).toEqual([
      ["license_plate"],
      ["page_size"],
    ]);
  });

  it("survives an answer with no JSON body", () => {
    expect(toApiError(502, undefined)).toMatchObject({
      kind: "server",
      detail: undefined,
    });
  });
});

describe("asApiError", () => {
  it("treats a thrown fetch failure as a network error", () => {
    expect(asApiError(new TypeError("Failed to fetch"))).toMatchObject({
      kind: "network",
      status: 0,
    });
  });

  it("returns an API error unchanged", () => {
    const error = new ApiError("notFound", 404, undefined, []);
    expect(asApiError(error)).toBe(error);
  });
});

describe("describeFieldError", () => {
  it("names the limit Pydantic reported", () => {
    expect(
      describeFieldError({
        name: ["year"],
        type: "greater_than_equal",
        context: { ge: 1900 },
        message: "",
      }),
    ).toEqual({ key: "fieldErrors.tooSmall", values: { min: 1900 } });
  });

  it("falls back to a generic message for an unknown type", () => {
    expect(
      describeFieldError({
        name: ["vin"],
        type: "value_error",
        context: {},
        message: "",
      }).key,
    ).toBe("fieldErrors.invalid");
  });
});
