/**
 * Read hooks of the fleet domain (web-portal-conventions.md §9).
 */
import { useQuery } from "@tanstack/react-query";

import { apiClient, unwrap } from "../../../shared/api/client";
import { MAX_PAGE_SIZE } from "../../../shared/lib/listSearch";
import { fleetKeys } from "./keys";

/**
 * The caller's fleets (`GET /fleets/`), every level of the tree, first 100:
 * a manager limited to some fleets sees only those (FL-10).
 *
 * @returns The query of the fleet page.
 */
export function useFleetListQuery() {
  return useQuery({
    queryKey: fleetKeys.list(),
    queryFn: async () =>
      unwrap(
        await apiClient.GET("/api/v1/fleets/", {
          params: { query: { page: 1, page_size: MAX_PAGE_SIZE } },
        }),
      ),
  });
}
