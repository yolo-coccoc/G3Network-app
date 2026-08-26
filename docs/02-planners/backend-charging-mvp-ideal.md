# Planner: Charging backend MVP trong điều kiện lý tưởng

> Mã chức năng: AD-03 và lifecycle cơ bản của S-02
>
> Trạng thái: 🚧 Active source và automated smoke test tối thiểu đã hoàn tất;
> integration test còn theo dõi trong
> [`backend-automated-tests.md`](./backend-automated-tests.md)
>
> Ngày cập nhật: 2026-08-02

Planner này là phiên bản rút gọn của
[`backend-charging.md`](./backend-charging.md). Planner cũ không bị xóa vì vẫn
còn mô tả các nhánh production. Planner này chỉ dùng cho MVP local/demo, trong
đó thiết bị luôn online và phiên sạc luôn chạy đúng luồng.

Mỗi bước dưới đây là một đơn vị triển khai độc lập. Không chuyển sang bước kế
tiếp nếu chưa kiểm tra xong tiêu chí nghiệm thu của bước hiện tại.

## 1. Giả định và ranh giới cố định

### 1.1. Giả định vận hành

- Station, EVSE và connector đã được pre-provision trước khi simulator chạy.
- Thiết bị luôn online, active và giữ một WebSocket ổn định.
- Một phiên luôn đi theo thứ tự `Started → Updated/MeterValues → Ended`.
- Không có mất kết nối, reconnect, timeout, retry, duplicate, conflict hoặc
  out-of-order message.
- TransactionEvent và MeterValues đều hợp lệ; input sai có thể fail-fast.
- Không cần authorization, remote control, pricing, payment, debt hoặc
  driver/vehicle policy.

### 1.2. Ranh giới domain

```text
Station simulator ⇄ OCPP 2.0.1 gateway → charging_sessions
```

- `charging_stations` sở hữu station/EVSE/connector, OCPP gateway và việc
  resolve identity.
- `charging_sessions` sở hữu session aggregate, session event và meter sample.
- Gateway chỉ gọi public service của `charging_sessions`; không import
  `models.py` hoặc `repository.py` của domain đó.
- Không thêm bảng hoặc source cho authorization, remote command, pricing,
  payment, debt hay reliability production.

## 2. Schema active sau khi rút gọn

MVP giữ sáu bảng active:

1. `charging_stations`: `station_id`, `ocpp_identity`, `display_name` và
   timestamps.
2. `charging_evses`: `evse_id`, `station_id`, `ocpp_evse_id` và timestamps.
3. `charging_connectors`: `connector_id`, `evse_id`, `ocpp_connector_id` và
   timestamps.
4. `charging_sessions`: topology IDs, `ocpp_transaction_id`, `status`, thời
   gian bắt đầu/kết thúc, meter đầu/cuối, energy delivered và timestamps.
5. `charging_session_events`: `event_id`, `event_occurred_at`, `session_id`
   và `event_type` (`Started | Updated | Ended`).
6. `charging_session_meter_values`: `meter_value_id`, `sampled_at`,
   `session_id` và `value_wh`.

`charging_station_status_events` không nằm trong active path vì topology được
giả định luôn online/active. Những model, enum, helper và nhánh xử lý cũ liên
quan đến status history, interruption, idempotency, ordering, retry,
reconciliation, reconnect và timeout phải được comment trong source, không xóa;
lý do hoãn phải ghi trong `docs/01-requirements/future.md`.

### 2.1. Đọc sáu bảng theo cách dễ hiểu

Ba bảng đầu mô tả “trạm có những gì”; ba bảng sau mô tả “một lần sạc diễn ra
như thế nào”:

| Bảng | Vai trò dễ hiểu | Có phải hypertable? |
|---|---|---|
| `charging_stations` | Hồ sơ của cả trạm sạc: mã OCPP, tên hiển thị và thời gian tạo/sửa. | Không |
| `charging_evses` | Một bộ sạc/EVSE nằm bên trong trạm. Một trạm có thể có nhiều EVSE. | Không |
| `charging_connectors` | Một đầu cắm thuộc EVSE, dùng để xác định đúng cổng đang sạc. | Không |
| `charging_sessions` | Một dòng tổng hợp cho một lần sạc, từ lúc bắt đầu đến lúc kết thúc. | Không |
| `charging_session_events` | Nhật ký các mốc `Started`, `Updated`, `Ended` của phiên sạc. | Có |
| `charging_session_meter_values` | Các số đo điện năng theo thời gian của phiên sạc, lưu theo Wh. | Có |

Trong toàn database có đúng ba hypertable:

1. `vehicle_telemetry`: số liệu thời gian thực/lịch sử của xe.
2. `charging_session_events`: lịch sử sự kiện của phiên sạc.
3. `charging_session_meter_values`: lịch sử số đo điện năng của phiên sạc.

Hypertable chỉ là cách TimescaleDB chia bảng dữ liệu theo thời gian để truy vấn
lịch sử lớn hiệu quả hơn. Vì vậy `charging_sessions` vẫn là bảng quan hệ thông
thường: nó giữ “hồ sơ tổng hợp” của phiên, còn hai bảng history mới tăng nhanh
theo thời gian.

## 3. Luồng nghiệp vụ active

### 3.1. `Started`

1. Gateway resolve `station_id`, `evse_id` và `connector_id` từ identity OCPP.
2. Gọi `charging_sessions.ingest_transaction_event(...)` với primitive values.
3. Service tạo một session có `status = active`.
4. Lưu meter đầu phiên nếu message có giá trị.
5. Append một event `Started`.

### 3.2. `Updated` và `MeterValues`

1. Resolve session theo `session_id` đã có.
2. `Updated` append event `Updated`.
3. `MeterValues` append từng sample `value_wh`.
4. Cập nhật meter cuối và energy delivered theo thứ tự message nhận được.

### 3.3. `Ended`

1. Resolve session đang active.
2. Cập nhật `ended_at`, meter cuối và energy delivered.
3. Chuyển thẳng status sang `completed`.
4. Append một event `Ended`.

Mỗi TransactionEvent hoặc từng MeterValues message chạy trong một transaction
atomic.
`service.py` và `repository.py` không gọi `commit()`/`rollback()`; entry
boundary sở hữu transaction. Không có nhánh retry, duplicate, idempotency,
interruption hoặc unknown transaction trong active path.

## 4. Thứ tự triển khai

### Bước 0 — Rà hiện trạng và chốt phạm vi MVP

**Prompt thực hiện:**

```text
Đọc AGENTS.md, feature-list.md, future.md, planner backend-charging.md và
planner backend-charging-mvp-ideal.md. Rà source hiện tại của
charging_stations/charging_sessions.

Chốt rằng MVP chỉ hỗ trợ topology đã provision, thiết bị luôn online và luồng
Started → Updated/MeterValues → Ended. Liệt kê các thành phần reliability,
technical status history, authorization, remote control, pricing, payment và
debt cần hoãn. Chỉ cập nhật planner/future nếu cần; chưa sửa logic active.
```

**Kết quả cần đạt:**

- Scope active và phần hoãn được ghi rõ trong planner và `future.md`.
- Không tạo thêm bảng cho phần hoãn.
- Các thành phần bị loại không còn nằm trong active source; phạm vi khôi phục
  được ghi rõ trong `future.md`.

**Kết quả thực tế (rà soát ngày 2026-08-02):** Bước 0 đã hoàn tất. Phạm vi
MVP được chốt như sau:

1. Active path chỉ hỗ trợ topology đã pre-provision (`station`/`EVSE`/
   `connector`), thiết bị luôn online/active và một phiên đi theo thứ tự
   `Started → Updated/MeterValues → Ended`.
2. `charging_stations` sở hữu station, EVSE, connector, OCPP 2.0.1 và việc
   resolve identity OCPP. Gateway chỉ chuyển primitive values qua public
   service của `charging_sessions`.
3. `charging_sessions` sở hữu aggregate session, event lifecycle và meter
   sample; không sở hữu WebSocket/OCPP, không gọi ngược
   `charging_stations` và không chứa business rule authorization.
4. Các nhóm sau được hoãn khỏi active path và đã ghi nhận trong
   `docs/01-requirements/future.md` mục 26–27: reconnect/connection registry,
   offline detector và heartbeat/timeout; retry, duplicate/idempotency,
   out-of-order/conflict và reconciliation; interruption/meter reset;
   technical status history và raw OCPP payload audit; authorization,
   RFID/`idToken`, driver/vehicle policy, remote start/stop, pricing, payment,
   webhook, overdue và debt.

Không thêm bảng, source hoặc placeholder cho các nhóm đã hoãn. Theo quyết định
ngày 2026-08-02, source legacy không phải contract bắt buộc bảo toàn bằng
comment và các khối legacy charging đã được xóa khỏi source active. Phạm vi,
vai trò và điều kiện khôi phục được ghi tại `docs/01-requirements/future.md`
mục 27–28.

Các file `config.py` và `backend/.env.example` chỉ ghi chú các setting
production đã hoãn; chúng không tạo active behavior cho MVP. Bước này không
thay đổi source code, dependency, config, migration hoặc logic active.

### Bước 1 — Rút gọn model, enum và config active

**Prompt thực hiện:**

```text
Rút gọn charging models/types/config theo schema mục 2.

Giữ lại sáu bảng active và các cột tối thiểu cho topology, session, event và
meter. Loại khỏi active mọi class/enum/field/helper chỉ phục vụ status history,
interruption, retry, idempotency, ordering, reconciliation, reconnect và
timeout. Không tạo placeholder; lý do hoãn và contract cần khôi phục phải ghi
trong `future.md`.

Comment các config không còn được đọc trong active path. Không thêm placeholder
hoặc bảng mới. Cập nhật Alembic metadata để không load model technical status.
```

**File/khu vực chính:**

- `backend/app/domains/charging_stations/models.py`
- `backend/app/domains/charging_stations/types.py`
- `backend/app/domains/charging_sessions/models.py`
- `backend/app/domains/charging_sessions/types.py`
- `backend/app/libs/common/config.py`
- `backend/app/libs/db/migrations/env.py`
- `backend/.env.example`

**Tiêu chí nghiệm thu:**

- Metadata chỉ còn sáu bảng active.
- Không còn import model status history trong Alembic.
- Không còn source legacy hoặc import technical status trong active path.
- Ruff, mypy và compileall không phát hiện lỗi.

**Kết quả thực tế (rà soát ngày 2026-08-02):** Bước 1 đã được triển khai.

- Metadata active có đúng sáu bảng: `charging_stations`, `charging_evses`,
  `charging_connectors`, `charging_sessions`, `charging_session_events` và
  `charging_session_meter_values`.
- Ba bảng topology chỉ còn internal ID, OCPP identity, FK topology, timestamps
  và `deleted_at`; các field location, capability, device metadata,
  administrative/technical/connection status và status timestamp đã bị loại
  khỏi active model.
- `charging_sessions` chỉ giữ status `active|completed`; event chỉ giữ
  `Started|Updated|Ended`; meter chỉ giữ `sampled_at`, `session_id` và
  `value_wh`. Các field reliability không còn thuộc active contract; source
  legacy không phải API hay persistence contract cần bảo toàn.
- Alembic chỉ import sáu model active của charging; không import
  `ChargingStationStatusEvent` hoặc model technical status history.
- Config active chỉ còn `CHARGING_OCPP_HOST` và `CHARGING_OCPP_PORT`; các
  heartbeat/offline/retry/raw-payload settings tiếp tục là comment. API
  topology đã được đồng bộ để không tham chiếu các field đã hoãn.
- Chưa tạo migration trong Bước 1; chuyển schema database thực tế thuộc Bước 2.
  Quyết định ngày 2026-08-02 cho phép xóa source legacy đã loại khỏi active;
  chi tiết các nhóm bị loại và điều kiện khôi phục được ghi tại
  `docs/01-requirements/future.md` mục 28.

### Bước 2 — Dựng migration reset và baseline schema

**Prompt thực hiện:**

```text
Vì database đang ở giai đoạn khởi tạo, xóa graph migration cũ và tạo lại graph
ngắn, bắt đầu bằng migration reset schema ứng dụng. Reset chỉ áp dụng cho
database local được phép mất dữ liệu; không chạy trên database cần bảo toàn.

Tạo các migration theo thứ tự: reset schema cũ; vehicles/telematics; telemetry
hypertable; sáu bảng charging active. Hai bảng history charging phải là
hypertable, còn `charging_sessions` là bảng quan hệ.

Review timezone, FK, check/unique constraint, index và thứ tự drop/create.
Downgrade của baseline chỉ cần xóa schema baseline; không khôi phục dữ liệu
legacy đã bị reset.
```

**File chính:**

- `backend/app/libs/db/migrations/versions/0001_reset_application_schema.py`
- `backend/app/libs/db/migrations/versions/0002_create_vehicles_and_telematics.py`
- `backend/app/libs/db/migrations/versions/0003_create_vehicle_telemetry.py`
- `backend/app/libs/db/migrations/versions/0004_create_charging_mvp_schema.py`

**Tiêu chí nghiệm thu:**

- `alembic heads` chỉ có `0004_create_charging_mvp_schema`.
- `upgrade → downgrade → upgrade` chạy được trên database tạm.
- Catalog có đúng sáu bảng active và không có bảng status history.
- Reset migration chỉ xóa bảng/type nghiệp vụ, không xóa extension hoặc
  `alembic_version`.

**Kết quả thực tế (triển khai ngày 2026-08-26):** Đã thay thế graph migration
cũ bằng bốn migration khởi tạo.

- `0001_reset_application_schema` xóa các bảng/type nghiệp vụ cũ theo allowlist;
  dữ liệu local cũ bị xóa theo quyết định giai đoạn khởi tạo.
- Ba migration sau tạo vehicles/telematics, `vehicle_telemetry` và sáu bảng
  charging active.
- Schema active có đúng sáu bảng `charging_stations`, `charging_evses`,
  `charging_connectors`, `charging_sessions`, `charging_session_events` và
  `charging_session_meter_values`; status history không còn trong catalog.
- `charging_session_events` và `charging_session_meter_values` được tạo lại
  thành hypertable; `charging_sessions` vẫn là bảng aggregate quan hệ.
- Downgrade của graph mới xóa schema baseline; không giả vờ khôi phục dữ liệu
  đã bị reset.
- `alembic heads` chỉ còn `0004_create_charging_mvp_schema`.

### Bước 3 — Implement session happy path

**Prompt thực hiện:**

```text
Viết lại charging_sessions repository/service theo luồng happy path.

Implement ingest_transaction_event(...) cho Started, Updated và Ended:
- Started tạo session active và event Started.
- Updated append event Updated.
- Ended cập nhật meter/thời gian, chuyển completed và append event Ended.

Implement ingest_meter_values(...) để append một sample Wh cho mỗi lần gọi và
cập nhật meter cuối. Boundary chỉ nhận UUID, enum, datetime, Decimal và một
MeterSampleInput. Service/repository không commit/rollback và không
import charging_stations.

Giữ source reliability cũ dưới dạng comment, không đưa retry/idempotency,
interruption/reconciliation hoặc unknown-transaction branch vào active path.
```

**File chính:**

- `backend/app/domains/charging_sessions/repository.py`
- `backend/app/domains/charging_sessions/service.py`
- `backend/app/domains/charging_sessions/exceptions.py`
- `backend/app/domains/charging_sessions/types.py`

**Tiêu chí nghiệm thu:**

- Một session đi được đầy đủ `Started → Updated/MeterValues → Ended`.
- Session kết thúc có `status = completed`, meter cuối và energy delivered.
- Event và meter sample được lưu cùng transaction với aggregate.
- Exception làm rollback operation ở entry boundary.
- Không còn active branch cho retry, duplicate, ordering hoặc interruption.

**Kết quả thực tế (triển khai ngày 2026-08-03):** Đã hoàn tất implementation
happy path trong `charging_sessions`.

- `ingest_transaction_event(...)` tạo aggregate `active` và event `Started`,
  append `Updated`, hoặc cập nhật meter/thời gian, chuyển `completed` và
  append `Ended`.
- `ingest_meter_values(...)` nhận đúng một `MeterSampleInput` mỗi lần gọi,
  append sample canonical Wh và cập nhật meter cuối/energy delivered.
- Repository chỉ `flush()` trong transaction hiện tại; service và repository
  không `commit()`/`rollback`, không import `charging_stations` và không truyền
  ORM/Pydantic/OCPP object qua public boundary.
- Retry, duplicate/idempotency, out-of-order, interruption, reconciliation và
  unknown transaction không có active branch; lý do hoãn và contract khôi phục
  được ghi trong `docs/01-requirements/future.md` mục 27–28.
- Đã kiểm tra smoke bằng fake repository cho luồng
  `Started → Updated/MeterValues → Ended`; kiểm tra database integration chưa
  chạy trong bước này vì cần PostgreSQL/TimescaleDB đang hoạt động.

### Bước 4 — Rút gọn OCPP gateway

**Prompt thực hiện:**

```text
Giữ OCPP gateway ở mức handshake tối thiểu.

Gateway chỉ bind host/port từ config, accept WebSocket path
/ocpp/{ocpp_identity}, negotiate ocpp2.0.1 và reject identity chưa
pre-provision. Giữ một connection ổn định trong process và chuyển primitive
values sang charging_sessions service.

Comment ConnectionRegistry nâng cao, reconnect replacement, offline detector,
timeout, retry và shutdown recovery cũ; không xóa source. Không tự tạo
station/EVSE/connector từ OCPP message.
```

**File chính:**

- `backend/app/domains/charging_stations/ocpp/ocpp_server.py`
- `backend/app/domains/charging_stations/ocpp/entrypoint.py`
- `backend/app/domains/charging_stations/service.py`

**Tiêu chí nghiệm thu:**

- Identity hợp lệ kết nối được bằng subprotocol `ocpp2.0.1`.
- Identity chưa provision bị từ chối.
- Gateway không chứa active logic reconnect/timeout/retry.
- OCPP adapter không truyền ORM model, Pydantic schema hoặc object OCPP qua
  boundary domain.

**Kết quả thực tế (triển khai ngày 2026-08-03):** Đã hoàn tất gateway OCPP
2.0.1 tối thiểu cho active path.

- Gateway bind `CHARGING_OCPP_HOST`/`CHARGING_OCPP_PORT`, chỉ accept path
  `/ocpp/{ocpp_identity}` và subprotocol `ocpp2.0.1`; identity phải là station
  active đã pre-provision.
- Adapter resolve station/EVSE/connector OCPP thành UUID primitive, chuyển
  `TransactionEvent` và từng energy sample `MeterValues` sang public service
  `charging_sessions` trong transaction boundary của gateway.
- Mapping transaction/session chỉ tồn tại trong từng WebSocket connection và
  chỉ cập nhật sau khi transaction persistence thành công; không có
  `ConnectionRegistry`, reconnect replacement, offline detector, timeout,
  retry hoặc shutdown recovery production.
- Smoke test với payload dataclass của `python-ocpp` đạt: Started, MeterValues,
  Ended và kiểm tra boundary chỉ nhận primitive/standard-library values.

### Bước 5 — Viết simulator handshake và happy path

**Prompt thực hiện:**

```text
Tạo simulator local cho một station đã pre-provision.

Simulator phải:
1. Kết nối WebSocket với subprotocol ocpp2.0.1.
2. Gửi TransactionEvent Started cho EVSE/connector hợp lệ.
3. Gửi từng MeterValues message có một sample value Wh.
4. Gửi TransactionEvent Updated nếu flow cần event trung gian.
5. Gửi TransactionEvent Ended.
6. Đóng kết nối sau khi nhận phản hồi thành công.

Cho phép truyền identity, EVSE ID, connector ID và transaction ID qua
constructor/CLI. Không thêm delay, retry, duplicate, reconnect, mất mạng,
random failure hoặc case reject vào simulator MVP. Simulator chỉ chạy happy
path với identity đã pre-provision; handshake reject được kiểm tra riêng ở
gateway smoke test.
```

**File/khu vực chính:**

- `simulator/`
- test/smoke script liên quan nếu đã có convention trong repo

**Tiêu chí nghiệm thu:**

- Một lệnh simulator tạo được session `active` sau Started.
- Meter sample được lưu đúng `value_wh`.
- Sau Ended, session chuyển `completed` và có event Ended.
- Simulator kết nối được với identity hợp lệ đã pre-provision.
- Không tạo dependency production mới chỉ để chạy simulator.

**Kết quả thực tế (triển khai ngày 2026-08-04):** Đã hoàn tất simulator OCPP
happy path tại `simulator/charging_session_simulator.py`.

- Simulator kết nối bằng subprotocol `ocpp2.0.1`, gửi `TransactionEvent
  Started`, từng `MeterValues` một sample Wh, `Updated` và `Ended`; mỗi CALL
  đều chờ phản hồi trước khi đi tiếp.
- Identity, EVSE ID, connector ID và transaction ID đều truyền được qua
  `SimulatorConfig` hoặc CLI. Connection được đóng sau khi nhận ACK `Ended`.
- Simulator chỉ chạy happy path với identity đã pre-provision; không có delay,
  retry, duplicate, reconnect, reject hoặc random failure.
- Code được đặt ngoài backend domain tại `simulator/`, dùng các dependency
  đã có trong backend và không thêm dependency production mới.

### Bước 6 — Monitoring tối thiểu và kiểm tra tích hợp

**Prompt thực hiện:**

```text
Thêm hoặc hoàn thiện API monitoring tối thiểu cho session MVP nếu planner
hiện tại đã yêu cầu router.

Chỉ expose session, event và meter cần để kiểm tra happy path; không expose
payment, authorization, debt hoặc raw payload. Chạy smoke test từ simulator
đến DB và kiểm tra topology/session/event/meter bằng API hoặc SQL read-only.
```

**Tiêu chí nghiệm thu:**

- Có thể xem session theo ID và xác nhận event/meter đã lưu.
- Không có N+1 rõ ràng trong endpoint được thêm.
- Response không chứa các cột đã loại khỏi MVP.
- Transaction boundary vẫn nằm ở HTTP dependency/worker entrypoint.

**Kết quả thực tế (triển khai ngày 2026-08-04):** Đã hoàn tất API monitoring và
integration smoke cho happy path.

- Thêm các endpoint read-only:
  `GET /api/v1/charging-sessions`,
  `/api/v1/charging-sessions/{session_id}`, `/events` và `/meter-values`;
  endpoint list trả session mới nhất trước để lấy `session_id`, response chỉ
  chứa aggregate session, lifecycle event và meter canonical Wh, không có raw
  OCPP/policy/payment field.
- Thêm phân trang ổn định theo timestamp + internal UUID. Event/meter history
  dùng query items/count riêng, không eager-load quan hệ nên không tạo N+1.
- Khởi động PostgreSQL/TimescaleDB, API và OCPP gateway local; pre-provision
  station/EVSE/connector test, chạy simulator `STEP6-TX-002` qua WebSocket thật.
- Kết quả DB: session `completed`, meter đầu `1000 Wh`, meter cuối `1500 Wh`,
  energy delivered `500 Wh`; có 3 event `Started → Updated → Ended` và 2
  meter sample `1250 Wh`, `1500 Wh`. Cả 3 endpoint monitoring trả đúng dữ liệu.
- Integration phát hiện và đã sửa tương thích parser `python-ocpp`: nested
  `transactionInfo`, `evse` và `meterValue` thực tế có thể là mapping thay vì
  dataclass. Adapter hiện canonicalize hai dạng trước khi ingest.
- Đã dọn toàn bộ fixture test khỏi PostgreSQL local sau smoke. Chưa kiểm tra
  production broker/reliability path; các nhóm đó vẫn ngoài phạm vi MVP.

### Bước 7 — Review, kiểm tra và ghi nhận phần hoãn

**Prompt thực hiện:**

```text
Chạy kiểm tra cuối cho toàn bộ thay đổi charging:

1. compileall, Black, isort, Ruff và mypy.
2. Alembic upgrade/downgrade/upgrade trên DB dev nếu môi trường cho phép.
3. git diff --check.
4. rg audit import chéo models/repositories, commit/rollback trong service/
   repository, HTTPException ngoài router, datetime.utcnow và energy float.
5. Đối chiếu source bị comment với mục tương ứng trong future.md.
6. Cập nhật planner bằng kết quả thực tế, giới hạn kiểm thử và commit theo
   Conventional Commits.
```

**Tiêu chí hoàn thành planner:**

- Simulator chạy được luồng `Started → MeterValues/Updated → Ended`.
- DB có đúng sáu bảng active theo mục 2.
- Không có retry, idempotency, reconnect, timeout hoặc interruption trong
  active path.
- Không có source legacy/reliability chạy trong active path; các giới hạn cần
  giữ được ghi bằng docstring/comment ở nơi còn invariant, còn source legacy
  không thuộc contract MVP có thể được loại khỏi active source theo quyết định
  ở Bước 0.
- `future.md` mô tả đầy đủ tác dụng, lý do hoãn và planner cần mở lại cho từng
  nhóm reliability/production.
- Kết quả kiểm tra và giới hạn môi trường được ghi ở cuối bước này.

**Kết quả thực tế (triển khai ngày 2026-08-04):** Đã hoàn tất review và kiểm tra
cuối cho charging MVP.

- `compileall`, Black, isort, Ruff, mypy strict và `git diff --check` đều đạt.
  Bộ automated smoke test tối thiểu hiện đã được bổ sung theo
  [`backend-automated-tests.md`](./backend-automated-tests.md).
- Alembic graph mới có đúng head `0004_create_charging_mvp_schema`; SQL offline
  đã sinh đủ bốn bước reset/baseline và không còn tham chiếu revision cũ.
- Đã kiểm tra catalog/DDL offline của sáu bảng charging active và hai
  hypertable history; kiểm thử upgrade/downgrade thật trên database tạm vẫn là
  bước tiếp theo trong planner automated tests.
- Catalog sau khi upgrade có đúng sáu bảng charging active và không có
  `charging_station_status_events`; Alembic chỉ còn head
  `0004_create_charging_mvp_schema`.
- Audit không phát hiện import chéo `models.py`/`repository.py` giữa domain,
  `commit()`/`rollback()` trong charging service/repository,
  `HTTPException` ngoài router hoặc `datetime.utcnow()`. Giá trị năng lượng
  được canonicalize về `Decimal` trước khi chuyển vào session service/database;
  adapter chỉ dùng kiểu số của thư viện OCPP ở lớp parse.
- `future.md` đã ghi nhận các nhóm mở lại gồm nghiệp vụ authorization/remote
  control/pricing/payment/debt, reliability/technical status và operational
  error handling/observability của OCPP gateway. Active path vẫn chỉ là
  `Started → Updated/MeterValues → Ended`, không có retry, idempotency,
  reconnect, timeout hoặc interruption.
- Giới hạn còn lại: chưa kiểm tra thiết bị thật, outage DB/broker, reliability
  production hoặc automated regression suite. Đây là các hạng mục ngoài phạm
  vi planner MVP và phải mở lại planner production trước khi triển khai.

## 5. Ngoài phạm vi và đường quay lại

Khi hệ thống cần chạy với thiết bị thật hoặc điều kiện không lý tưởng, phải mở
lại planner production cũ và mục tương ứng trong
`docs/01-requirements/future.md`. Các nhóm cần thiết kế lại gồm:

- reconnect, connection registry, timeout và offline detection;
- retry, idempotency, duplicate/conflict và out-of-order event;
- interruption, reconciliation và meter reset;
- technical status history;
- authorization, driver/vehicle policy, remote control, pricing, payment và
  debt.

Không tự mở lại các nhóm này bằng cách thêm dần placeholder vào source MVP.
