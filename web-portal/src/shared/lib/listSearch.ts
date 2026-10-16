/**
 * Parsing of list search params (web-portal-conventions.md §9): page, page
 * size, sort and filters of a list live in the URL, so a filtered list is a
 * shareable link. A route's `validateSearch` uses these helpers; a value that
 * does not parse is dropped (the default applies) instead of failing the page.
 */

/** The backend's largest page (`API_MAX_PAGE_SIZE`). */
export const MAX_PAGE_SIZE = 100;

/** Page size of a list when the URL names none. */
export const DEFAULT_PAGE_SIZE = 20;

/**
 * Pagination part of every list's search params. Both are optional in the
 * URL, so a link to a list needs no search params; `resolvePage` applies the
 * defaults.
 */
export interface PageSearch {
  page?: number;
  page_size?: number;
}

/**
 * Read a positive whole number.
 *
 * @param value - The raw search value (TanStack Router parses JSON-like values).
 * @returns The number, or `undefined` when it is missing or not a positive integer.
 */
function toPositiveInteger(value: unknown): number | undefined {
  const parsed = typeof value === "string" ? Number(value) : value;
  return typeof parsed === "number" && Number.isInteger(parsed) && parsed > 0
    ? parsed
    : undefined;
}

/**
 * Parse page and page size, clamping the size to the backend's maximum.
 *
 * @param search - The raw search params.
 * @returns The page and page size when valid; a missing or bad value is left out.
 */
export function parsePageSearch(search: Record<string, unknown>): PageSearch {
  const pageSize = toPositiveInteger(search.page_size);
  return {
    page: toPositiveInteger(search.page),
    page_size:
      pageSize === undefined ? undefined : Math.min(pageSize, MAX_PAGE_SIZE),
  };
}

/**
 * The page to fetch and show: the URL's values or the defaults.
 *
 * @param search - The parsed pagination params.
 * @returns Page (default 1) and page size (default 20).
 */
export function resolvePage(search: PageSearch): Required<PageSearch> {
  return {
    page: search.page ?? 1,
    page_size: search.page_size ?? DEFAULT_PAGE_SIZE,
  };
}

/**
 * Parse an optional free-text filter.
 *
 * @param value - The raw search value.
 * @returns The trimmed text, or `undefined` when it is missing or blank.
 */
export function parseOptionalText(value: unknown): string | undefined {
  if (typeof value !== "string" && typeof value !== "number") {
    return undefined;
  }
  const text = String(value).trim();
  return text === "" ? undefined : text;
}

/**
 * Parse an optional value that must be one of a fixed list (a status filter).
 *
 * @param value - The raw search value.
 * @param allowed - The accepted values.
 * @returns The value, or `undefined` when it is not in the list.
 */
export function parseOptionalEnum<Value extends string>(
  value: unknown,
  allowed: readonly Value[],
): Value | undefined {
  return allowed.find((candidate) => candidate === value);
}

/**
 * Parse an optional boolean flag (`true`/`false`, as JSON or text).
 *
 * @param value - The raw search value.
 * @returns The flag, or `undefined` when it is missing or not a boolean.
 */
export function parseOptionalBoolean(value: unknown): boolean | undefined {
  if (value === true || value === "true") {
    return true;
  }
  if (value === false || value === "false") {
    return false;
  }
  return undefined;
}
