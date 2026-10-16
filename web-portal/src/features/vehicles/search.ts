/**
 * Search params of the vehicle list (web-portal-conventions.md §9): page,
 * page size and filters live in the URL. The fields keep the API's names, so
 * the parsed object is the list call's query as it is.
 */
import type { Schema } from "@g3/api-client";

import {
  type PageSearch,
  parseOptionalEnum,
  parseOptionalText,
  parsePageSearch,
} from "../../shared/lib/listSearch";

/** A vehicle's service status. */
export type VehicleStatus = Schema<"VehicleStatus">;

/** Every vehicle status, in the order the filter offers them. */
export const VEHICLE_STATUSES = [
  "ACTIVE",
  "INACTIVE",
] as const satisfies readonly VehicleStatus[];

/**
 * Filters and page of the vehicle list.
 *
 * Attributes:
 *   q: Free text, matched against plate and VIN by the backend.
 *   status: Only vehicles in this status.
 */
export interface VehicleListSearch extends PageSearch {
  q?: string;
  status?: VehicleStatus;
}

/**
 * Parse the vehicle list's search params (a route's `validateSearch`).
 *
 * @param search - The raw search params.
 * @returns Valid params; a bad value is dropped.
 */
export function parseVehicleListSearch(
  search: Record<string, unknown>,
): VehicleListSearch {
  return {
    ...parsePageSearch(search),
    q: parseOptionalText(search.q),
    status: parseOptionalEnum(search.status, VEHICLE_STATUSES),
  };
}
