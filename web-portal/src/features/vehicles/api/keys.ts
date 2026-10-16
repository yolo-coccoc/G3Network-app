/**
 * Query keys of the vehicles domain (web-portal-conventions.md §6, §9). A
 * mutation invalidates the keys it affects through these factories.
 */
import type { VehicleListSearch } from "../search";

/** Keys of vehicle queries. */
export const vehicleKeys = {
  all: ["vehicles"] as const,
  lists: () => [...vehicleKeys.all, "list"] as const,
  list: (search: VehicleListSearch) =>
    [...vehicleKeys.lists(), search] as const,
  details: () => [...vehicleKeys.all, "detail"] as const,
  detail: (vehicleId: string) => [...vehicleKeys.details(), vehicleId] as const,
  ownershipPeriods: (vehicleId: string) =>
    [...vehicleKeys.detail(vehicleId), "ownership-periods"] as const,
};

/** Keys of vehicle model (catalog) queries. */
export const vehicleModelKeys = {
  all: ["vehicle-models"] as const,
  list: () => [...vehicleModelKeys.all, "list"] as const,
};
