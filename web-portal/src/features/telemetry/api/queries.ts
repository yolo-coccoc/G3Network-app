/**
 * Read hooks of the telemetry domain (web-portal-conventions.md §9).
 */
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { apiClient, unwrap } from "../../../shared/api/client";
import { LIVE_MAP_REFETCH_INTERVAL_MS } from "../../../shared/api/polling";
import { MAX_PAGE_SIZE } from "../../../shared/lib/listSearch";
import { telemetryKeys } from "./keys";

/**
 * Latest position and state of a fleet's trucks
 * (`GET /telemetry/fleets/{fleet_id}/vehicles/latest`, FLT-04), polled while
 * the browser tab is visible. Only the first 100 trucks are fetched; the
 * response's `total` says how many there are.
 *
 * @param fleetId - The fleet; nothing is fetched until one is picked.
 * @param includeDescendants - Add the trucks of every sub-fleet.
 * @returns The polled query.
 */
export function useFleetLiveStatusListQuery(
  fleetId: string | undefined,
  includeDescendants: boolean,
) {
  return useQuery({
    queryKey: telemetryKeys.fleetLiveStatus(fleetId ?? "", includeDescendants),
    queryFn: async () =>
      unwrap(
        await apiClient.GET(
          "/api/v1/telemetry/fleets/{fleet_id}/vehicles/latest",
          {
            params: {
              path: { fleet_id: fleetId ?? "" },
              query: {
                page: 1,
                page_size: MAX_PAGE_SIZE,
                include_descendants: includeDescendants,
              },
            },
          },
        ),
      ),
    enabled: fleetId !== undefined,
    refetchInterval: LIVE_MAP_REFETCH_INTERVAL_MS,
    placeholderData: keepPreviousData,
  });
}
