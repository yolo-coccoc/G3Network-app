# Planner: Backend CRUD Telematics (AD-02, AD-05)

> Mã chức năng: AD-02 (Quản lý dữ liệu telematics), AD-05 (Quản lý xe và thiết bị)
> Trạng thái: 🚧 Source đã triển khai; automated regression test còn theo dõi
> trong [`backend-automated-tests.md`](./backend-automated-tests.md)
> Ngày tạo: 2026-07-28

## Phân biệt thuật ngữ bắt buộc

- **Telematic**: thiết bị vật lý (TBOX) được lắp trên xe, có serial riêng,
  firmware, trạng thái hoạt động và mapping tới một vehicle.
- **Telemetry**: bản ghi/bản tin dữ liệu mà một Telematic gửi đi, ví dụ vị trí,
  tốc độ, pin và thời điểm đo. Telemetry không phải là thiết bị và có thể có
  nhiều bản ghi từ cùng một Telematic.

CRUD trong planner này chỉ quản lý **Telematic** (thiết bị vật lý). Domain
`telemetry` chỉ xử lý các bản tin/bản ghi telemetry do thiết bị gửi qua MQTT và
không sở hữu CRUD hồ sơ thiết bị.

## Tổng quan

Xây dựng CRUD API cho thiết bị Telematic trong domain `telematics`, **tương tự
CRUD `vehicles`**. API phục vụ quản lý hồ sơ và provisioning thủ công trước khi
thiết bị gửi các bản tin telemetry qua MQTT.

Phạm vi:

- Chỉ backend FastAPI, triển khai theo cùng cách tiếp cận và mức phạm vi với
  planner `backend-crud-vehicles.md`, kiểm thử qua Swagger UI.
- Create, list, get detail, update và soft delete telematics.
- Khi tạo hoặc cập nhật, client nhập `vehicle_vin`; backend tìm xe theo VIN và lưu
  `vehicles.vehicle_id` vào `telematics.vehicle_id`.
- Nếu VIN không tìm thấy, vẫn tạo/cập nhật thiết bị với `vehicle_id = NULL`.
- Không bao gồm frontend, API gán/tháo thiết bị riêng, MQTT command hoặc bulk import.

## Quyết định nghiệp vụ

1. `telematic_serial` là business identifier của thiết bị và bắt buộc unique.
2. `vehicle_vin` là input thuận tiện cho API, không lưu lặp trong bảng `telematics`.
   Response trả lại VIN hiện tại bằng cách đọc từ vehicle tương ứng.
3. VIN không tồn tại không phải lỗi validation; thiết bị được tạo ở trạng thái chưa
   gán xe (`vehicle_id = NULL`). VIN rỗng/null cũng cho kết quả tương tự.
4. Nếu VIN tìm thấy nhưng xe đã bị soft delete, coi như không tìm thấy và để
   `vehicle_id = NULL`.
5. Nếu một VIN hợp lệ đã gắn với telematic khác, trả lỗi conflict; không tự tháo
   thiết bị cũ. Constraint unique trên `telematics.vehicle_id` vẫn là lớp bảo vệ cuối.
6. Update `vehicle_vin` sẽ resolve lại mapping. Không gửi field này nghĩa là giữ
   mapping hiện tại; gửi `null` nghĩa là tháo mapping trong phạm vi CRUD này.
7. Delete là soft delete để không làm hỏng lịch sử telemetry; ingestion và các API
   list/detail mặc định bỏ qua thiết bị đã xoá.

## Ranh giới domain và transaction

- Code CRUD nằm tại `backend/app/domains/telematics/`. Domain `telemetry` chỉ chịu
  trách nhiệm dữ liệu đo đạc và MQTT ingestion.
- `telematics.service` được phép gọi public function của `vehicles.service` để tìm xe
  theo VIN. Không import `vehicles.repository` hoặc `vehicles.models`.
- `telemetry.service`/ingestion gọi public service của `telematics` để resolve
  `telematic_serial`; không import `telematics.repository` hoặc `telematics.models`.
- Router chỉ xử lý HTTP và chuyển domain exception thành status code.
- Service/repository không gọi `commit()`/`rollback()`; `Depends(get_db)` sở hữu
  transaction cho request.
- Create/update mapping VIN và telematic phải atomic trong cùng transaction.

## Thiết kế dữ liệu

Sử dụng model hiện có `Telematic` và rà soát/bổ sung nếu cần:

- `telematic_id`: UUID primary key.
- `telematic_serial`: `VARCHAR(50)`, unique, not null, indexed.
- `vehicle_id`: UUID nullable, FK tới `vehicles.vehicle_id`, `ON DELETE SET NULL`,
  indexed; unique để mỗi xe có tối đa một telematic, nhiều giá trị NULL vẫn hợp lệ.
- `status`: `active | inactive | maintenance`.
- `firmware_version`: nullable.
- `created_at`, `updated_at`: timezone-aware UTC.
- `deleted_at`: timezone-aware nullable, dùng cho soft delete.

Không tạo migration mới nếu schema hiện tại đã đáp ứng đầy đủ; nếu thiếu
`deleted_at`, tạo migration riêng với upgrade/downgrade và rà soát ảnh hưởng tới
ingestion lookup.

## API contract

Prefix đề xuất: `/api/v1/telematics`.

- `POST /telematics`
  - Request: `telematic_serial`, `vehicle_vin?`, `status`, `firmware_version?`.
  - Resolve VIN; VIN không tồn tại vẫn trả `201` với `vehicle_id = null`.
  - Serial trùng: `409`.
  - VIN đã gắn thiết bị khác: `409`.
- `GET /telematics?page=1&page_size=20&status=...`
  - Chỉ trả bản ghi chưa soft delete, có pagination và total.
- `GET /telematics/{telematic_id}`
  - Trả `telematic_id`, serial, status, firmware, `vehicle_id`, `vehicle_vin`, timestamps.
- `PATCH /telematics/{telematic_id}`
  - Partial update theo `exclude_unset=True`.
  - `vehicle_vin: null` tháo mapping; VIN không tìm thấy đặt mapping về NULL.
  - Không cho sửa `telematic_id`; xử lý conflict serial/VIN bằng domain error.
- `DELETE /telematics/{telematic_id}`
  - Set `deleted_at`, không xoá lịch sử `vehicle_telemetry`; trả `204`.

## Danh sách bước triển khai

### Bước 1: Rà soát model và migration

- Kiểm tra model `Telematic`, FK, unique constraint, timezone và `deleted_at`.
- Bổ sung migration chỉ cho phần còn thiếu; không sửa migration đã merge.
- Xác nhận `vehicle_id` nullable và `ON DELETE SET NULL`.

### Bước 2: Mở public lookup service của vehicles

- Thêm function public kiểu `get_active_vehicle_id_by_vin(...)` trong
  `vehicles/service.py` hoặc contract tương đương đã có.
- Function chỉ trả vehicle identity phù hợp hoặc `None`, không phụ thuộc FastAPI.
- Không để telemetry truy cập trực tiếp repository/model nội bộ của vehicles.

### Bước 3: Implement schemas, repository và service telematics

- Tạo package `backend/app/domains/telematics/` theo đúng pattern CRUD của domain
  `vehicles`: `router.py`, `service.py`, `repository.py`, `schemas.py`, `models.py`
  và `exceptions.py` khi cần.
- Tạo/cập nhật schemas Create, Update, Response, ListResponse và examples.
- Repository hỗ trợ list/count/detail, create, update, soft delete; không chứa policy.
- Service resolve VIN, kiểm tra conflict, lọc bản ghi đã xoá và giữ transaction boundary.
- Cập nhật docstring/comment tiếng Việt theo AGENTS.md.

### Bước 4: Implement router và đăng ký API

- Tạo endpoint với tags `telematics` và status code đúng contract.
- Đăng ký router trong `app/api/main.py`.
- Bảo đảm `__init__.py` chỉ có module docstring.

### Bước 5: Kiểm tra và nghiệm thu

- Chạy Black, isort, Ruff và mypy bằng `uv`.
- Smoke test tối thiểu: tạo với VIN hợp lệ, VIN không tồn tại, null VIN, conflict,
  update đổi/tháo VIN, list/detail và soft delete.
- Xác nhận ingestion không resolve lại VIN và vẫn nhận được `vehicle_id = NULL` khi
  thiết bị chưa được gán xe.
- Kiểm tra import boundary giữa `telemetry` và `vehicles` bằng review/`rg`.

## Tiêu chí hoàn thành

- [x] CRUD `/api/v1/telematics` đã có trong source và OpenAPI.
- [x] Create/update nhận `vehicle_vin` và resolve đúng sang `vehicle_id`.
- [x] VIN không tồn tại không làm request thất bại; mapping là NULL.
- [x] Không có import trực tiếp `vehicles.repository`/`vehicles.models` từ telemetry.
- [x] Soft delete không làm mất dữ liệu telemetry lịch sử theo thiết kế FK.
- [ ] Automated regression test sẽ hoàn tất theo `backend-automated-tests.md`.

## Ngoài phạm vi / cần planner riêng

- API attach/detach chuyên biệt với audit và phân quyền.
- Tự động provision từ MQTT hoặc đồng bộ serial từ thiết bị.
- Bulk import, firmware management, command và cache mapping.
