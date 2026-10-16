/**
 * Serialized access-token refresh (IS-13, web-portal-scaffold.md D5).
 *
 * A refresh rotates the refresh-token cookie: the old token stops working.
 * Two refreshes at once would therefore log the slower one out. Within a tab,
 * concurrent callers share one request in flight; across tabs, the Web Locks
 * API makes a second tab wait until the first has finished, and it then sends
 * the already-rotated cookie (the cookie jar is shared by every tab).
 */

/** Name of the browser-wide lock every tab of the portal takes to refresh. */
export const REFRESH_LOCK_NAME = "g3-portal-token-refresh";

/** Runs a task while holding a lock, so only one runs at a time. */
export type ExclusiveRunner = <Result>(
  task: () => Promise<Result>,
) => Promise<Result>;

/**
 * Run a task under the browser-wide refresh lock, or directly when the
 * browser has no Web Locks API (then only the in-tab sharing applies).
 *
 * @param task - The refresh request.
 * @returns The task's result.
 */
export function runWithBrowserLock<Result>(
  task: () => Promise<Result>,
): Promise<Result> {
  if (typeof navigator !== "undefined" && "locks" in navigator) {
    // The lock API resolves to whatever the task resolves to.
    return navigator.locks.request(REFRESH_LOCK_NAME, task) as Promise<Result>;
  }
  return task();
}

/**
 * Create the single-flight refresh function.
 *
 * @param requestNewAccessToken - Sends `POST /auth/refresh`; resolves to the
 *   new access token, or `null` when the session has ended.
 * @param runExclusive - Serializes refreshes across tabs (the Web Locks API by default).
 * @returns A function that refreshes once for every caller waiting at the same time.
 */
export function createTokenRefresher(
  requestNewAccessToken: () => Promise<string | null>,
  runExclusive: ExclusiveRunner = runWithBrowserLock,
): () => Promise<string | null> {
  let refreshInFlight: Promise<string | null> | null = null;
  return () => {
    refreshInFlight ??= runExclusive(requestNewAccessToken).finally(() => {
      refreshInFlight = null;
    });
    return refreshInFlight;
  };
}
