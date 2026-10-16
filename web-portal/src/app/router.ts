/**
 * The router (TanStack Router, file-based, web-portal-conventions.md §2, §4).
 *
 * The route tree is generated from `src/routes/` by the Vite plugin into
 * `routeTree.gen.ts`. The shell wraps every page (`InnerWrap`); routes get the
 * query client and the home-page rule through the router context, so they
 * never import `app/`.
 */
import { createRouter } from "@tanstack/react-router";

import { setSessionExpiredHandler } from "../shared/api/client";
import { AppShell } from "./AppShell";
import { queryClient } from "./queryClient";
import { routeTree } from "./routeTree.gen";
import { resolveHomePath } from "./tabs";

export const router = createRouter({
  routeTree,
  context: { queryClient, resolveHomePath },
  InnerWrap: AppShell,
  defaultPreload: "intent",
  scrollRestoration: true,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

// A refresh refused during use means the session is over (expired, or ended
// on another device): drop the cached data and return to the login page,
// coming back to the same page afterwards.
setSessionExpiredHandler(() => {
  queryClient.clear();
  void router.navigate({
    to: "/login",
    search: { redirect: router.state.location.href },
  });
});
