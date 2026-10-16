/**
 * `<Can feature="VEH-01">`: shows its children only when the caller has the
 * feature (web-portal-conventions.md §7, level 3). A missing feature hides the
 * button or menu item; it is never shown disabled.
 */
import type { ReactNode } from "react";

import { hasAnyFeature } from "./features";
import { useSessionStore } from "./sessionStore";

/**
 * Props of `Can`.
 *
 * Attributes:
 *   feature: The feature code the action needs, or several codes of which
 *     any one is enough (an endpoint that serves several features).
 *   children: The action (button, link, menu item).
 */
interface CanProps {
  feature: string | readonly string[];
  children: ReactNode;
}

/** Render `children` only when the caller has `feature` (or any of them). */
export function Can({ feature, children }: CanProps) {
  const featureCodes = typeof feature === "string" ? [feature] : feature;
  const isAllowed = useSessionStore((state) =>
    hasAnyFeature(state.me?.features, featureCodes),
  );
  return isAllowed ? children : null;
}
