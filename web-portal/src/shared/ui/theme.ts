/**
 * The portal's Ant Design theme (web-portal-ui.md §1, TT-29): the only place
 * colors, density and font are chosen. Screens read tokens through
 * `theme.useToken()` or component props; no other file picks a color.
 */
import { theme, type ThemeConfig } from "antd";

/** The one G3 brand color (D14: the prototype's green until the official one is given). */
export const BRAND_COLOR = "#0e9f6e";

/** Be Vietnam Pro (self-hosted, `globalStyles.ts`), then system fonts. */
const FONT_FAMILY =
  "'Be Vietnam Pro', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif";

/** Light theme only (no dark theme until asked for), compact density. */
export const portalTheme: ThemeConfig = {
  algorithm: [theme.defaultAlgorithm, theme.compactAlgorithm],
  token: {
    colorPrimary: BRAND_COLOR,
    colorLink: BRAND_COLOR,
    fontFamily: FONT_FAMILY,
  },
};
