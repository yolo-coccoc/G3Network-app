/**
 * Route guards for TanStack Router's `beforeLoad` (web-portal-conventions.md
 * §7, levels 1 and 2). They throw the router's `redirect`, so a page never
 * renders for a caller who may not see it.
 */
import { redirect } from "@tanstack/react-router";

import { hasAnyFeature } from "./features";
import { restoreSession } from "./session";
import type { Me } from "./sessionStore";

/** The caller inside the signed-in area: an organization is always picked. */
export type SessionMe = Me & { organization_id: string };

/**
 * Require a session with an organization picked.
 *
 * @param currentHref - Where the user was going; the login page returns there.
 * @returns The caller's profile, with the organization they act for.
 * @throws Redirect - To `/login` with no session, to `/select-organization`
 *   when the person has not picked an organization yet.
 */
export async function requireSession(currentHref: string): Promise<SessionMe> {
  const me = await restoreSession();
  if (me === null) {
    throw redirect({ to: "/login", search: { redirect: currentHref } });
  }
  if (me.organization_id === null) {
    throw redirect({
      to: "/select-organization",
      search: { redirect: currentHref },
    });
  }
  return { ...me, organization_id: me.organization_id };
}

/**
 * Require a feature for a route.
 *
 * @param me - The caller (from `requireSession`, in the route context).
 * @param featureCodes - The route's feature, or several of which any one is
 *   enough (the endpoint behind the page serves several features).
 * @throws Redirect - To `/no-access` when none is granted.
 */
export function requireFeature(
  me: Me,
  featureCodes: string | readonly string[],
): void {
  if (
    !hasAnyFeature(
      me.features,
      typeof featureCodes === "string" ? [featureCodes] : featureCodes,
    )
  ) {
    throw redirect({ to: "/no-access" });
  }
}
