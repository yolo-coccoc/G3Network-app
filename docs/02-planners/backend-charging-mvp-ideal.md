# Planner: Charging backend MVP trong điều kiện lý tưởng

> Mã chức năng: AD-03 và lifecycle cơ bản của S-02
>
> Trạng thái: 📋 Planner triển khai theo từng bước
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

Mỗi TransactionEvent hoặc batch MeterValues chạy trong một transaction atomic.
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

### Bước 2 — Tạo migration chuyển schema

**Prompt thực hiện:**

```text
Tạo một Alembic migration mới để chuyển schema charging hiện tại sang schema
MVP lý tưởng. Không sửa migration đã merge và không xóa dữ liệu ngoài phạm vi
đã được xác nhận.

Migration phải loại bỏ bảng technical status history khỏi schema active, bỏ các
cột reliability khỏi sáu bảng còn lại, giữ UUID/FK/index cần thiết cho topology
và session, rồi tạo lại hypertable cho session events và meter values nếu DB
đang dùng TimescaleDB.

Viết upgrade và downgrade đối xứng trong phạm vi migration. Review timezone,
FK, check/unique constraint, index và thứ tự drop/create trước khi chạy.
```

**File chính:**

- `backend/app/libs/db/migrations/versions/<revision>_simplify_charging_mvp_ideal.py`

**Tiêu chí nghiệm thu:**

- `alembic heads` chỉ có head mới hợp lệ.
- `upgrade → downgrade → upgrade` chạy được trên DB dev.
- Catalog có đúng sáu bảng active và không có bảng status history.
- Không sửa file migration cũ.

### Bước 3 — Implement session happy path

**Prompt thực hiện:**

```text
Viết lại charging_sessions repository/service theo luồng happy path.

Implement ingest_transaction_event(...) cho Started, Updated và Ended:
- Started tạo session active và event Started.
- Updated append event Updated.
- Ended cập nhật meter/thời gian, chuyển completed và append event Ended.

Implement ingest_meter_values(...) để append sample Wh và cập nhật meter cuối
theo thứ tự nhận được. Boundary chỉ nhận UUID, enum, datetime, Decimal và
Sequence primitive values. Service/repository không commit/rollback và không
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

### Bước 5 — Viết simulator handshake và happy path

**Prompt thực hiện:**

```text
Tạo simulator local cho một station đã pre-provision.

Simulator phải:
1. Kết nối WebSocket với subprotocol ocpp2.0.1.
2. Gửi TransactionEvent Started cho EVSE/connector hợp lệ.
3. Gửi một hoặc nhiều MeterValues có value Wh.
4. Gửi TransactionEvent Updated nếu flow cần event trung gian.
5. Gửi TransactionEvent Ended.
6. Đóng kết nối sau khi nhận phản hồi thành công.

Cho phép truyền identity, EVSE ID, connector ID và transaction ID qua
constructor/CLI. Không thêm delay, retry, duplicate, reconnect, mất mạng hoặc
random failure vào simulator MVP. Có một case reject identity chưa provision
để kiểm tra handshake.
```

**File/khu vực chính:**

- `backend/app/domains/charging_stations/ocpp/simulator/`
- test/smoke script liên quan nếu đã có convention trong repo

**Tiêu chí nghiệm thu:**

- Một lệnh simulator tạo được session `active` sau Started.
- Meter sample được lưu đúng `value_wh`.
- Sau Ended, session chuyển `completed` và có event Ended.
- Simulator nhận diện được identity hợp lệ và identity không hợp lệ.
- Không tạo dependency production mới chỉ để chạy simulator.

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
- Source legacy chỉ bị comment, không bị xóa.
- `future.md` mô tả đầy đủ tác dụng, lý do hoãn và planner cần mở lại cho từng
  nhóm reliability/production.
- Kết quả kiểm tra và giới hạn môi trường được ghi ở cuối bước này.

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
