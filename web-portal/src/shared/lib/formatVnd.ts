/**
 * Money display (web-portal-conventions.md §10): amounts are whole Vietnamese
 * dong, shown with dot grouping and the ₫ sign, e.g. `1.250.000 ₫`.
 */

const VND_NUMBER_FORMAT = new Intl.NumberFormat("vi-VN", {
  maximumFractionDigits: 0,
});

/**
 * Format an amount of dong.
 *
 * @param amount - Whole dong as the API sends it; a fraction is rounded.
 * @returns The amount with dot grouping and the ₫ sign.
 */
export function formatVnd(amount: number): string {
  return `${VND_NUMBER_FORMAT.format(Math.round(amount))} ₫`;
}
