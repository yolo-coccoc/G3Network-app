/**
 * The `?redirect=` of the login page: where to go back after logging in. Only
 * a path of this portal is accepted, so a crafted link cannot send a user to
 * another site after login (open redirect).
 */

/**
 * Keep a return path only when it is a path of this site.
 *
 * @param value - The raw search value.
 * @returns The path, or `undefined` for anything else (another origin,
 *   `//host`, the login page itself).
 */
export function toSafeRedirect(value: unknown): string | undefined {
  if (
    typeof value !== "string" ||
    !value.startsWith("/") ||
    value.startsWith("//")
  ) {
    return undefined;
  }
  if (value.startsWith("/login") || value.includes("\\")) {
    return undefined;
  }
  return value;
}
