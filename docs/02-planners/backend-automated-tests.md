# Planner: Automated tests tối thiểu cho backend hiện có

> Mã chức năng: AD-02, AD-03, AD-05, FM-01, FM-02, S-02
>
> Trạng thái: 🚧 Đã triển khai bộ test tối thiểu; integration test database còn để
> ở phase tiếp theo
>
> Mục tiêu: tạo một bộ pytest nhỏ, chạy nhanh và bảo vệ các contract quan trọng
> của backend hiện tại. Planner này không nhằm đạt coverage cao.

## 1. Phạm vi

Backend hiện có gồm các domain:

- `vehicles`: CRUD và soft delete.
- `telematics`: CRUD, resolve VIN và mapping thiết bị–xe.
- `telemetry`: validation message, normalize UTC, query latest và ingestion worker.
- `charging_stations`: topology CRUD và resolve OCPP topology.
- `charging_sessions`: lifecycle `Started → Updated/MeterValues → Ended`.
- `charging_stations/ocpp`: parse handshake, timestamp, meter và boundary sang
  `charging_sessions`.
- `app.api.main`: health endpoint và đăng ký router.

Không test reliability production, retry, duplicate detection, reconnect,
authorization, payment hoặc UI; các nhóm này chưa thuộc MVP active.

## 2. Nguyên tắc đơn giản

- Dùng `pytest` và `pytest-asyncio` đã có trong nhóm dependency dev.
- Ưu tiên unit/smoke test không cần PostgreSQL, EMQX hoặc Docker.
- Không tạo fixture framework phức tạp và không thêm thư viện test mới.
- Mỗi test kiểm tra một hành vi observable; không kiểm tra implementation detail.
- Database integration chỉ là một nhóm tùy chọn chạy khi hạ tầng dev sẵn sàng.
- Tổng số test ban đầu nên giữ khoảng 14–16 test case.

## 3. Cách hiểu đúng về số lượng test

Test được thiết kế theo hành vi/luồng nghiệp vụ và contract observable, không
phải mỗi file một test hoặc mỗi method một test.

Ví dụ với charging:

```text
OCPP/HTTP input → schema/adapter → service → fake repository → kết quả
```

Một test có thể gọi nhiều method cùng thuộc một luồng `Started` hoặc `Ended`.
Ngược lại, một method chỉ cần test riêng khi nó có rule quan trọng, ví dụ
normalize Wh/kWh, từ chối timestamp không có timezone hoặc xử lý lỗi worker.

Vì vậy 10–15 test case là số case hành vi, không phải 10–15 file hay 10–15
method. Mỗi test chỉ nên kiểm tra một kết quả observable; các helper nội bộ
không có rule riêng thì không cần test trực tiếp.

## 4. Cấu trúc test đề xuất

### 4.1. `backend/tests/test_api_smoke.py`

Khoảng 2 test:

1. Ứng dụng tạo được và OpenAPI có `/health`, vehicles, telematics, telemetry,
   charging stations và charging sessions.
2. Health endpoint trả `status=healthy` và version hiện tại.

Không gọi database.

### 4.2. `backend/tests/test_schema_smoke.py`

Khoảng 4 test:

1. `TelemetryMessage` normalize timestamp có timezone về UTC.
2. `TelemetryMessage` từ chối timestamp không có timezone và payload sai range.
3. Vehicle/telematic request schema nhận đúng dữ liệu hợp lệ và reject dữ liệu
   sai contract cơ bản.
4. Meter OCPP canonicalize Wh/kWh về `Decimal` Wh.

### 4.3. `backend/tests/test_service_smoke.py`

Khoảng 5 test, dùng fake repository hoặc monkeypatch nhỏ để phủ các luồng
chính của toàn bộ backend:

1. Vehicle tạo/cập nhật record hợp lệ qua service.
2. Vehicle soft-delete không còn xuất hiện trong lookup/list active.
3. Telematic resolve `vehicle_vin` đúng sang `vehicle_id`, VIN không tồn tại
   thì mapping là `NULL` theo contract.
4. Telemetry bỏ qua message không resolve được mapping và persist message hợp
   lệ đúng một lần.
5. Charging chạy được `Started → MeterValues/Updated → Ended`, chuyển session
   sang completed và tính meter cuối/energy delivered.

Test không tạo `AsyncSession` thật nếu không cần; transaction boundary được kiểm
tra bằng việc service không gọi `commit()`/`rollback()`.

### 4.4. `backend/tests/test_worker_smoke.py`

Khoảng 2 test:

1. `MessageWorker` start/stop được khi queue rỗng.
2. Worker dừng và propagate lỗi persistence theo policy MVP.

### 4.5. `backend/tests/test_migrations_smoke.py`

Ban đầu chỉ cần 1–2 test tĩnh:

1. Alembic có đúng một head `0004_create_charging_mvp_schema`.
2. Upgrade offline có đủ bốn bước: reset, vehicles/telematics,
   vehicle telemetry và charging MVP.
3. Reset migration chỉ xóa bảng/type nghiệp vụ trong allowlist, không xóa
   `alembic_version` hoặc extension database.

Sau khi có database test riêng, bổ sung một test upgrade/downgrade/upgrade trên
database tạm. Không chạy rollback trên database dev đang chứa dữ liệu thật.

## 5. Tiêu chí hoàn thành

- `pytest` thu thập được test source từ Git, không còn kết quả `0 tests`.
- Bộ test chạy được không cần Docker.
- Tất cả test case trong phạm vi trên pass.
- `pytest` được thêm vào lệnh kiểm tra backend trong Makefile nếu không làm thay
  đổi convention hiện tại.
- Chạy được:

```bash
cd backend
uv run pytest
uv run ruff check .
uv run black --check .
uv run isort --check-only .
uv run mypy .
# Chạy từ backend để import được package app khi kiểm tra simulator.
uv run mypy ../simulator
```

- Ghi rõ test nào cần PostgreSQL/TimescaleDB và test nào chạy unit-only.

## 6. Thứ tự triển khai

1. Tạo test API/schema/worker không cần hạ tầng.
2. Tạo fake repository tối giản cho telemetry và charging service.
3. Hoàn tất migration reset/baseline, sau đó thêm kiểm tra migration tĩnh.
4. Chạy toàn bộ static checks và cập nhật README/planner bằng kết quả thực tế.

## 7. Kế hoạch migration reset/baseline

### Quyết định áp dụng

Vì database đang ở giai đoạn khởi tạo, không tiếp tục bảo trì chuỗi migration
legacy. Toàn bộ migration cũ đã được thay bằng một graph ngắn:

1. `0001_reset_application_schema`: xóa các bảng/type nghiệp vụ cũ trong
   allowlist, giữ `alembic_version` và extension.
2. `0002_vehicles_telematics`: tạo hai bảng hồ sơ.
3. `0003_create_vehicle_telemetry`: tạo telemetry hypertable.
4. `0004_create_charging_mvp_schema`: tạo sáu bảng charging active, trong đó
   events và meter values là hypertable.

### Cách áp dụng cho database local đang có

Do graph cũ đã bị xóa, database đang giữ revision cũ không thể tự nhận graph
mới. Trên database local được phép mất dữ liệu:

1. Dừng API/worker đang kết nối database.
2. Xóa bảng `alembic_version` hoặc đưa nó về trạng thái `base` bằng thao tác
   quản trị database đã được xác nhận.
3. Chạy `alembic upgrade head`; migration `0001` sẽ xóa schema nghiệp vụ cũ,
   sau đó ba migration còn lại dựng baseline mới.
4. Kiểm tra catalog có 9 bảng ứng dụng và 3 hypertable.

Không chạy quy trình này trên database có dữ liệu cần giữ. Rollback của graph
mới chỉ dùng để kiểm tra cấu trúc; reset không có ý định khôi phục dữ liệu
legacy.

## 8. Không làm trong planner này

- Không viết test cho web portal hoặc vehicle app vì hai thành phần chưa có source.
- Không xây test harness OCPP end-to-end ngay từ đầu.
- Không thêm coverage tool, snapshot tool, factory library hoặc Docker test
  framework.

## 9. Kết quả triển khai

Đã tạo 16 test case theo hành vi/contract, phân thành 5 file:

- `backend/tests/test_api_smoke.py`: health endpoint và đăng ký router.
- `backend/tests/test_schema_smoke.py`: UTC, validation GPS, request schema và
  quy đổi meter.
- `backend/tests/test_service_smoke.py`: vehicle, telematic, telemetry và
  charging lifecycle.
- `backend/tests/test_worker_smoke.py`: start/stop và propagate lỗi worker.
- `backend/tests/test_migrations_smoke.py`: migration head và reset allowlist.

Kết quả chạy local:

- `cd backend && uv run pytest`: **16 passed**.
- Ruff, Black, isort và mypy strict trên backend: **đạt**.
- Test hiện không cần PostgreSQL, TimescaleDB, EMQX hoặc Docker.

Chưa làm trong lượt này: test upgrade/downgrade thật trên database tạm, query
repository với PostgreSQL và end-to-end OCPP qua WebSocket. Đây là các test
integration riêng, chỉ nên bổ sung khi có database test được cô lập.
