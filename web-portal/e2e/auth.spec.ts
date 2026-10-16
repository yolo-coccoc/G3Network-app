/**
 * Login, organization picker and session (ACC-04, IS-13, Step 5).
 */
import { expect, test } from "./test";

import { MEMBERSHIPS } from "./fixtures";
import { mockApi } from "./mockApi";

test("login: a wrong password is refused, the right one opens the first allowed page", async ({
  page,
}) => {
  const mock = await mockApi(page, { hasSession: false });
  await page.goto("/fleet/vehicles");

  await expect(page).toHaveURL(/\/login\?redirect=/);
  await expect(page.getByRole("heading", { name: "Đăng nhập" })).toBeVisible();
  await expect(page).toHaveScreenshot("login.png");

  await page.getByLabel("Số điện thoại").fill("0901234567");
  await page.getByLabel("Mật khẩu").fill("sai-mat-khau");
  await page.getByRole("button", { name: "Đăng nhập" }).click();
  await expect(
    page.getByText("Sai số điện thoại hoặc mật khẩu."),
  ).toBeVisible();

  await page.getByLabel("Mật khẩu").fill("dung-mat-khau");
  await page.getByRole("button", { name: "Đăng nhập" }).click();

  await expect(page).toHaveURL(/\/fleet\/vehicles$/);
  await expect(
    page.getByRole("heading", { name: "Danh sách xe" }),
  ).toBeVisible();
  const loginCall = mock.requests.find(
    (call) => call.url.pathname === "/api/v1/auth/login",
  );
  expect(loginCall?.body).toMatchObject({
    phone_number: "0901234567",
    platform: "WEB",
  });
});

test("organization picker: a person in two organizations chooses one", async ({
  page,
}) => {
  const mock = await mockApi(page, {
    organizationId: null,
    memberships: MEMBERSHIPS,
  });
  await page.goto("/");

  await expect(page).toHaveURL(/\/select-organization/);
  await expect(page.getByText("Hợp tác xã Vận tải Bình Minh")).toBeVisible();
  await expect(page).toHaveScreenshot("select-organization.png");

  await page.getByRole("button", { name: "Chọn" }).first().click();

  await expect(
    page.getByRole("heading", { name: "Danh sách xe" }),
  ).toBeVisible();
  const switchCall = mock.requests.find(
    (call) => call.url.pathname === "/api/v1/auth/organization",
  );
  expect(switchCall?.body).toEqual({
    organization_id: MEMBERSHIPS[0]?.organization_id,
  });
});

test("a reload restores the session from the refresh cookie", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto("/fleet/vehicles");

  await expect(
    page.getByRole("heading", { name: "Danh sách xe" }),
  ).toBeVisible();
  await expect(page.getByText("Nguyễn Thị Hoa")).toBeVisible();
});

test("an access token that expires during use is refreshed once and the call retried", async ({
  page,
}) => {
  const mock = await mockApi(page, { failNextVehicleListWith401: true });
  await page.goto("/fleet/vehicles");

  await expect(page.getByRole("cell", { name: "51C-101.01" })).toBeVisible();
  const refreshCalls = mock.requests.filter(
    (call) => call.url.pathname === "/api/v1/auth/refresh",
  );
  const listCalls = mock.requests.filter(
    (call) => call.url.pathname === "/api/v1/vehicles/",
  );
  // One refresh restores the session on load, one answers the 401.
  expect(refreshCalls).toHaveLength(2);
  expect(listCalls).toHaveLength(2);
});

test("logout ends the session and returns to the login page", async ({
  page,
}) => {
  const mock = await mockApi(page);
  await page.goto("/fleet/vehicles");

  await page.getByRole("button", { name: "Tài khoản" }).click();
  await page.getByRole("menuitem", { name: "Đăng xuất" }).click();

  await expect(page).toHaveURL(/\/login/);
  expect(
    mock.requests.some((call) => call.url.pathname === "/api/v1/auth/logout"),
  ).toBe(true);
});
