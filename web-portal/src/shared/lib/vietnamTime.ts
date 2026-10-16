/**
 * Time display and input (web-portal-conventions.md §10): the API sends and
 * receives UTC instants; the portal shows and accepts Vietnam time
 * (`Asia/Ho_Chi_Minh`, the backend's `APP_REPORT_TIMEZONE`). Every screen
 * formats times through these helpers only, so the browser's own time zone
 * never matters.
 */
import { formatInTimeZone, fromZonedTime } from "date-fns-tz";

/** The calendar of every date and time the portal shows. */
export const APP_TIME_ZONE = "Asia/Ho_Chi_Minh";

/** What an empty value is shown as (web-portal-ui.md §7). */
export const EMPTY_VALUE = "—";

/**
 * Format an instant as Vietnam date and time, `dd/MM/yyyy HH:mm`.
 *
 * @param value - An ISO 8601 instant from the API, or nothing.
 * @returns The formatted time, or `—` when there is no value.
 */
export function formatVietnamDateTime(
  value: string | null | undefined,
): string {
  return value
    ? formatInTimeZone(value, APP_TIME_ZONE, "dd/MM/yyyy HH:mm")
    : EMPTY_VALUE;
}

/**
 * Format an instant as a Vietnam calendar date, `dd/MM/yyyy`.
 *
 * @param value - An ISO 8601 instant from the API, or nothing.
 * @returns The formatted date, or `—` when there is no value.
 */
export function formatVietnamDate(value: string | null | undefined): string {
  return value
    ? formatInTimeZone(value, APP_TIME_ZONE, "dd/MM/yyyy")
    : EMPTY_VALUE;
}

/**
 * Format a moment as Vietnam clock time with seconds, `HH:mm:ss` (the "last
 * refreshed" line of live screens).
 *
 * @param value - A timestamp in milliseconds, as TanStack Query reports it.
 * @returns The clock time in Vietnam.
 */
export function formatVietnamClock(value: number): string {
  return formatInTimeZone(value, APP_TIME_ZONE, "HH:mm:ss");
}

/**
 * Convert a date picked in the portal to the UTC instant the API expects:
 * the start of that day in Vietnam.
 *
 * @param date - A calendar date, `yyyy-MM-dd`.
 * @returns The ISO 8601 UTC instant of 00:00 that day in Vietnam.
 */
export function vietnamDateToUtcIso(date: string): string {
  return fromZonedTime(`${date}T00:00:00`, APP_TIME_ZONE).toISOString();
}
