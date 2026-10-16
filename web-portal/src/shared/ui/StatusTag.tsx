/**
 * `<StatusTag value="ACTIVE" />`: the only way a screen shows a status
 * (web-portal-ui.md §2). Color from the status map, label from the `common`
 * catalog; a screen never chooses either.
 */
import { Tag } from "antd";
import { useTranslation } from "react-i18next";

import { STATUS_PRESETS, type StatusValue } from "./status";

/**
 * Props of `StatusTag`.
 *
 * Attributes:
 *   value: The backend status value (or `ONLINE`/`OFFLINE` for a derived flag).
 */
interface StatusTagProps {
  value: StatusValue;
}

/** A status as a colored tag with its localized label. */
export function StatusTag({ value }: StatusTagProps) {
  const { t } = useTranslation();
  return <Tag color={STATUS_PRESETS[value]}>{t(`status.${value}`)}</Tag>;
}
