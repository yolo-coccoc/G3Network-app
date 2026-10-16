/**
 * The portal's one API client (web-portal-conventions.md §8, IS-13).
 *
 * Typed by `@g3/api-client`, same origin (`/api/v1`; Vite proxies it in
 * development). A middleware adds the in-memory access token to every call; on
 * a `401` it refreshes the token once (single flight, `tokenRefresh.ts`) and
 * retries the call once. When the refresh fails the session is over: the
 * session is cleared and the handler the app registered sends the user to the
 * login page. Nothing outside `shared/api` creates a client or reads tokens
 * (an ESLint rule enforces the import side of this).
 */
import { createApiClient, type Schema } from "@g3/api-client";

import { useSessionStore } from "../auth/sessionStore";
import { ApiError, toApiError } from "./errors";
import { createTokenRefresher } from "./tokenRefresh";

/** Calls that must never trigger a refresh-and-retry (they are the session itself). */
const SESSION_PATHS = [
  "/api/v1/auth/login",
  "/api/v1/auth/refresh",
  "/api/v1/auth/logout",
];

/** Path of the refresh call; the browser sends the `g3_refresh` cookie with it. */
const REFRESH_PATH = "/api/v1/auth/refresh";

/** Called when the session has ended (refresh refused); set by the app. */
let handleSessionExpired: () => void = () => undefined;

/**
 * Register what to do when the session ends during use (go to the login page).
 *
 * @param handler - Called once per failed refresh, after the session is cleared.
 */
export function setSessionExpiredHandler(handler: () => void): void {
  handleSessionExpired = handler;
}

/**
 * Ask the backend for a new access token with the refresh cookie.
 *
 * @returns The new access token, or `null` when the session has ended (401).
 * @throws ApiError - Any other failure (server error, network).
 */
async function requestNewAccessToken(): Promise<string | null> {
  let response: Response;
  try {
    response = await fetch(REFRESH_PATH, {
      method: "POST",
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError("network", 0, undefined, []);
  }
  if (response.status === 401) {
    return null;
  }
  if (!response.ok) {
    throw toApiError(response.status, undefined);
  }
  const tokenResponse = (await response.json()) as Schema<"TokenResponse">;
  return tokenResponse.access_token;
}

const refreshTokenOnce = createTokenRefresher(requestNewAccessToken);

/**
 * Refresh the access token and keep it in the session store.
 *
 * @returns The new token, or `null` when the session has ended.
 */
export async function refreshAccessToken(): Promise<string | null> {
  const accessToken = await refreshTokenOnce();
  useSessionStore.getState().setAccessToken(accessToken);
  return accessToken;
}

/** The typed client; feature hooks call it by path, e.g. `apiClient.GET("/api/v1/vehicles/")`. */
export const apiClient = createApiClient();

/**
 * Copies of outgoing requests, kept so a call refused with `401` can be sent
 * again after the refresh (a request body can be read only once).
 */
const retryableRequests = new WeakMap<Request, Request>();

apiClient.use({
  onRequest({ request }) {
    const { accessToken } = useSessionStore.getState();
    if (accessToken !== null) {
      request.headers.set("Authorization", `Bearer ${accessToken}`);
    }
    if (!SESSION_PATHS.some((path) => new URL(request.url).pathname === path)) {
      retryableRequests.set(request, request.clone());
    }
    return request;
  },
  async onResponse({ request, response }) {
    const retryRequest = retryableRequests.get(request);
    if (response.status !== 401 || retryRequest === undefined) {
      return response;
    }
    const accessToken = await refreshAccessToken();
    if (accessToken === null) {
      useSessionStore.getState().clearSession();
      handleSessionExpired();
      return response;
    }
    retryRequest.headers.set("Authorization", `Bearer ${accessToken}`);
    return fetch(retryRequest);
  },
});

/**
 * Return the data of an openapi-fetch answer or throw its `ApiError`.
 *
 * @param result - What a client call resolved to.
 * @returns The response body of a successful call.
 * @throws ApiError - The call failed (the kind follows the status).
 */
export function unwrap<Data>(result: {
  data?: Data;
  error?: unknown;
  response: Response;
}): Data {
  if (!result.response.ok || result.data === undefined) {
    throw toApiError(result.response.status, result.error);
  }
  return result.data;
}
