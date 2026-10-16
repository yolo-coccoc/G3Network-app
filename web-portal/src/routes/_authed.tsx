/**
 * The signed-in area (web-portal-conventions.md §7, level 2): every page
 * under it needs a session with an organization picked. The caller's profile
 * is put in the route context (`me`) for the pages' feature guards.
 */
import { createFileRoute, Outlet } from "@tanstack/react-router";

import { requireSession } from "../shared/auth/guards";

export const Route = createFileRoute("/_authed")({
  beforeLoad: async ({ location }) => ({
    me: await requireSession(location.href),
  }),
  component: Outlet,
});
