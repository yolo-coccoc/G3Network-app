/**
 * Query keys of the identity domain (web-portal-conventions.md §6, §9). The
 * session itself (`/auth/me`) is not a query: it lives in the session store.
 */

/** Keys of organization queries. */
export const organizationKeys = {
  all: ["organizations"] as const,
  lists: () => [...organizationKeys.all, "list"] as const,
  list: (searchText: string) =>
    [...organizationKeys.lists(), searchText] as const,
  detail: (organizationId: string) =>
    [...organizationKeys.all, "detail", organizationId] as const,
  settings: (organizationId: string) =>
    [...organizationKeys.all, "settings", organizationId] as const,
};
