/**
 * Query keys of the fleet domain (web-portal-conventions.md §6, §9).
 */

/** Keys of fleet queries. */
export const fleetKeys = {
  all: ["fleets"] as const,
  list: () => [...fleetKeys.all, "list"] as const,
};
