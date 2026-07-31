# Planner: Backend quản lý trụ sạc và phiên sạc

> Mã chức năng: AD-03 và S-02; đối chiếu `docs/01-requirements/feature-list.md`
>
> Trạng thái: 📋 Dự kiến; chưa triển khai source code hoặc migration charging
>
> Ngày tạo/hợp nhất: 2026-07-31

Tài liệu này là planner duy nhất cho toàn bộ backend charging. Planner vẫn chia
thành hai bounded context, nhưng các bước được sắp xếp trong một luồng để tránh
trùng hoặc bỏ sót dependency giữa hai domain.

## 1. Ranh giới hai domain

### 1.1. `charging_stations` — thiết bị vật lý và OCPP

Sở hữu:

- hồ sơ Charging Station, EVSE và Connector;
- topology vật lý, capability và trạng thái online/offline;
- lịch sử trạng thái kỹ thuật và sự kiện thiết bị;
- WebSocket gateway OCPP 2.0.1, connection registry và dispatcher;
- bảng `charging_remote_commands`, tức transport/audit của lệnh gửi tới trụ.

Không sở hữu vòng đời phiên, điều kiện tài xế/xe, giá, thanh toán hoặc công nợ.

### 1.2. `charging_sessions` — vận hành phiên sạc

Sở hữu:

- phiên sạc, driver/vehicle mapping và lifecycle;
- TransactionEvent, meter samples, reconciliation với telemetry xe;
- rule remote start/stop (quyền, active session, debt, payment method);
- tariff, pricing snapshot, payment transaction, webhook và công nợ;
- API giám sát, đối soát và báo cáo tiền phiên.

Không sở hữu WebSocket, OCPP dataclass hoặc repository/model nội bộ của
`charging_stations`.

### 1.3. Dependency và nguyên tắc chung

- Chiều gọi duy nhất: `charging_stations` → public service của
  `charging_sessions`; không có chiều ngược lại.
- Adapter OCPP chuyển payload thành primitive/standard-library values trước khi
  gọi service phiên.
- UUID là internal primary key cho bảng chính; FK luôn tham chiếu internal ID.
- PostgreSQL 16 dùng UTC timezone-aware; tiền/điện năng dùng `NUMERIC`/`Decimal`.
- Bảng aggregate là bảng quan hệ thường; event, meter và status history là
  TimescaleDB hypertable. Vị trí trụ dùng PostGIS `geography(Point, 4326)`.
- HTTP boundary/worker sở hữu AsyncSession và commit/rollback; service và
  repository không commit/rollback.
- Không hard-code topology hai súng: MVP test dùng `2 EVSE × 1 connector`, nhưng
  schema phải hỗ trợ N EVSE và N connector.

## 2. Contract và quyết định đã chốt

- Pre-provision station/topology qua Admin API; `BootNotification` không tự tạo
  station/EVSE/connector lạ.
- OCPP 2.0.1 qua WebSocket `/ocpp/{ocpp_identity}`.
- Development có thể không TLS/không authentication trong môi trường cô lập;
  production phải chốt Security Profile 2 hoặc 3 trước khi triển khai thật.
- RFID card cung cấp `idToken`; hash/reference của token map tới driver, driver
  map tới vehicle qua active shift/assignment. Không lưu raw card ID.
- Remote control: Admin vận hành và tài xế được gán xe; Admin được force stop,
  nhưng không force start khi còn debt.
- OCPP response timeout 30 giây, chờ TransactionEvent xác nhận 60 giây; không tự
  retry khi không biết trụ đã nhận lệnh hay chưa.
- Dự kiến trụ có offline buffer; sequence/replay phải xác nhận khi test thiết bị
  thật.
- `Accepted` của remote command chỉ nghĩa là trụ chấp nhận request, không chứng
  minh phiên đã bắt đầu/dừng; phải correlate bằng `TransactionEvent`.
- Payment provider, công thức tariff, rounding, retry và overdue phải được chốt
  trước khi code phần billing/payment (không tự bịa credential/provider).

## 3. Mô hình dữ liệu đích

### 3.1. Domain `charging_stations`

- `charging_stations`: UUID, unique `ocpp_identity`, metadata, PostGIS location,
  administrative/connection status, `last_seen_at`, soft delete.
- `charging_evses`: UUID, station FK, positive `ocpp_evse_id`, availability,
  status, capability; unique `(station_id, ocpp_evse_id)`.
- `charging_connectors`: UUID, EVSE FK, positive `ocpp_connector_id`, type/status,
  capability; unique `(evse_id, ocpp_connector_id)`.
- `charging_station_status_events`: hypertable theo `recorded_at`, station/
  EVSE/connector refs, source, status/event, OCPP message ID, sanitized JSONB.
- `charging_remote_commands`: một dòng cho mỗi remote start/stop request; UUID,
  command type, topology refs, optional opaque session ref, actor/driver/vehicle
  refs, idempotency key/fingerprint, state, attempt/response/confirmation times,
  error. Không lưu raw RFID và không gửi OCPP trong HTTP request.

Command state:

```text
pending → dispatching → accepted → confirmed
       ↘ rejected       ↘ failed
       ↘ timed_out       ↘ confirmation_timed_out
       ↘ cancelled
```

### 3.2. Domain `charging_sessions`

- `charging_sessions`: aggregate quan hệ thường; station/EVSE/connector refs,
  vehicle/driver refs, OCPP transaction ID, operational/pricing/payment status,
  meter summary, tariff/pricing snapshot, total/currency.
- `charging_session_events`: hypertable/audit event bất biến.
- `charging_session_meter_values`: hypertable, canonical Wh và raw sampled value.
- `charging_session_start_intents`: serialize remote start theo vehicle/EVSE,
  idempotency và correlation với command.
- `charging_tariffs`/versions/assignments: tariff có effective period, version
  bất biến sau khi được session tham chiếu.
- `charging_payment_methods`: chỉ provider token/reference và metadata an toàn;
  không PAN/CVV.
- `charging_payment_transactions` và immutable payment/webhook events.
- `charging_debts`: số tiền, due date, trạng thái, resolve/reopen và audit.

Session state tách ba chiều:

```text
operational: pending → active → ending → completed|interrupted
pricing:     not_ready → pending → calculated|calculation_failed
payment:     not_ready → unpaid → processing → paid|failed|overdue
```

`paid` không thay đổi operational status. Session completed/interrupted phải giữ
được pricing snapshot để giải thích số tiền lịch sử.

## 4. Thứ tự thực hiện

Mỗi bước dưới đây là một prompt độc lập. Chỉ đánh dấu bước hoàn thành sau khi
chạy kiểm tra và ghi “Kết quả thực tế” vào chính file này.

### Bước 0 — Rà hiện trạng và chốt business contract

**Prompt:**

```text
Đọc AGENTS.md, feature-list.md, future.md và docs/02-planners/backend-charging.md.
Rà source/migration/config để xác nhận charging chưa có implementation.

Chỉ thực hiện Bước 0, chưa viết source code. Ghi vào planner:
1. Các quyết định đã chốt về pre-provision, OCPP 2.0.1, topology 2 EVSE × 1
   connector, RFID/idToken, actor remote control, timeout và offline buffer.
2. Câu hỏi blocking về payment provider, tariff/currency/rounding, meter
   reconciliation, retry/overdue/debt, webhook và production security.
3. Phân biệt thông tin cần trước khi code với thông tin chỉ cần trước khi test
   trụ thật. Không tự đặt credential, provider hoặc công thức giá.
```

**Kiểm tra:** không tạo file source/dependency/migration; kết quả rà hiện trạng
phải chỉ ra rõ chưa có các bảng `charging_sessions` và
`charging_remote_commands`.

**Kết quả thực tế:** Thiết bị/OCPP MVP đã chốt ngày 2026-07-31; business
tariff/payment và production security vẫn cần xác nhận trước khi code phần đó.

### Bước 1 — Chốt contract liên domain và schema tổng thể

**Prompt:**

```text
Thực hiện Bước 1 của planner. Chỉ sửa planner/tài liệu contract.

Thiết kế chi tiết schema/index/FK/constraint/soft-delete/hypertable cho cả hai
domain theo mục 3. Chốt:
1. Primitive public service giữa charging_stations và charging_sessions.
2. State machine session, pricing, payment và remote command.
3. Idempotency của OCPP event, remote command, start intent, payment và webhook.
4. API CRUD topology, status/history, session monitoring, remote command,
   tariff, payment, debt và reconciliation.
5. Transaction boundary, partial failure, timeout và HTTP error mapping.

Không tạo source và không để charging_sessions import charging_stations.
```

**Kiểm tra:** accepted/confirmed được phân biệt; tariff snapshot audit được;
concurrent start/payment có invariant DB/service; không có dependency cycle.

**Kết quả thực tế:** Contract topology và remote command đã chốt trong planner
cũ; cần hợp nhất thành contract ở file này trước khi triển khai.

### Bước 2 — Tạo dependency/config và migration cho hai domain

**Prompt:**

```text
Thực hiện Bước 2 theo contract Bước 1.
1. Tạo package charging_stations và charging_sessions với __init__.py chỉ có
   docstring, types/exceptions/models cần thiết.
2. Thêm python-ocpp bằng uv, đồng bộ pyproject.toml/uv.lock; settings namespace
   OCPP_ và PAYMENT_ chỉ sau khi provider/security đã chốt.
3. Tạo Alembic migration mới cho toàn bộ bảng topology, status event, remote
   command, session, event, meter, start intent, tariff, payment và debt.
4. Dùng shared Base/session; UUID, UTC, PostGIS và TimescaleDB đúng contract.
5. Review upgrade/downgrade, partial unique index, hypertable partition key,
   FK/soft-delete/cascade và worker-claim index.
6. Chạy format, lint, type, import và migration smoke test; chưa tạo dữ liệu giả.
```

### Bước 3 — CRUD station/EVSE/connector và topology API

**Prompt:**

```text
Thực hiện Bước 3. Tạo schemas/repository/service/router cho CRUD và soft-delete
station, EVSE, connector. Hỗ trợ N EVSE/N connector, PATCH theo convention,
validate unique ocpp_identity và topology. Trước deactivate/delete, gọi public
service charging_sessions để chặn active/ending session; không import model/
repository domain kia. Chuyển IntegrityError thành domain error, đăng ký router,
smoke test topology 2 EVSE × 1 connector và chạy static checks.
```

### Bước 4 — Vòng đời session và public ingestion service

**Prompt:**

```text
Thực hiện Bước 4 cho charging_sessions. Implement repository/service nhận
TransactionEvent Started/Updated/Ended và MeterValues bằng primitive values.
Started tạo/nối session hoặc start intent; Updated/Ended không làm state lùi;
duplicate/out-of-order/reconnect idempotent; normalize unit về Wh bằng Decimal.
Expose public has_active_session_for_topology và ingest functions cho domain trụ.
Service/repository không commit/rollback, không import charging_stations. Test
duplicate seqNo, meter reset, hai EVSE đồng thời và rollback.
```

### Bước 5 — OCPP WebSocket gateway và connection lifecycle

**Prompt:**

```text
Thực hiện Bước 5 cho charging_stations/ocpp. Tạo ocpp_server.py và entrypoint.py
dùng python-ocpp v201, chỉ negotiate subprotocol ocpp2.0.1, validate identity,
registry một connection/station, reconnect và graceful shutdown. Dùng shared
async_session_factory; không tạo engine/logger riêng. Có simulator connect/reject
protocol tối thiểu. Cập nhật online/offline có transaction và structured logging.
```

### Bước 6 — Boot, heartbeat, status và lịch sử thiết bị

**Prompt:**

```text
Thực hiện Bước 6. Implement BootNotification, Heartbeat, StatusNotification và
NotifyEvent. Boot chỉ cập nhật station đã pre-provision; unknown EVSE/connector
không tự tạo. Snapshot và charging_station_status_events phải atomic, duplicate/
out-of-order không làm lùi trạng thái; offline detection lấy timeout từ config.
Tạo GET status/status-history có filter UTC, pagination ổn định, không trả raw
payload mặc định. Test unknown topology và DB rollback.
```

### Bước 7 — Cầu nối OCPP transaction/meter và reconciliation

**Prompt:**

```text
Thực hiện Bước 7. OCPP adapter resolve internal topology ID, giữ transactionId,
seqNo, triggerReason, stoppedReason và meter rồi gọi public charging_sessions
service; không truyền ocpp.v201 qua boundary. Sau Ended, reconciliation worker
dùng telemetry.service (nếu mapping đáng tin), tolerance/timeout đã chốt, tính
meter_start/end/energy bằng Decimal, chuyển completed hoặc interrupted và đặt
pricing=pending. Test station-only, mismatch, missing Ended, reset và worker
concurrent.
```

### Bước 8 — API giám sát phiên và meter

**Prompt:**

```text
Thực hiện Bước 8 cho charging_sessions. Tạo API list/active/detail/meter-values/
events với filter station, EVSE, connector, vehicle, ba nhóm status và UTC
range; pagination/cursor ổn định, không N+1. Detail trả energy/pricing/payment/
reconciliation nhưng không raw payload, token hoặc secret. Áp dụng quyền driver
chỉ thấy session của mình và Admin theo identity contract; nếu identity chưa có,
ghi blocker thay vì tự thêm middleware. Chạy smoke tests.
```

### Bước 9 — Tariff và pricing engine

**Prompt:**

```text
Thực hiện Bước 9 sau khi business rules đã được xác nhận. Implement tariff CRUD,
version/effective period/assignment và resolver; version đã dùng bất biến, không
overlap. Implement pure pricing engine bằng Decimal nhận Wh, duration, tariff
snapshot, tax/currency/rounding; lưu line items, formula version, subtotal/tax/
total atomic. Pricing worker phải idempotent, calculation_failed không tạo payment.
Test boundary tariff, rounding, zero energy, interrupted và retry.
```

### Bước 10 — Payment provider, payment transaction và webhook

**Prompt:**

```text
Thực hiện Bước 10 theo provider đã chốt. Tạo adapter/protocol, không để SDK lan
vào business service; config PAYMENT_ và secret chỉ qua env. Payment method chỉ
lưu provider token/reference, không PAN/CVV. Implement tạo/list payment với
Idempotency-Key, amount/currency lấy từ pricing snapshot, retry tạo attempt mới,
provider idempotency key và partial-failure recovery. Implement webhook verify
signature/timestamp trước side effect, dedupe provider event ID, validate amount/
currency, không cho state lùi. Test success/failure/timeout/duplicate/replay.
```

### Bước 11 — Remote control: business guard và transport OCPP

**Prompt:**

```text
Thực hiện Bước 11, chia rõ hai phần nhưng hoàn thành end-to-end:

A. charging_sessions.service expose authorize_remote_start/stop, tạo
   charging_session_start_intent idempotent, kiểm tra actor, RFID->driver->vehicle,
   active session, EVSE, debt/payment method và concurrent start; expose
   correlate_transaction_started/mark_control_result. Không gọi ngược station.

B. charging_stations router ghi charging_remote_commands rồi trả 202; dispatcher
   claim pending bằng PostgreSQL locking, gửi RequestStartTransaction/
   RequestStopTransaction, áp dụng timeout 30/60 giây, cập nhật state và correlate
   TransactionEvent. Cùng idempotency key + payload trả command cũ; payload khác
   409. Không retry khi kết quả transport không rõ. Test accepted/confirmed,
   rejected, timeout, reconnect, debt và force stop.
```

### Bước 12 — Overdue, debt, báo cáo và đối soát

**Prompt:**

```text
Thực hiện Bước 12. Implement overdue worker tạo charging_debts idempotent theo
đặc tả đã chốt, khóa remote start khi debt active, resolve/reopen có actor/reason/
immutable audit. Payment thành công sau overdue xử lý đúng rule, không xóa lịch sử.
Tạo Admin APIs read-only cho session/payment/debt/reconciliation/revenue; định
nghĩa timestamp và currency rõ ràng, không cộng khác currency, không lộ secret.
Test due_at boundary, concurrent worker, manual resolve và quyền.
```

### Bước 13 — Simulator, integration và nghiệm thu

**Prompt:**

```text
Thực hiện Bước 13 và nghiệm thu planner duy nhất này.
1. Simulator OCPP mô phỏng station 2 EVSE × 1 connector, hai phiên đồng thời,
   Boot/Heartbeat/Status/Notify/Transaction/Meter, remote start/stop, delay,
   disconnect/reconnect, duplicate và out-of-order.
2. E2E: remote start -> Started -> meter -> Ended -> reconciliation -> pricing ->
   payment/webhook; payment failure/retry; overdue/debt/block/unblock; stop
   accepted/rejected/timeout; interrupted session.
3. Chạy Black, isort, Ruff, mypy, compile và migration upgrade/downgrade/upgrade.
4. Dùng rg audit import boundary, __init__.py, HTTPException, commit/rollback,
   datetime.utcnow, float cho tiền/energy, logger f-string, secret/card/raw
   payload và TODO/placeholder.
5. Cập nhật kết quả từng bước với command/evidence; ghi rõ phần chưa test với
   trụ/provider production và chuyển hạng mục thật sự hoãn vào future.md.
```

## 5. Tiêu chí hoàn thành

- Hai domain có source và migration riêng, không import nội bộ chéo.
- Topology hỗ trợ N EVSE/N connector; MVP simulator chạy đúng 2 EVSE × 1 connector.
- OCPP 2.0.1 lifecycle, status/history, TransactionEvent và meter hoạt động.
- Session idempotent, reconciliation rõ nguồn sự thật và giám sát được.
- Remote start/stop durable, phân quyền, debt guard, timeout và correlation đầy đủ.
- Pricing deterministic có snapshot/audit; payment/webhook không double charge và
  không lưu dữ liệu thẻ thô.
- Overdue/debt chặn/mở phiên đúng rule; API báo cáo không lộ secret.
- Static checks, migration smoke test và E2E có bằng chứng; chưa đánh dấu pass chỉ
  vì code “có vẻ chạy”.

## 6. Tài liệu giao thức tham chiếu

- [OCPP 2.0.1 JSON schemas](https://ocpp-spec.org/schemas/v2.0.1/)
- [OCPP 2.x numbering](https://ocpp-spec.org/docs/ocpp_2_0/architecture/numbering/)
- [`python-ocpp`](https://github.com/mobilityhouse/ocpp)
