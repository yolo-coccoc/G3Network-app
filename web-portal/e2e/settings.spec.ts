/**
 * Organization settings (ACC-23, ID-45), the Settings template's sample page
 * (D12): each card saves on its own, with a reason.
 */
import { expect, test } from "./test";

import { mockApi } from "./mockApi";

test("settings: a change is saved with its reason", async ({ page }) => {
  const mock = await mockApi(page);
  await page.goto("/administration/organization");

  const autoEndField = page.getByLabel("Tự kết thúc phiên lái xe sau (phút)");
  await expect(autoEndField).toHaveValue("120");
  await expect(page).toHaveScreenshot("organization-settings.png", {
    fullPage: true,
  });

  await autoEndField.fill("90");
  await page.getByRole("button", { name: "Lưu" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Lưu cài đặt" });
  await dialog.getByLabel("Lý do").fill("Tài xế nghỉ giữa ca lâu hơn");
  await dialog.getByRole("button", { name: "Lưu" }).click();

  await expect(page.getByText("Đã lưu cài đặt.")).toBeVisible();
  const patchCall = mock.requests.find((call) => call.method === "PATCH");
  expect(patchCall?.body).toEqual({
    driving_session_auto_end_minutes: 90,
    reason: "Tài xế nghỉ giữa ca lâu hơn",
  });
});
