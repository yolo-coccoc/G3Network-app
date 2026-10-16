/** Feature checks read `features` only, never role names (ID-44, ID-54). */
import { act, render, renderHook, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Can } from "./Can";
import { hasAnyFeature, hasFeature, useHasFeature } from "./features";
import { type Me, useSessionStore } from "./sessionStore";

/** A signed-in fleet manager with the given features. */
function buildMe(features: string[]): Me {
  return {
    user: {
      user_id: "u1",
      phone_number: "+84901234567",
      email: null,
      full_name: "Nguyễn Văn A",
      status: "ACTIVE",
      status_reason: null,
      created_at: "2026-10-01T00:00:00Z",
    },
    session_id: "s1",
    organization_id: "o1",
    is_internal: false,
    roles: ["FLEET_MANAGER"],
    features,
    memberships: [],
  };
}

afterEach(() => {
  useSessionStore.getState().clearSession();
});

describe("hasFeature / hasAnyFeature", () => {
  it("finds a granted code and refuses a missing one or a missing session", () => {
    expect(hasFeature(["VEH-01"], "VEH-01")).toBe(true);
    expect(hasFeature(["VEH-01"], "FLT-04")).toBe(false);
    expect(hasFeature(undefined, "VEH-01")).toBe(false);
  });

  it("shows a tab when any of its features is granted", () => {
    expect(hasAnyFeature(["FLT-04"], ["VEH-01", "FLT-04"])).toBe(true);
    expect(hasAnyFeature(["ACC-23"], ["VEH-01", "FLT-04"])).toBe(false);
  });
});

describe("useHasFeature and <Can>", () => {
  it("follow the session store", () => {
    const { result } = renderHook(() => useHasFeature("VEH-01"));
    expect(result.current).toBe(false);

    act(() => {
      useSessionStore.getState().setMe(buildMe(["VEH-01"]));
    });
    expect(result.current).toBe(true);
  });

  it("hide an action the caller cannot use instead of disabling it", () => {
    useSessionStore.getState().setMe(buildMe(["VEH-01"]));
    render(
      <>
        <Can feature="VEH-01">
          <button type="button">edit</button>
        </Can>
        <Can feature="ACC-23">
          <button type="button">settings</button>
        </Can>
        <Can feature={["ACC-23", "VEH-01"]}>
          <button type="button">any</button>
        </Can>
      </>,
    );

    expect(screen.getByRole("button", { name: "edit" })).toBeDefined();
    expect(screen.queryByRole("button", { name: "settings" })).toBeNull();
    expect(screen.getByRole("button", { name: "any" })).toBeDefined();
  });
});
