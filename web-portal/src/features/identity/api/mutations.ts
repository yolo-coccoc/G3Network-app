/**
 * Write hooks of the identity domain (web-portal-conventions.md §9). Login and
 * the organization switch go through `shared/auth`, which keeps the session.
 */
import type { Schema } from "@g3/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { apiClient, unwrap } from "../../../shared/api/client";
import { startSession, switchOrganization } from "../../../shared/auth/session";
import { organizationKeys } from "./keys";

/**
 * Credentials typed on the login page.
 *
 * Attributes:
 *   phone_number: The login phone number (the backend normalizes it to E.164).
 *   password: The password.
 */
export interface LoginCredentials {
  phone_number: string;
  password: string;
}

/**
 * Log in from the portal (`POST /auth/login` with `platform: "WEB"`, IS-13):
 * the refresh token arrives as an `HttpOnly` cookie, the access token stays in
 * memory, and `/auth/me` is loaded.
 *
 * @returns The mutation; its result is the caller's profile.
 */
export function useLoginMutation() {
  return useMutation({
    mutationFn: async (credentials: LoginCredentials) => {
      const tokenResponse = unwrap(
        await apiClient.POST("/api/v1/auth/login", {
          body: { ...credentials, platform: "WEB" },
        }),
      );
      return startSession(tokenResponse);
    },
  });
}

/**
 * Work in another organization (`POST /auth/organization`). Every cached
 * query belonged to the previous organization, so the cache is dropped.
 *
 * @returns The mutation, called with the organization ID; its result is the new profile.
 */
export function useSwitchOrganizationMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: switchOrganization,
    onSuccess: () => {
      queryClient.removeQueries();
    },
  });
}

/**
 * Change an organization's settings (`PATCH /organizations/{id}/settings`);
 * the table is tracked, so the change carries a reason (ID-45).
 *
 * @param organizationId - The organization's ID.
 * @returns The mutation; its result is the saved settings.
 */
export function useUpdateOrganizationSettingsMutation(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (
      settingsUpdateRequest: Schema<"OrganizationSettingsUpdateRequest">,
    ) =>
      unwrap(
        await apiClient.PATCH(
          "/api/v1/organizations/{organization_id}/settings",
          {
            params: { path: { organization_id: organizationId } },
            body: settingsUpdateRequest,
          },
        ),
      ),
    onSuccess: (settings) => {
      queryClient.setQueryData(
        organizationKeys.settings(organizationId),
        settings,
      );
    },
  });
}
