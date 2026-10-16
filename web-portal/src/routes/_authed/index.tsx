/**
 * `/`: sends the user to the first page their features open (or no-access).
 */
import { createFileRoute, redirect } from "@tanstack/react-router";

export const Route = createFileRoute("/_authed/")({
  beforeLoad: ({ context }) => {
    throw redirect({ href: context.resolveHomePath(context.me.features) });
  },
});
