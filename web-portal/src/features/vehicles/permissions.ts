/**
 * Feature codes the vehicles UI checks (web-portal-conventions.md §7). They
 * mirror the backend's `vehicles/router.py`: `VEHICLE_READERS` and
 * `VEHICLE_WRITERS` (`roles_for(...)`), so the portal shows exactly what the
 * API lets the caller do.
 */

/** Reading the registry: VEH-01 registry, VEH-02 ownership, MON-02, BAT-01, WAR-01. */
export const VEHICLE_READ_FEATURES = [
  "VEH-01",
  "VEH-02",
  "MON-02",
  "BAT-01",
  "WAR-01",
] as const;

/** Creating, editing and removing vehicles: VEH-01 registry, VEH-02 ownership. */
export const VEHICLE_WRITE_FEATURES = ["VEH-01", "VEH-02"] as const;
