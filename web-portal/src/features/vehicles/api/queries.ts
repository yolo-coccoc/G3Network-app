/**
 * Read hooks of the vehicles domain (web-portal-conventions.md §9): one
 * TanStack Query hook per API read; components never call the client.
 */
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { apiClient, unwrap } from "../../../shared/api/client";
import { MAX_PAGE_SIZE } from "../../../shared/lib/listSearch";
import type { VehicleListSearch } from "../search";
import { vehicleKeys, vehicleModelKeys } from "./keys";

/** The model catalog changes rarely; keep it for five minutes. */
const VEHICLE_MODEL_STALE_TIME_MS = 5 * 60_000;

/**
 * One page of the vehicle list (`GET /vehicles/`); the previous page stays on
 * screen while the next loads.
 *
 * @param search - Page, page size and filters from the URL.
 * @returns The query of the page (`items`, `total`).
 */
export function useVehicleListQuery(search: VehicleListSearch) {
  return useQuery({
    queryKey: vehicleKeys.list(search),
    queryFn: async () =>
      unwrap(
        await apiClient.GET("/api/v1/vehicles/", { params: { query: search } }),
      ),
    placeholderData: keepPreviousData,
  });
}

/**
 * One vehicle (`GET /vehicles/{vehicle_id}`); a vehicle out of reach is a 404.
 *
 * @param vehicleId - The vehicle's ID.
 * @returns The query of the vehicle.
 */
export function useVehicleQuery(vehicleId: string) {
  return useQuery({
    queryKey: vehicleKeys.detail(vehicleId),
    queryFn: async () =>
      unwrap(
        await apiClient.GET("/api/v1/vehicles/{vehicle_id}", {
          params: { path: { vehicle_id: vehicleId } },
        }),
      ),
  });
}

/**
 * The owners of a vehicle over time (`GET /vehicles/{vehicle_id}/ownership-periods`).
 *
 * @param vehicleId - The vehicle's ID.
 * @returns The query of the periods, oldest first as the API sends them.
 */
export function useVehicleOwnershipPeriodListQuery(vehicleId: string) {
  return useQuery({
    queryKey: vehicleKeys.ownershipPeriods(vehicleId),
    queryFn: async () =>
      unwrap(
        await apiClient.GET("/api/v1/vehicles/{vehicle_id}/ownership-periods", {
          params: { path: { vehicle_id: vehicleId } },
        }),
      ),
  });
}

/**
 * The vehicle model catalog (`GET /vehicle-models/`), its first 100 models:
 * enough for the model select and for naming models in lists.
 *
 * @returns The query of the catalog page.
 */
export function useVehicleModelListQuery() {
  return useQuery({
    queryKey: vehicleModelKeys.list(),
    queryFn: async () =>
      unwrap(
        await apiClient.GET("/api/v1/vehicle-models/", {
          params: { query: { page: 1, page_size: MAX_PAGE_SIZE } },
        }),
      ),
    staleTime: VEHICLE_MODEL_STALE_TIME_MS,
  });
}
