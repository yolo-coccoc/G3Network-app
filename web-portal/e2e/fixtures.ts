/**
 * API answers the Playwright tests serve (web-portal-conventions.md §11):
 * typed by the generated client, so a backend shape change breaks the type
 * check here instead of silently drifting from the real API.
 */
import type { Schema } from "@g3/api-client";

export const ORGANIZATION_ID = "0b3c6c1e-5f0e-4b8a-9d7a-1f2e3d4c5b6a";
export const SECOND_ORGANIZATION_ID = "7d2a9f40-1c3b-4e5d-8f6a-2b1c0d9e8f7a";
export const FLEET_ID = "c4f1e2d3-a5b6-4c7d-8e9f-0a1b2c3d4e5f";
export const VEHICLE_MODEL_ID = "5e6f7a8b-9c0d-4e1f-a2b3-c4d5e6f7a8b9";

/** Features of an organization administrator (ID-54: every router feature). */
export const ORG_ADMIN_FEATURES = [
  "ACC-04",
  "ACC-09",
  "ACC-12",
  "ACC-13",
  "ACC-23",
  "VEH-01",
  "VEH-02",
  "FLT-01",
  "FLT-04",
];

/** The two memberships of a person who works for two organizations. */
export const MEMBERSHIPS: Schema<"MembershipSummaryResponse">[] = [
  {
    membership_id: "m-1",
    organization_id: ORGANIZATION_ID,
    organization_name: "Công ty Vận tải Xanh",
    organization_status: "ACTIVE",
    is_internal: false,
    status: "ACTIVE",
    roles: ["ORG_ADMIN", "FLEET_MANAGER"],
  },
  {
    membership_id: "m-2",
    organization_id: SECOND_ORGANIZATION_ID,
    organization_name: "Hợp tác xã Vận tải Bình Minh",
    organization_status: "ACTIVE",
    is_internal: false,
    status: "ACTIVE",
    roles: ["DISPATCHER"],
  },
];

/**
 * Build the `/auth/me` answer.
 *
 * @param organizationId - The organization picked, or `null` before the picker.
 * @param features - The caller's features.
 * @param memberships - The caller's memberships.
 */
export function buildMe(
  organizationId: string | null,
  features: string[],
  memberships: Schema<"MembershipSummaryResponse">[],
): Schema<"MeResponse"> {
  return {
    user: {
      user_id: "1a2b3c4d-0000-4000-8000-000000000001",
      phone_number: "+84901234567",
      email: null,
      full_name: "Nguyễn Thị Hoa",
      status: "ACTIVE",
      status_reason: null,
      created_at: "2026-09-01T02:00:00Z",
    },
    session_id: "2b3c4d5e-0000-4000-8000-000000000002",
    organization_id: organizationId,
    is_internal: false,
    roles: ["ORG_ADMIN", "FLEET_MANAGER"],
    features,
    memberships,
  };
}

/**
 * Build a login or refresh answer (WEB: no refresh token in the body, IS-13).
 *
 * @param organizationId - The session's organization.
 */
export function buildTokenResponse(
  organizationId: string | null,
): Schema<"TokenResponse"> {
  return {
    access_token: "access-token-1",
    token_type: "bearer",
    expires_in: 900,
    refresh_token: null,
    session_id: "2b3c4d5e-0000-4000-8000-000000000002",
    organization_id: organizationId,
    memberships: MEMBERSHIPS,
  };
}

export const VEHICLE_MODELS: Schema<"VehicleModelListResponse"> = {
  items: [
    {
      vehicle_model_id: VEHICLE_MODEL_ID,
      make: "Tri-Ring",
      model_name: "T5 Đầu kéo điện",
      gross_vehicle_weight_kg: 49000,
      max_payload_kg: 35000,
      nominal_battery_capacity_kwh: 423,
      consumption_curve: null,
      created_at: "2026-08-01T00:00:00Z",
      updated_at: "2026-08-01T00:00:00Z",
    },
  ],
  total: 1,
  page: 1,
  page_size: 100,
};

/**
 * Build a vehicle.
 *
 * @param index - Makes the IDs, plate and VIN unique.
 * @param overrides - Fields to change.
 */
export function buildVehicle(
  index: number,
  overrides: Partial<Schema<"VehicleResponse">> = {},
): Schema<"VehicleResponse"> {
  const number = String(index).padStart(2, "0");
  return {
    vehicle_id: `9f8e7d6c-0000-4000-8000-0000000000${number}`,
    organization_id: ORGANIZATION_ID,
    acquired_at: "2026-03-14T17:00:00Z",
    license_plate: `51C-${String(100 + index)}.${number}`,
    vin: `LTRT5EV00SH0000${number}`,
    vehicle_model_id: VEHICLE_MODEL_ID,
    year: 2025,
    status: index % 5 === 0 ? "INACTIVE" : "ACTIVE",
    status_reason: index % 5 === 0 ? "Đang sửa chữa tại xưởng" : null,
    created_at: "2026-03-15T03:00:00Z",
    updated_at: "2026-10-01T03:00:00Z",
    ...overrides,
  };
}

/** Twelve trucks of the organization. */
export const VEHICLES = Array.from({ length: 12 }, (_, index) =>
  buildVehicle(index + 1),
);

export const FLEETS: Schema<"FleetListResponse"> = {
  items: [
    {
      fleet_id: FLEET_ID,
      fleet_code: "HCM-01",
      name: "Đội xe Hồ Chí Minh",
      parent_fleet_id: null,
      organization_id: ORGANIZATION_ID,
      vehicle_count: 4,
      created_at: "2026-04-01T00:00:00Z",
      updated_at: "2026-04-01T00:00:00Z",
    },
  ],
  total: 1,
  page: 1,
  page_size: 100,
};

/** One truck's live state, for the live-map fixture. */
type LiveVehicle = Schema<"FleetVehicleLiveStatusResponse">;

/**
 * Build a truck's live state.
 *
 * @param index - Which of `VEHICLES` it is.
 * @param position - Where it is and whether it is online; `null` for no data yet.
 */
function buildLiveVehicle(
  index: number,
  position: { latitude: number; longitude: number; isOnline: boolean } | null,
): LiveVehicle {
  const vehicle = VEHICLES[index] ?? buildVehicle(index + 1);
  const recordedAt =
    position?.isOnline === true
      ? "2026-10-16T02:58:30Z"
      : "2026-10-15T11:20:00Z";
  return {
    vehicle_id: vehicle.vehicle_id,
    vin: vehicle.vin,
    license_plate: vehicle.license_plate,
    vehicle_status: vehicle.status,
    latitude: position?.latitude ?? null,
    longitude: position?.longitude ?? null,
    recorded_at: position === null ? null : recordedAt,
    received_at: position === null ? null : recordedAt,
    is_online: position?.isOnline ?? false,
    signal_strength_dbm: position === null ? null : -71,
  };
}

/** Live positions of the fleet's four trucks: two online, one offline, one without data. */
export const FLEET_LIVE_STATUS: Schema<"FleetVehicleLiveStatusListResponse"> = {
  items: [
    buildLiveVehicle(0, {
      latitude: 10.8231,
      longitude: 106.6297,
      isOnline: true,
    }),
    buildLiveVehicle(1, {
      latitude: 10.7626,
      longitude: 106.6602,
      isOnline: true,
    }),
    buildLiveVehicle(2, {
      latitude: 10.85,
      longitude: 106.77,
      isOnline: false,
    }),
    buildLiveVehicle(3, null),
  ],
  total: 4,
  page: 1,
  page_size: 100,
};

export const ORGANIZATION_SETTINGS: Schema<"OrganizationSettingsResponse"> = {
  organization_id: ORGANIZATION_ID,
  telemetry_interval_seconds: 30,
  driving_session_auto_end_minutes: 120,
  updated_at: "2026-10-01T03:00:00Z",
};

/** The fixed "now" of every test (10:00 in Vietnam). */
export const FIXED_NOW = new Date("2026-10-16T03:00:00Z");
