/**
 * The tab registry (web-portal-conventions.md §4, §7, D11). A tab is a group
 * of pages for one kind of work, not a role; it is shown when the user has
 * any feature of its pages, and a tab is added only when its first page is
 * built. The home page is the first page the user may open.
 */
import { ORGANIZATION_SETTINGS_FEATURE } from "../features/identity";
import { FLEET_LIVE_MAP_FEATURE } from "../features/fleet";
import { VEHICLE_READ_FEATURES } from "../features/vehicles";
import { hasAnyFeature } from "../shared/auth/features";

/**
 * One page reachable from the tab bar.
 *
 * Attributes:
 *   path: Where the page is.
 *   labelKey: Its name in the `common` catalog.
 *   features: The features that open it (any one is enough).
 *   activePrefixes: Paths that belong to this page (its detail pages too).
 */
export interface TabPage {
  path: string;
  labelKey:
    | "navigation.vehicles"
    | "navigation.liveMap"
    | "navigation.organizationSettings";
  features: readonly string[];
  activePrefixes: readonly string[];
}

/**
 * One tab.
 *
 * Attributes:
 *   id: Stable identifier.
 *   labelKey: Its name in the `common` catalog.
 *   pages: Its pages, in menu order.
 */
export interface TabDefinition {
  id: string;
  labelKey: "tabs.fleet" | "tabs.administration";
  pages: readonly TabPage[];
}

/** Every tab, in tab-bar order. */
export const TABS: readonly TabDefinition[] = [
  {
    id: "fleet",
    labelKey: "tabs.fleet",
    pages: [
      {
        path: "/fleet/vehicles",
        labelKey: "navigation.vehicles",
        features: VEHICLE_READ_FEATURES,
        activePrefixes: ["/fleet/vehicles", "/vehicles/"],
      },
      {
        path: "/fleet/live",
        labelKey: "navigation.liveMap",
        features: [FLEET_LIVE_MAP_FEATURE],
        activePrefixes: ["/fleet/live"],
      },
    ],
  },
  {
    id: "administration",
    labelKey: "tabs.administration",
    pages: [
      {
        path: "/administration/organization",
        labelKey: "navigation.organizationSettings",
        features: [ORGANIZATION_SETTINGS_FEATURE],
        activePrefixes: ["/administration/organization"],
      },
    ],
  },
];

/** Where a user with no page at all lands. */
export const NO_ACCESS_PATH = "/no-access";

/**
 * The pages of a tab the user may open.
 *
 * @param tab - The tab.
 * @param features - The user's features.
 * @returns The allowed pages, in menu order.
 */
export function allowedPagesOf(
  tab: TabDefinition,
  features: readonly string[],
): TabPage[] {
  return tab.pages.filter((page) => hasAnyFeature(features, page.features));
}

/**
 * The tabs to show: those with at least one allowed page.
 *
 * @param features - The user's features.
 * @returns The visible tabs with their allowed pages.
 */
export function visibleTabsOf(
  features: readonly string[],
): { tab: TabDefinition; pages: TabPage[] }[] {
  return TABS.map((tab) => ({
    tab,
    pages: allowedPagesOf(tab, features),
  })).filter(({ pages }) => pages.length > 0);
}

/**
 * The page a user lands on after login: the first allowed page.
 *
 * @param features - The user's features.
 * @returns Its path, or the no-access page when nothing is allowed.
 */
export function resolveHomePath(features: readonly string[]): string {
  return visibleTabsOf(features)[0]?.pages[0]?.path ?? NO_ACCESS_PATH;
}

/**
 * The page a path belongs to, for highlighting the tab bar.
 *
 * @param pathname - The current path.
 * @returns The page's path, or `undefined` outside every tab.
 */
export function activePagePathOf(pathname: string): string | undefined {
  return TABS.flatMap((tab) => tab.pages).find((page) =>
    page.activePrefixes.some((prefix) => pathname.startsWith(prefix)),
  )?.path;
}
