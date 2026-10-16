/**
 * The portal's TanStack Query client (web-portal-conventions.md §9): server
 * data lives in its cache only, never copied into Zustand.
 */
import { QueryClient } from "@tanstack/react-query";

import { asApiError } from "../shared/api/errors";

/** Retries of a failed read: only for failures a retry can fix. */
const MAX_READ_RETRIES = 2;

/** Data is fresh for 30 s, so moving between pages does not refetch everything. */
const DEFAULT_STALE_TIME_MS = 30_000;

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: DEFAULT_STALE_TIME_MS,
      // A 4xx answer will not change on retry; a server or network failure may.
      retry: (failureCount, error) => {
        const { kind } = asApiError(error);
        return (
          (kind === "server" || kind === "network") &&
          failureCount < MAX_READ_RETRIES
        );
      },
    },
  },
});
