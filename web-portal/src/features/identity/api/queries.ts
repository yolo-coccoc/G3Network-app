/**
 * Read hooks of the identity domain (web-portal-conventions.md §9).
 */
import { useQuery } from "@tanstack/react-query";

import { apiClient, unwrap } from "../../../shared/api/client";
import { organizationKeys } from "./keys";

/** How many organizations a search returns. */
const ORGANIZATION_SEARCH_PAGE_SIZE = 20;

/**
 * Organizations matching a search (`GET /organizations/?q=`): internal staff
 * see every organization, everyone else only their own.
 *
 * @param searchText - Free text; empty lists the first page.
 * @returns The query of the first page of matches.
 */
export function useOrganizationListQuery(searchText: string) {
  return useQuery({
    queryKey: organizationKeys.list(searchText),
    queryFn: async () =>
      unwrap(
        await apiClient.GET("/api/v1/organizations/", {
          params: {
            query: {
              page: 1,
              page_size: ORGANIZATION_SEARCH_PAGE_SIZE,
              q: searchText === "" ? undefined : searchText,
            },
          },
        }),
      ),
  });
}

/**
 * One organization (`GET /organizations/{organization_id}`); another
 * organization is a 404 for a customer user.
 *
 * @param organizationId - The organization's ID.
 * @param isEnabled - Whether to fetch (skip when the name is already known).
 * @returns The query of the organization.
 */
export function useOrganizationQuery(
  organizationId: string,
  isEnabled: boolean,
) {
  return useQuery({
    queryKey: organizationKeys.detail(organizationId),
    queryFn: async () =>
      unwrap(
        await apiClient.GET("/api/v1/organizations/{organization_id}", {
          params: { path: { organization_id: organizationId } },
        }),
      ),
    enabled: isEnabled,
    retry: false,
  });
}

/**
 * An organization's own settings (`GET /organizations/{organization_id}/settings`, ID-45).
 *
 * @param organizationId - The organization's ID.
 * @returns The query of the settings.
 */
export function useOrganizationSettingsQuery(organizationId: string) {
  return useQuery({
    queryKey: organizationKeys.settings(organizationId),
    queryFn: async () =>
      unwrap(
        await apiClient.GET(
          "/api/v1/organizations/{organization_id}/settings",
          {
            params: { path: { organization_id: organizationId } },
          },
        ),
      ),
  });
}
