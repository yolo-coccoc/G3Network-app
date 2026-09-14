# Planner: Backend Telemetry Query API

> Mã chức năng: FM-01 (Dashboard realtime), FM-02 (Lịch sử vị trí/trạng thái)
> Trạng thái: 🚧 Đã triển khai phạm vi đầu tiên; API mở rộng và automated
> regression test còn pending
> Ngày tạo: 2026-07-29

## 1. Mục tiêu

Xây dựng lớp HTTP API chỉ đọc dữ liệu đã được lưu trong bảng
`vehicle_telemetry`. Planner này là nơi bổ sung các API query telemetry trong
tương lai; không xử lý MQTT ingestion, không ghi telemetry và không quản lý hồ
sơ thiết bị telematic.

Phạm vi đầu tiên chỉ gồm API lấy bản ghi telemetry mới nhất của một xe.

## 2. API trong scope hiện tại

### 2.1. Lấy telemetry mới nhất của một xe

```http
GET /api/v1/telemetry/vehicles/{vehicle_id}/latest
```

Quy tắc:

- Lấy đúng một bản ghi có `recorded_at` lớn nhất theo `vehicle_id`.
- `vehicle_id` là internal ID của xe; không dùng biển số hoặc
  `telematic_serial` làm định danh endpoint.
- Xe không tồn tại hoặc đã soft delete: trả `404` theo domain `vehicles`.
- Xe tồn tại nhưng chưa có telemetry: trả `404` với lỗi nghiệp vụ rõ ràng.
- Không tự suy diễn trạng thái online/offline và không tạo dữ liệu mới.
- Query phải tận dụng index theo `vehicle_id` và `recorded_at DESC`.

Response tối thiểu gồm `vehicle_id`, `telematic_serial`, `recorded_at`, vị trí,
tốc độ, hướng, SOC, odometer, các trường pin, tín hiệu và mã lỗi đang có trong
model.

## 3. Ranh giới triển khai

- Router chỉ xử lý HTTP và chuyển domain exception thành status code.
- Service xử lý use case, không import FastAPI và không commit/rollback.
- Repository chỉ query model `VehicleTelemetry`, không chứa business logic.
- Schema response không import SQLAlchemy model.
- Kiểm tra xe và quyền truy cập thông qua public service của domain `vehicles`;
  không import trực tiếp `vehicles.repository` hoặc `vehicles.models`.
- Dùng `Depends(get_db)` làm entry boundary sở hữu session và transaction.

## 4. Kết quả triển khai phạm vi đầu tiên

- Đã có repository query bản ghi mới nhất theo `vehicle_id` và `recorded_at`.
- Đã có service kiểm tra xe active qua public service của `vehicles`.
- Đã có response schema và endpoint `/api/v1/telemetry/vehicles/{vehicle_id}/latest`.
- Các API lịch sử, hành trình, tổng hợp và push realtime vẫn chưa triển khai.
- Automated regression test được tách sang
  [`backend-automated-tests.md`](./backend-automated-tests.md).

## 5. Phân rã công việc mở rộng

1. Thêm query repository lấy bản ghi mới nhất theo `vehicle_id`.
2. Thêm service function và domain exception cho trường hợp không có dữ liệu.
3. Thêm response schema riêng cho API.
4. Tạo `backend/app/domains/telemetry/router.py` nếu domain chưa có router.
5. Đăng ký router trong `app.api.main` với prefix `/api/v1/telemetry`.
6. Bổ sung kiểm tra quyền theo phạm vi đội xe khi contract identity đã sẵn sàng.
7. Smoke test: có dữ liệu, nhiều bản ghi, chưa có dữ liệu, xe không tồn tại và
   xe đã soft delete.

## 6. Các API sẽ bổ sung sau

Các API sau sẽ được cập nhật vào planner này khi được chốt:

- lịch sử telemetry theo khoảng thời gian;
- lịch sử hành trình GPS;
- dữ liệu tổng hợp theo ngày/ca lái;
- trạng thái tổng hợp cho dashboard;
- cơ chế push realtime nếu quyết định dùng WebSocket hoặc SSE.
