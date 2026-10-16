/**
 * The fleet live map (FLT-04), the Live map template's sample page (D12).
 */
import { expect, test } from "./test";

import { mockApi } from "./mockApi";

test("live map: pick a fleet, see its trucks, select one from the list", async ({
  page,
}) => {
  const mock = await mockApi(page);
  await page.goto("/fleet/live");

  await expect(
    page.getByText("Chọn một đội xe để xem vị trí các xe."),
  ).toBeVisible();
  await page.getByLabel("Chọn đội xe").click();
  await page.getByTitle("Đội xe Hồ Chí Minh").click();

  await expect(page).toHaveURL(/fleet_id=/);
  await expect(
    page.getByRole("menuitem", { name: /51C-101\.01/ }),
  ).toBeVisible();
  await expect(page.getByText("Cập nhật lúc 10:00:00")).toBeVisible();
  // The map draws asynchronously; wait until its canvas has been painted.
  await expect(page.locator(".maplibregl-canvas")).toBeVisible();
  await expect(page).toHaveScreenshot("live-map.png", { fullPage: true });

  await page.getByRole("menuitem", { name: /51C-102\.02/ }).click();
  await expect(
    page.locator(".maplibregl-marker").getByText("51C-102.02"),
  ).toBeVisible();

  const liveCall = mock.requests.find((call) =>
    call.url.pathname.endsWith("/vehicles/latest"),
  );
  expect(liveCall?.url.searchParams.get("include_descendants")).toBe("false");
});
