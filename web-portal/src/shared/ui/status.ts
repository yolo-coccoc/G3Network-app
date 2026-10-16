/**
 * The status map (web-portal-ui.md §2): every backend status value, and the
 * derived online/offline flag, gets one Ant Design status preset, so the same
 * value has the same color on every screen. A status a screen shows must be
 * listed here, with its label in the `common` catalog (`status.<VALUE>`),
 * before the screen is built.
 */

/** Ant Design's status presets. */
export type StatusPreset =
  "success" | "processing" | "warning" | "error" | "default";

/** Preset of every known status value. */
export const STATUS_PRESETS = {
  // Working, done, available.
  ACTIVE: "success",
  HEALTHY: "success",
  Available: "success",
  ACCEPTED: "success",
  COMPLETED: "success",
  SUCCEEDED: "success",
  BILLED: "success",
  ONLINE: "success",
  // In progress, waiting for the system.
  PENDING: "processing",
  Charging: "processing",
  IN_PROGRESS: "processing",
  // Needs attention soon.
  SILENT: "warning",
  ATTENTION: "warning",
  ON_HOLD: "warning",
  SuspendedEV: "warning",
  SuspendedEVSE: "warning",
  // Broken, refused, blocked.
  Faulted: "error",
  FAILED: "error",
  REJECTED: "error",
  ERROR: "error",
  TIMEOUT: "error",
  LOCKED: "error",
  SUSPENDED: "error",
  // Ended, out of use, or no data.
  INACTIVE: "default",
  CLOSED: "default",
  CANCELLED: "default",
  VOIDED: "default",
  ABANDONED: "default",
  NOT_SENT: "default",
  NOT_MOUNTED: "default",
  NO_DATA: "default",
  Unavailable: "default",
  OFFLINE: "default",
} as const satisfies Record<string, StatusPreset>;

/** A status value the portal knows how to show. */
export type StatusValue = keyof typeof STATUS_PRESETS;

/**
 * The status value of the derived online flag (`is_online`).
 *
 * @param isOnline - Whether the device or charger is online.
 * @returns `ONLINE` or `OFFLINE`.
 */
export function toOnlineStatus(isOnline: boolean): StatusValue {
  return isOnline ? "ONLINE" : "OFFLINE";
}
