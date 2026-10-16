/**
 * Root route: the router context every route receives, the not-found page,
 * and the error page of a failed guard or loader (server down while
 * restoring the session).
 */
import type { QueryClient } from "@tanstack/react-query";
import {
  createRootRouteWithContext,
  Link,
  Outlet,
} from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { ErrorState, NotFoundState } from "../shared/ui/states";

/**
 * What the app gives every route (`app/router.ts`).
 *
 * Attributes:
 *   queryClient: The TanStack Query client.
 *   resolveHomePath: The first page a user may open, from their features
 *     (the tab registry lives in `app/`, which routes do not import).
 */
export interface RouterContext {
  queryClient: QueryClient;
  resolveHomePath: (features: readonly string[]) => string;
}

export const Route = createRootRouteWithContext<RouterContext>()({
  component: Outlet,
  notFoundComponent: RootNotFoundPage,
  errorComponent: ({ error, reset }) => (
    <ErrorState error={error} onRetry={reset} />
  ),
});

/** An address with no page. */
function RootNotFoundPage() {
  const { t } = useTranslation();
  return <NotFoundState action={<Link to="/">{t("actions.backHome")}</Link>} />;
}
