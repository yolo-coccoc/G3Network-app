/**
 * One control of a list's filter bar, with a width taken from the theme so
 * filter bars look the same on every list (screens set no sizes themselves,
 * web-portal-ui.md §1). Selects inside fill the field.
 */
import { ConfigProvider, theme } from "antd";
import type { ReactNode } from "react";

/** How many control heights wide each field size is. */
const FIELD_WIDTH_IN_CONTROL_HEIGHTS = { wide: 10, narrow: 6 } as const;

/**
 * Props of `FilterField`.
 *
 * Attributes:
 *   size: `wide` for search boxes, `narrow` for selects.
 *   children: The control.
 */
interface FilterFieldProps {
  size: keyof typeof FIELD_WIDTH_IN_CONTROL_HEIGHTS;
  children: ReactNode;
}

/** A fixed-width slot for a filter control. */
export function FilterField({ size, children }: FilterFieldProps) {
  const { token } = theme.useToken();
  return (
    <div
      style={{
        width: token.controlHeight * FIELD_WIDTH_IN_CONTROL_HEIGHTS[size],
        maxWidth: "100%",
      }}
    >
      <ConfigProvider select={{ style: { width: "100%" } }}>
        {children}
      </ConfigProvider>
    </div>
  );
}
