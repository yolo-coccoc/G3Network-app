/**
 * Query keys of the telemetry domain (web-portal-conventions.md §6, §9).
 */

/** Keys of telemetry queries. */
export const telemetryKeys = {
  all: ["telemetry"] as const,
  fleetLiveStatus: (fleetId: string, includeDescendants: boolean) =>
    [
      ...telemetryKeys.all,
      "fleet-live-status",
      fleetId,
      includeDescendants,
    ] as const,
};
