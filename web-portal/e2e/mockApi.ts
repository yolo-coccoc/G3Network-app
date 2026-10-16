/**
 * A mocked backend for the Playwright tests (D10): every `/api/v1` call is
 * answered in the browser from the fixtures, so the tests are deterministic
 * and need no server, database or seed data. The map style is replaced by a
 * plain background so the map draws the same picture every time.
 */
import type { Schema } from "@g3/api-client";
import type { Page, Request, Route } from "@playwright/test";

import {
  buildMe,
  buildTokenResponse,
  FIXED_NOW,
  FLEET_LIVE_STATUS,
  FLEETS,
  MEMBERSHIPS,
  ORG_ADMIN_FEATURES,
  ORGANIZATION_ID,
  ORGANIZATION_SETTINGS,
  VEHICLE_MODELS,
  VEHICLES,
} from "./fixtures";

/**
 * What the mocked backend starts with.
 *
 * Attributes:
 *   hasSession: Whether the browser holds a valid refresh cookie (a reload
 *     restores the session without logging in).
 *   organizationId: The session's organization; `null` makes the picker show.
 *   features: The caller's features.
 *   memberships: The caller's memberships.
 *   failNextVehicleListWith401: Answer the next vehicle list with 401 once
 *     (an access token that expired during use).
 */
export interface MockApiOptions {
  hasSession?: boolean;
  organizationId?: string | null;
  features?: string[];
  memberships?: Schema<"MembershipSummaryResponse">[];
  failNextVehicleListWith401?: boolean;
}

/**
 * The calls the tests check afterwards.
 *
 * Attributes:
 *   requests: Every API call, as `METHOD path?query` with its JSON body.
 */
export interface MockApi {
  requests: { method: string; url: URL; body: unknown }[];
}

/** A MapLibre style with only a background layer: no tiles, no network. */
const BLANK_MAP_STYLE = {
  version: 8,
  sources: {},
  layers: [
    {
      id: "background",
      type: "background",
      paint: { "background-color": "#eef2ef" },
    },
  ],
};

/** Answer a call with JSON. */
async function reply(
  route: Route,
  status: number,
  body?: unknown,
): Promise<void> {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: body === undefined ? "" : JSON.stringify(body),
  });
}

/** The JSON body of a request, if any. */
function bodyOf(request: Request): unknown {
  const text = request.postData();
  return text === null || text === ""
    ? undefined
    : (JSON.parse(text) as unknown);
}

/**
 * Install the mocked backend on a page and fix the clock.
 *
 * @param page - The test's page.
 * @param options - The starting state.
 * @returns The recorded calls.
 */
export async function mockApi(
  page: Page,
  options: MockApiOptions = {},
): Promise<MockApi> {
  const state = {
    hasSession: options.hasSession ?? true,
    organizationId:
      options.organizationId === undefined
        ? ORGANIZATION_ID
        : options.organizationId,
    features: options.features ?? ORG_ADMIN_FEATURES,
    memberships: options.memberships ?? MEMBERSHIPS.slice(0, 1),
    failNextVehicleListWith401: options.failNextVehicleListWith401 ?? false,
    vehicles: [...VEHICLES],
    settings: { ...ORGANIZATION_SETTINGS },
  };
  const mock: MockApi = { requests: [] };
  const me = () =>
    buildMe(state.organizationId, state.features, state.memberships);

  await page.clock.setFixedTime(FIXED_NOW);
  await page.route("https://demotiles.maplibre.org/**", (route) =>
    reply(route, 200, BLANK_MAP_STYLE),
  );

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const path = url.pathname.replace(/^\/api\/v1/, "");
    const body = bodyOf(request);
    mock.requests.push({ method, url, body });
    const isAuthorized =
      request.headers().authorization === "Bearer access-token-1";

    // Session.
    if (method === "POST" && path === "/auth/refresh") {
      return state.hasSession
        ? reply(route, 200, buildTokenResponse(state.organizationId))
        : reply(route, 401, { detail: "Session ended" });
    }
    if (method === "POST" && path === "/auth/login") {
      const credentials = body as { password?: string };
      if (credentials.password !== "dung-mat-khau") {
        return reply(route, 401, {
          detail: "Invalid phone number or password",
        });
      }
      state.hasSession = true;
      return reply(route, 200, buildTokenResponse(state.organizationId));
    }
    if (method === "POST" && path === "/auth/logout") {
      state.hasSession = false;
      return reply(route, 204);
    }
    if (!isAuthorized) {
      return reply(route, 401, { detail: "Not authenticated" });
    }
    if (method === "GET" && path === "/auth/me") {
      return reply(route, 200, me());
    }
    if (method === "POST" && path === "/auth/organization") {
      state.organizationId = (
        body as { organization_id: string }
      ).organization_id;
      return reply(route, 200, me());
    }

    // Vehicles.
    if (method === "GET" && path === "/vehicle-models/") {
      return reply(route, 200, VEHICLE_MODELS);
    }
    if (method === "GET" && path === "/vehicles/") {
      if (state.failNextVehicleListWith401) {
        state.failNextVehicleListWith401 = false;
        return reply(route, 401, { detail: "Token expired" });
      }
      const searchText = url.searchParams.get("q")?.toLowerCase();
      const status = url.searchParams.get("status");
      const page = Number(url.searchParams.get("page") ?? "1");
      const pageSize = Number(url.searchParams.get("page_size") ?? "20");
      const matches = state.vehicles.filter(
        (vehicle) =>
          (status === null || vehicle.status === status) &&
          (searchText === undefined ||
            vehicle.license_plate.toLowerCase().includes(searchText) ||
            vehicle.vin.toLowerCase().includes(searchText)),
      );
      return reply(route, 200, {
        items: matches.slice((page - 1) * pageSize, page * pageSize),
        total: matches.length,
        page,
        page_size: pageSize,
      });
    }
    if (method === "POST" && path === "/vehicles/") {
      const vehicleCreateRequest = body as Schema<"VehicleCreateRequest">;
      if (
        state.vehicles.some(
          (vehicle) =>
            vehicle.license_plate === vehicleCreateRequest.license_plate,
        )
      ) {
        return reply(route, 409, {
          detail: "A vehicle with this license plate already exists",
        });
      }
      const vehicle: Schema<"VehicleResponse"> = {
        ...vehicleCreateRequest,
        vehicle_id: "9f8e7d6c-0000-4000-8000-000000000099",
        organization_id: ORGANIZATION_ID,
        acquired_at:
          vehicleCreateRequest.acquired_at ?? FIXED_NOW.toISOString(),
        status_reason: null,
        created_at: FIXED_NOW.toISOString(),
        updated_at: FIXED_NOW.toISOString(),
      };
      state.vehicles.push(vehicle);
      return reply(route, 201, vehicle);
    }
    const vehicleMatch = /^\/vehicles\/([^/]+)(\/ownership-periods)?$/.exec(
      path,
    );
    if (vehicleMatch !== null) {
      const vehicle = state.vehicles.find(
        (candidate) => candidate.vehicle_id === vehicleMatch[1],
      );
      if (vehicle === undefined) {
        return reply(route, 404, { detail: "Vehicle not found" });
      }
      if (vehicleMatch[2] !== undefined) {
        return reply(route, 200, {
          items: [
            {
              organization_id: "3c4d5e6f-0000-4000-8000-0000000000aa",
              owned_from: "2025-11-02T17:00:00Z",
              owned_until: "2026-03-14T17:00:00Z",
            },
            {
              organization_id: ORGANIZATION_ID,
              owned_from: "2026-03-14T17:00:00Z",
              owned_until: null,
            },
          ],
        });
      }
      if (method === "GET") {
        return reply(route, 200, vehicle);
      }
      if (method === "PATCH") {
        Object.assign(vehicle, body as object, {
          updated_at: FIXED_NOW.toISOString(),
        });
        return reply(route, 200, vehicle);
      }
      if (method === "DELETE") {
        state.vehicles = state.vehicles.filter(
          (candidate) => candidate !== vehicle,
        );
        return reply(route, 200, { message: "Vehicle deleted successfully" });
      }
    }
    if (method === "GET" && /^\/organizations\/[^/]+$/.test(path)) {
      return reply(route, 404, { detail: "Organization not found" });
    }

    // Fleets and live positions.
    if (method === "GET" && path === "/fleets/") {
      return reply(route, 200, FLEETS);
    }
    if (
      method === "GET" &&
      /^\/telemetry\/fleets\/[^/]+\/vehicles\/latest$/.test(path)
    ) {
      return reply(route, 200, FLEET_LIVE_STATUS);
    }

    // Organization settings.
    if (path === `/organizations/${state.organizationId ?? ""}/settings`) {
      if (method === "PATCH") {
        const settingsUpdate =
          body as Schema<"OrganizationSettingsUpdateRequest">;
        state.settings = {
          ...state.settings,
          telemetry_interval_seconds:
            settingsUpdate.telemetry_interval_seconds ??
            state.settings.telemetry_interval_seconds,
          driving_session_auto_end_minutes:
            settingsUpdate.driving_session_auto_end_minutes ??
            state.settings.driving_session_auto_end_minutes,
          updated_at: FIXED_NOW.toISOString(),
        };
      }
      return reply(route, 200, state.settings);
    }

    return reply(route, 404, { detail: `No mock for ${method} ${path}` });
  });
  return mock;
}
