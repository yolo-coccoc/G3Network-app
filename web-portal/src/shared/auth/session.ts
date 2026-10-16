/**
 * Session lifecycle (IS-13, web-portal-conventions.md §7, §8): start a session
 * from a login answer, restore one after a reload from the refresh cookie,
 * switch organization, and end it. After each of these the caller's
 * `GET /auth/me` answer (features included) is in the session store.
 */
import type { Schema } from "@g3/api-client";

import { apiClient, refreshAccessToken, unwrap } from "../api/client";
import { asApiError } from "../api/errors";
import { type Me, useSessionStore } from "./sessionStore";

/**
 * Read the caller's profile and features and keep them in the store.
 *
 * @returns The `GET /auth/me` answer.
 * @throws ApiError - The call failed.
 */
export async function loadMe(): Promise<Me> {
  const me = unwrap(await apiClient.GET("/api/v1/auth/me"));
  useSessionStore.getState().setMe(me);
  return me;
}

/**
 * Begin the session a login (or sign-up) answered with.
 *
 * @param tokenResponse - The login answer; its refresh token stays in the cookie.
 * @returns The caller's profile.
 * @throws ApiError - `/auth/me` failed.
 */
export async function startSession(
  tokenResponse: Schema<"TokenResponse">,
): Promise<Me> {
  useSessionStore.getState().setAccessToken(tokenResponse.access_token);
  return loadMe();
}

/**
 * Make sure a session is loaded: reuse the one in memory, or restore it from
 * the refresh cookie after a reload.
 *
 * @returns The caller's profile, or `null` when there is no session.
 * @throws ApiError - A failure other than an ended session (server, network).
 */
export async function restoreSession(): Promise<Me | null> {
  const { accessToken, me } = useSessionStore.getState();
  if (accessToken !== null && me !== null) {
    return me;
  }
  if (accessToken === null && (await refreshAccessToken()) === null) {
    return null;
  }
  try {
    return await loadMe();
  } catch (error) {
    if (asApiError(error).kind === "unauthenticated") {
      useSessionStore.getState().clearSession();
      return null;
    }
    throw error;
  }
}

/**
 * Work in another organization (`POST /auth/organization`). The session keeps
 * its token; the answer is the new `/auth/me`.
 *
 * @param organizationId - One of the caller's memberships.
 * @returns The caller's profile in that organization.
 * @throws ApiError - The switch was refused.
 */
export async function switchOrganization(organizationId: string): Promise<Me> {
  const me = unwrap(
    await apiClient.POST("/api/v1/auth/organization", {
      body: { organization_id: organizationId },
    }),
  );
  useSessionStore.getState().setMe(me);
  return me;
}

/**
 * Log out: end the session on the server (which also clears the cookie) and
 * forget it here. The local session is cleared even if the call fails.
 */
export async function endSession(): Promise<void> {
  try {
    await apiClient.POST("/api/v1/auth/logout", {
      body: { all_devices: false },
    });
  } finally {
    useSessionStore.getState().clearSession();
  }
}
