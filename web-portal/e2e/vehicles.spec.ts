/**
 * The vehicle pages (VEH-01): list, detail, create and edit forms, removal
 * (the List, Detail and Form templates' sample pages, D12).
 */
import { expect, test } from "./test";

import { VEHICLES } from "./fixtures";
import { mockApi } from "./mockApi";

const FIRST_VEHICLE = VEHICLES[0];

test("list: paged table, status filter in the URL", async ({ page }) => {
  const mock = await mockApi(page);
  await page.goto("/fleet/vehicles");

  await expect(page.getByRole("link", { name: "51C-101.01" })).toHaveAttribute(
    "href",
    `/vehicles/${FIRST_VEHICLE?.vehicle_id ?? ""}`,
  );
  await expect(page.getByText("Tổng 12 mục")).toBeVisible();
  await expect(page).toHaveScreenshot("vehicle-list.png", { fullPage: true });

  await page.getByLabel("Trạng thái").click();
  await page.getByTitle("Không hoạt động").click();

  await expect(page).toHaveURL(/status=INACTIVE/);
  await expect(page.getByText("Tổng 2 mục")).toBeVisible();
  const lastListCall = mock.requests
    .filter((call) => call.url.pathname === "/api/v1/vehicles/")
    .at(-1);
  expect(lastListCall?.url.searchParams.get("status")).toBe("INACTIVE");
});

test("detail: summary and ownership history, removal through the reason dialog", async ({
  page,
}) => {
  const mock = await mockApi(page);
  await page.goto(`/vehicles/${FIRST_VEHICLE?.vehicle_id ?? ""}`);

  await expect(page.getByRole("heading", { name: "51C-101.01" })).toBeVisible();
  await expect(page.getByText("Tri-Ring T5 Đầu kéo điện")).toBeVisible();
  await expect(page.getByRole("cell", { name: "Hiện tại" })).toBeVisible();
  await expect(page).toHaveScreenshot("vehicle-detail.png", { fullPage: true });

  await page.getByRole("button", { name: "Xóa" }).click();
  const dialog = page.getByRole("dialog", { name: "Gỡ xe khỏi hệ thống" });
  await dialog.getByRole("button", { name: "Gỡ xe" }).click();
  await expect(dialog.getByText("Trường này là bắt buộc.")).toBeVisible();

  await dialog.getByLabel("Lý do").fill("Xe đã thanh lý");
  await dialog.getByRole("button", { name: "Gỡ xe" }).click();

  await expect(page).toHaveURL(/\/fleet\/vehicles$/);
  const deleteCall = mock.requests.find((call) => call.method === "DELETE");
  expect(deleteCall?.url.searchParams.get("reason")).toBe("Xe đã thanh lý");
});

test("create: a duplicate plate is reported, a valid vehicle opens its page", async ({
  page,
}) => {
  const mock = await mockApi(page);
  await page.goto("/fleet/vehicles/new");

  await expect(page.getByRole("heading", { name: "Thêm xe" })).toBeVisible();
  await expect(page).toHaveScreenshot("vehicle-create.png", { fullPage: true });

  await page.getByLabel("Biển số").fill("51C-101.01");
  await page.getByLabel("Số VIN").fill("ltrt5ev00sh000077");
  await page.getByLabel("Dòng xe").click();
  await page.getByTitle("Tri-Ring T5 Đầu kéo điện").click();
  await page.getByLabel("Năm sản xuất").fill("2026");
  await page.getByRole("button", { name: "Lưu" }).click();
  await expect(page.getByText(/Dữ liệu bị trùng/)).toBeVisible();

  await page.getByLabel("Biển số").fill("51C-999.77");
  await page.getByRole("button", { name: "Lưu" }).click();

  await expect(page.getByRole("heading", { name: "51C-999.77" })).toBeVisible();
  const createCall = mock.requests
    .filter(
      (call) =>
        call.method === "POST" && call.url.pathname === "/api/v1/vehicles/",
    )
    .at(-1);
  expect(createCall?.body).toMatchObject({
    vin: "LTRT5EV00SH000077",
    year: 2026,
    status: "ACTIVE",
  });
});

test("edit: a status change requires its reason and only changed fields are sent", async ({
  page,
}) => {
  const mock = await mockApi(page);
  await page.goto(`/vehicles/${FIRST_VEHICLE?.vehicle_id ?? ""}/edit`);

  await expect(
    page.getByRole("heading", { name: "Sửa xe 51C-101.01" }),
  ).toBeVisible();
  await page.getByLabel("Trạng thái").click();
  await page.getByTitle("Không hoạt động").click();
  await page.getByRole("button", { name: "Lưu" }).click();
  await expect(
    page.getByText("Vui lòng nhập lý do khi đổi trạng thái."),
  ).toBeVisible();

  await page.getByLabel("Lý do trạng thái").fill("Đưa vào xưởng bảo dưỡng");
  await page.getByRole("button", { name: "Lưu" }).click();

  await expect(page.getByRole("heading", { name: "51C-101.01" })).toBeVisible();
  const patchCall = mock.requests.find((call) => call.method === "PATCH");
  expect(patchCall?.body).toEqual({
    status: "INACTIVE",
    status_reason: "Đưa vào xưởng bảo dưỡng",
  });
});

test("a vehicle out of reach shows the not-found state", async ({ page }) => {
  await mockApi(page);
  await page.goto("/vehicles/00000000-0000-4000-8000-000000000000");

  await expect(page.getByText("Không tìm thấy", { exact: true })).toBeVisible();
});
