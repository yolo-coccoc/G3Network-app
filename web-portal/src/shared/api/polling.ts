/**
 * Polling intervals (web-portal-conventions.md §9). The backend has no push
 * channel to the browser, so the few screens that must stay current poll with
 * TanStack Query's `refetchInterval`; polling pauses while the browser tab is
 * hidden (the default `refetchIntervalInBackground: false`).
 */

/** Live map positions: the T-Box reports every few seconds; 15 s keeps the load low. */
export const LIVE_MAP_REFETCH_INTERVAL_MS = 15_000;
