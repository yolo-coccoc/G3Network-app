/** Route guards send the caller to login, the organization picker or no-access. */
import { isRedirect } from "@tanstack/react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { requireFeature, requireSession } from "./guards";
import { restoreSession } from "./session";
import type { Me } from "./sessionStore";

vi.mock("./session", () => ({ restoreSession: vi.fn() }));

const restoreSessionMock = vi.mocked(restoreSession);

/** A caller with the given organization and features. */
function buildMe(organizationId: string | null, features: string[] = []): Me {
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
    organization_id: organizationId,
    is_internal: false,
    roles: [],
    features,
    memberships: [],
  };
}

/** Run a guard and return the redirect target it threw. */
async function redirectTargetOf(guard: () => unknown): Promise<unknown> {
  try {
    await guard();
  } catch (error) {
    if (isRedirect(error)) {
      return error.options.to;
    }
    throw error;
  }
  return undefined;
}

beforeEach(() => {
  restoreSessionMock.mockReset();
});

describe("requireSession", () => {
  it("sends a visitor without a session to the login page", async () => {
    restoreSessionMock.mockResolvedValue(null);

    expect(
      await redirectTargetOf(() => requireSession("/fleet/vehicles")),
    ).toBe("/login");
  });

  it("sends a person who has not picked an organization to the picker", async () => {
    restoreSessionMock.mockResolvedValue(buildMe(null));

    expect(await redirectTargetOf(() => requireSession("/"))).toBe(
      "/select-organization",
    );
  });

  it("returns the caller when the session is complete", async () => {
    const me = buildMe("o1");
    restoreSessionMock.mockResolvedValue(me);

    expect(await requireSession("/")).toEqual(me);
  });
});

describe("requireFeature", () => {
  it("lets a caller with the feature through", async () => {
    expect(
      await redirectTargetOf(() => {
        requireFeature(buildMe("o1", ["VEH-01"]), "VEH-01");
      }),
    ).toBeUndefined();
  });

  it("accepts any one of several features", async () => {
    expect(
      await redirectTargetOf(() => {
        requireFeature(buildMe("o1", ["VEH-02"]), ["VEH-01", "VEH-02"]);
      }),
    ).toBeUndefined();
  });

  it("sends a caller without the feature to the no-access page", async () => {
    expect(
      await redirectTargetOf(() => {
        requireFeature(buildMe("o1", ["FLT-04"]), "VEH-01");
      }),
    ).toBe("/no-access");
  });
});
