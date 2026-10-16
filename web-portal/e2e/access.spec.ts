/**
 * What a user sees follows their features only (web-portal-conventions.md §7,
 * ID-54): tabs, routes and actions.
 */
import { expect, test } from "./test";

import { mockApi } from "./mockApi";

test("a dispatcher sees only the live map, and a vehicle page sends them to no-access", async ({
  page,
}) => {
  await mockApi(page, {
    features: ["ACC-16", "FLT-04", "FLT-05", "MON-02", "MON-09"],
  });
  await page.goto("/");

  // MON-02 also reads vehicles, so the list is the first page; the live map is beside it.
  await expect(
    page.getByRole("heading", { name: "Danh sách xe" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Thêm xe" })).toHaveCount(0);
  await expect(page.getByRole("menuitem", { name: "Quản trị" })).toHaveCount(0);

  await page.goto("/fleet/vehicles/new");
  await expect(page).toHaveURL(/\/no-access$/);
  await expect(
    page.getByText("Tài khoản của bạn chưa được cấp tính năng này."),
  ).toBeVisible();
});

test("a user with no portal feature lands on the no-access page", async ({
  page,
}) => {
  await mockApi(page, { features: ["ACC-04"] });
  await page.goto("/");

  await expect(page).toHaveURL(/\/no-access$/);
  await expect(page.getByRole("menuitem")).toHaveCount(0);
});
