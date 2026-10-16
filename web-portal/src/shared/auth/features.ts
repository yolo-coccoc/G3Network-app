/**
 * Feature checks (web-portal-conventions.md §7, ID-44, ID-54): access is
 * decided by feature code only, never by role name. The codes come from
 * `GET /auth/me` (`features`), which lists what the routers allow; a person
 * with several roles has the union. The backend stays the real enforcement.
 */
import { useSessionStore } from "./sessionStore";

/**
 * Whether a feature list contains a code.
 *
 * @param features - The caller's feature codes, or nothing before login.
 * @param featureCode - A code of `features.yaml`, e.g. `"VEH-01"`.
 * @returns `true` when the code is granted.
 */
export function hasFeature(
  features: readonly string[] | undefined,
  featureCode: string,
): boolean {
  return features?.includes(featureCode) ?? false;
}

/**
 * Whether a feature list contains any of several codes (a tab is shown when
 * the user has any of its features).
 *
 * @param features - The caller's feature codes, or nothing before login.
 * @param featureCodes - The codes to look for.
 * @returns `true` when at least one is granted.
 */
export function hasAnyFeature(
  features: readonly string[] | undefined,
  featureCodes: readonly string[],
): boolean {
  return featureCodes.some((featureCode) => hasFeature(features, featureCode));
}

/**
 * React hook: whether the signed-in caller has a feature.
 *
 * @param featureCode - A code of `features.yaml`.
 * @returns `true` when granted; `false` with no session.
 */
export function useHasFeature(featureCode: string): boolean {
  return useSessionStore((state) =>
    hasFeature(state.me?.features, featureCode),
  );
}
