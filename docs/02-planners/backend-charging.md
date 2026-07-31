# Planner: Backend quản lý trụ sạc và lưu trữ phiên sạc

> Mã chức năng: AD-03 và phần lifecycle của S-02
>
> Trạng thái: 📋 MVP rút gọn; chưa triển khai source code hoặc migration charging
>
> Ngày cập nhật: 2026-07-31

Đây là planner duy nhất cho charging backend. Planner chia thành hai bounded
context nhưng chỉ mô tả phạm vi MVP đã chốt: quản lý thiết bị/OCPP và nhận, lưu,
cập nhật dữ liệu phiên sạc.

## 1. Ranh giới hai domain

### 1.1. `charging_stations`: thiết bị vật lý và OCPP

Sở hữu:

- hồ sơ Charging Station, EVSE và Connector;
- topology vật lý, capability và trạng thái kỹ thuật;
- WebSocket gateway OCPP 2.0.1, connection registry và lifecycle kết nối;
- nhận BootNotification, Heartbeat, StatusNotification, NotifyEvent,
  TransactionEvent và MeterValues;
- chuyển dữ liệu OCPP đã chuẩn hóa sang public service của
  `charging_sessions`.

Trong MVP này chưa triển khai remote start/stop hoặc command transport tới trụ.

### 1.2. `charging_sessions`: dữ liệu và lifecycle phiên sạc

Sở hữu:

- aggregate `charging_sessions`;
- lịch sử `charging_session_events`;
- meter samples `charging_session_meter_values`;
- tạo/cập nhật/kết thúc phiên từ dữ liệu do `charging_stations` gửi;
- reconciliation và API giám sát phiên.

Domain này không làm authorization, pricing, payment, debt hoặc remote-control
business rule trong MVP. Nó cũng không sở hữu WebSocket/OCPP adapter và không gọi
ngược `charging_stations`.

### 1.3. Chiều dữ liệu và public boundary

```text
Trụ sạc ⇄ OCPP ⇄ charging_stations → charging_sessions
```

- `charging_stations` gọi public service của `charging_sessions` để ingest event
  và meter.
- `charging_sessions` trả về kết quả xử lý kỹ thuật nếu caller cần, nhưng không
  gửi OCPP hoặc command điều khiển ngược cho trụ.
- Không import chéo `models.py`/`repository.py`; adapter OCPP chỉ truyền
  primitive/standard-library values và ID nội bộ cần thiết.
- API orchestration có thể đọc cả hai domain rồi ghép response; đó không phải
  dependency ngược giữa domain.

## 2. Phạm vi MVP và phần hoãn

### Trong phạm vi

- Pre-provision station/EVSE/connector qua Admin API.
- OCPP 2.0.1 qua WebSocket `/ocpp/{ocpp_identity}`.
- Development có thể không TLS/không authentication trong môi trường cô lập;
  production security profile chốt sau.
- MVP topology test: `2 EVSE × 1 connector`; schema hỗ trợ N EVSE/N connector.
- Lưu trạng thái thiết bị, OCPP technical events, transaction events và meter.
- Lưu session aggregate, event history, meter history và API monitoring.
- Timestamp UTC timezone-aware, điện năng canonical Wh, `NUMERIC`/`Decimal`.
- `charging_sessions` là bảng quan hệ thường; event/meter/status history là
  TimescaleDB hypertable; location dùng PostGIS geography khi cần.

### Hoãn khỏi MVP

- Authorization RFID/idToken, mapping driver/vehicle và phân quyền bắt đầu/dừng.
- Remote start/stop, `charging_remote_commands` và business guard.
- Tariff, pricing, payment, webhook, overdue và debt.
- Reservation, smart charging, firmware management, alert delivery.

Các mục hoãn phải được ghi trong `docs/01-requirements/future.md`; không tạo
placeholder source hoặc bảng cho chúng trong MVP.

## 3. Mô hình dữ liệu đích trong MVP

### 3.1. `charging_stations`

- UUID internal ID, unique `ocpp_identity`, metadata, location, administrative
  status, connection snapshot, `last_seen_at`, timestamps và soft delete.
- Không tự tạo station/EVSE/connector lạ từ `BootNotification`.

### 3.2. `charging_evses`

- UUID, station FK, positive `ocpp_evse_id`, availability/status và capability.
- Unique `(station_id, ocpp_evse_id)`.

### 3.3. `charging_connectors`

- UUID, EVSE FK, positive `ocpp_connector_id`, connector type/status/capability.
- Unique `(evse_id, ocpp_connector_id)`.

### 3.4. `charging_station_status_events`

Hypertable theo `recorded_at`, lưu station/EVSE/connector refs, source action,
status/event, OCPP message ID, received time và sanitized raw JSONB.

### 3.5. `charging_sessions`

Bảng aggregate thường, gồm tối thiểu:

- UUID internal ID;
- station/EVSE/connector IDs;
- OCPP transaction ID và các timestamp bắt đầu/kết thúc;
- operational status: `pending | active | ending | completed | interrupted`;
- meter start/end, energy delivered Wh, reconciliation status/error;
- created/updated timestamps.

Không thêm pricing/payment/authorization columns trong MVP này.

### 3.6. `charging_session_events` và `charging_session_meter_values`

Hai bảng hypertable/audit history, có idempotency key phù hợp với
`transaction_id`, `seq_no`, event timestamp và sampled value identity. Duplicate
hoặc out-of-order event không được làm state lùi.

## 4. Thứ tự thực hiện

Mỗi bước là một prompt độc lập. Chỉ đánh dấu hoàn thành sau khi chạy kiểm tra và
ghi kết quả thực tế vào file này.

### Bước 0 — Rà hiện trạng và chốt scope MVP

**Prompt:**

```text
Đọc AGENTS.md, feature-list.md, future.md và planner này. Rà source/migration/
config để xác nhận chưa có implementation charging.

Chỉ thực hiện Bước 0, chưa viết source code. Ghi rõ:
1. charging_stations sở hữu thiết bị vật lý, OCPP và technical events.
2. charging_sessions chỉ nhận/lưu/cập nhật session, event và meter từ stations.
3. Authorization, RFID/driver/vehicle policy, remote control, pricing, payment,
   overdue và debt đều ngoài scope MVP.
4. Các thông tin còn thiếu để test trụ thật: identity/credential production,
   EVSE/connector ID, connector type/công suất, heartbeat/sample interval và
   offline buffer.
Không tự đặt credential hoặc business rule ngoài scope.
```

**Kết quả thực tế:** Scope MVP rút gọn đã được xác nhận ngày 2026-07-31; không
code authorization/billing/remote-control business.

### Bước 1 — Chốt contract schema và public service

**Prompt:**

```text
Thực hiện Bước 1, chỉ sửa planner/contract.
1. Chốt fields/constraints/index/FK/soft-delete cho 5 nhóm bảng trong mục 3.
2. Chốt state machine session và idempotency cho TransactionEvent/MeterValues.
3. Thiết kế public service của charging_sessions:
   ingest_transaction_event(...), ingest_meter_values(...),
   mark_station_interrupted(...) nếu cần.
4. Quy định adapter OCPP chỉ truyền primitive/standard-library values.
5. Chốt HTTP API topology/status/history và session monitoring.
6. Chốt transaction boundary, duplicate/out-of-order, timeout và raw payload
   redaction.
Không thêm bảng remote command, tariff, payment hoặc debt.
```

### Bước 2 — Tạo package, dependency/config và migration

**Prompt:**

```text
Thực hiện Bước 2 theo contract Bước 1.
1. Tạo package charging_stations và charging_sessions với __init__.py chỉ có
   docstring, cùng types/exceptions/models cần thiết.
2. Thêm python-ocpp bằng uv, đồng bộ pyproject.toml/uv.lock.
3. Tạo Alembic migration cho station/EVSE/connector/status event/session/
   session event/meter value.
4. Dùng shared Base/session; UUID, UTC, PostGIS và TimescaleDB đúng contract.
5. Review hypertable partition key, unique/idempotency, FK/soft-delete,
   upgrade/downgrade và index truy vấn monitoring.
6. Chạy format/lint/type/import và migration smoke test; không tạo dữ liệu giả.
```

### Bước 3 — CRUD station, EVSE, connector

**Prompt:**

```text
Thực hiện Bước 3. Tạo schemas/repository/service/router cho CRUD và soft-delete
station, EVSE, connector. Hỗ trợ N EVSE/N connector, PATCH theo convention,
validate identity/unique topology, không tự tạo topology từ OCPP. Đăng ký router,
chạy Swagger smoke test cho topology 2 EVSE × 1 connector và conflict.
```

### Bước 4 — Session ingestion service và persistence

**Prompt:**

```text
Thực hiện Bước 4 cho charging_sessions. Implement repository/service nhận
TransactionEvent Started/Updated/Ended và MeterValues bằng primitive values.
Started tạo session; Updated/MeterValues append event/sample; Ended chuyển
ending rồi completed/interrupted theo contract. Duplicate/out-of-order/reconnect
idempotent, state không lùi, năng lượng normalize về Wh bằng Decimal. Service và
repository không commit/rollback, không import charging_stations. Test hai EVSE
đồng thời, duplicate seqNo, meter reset, unknown transaction và rollback.
```

### Bước 5 — OCPP WebSocket gateway

**Prompt:**

```text
Thực hiện Bước 5 cho charging_stations/ocpp. Tạo server/entrypoint dùng
python-ocpp v201, chỉ negotiate ocpp2.0.1, validate identity, giữ một active
connection/station, xử lý reconnect và graceful shutdown. Dùng shared session
factory, structured logging, không tạo engine/logger riêng. Tạo simulator
connect/reject protocol tối thiểu.
```

### Bước 6 — Boot, heartbeat, status và technical history

**Prompt:**

```text
Thực hiện Bước 6. Implement BootNotification, Heartbeat, StatusNotification và
NotifyEvent. Boot chỉ cập nhật station đã pre-provision; unknown topology không
tự tạo. Snapshot và status event atomic; duplicate/out-of-order không làm lùi
timestamp/status; offline detection lấy timeout từ config. Tạo API status và
status-history có filter UTC/pagination, không trả raw payload mặc định.
```

### Bước 7 — Bridge OCPP events sang sessions

**Prompt:**

```text
Thực hiện Bước 7. OCPP adapter resolve internal station/EVSE/connector IDs,
giữ transactionId/seqNo/timestamp/meter và gọi public charging_sessions.service.
Không truyền ocpp.v201 hoặc SQLAlchemy model qua boundary. Chỉ phản hồi OCPP sau
khi operation persistence thành công theo transaction contract. Test Started,
Updated, Ended, MeterValues, duplicate, out-of-order, unknown transaction và DB
rollback. Không triển khai authorize hay remote command.
```

### Bước 8 — API giám sát phiên

**Prompt:**

```text
Thực hiện Bước 8 cho charging_sessions. Implement:
GET /api/v1/charging-sessions
GET /api/v1/charging-sessions/active
GET /api/v1/charging-sessions/{session_id}
GET /api/v1/charging-sessions/{session_id}/meter-values
GET /api/v1/charging-sessions/{session_id}/events

Filter station/EVSE/connector/transaction/status/UTC range, pagination ổn định,
không N+1. Response chỉ chứa session/event/meter/technical reconciliation,
không có payment/authorization fields. Chạy smoke test empty/filter/pagination.
```

### Bước 9 — Simulator và nghiệm thu MVP

**Prompt:**

```text
Thực hiện Bước 9 và nghiệm thu planner MVP.
1. Simulator mô phỏng một station có hai EVSE, mỗi EVSE một connector; chạy hai
   session đồng thời, Boot/Heartbeat/Status/Notify/Transaction/Meter, delay,
   disconnect/reconnect, duplicate và out-of-order.
2. E2E: OCPP Started → session active → MeterValues → Ended → completed hoặc
   interrupted → API monitoring.
3. Chạy compileall, Black, isort, Ruff, mypy và migration upgrade/downgrade/
   upgrade.
4. Dùng rg audit import chéo models/repositories, HTTPException ngoài router,
   commit/rollback ngoài boundary, datetime.utcnow, float cho energy và raw
   secret. Ghi command/evidence/giới hạn test trụ thật.
5. Không thêm authorization, remote control, pricing, payment hoặc debt; các
   mục này chỉ được mở bằng planner tương lai riêng.
```

## 5. Tiêu chí hoàn thành

- Station/EVSE/connector CRUD và topology hoạt động.
- OCPP 2.0.1 gateway nhận technical events ổn định.
- Session được tạo/cập nhật/kết thúc từ event của station một cách idempotent.
- Meter/status/event history lưu đúng hypertable và query được.
- Hai EVSE có thể có hai session đồng thời trong simulator.
- Không có dependency ngược hoặc import nội bộ chéo giữa hai domain.
- Authorization, remote control business, pricing, payment và debt không xuất
  hiện trong source/migration MVP.

## 6. Tài liệu giao thức tham chiếu

- [OCPP 2.0.1 JSON schemas](https://ocpp-spec.org/schemas/v2.0.1/)
- [OCPP 2.x numbering](https://ocpp-spec.org/docs/ocpp_2_0/architecture/numbering/)
- [`python-ocpp`](https://github.com/mobilityhouse/ocpp)
