# Planner: Backend quản lý trụ sạc và lưu trữ phiên sạc

> Mã chức năng: AD-03 và phần lifecycle của S-02
>
> Trạng thái: 🚧 MVP rút gọn đã triển khai; production path và automated test vẫn
> hoãn
>
> Ngày cập nhật: 2026-07-31

Planner này mô tả phạm vi charging backend và được thực thi theo phiên bản rút
gọn tại [`backend-charging-mvp-ideal.md`](./backend-charging-mvp-ideal.md). Source
hiện tại chỉ cam kết topology pre-provision và happy path local; các yêu cầu
production trong tài liệu này vẫn là kế hoạch mở rộng.

Các phần “Kết quả thực tế” của những bước cũ bên dưới được giữ làm lịch sử
quyết định và bằng chứng triển khai. Khi nội dung cũ nói “chưa có source” hoặc
“chưa triển khai”, đó là trạng thái tại thời điểm ghi nhận; trạng thái hiện tại
được lấy từ phần đầu file và planner `backend-charging-mvp-ideal.md`.

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

### 3.7. Contract schema Bước 1 — quy ước chung

- Mọi bảng có internal ID UUID. Với ba bảng history là hypertable, primary key
  là composite `(internal_id, time_column)` để thỏa điều kiện unique index của
  TimescaleDB; UUID vẫn là ID nội bộ dùng trong API và log, không dùng business
  key làm primary key.
- Tất cả timestamp là `TIMESTAMP WITH TIME ZONE`, được normalize về UTC trước
  khi validate/persist. Mỗi history record có cả thời điểm phát sinh từ thiết bị
  (`recorded_at`/`event_occurred_at`/`sampled_at`) và thời điểm server nhận
  (`received_at`).
- Giá trị năng lượng và công suất dùng `Decimal` ở Python và `NUMERIC` ở DB;
  năng lượng canonical là Wh. Không dùng `float` để tính hoặc so sánh energy.
- Soft-delete chỉ áp dụng cho `charging_stations`, `charging_evses` và
  `charging_connectors` qua `deleted_at`. Không xóa session hoặc history; mọi
  foreign key topology/session dùng `ON DELETE RESTRICT`, và thao tác CRUD
  thông thường không physical-delete record.
- Unique identity topology bao gồm cả record đã soft-delete để tránh tái sử
  dụng business identity làm mơ hồ lịch sử; muốn dùng lại topology cũ phải
  restore record cũ theo contract CRUD sau này.
- `station_id`, `evse_id` và `connector_id` truyền qua boundary luôn là internal
  UUID. Adapter OCPP resolve topology trước khi gọi service; không truyền
  `ocpp.v201` object, Pydantic schema hoặc SQLAlchemy model qua boundary.

### 3.8. Contract fields/constraints/index cho topology

#### `charging_stations`

| Field | Contract |
|---|---|
| `station_id` | UUID, primary key |
| `ocpp_identity` | String không rỗng, tối đa 255 ký tự, unique toàn bảng; giữ nguyên giá trị phân biệt hoa thường để resolve đúng OCPP path |
| `display_name` | String không rỗng, tối đa 200 ký tự |
| `manufacturer`, `model`, `serial_number`, `firmware_version` | String nullable, chỉ là metadata kỹ thuật; không dùng làm identity |
| `location` | PostGIS `geography(Point, 4326)`, nullable; tọa độ phải nằm trong miền latitude/longitude hợp lệ |
| `administrative_status` | `active \| inactive \| maintenance`, bắt buộc |
| `connection_status` | `unknown \| connected \| offline`, snapshot kỹ thuật, bắt buộc |
| `last_seen_at`, `last_boot_at` | UTC-aware, nullable |
| `created_at`, `updated_at` | UTC-aware, bắt buộc |
| `deleted_at` | UTC-aware, nullable; station bị soft-delete không được OCPP resolve hoặc trả trong list active |

Index/constraint tối thiểu: unique `ocpp_identity`; index cho
`(administrative_status, deleted_at)`, `(connection_status, last_seen_at)`,
`deleted_at`; spatial GiST cho `location` nếu bật truy vấn theo vị trí.

#### `charging_evses`

| Field | Contract |
|---|---|
| `evse_id` | UUID, primary key |
| `station_id` | UUID, FK `charging_stations.station_id`, NOT NULL, `ON DELETE RESTRICT` |
| `ocpp_evse_id` | Integer dương (`> 0`), NOT NULL |
| `display_name` | String nullable, tối đa 100 ký tự |
| `administrative_status` | `active \| inactive`, bắt buộc |
| `technical_status` | `unknown \| available \| occupied \| unavailable \| faulted`, bắt buộc |
| `capabilities` | JSONB object, mặc định `{}`, chỉ chứa capability đã chuẩn hóa, không chứa credential |
| `last_status_at` | UTC-aware, nullable |
| `created_at`, `updated_at`, `deleted_at` | UTC-aware; `deleted_at` nullable để soft-delete |

Unique `(station_id, ocpp_evse_id)`; index cho `(station_id, deleted_at)` và
`(technical_status, station_id)`. Service phải kiểm tra EVSE thuộc station
được truyền khi xử lý event; FK đơn lẻ không đủ biểu diễn ràng buộc topology
chéo này.

#### `charging_connectors`

| Field | Contract |
|---|---|
| `connector_id` | UUID, primary key |
| `evse_id` | UUID, FK `charging_evses.evse_id`, NOT NULL, `ON DELETE RESTRICT` |
| `ocpp_connector_id` | Integer dương (`> 0`), NOT NULL |
| `connector_type` | String nullable, tối đa 100 ký tự; để nullable cho tới khi có dữ liệu trụ thật |
| `max_power_kw` | `NUMERIC(12,3)` nullable; nếu có thì `> 0` |
| `administrative_status` | `active \| inactive`, bắt buộc |
| `technical_status` | `unknown \| available \| occupied \| unavailable \| faulted`, bắt buộc |
| `capabilities` | JSONB object, mặc định `{}`, không chứa credential |
| `last_status_at` | UTC-aware, nullable |
| `created_at`, `updated_at`, `deleted_at` | UTC-aware; `deleted_at` nullable để soft-delete |

Unique `(evse_id, ocpp_connector_id)`; index cho `(evse_id, deleted_at)` và
`(technical_status, evse_id)`. Connector phải thuộc đúng EVSE của station
trong cùng operation; không tự tạo connector từ OCPP notification.

### 3.9. Contract fields/constraints/index cho technical history

#### `charging_station_status_events`

| Field | Contract |
|---|---|
| `status_event_id` | UUID internal ID |
| `recorded_at` | UTC-aware, NOT NULL, partition key của hypertable |
| `station_id` | UUID, FK station, NOT NULL, `ON DELETE RESTRICT` |
| `evse_id` | UUID, FK EVSE, nullable cho event cấp station |
| `connector_id` | UUID, FK connector, nullable; nếu có thì `evse_id` cũng bắt buộc |
| `source_action` | `BootNotification \| Heartbeat \| StatusNotification \| NotifyEvent` |
| `technical_status` | String nullable; status đã chuẩn hóa nếu message có status |
| `event_code` | String nullable; mã event đã chuẩn hóa cho `NotifyEvent` |
| `ocpp_message_id` | String nullable, tối đa 255 ký tự |
| `idempotency_key` | String không rỗng, tối đa 255 ký tự, do adapter tạo ổn định |
| `received_at` | UTC-aware, NOT NULL |
| `sanitized_raw_payload` | JSONB nullable; luôn redacted trước khi lưu |

Primary key là `(status_event_id, recorded_at)`. Unique database tối thiểu là
`(station_id, idempotency_key, recorded_at)` do hypertable yêu cầu partition
key nằm trong unique index; service vẫn kiểm tra `idempotency_key` logic để
phát hiện cùng event được gửi lại với timestamp khác. Index truy vấn gồm
`(station_id, recorded_at DESC)`, `(evse_id, recorded_at DESC)`,
`(connector_id, recorded_at DESC)` và `(source_action, recorded_at DESC)`.

#### `charging_session_events`

| Field | Contract |
|---|---|
| `event_id` | UUID internal ID |
| `event_occurred_at` | UTC-aware, NOT NULL, partition key của hypertable |
| `session_id` | UUID, FK `charging_sessions.session_id`, NOT NULL, `ON DELETE RESTRICT` |
| `event_type` | `Started \| Updated \| Ended \| Interrupted` |
| `seq_no` | Integer không âm, NOT NULL; thứ tự logic của TransactionEvent |
| `end_reason` | `normal \| abnormal \| offline \| unknown`, nullable và chỉ dùng khi Ended/Interrupted |
| `charging_state` | `pending \| active`, nullable |
| `idempotency_key` | String không rỗng, được dẫn xuất ổn định từ station/transaction/seq_no |
| `received_at` | UTC-aware, NOT NULL |
| `sanitized_raw_payload` | JSONB nullable, đã redacted |

Primary key là `(event_id, event_occurred_at)`. Unique `(session_id, seq_no,
event_occurred_at)` là lớp DB tối thiểu; service dùng logical key
`(session_id, seq_no)` và fingerprint payload để xử lý timestamp khác nhau.
Index `(session_id, event_occurred_at, event_id)` và
`(session_id, seq_no, event_occurred_at)` phục vụ history/idempotency.

#### `charging_session_meter_values`

| Field | Contract |
|---|---|
| `meter_value_id` | UUID internal ID |
| `sampled_at` | UTC-aware, NOT NULL, partition key của hypertable |
| `session_id` | UUID, FK `charging_sessions.session_id`, NOT NULL, `ON DELETE RESTRICT` |
| `measurand` | String không rỗng, tối đa 100 ký tự; MVP chỉ nhận measurand năng lượng đã chuẩn hóa (`energy_import_register` hoặc `energy_import_interval`) |
| `phase`, `context` | String nullable, tối đa 50 ký tự |
| `source_value` | `NUMERIC(24,6)`, NOT NULL |
| `source_unit` | String không rỗng, tối đa 20 ký tự |
| `value_wh` | `NUMERIC(24,3)`, NOT NULL, giá trị đã normalize về Wh và `>= 0` |
| `seq_no` | Integer không âm nullable nếu MeterValues không có sequence |
| `sample_idempotency_key` | String không rỗng, dẫn xuất từ transaction/sample identity |
| `received_at` | UTC-aware, NOT NULL |
| `sanitized_raw_payload` | JSONB nullable, đã redacted |

MVP chỉ lưu meter energy phục vụ reconciliation; các measurand công suất,
dòng điện, điện áp hoặc nhiệt độ không đi vào bảng session meter này. Primary
key là `(meter_value_id, sampled_at)`. Unique database tối thiểu là
`(session_id, sample_idempotency_key, sampled_at)`; service dùng logical sample
identity `(transaction_id, sampled_at, measurand, phase, context)` để nhận diện
duplicate dù timestamp partition khác nhau. Index `(session_id, sampled_at,
meter_value_id)` và `(session_id, measurand, sampled_at)`.

### 3.10. Contract fields/constraints/index cho session aggregate

#### `charging_sessions`

| Field | Contract |
|---|---|
| `session_id` | UUID, primary key |
| `station_id` | UUID, FK station, NOT NULL, `ON DELETE RESTRICT` |
| `evse_id` | UUID, FK EVSE, NOT NULL, `ON DELETE RESTRICT` |
| `connector_id` | UUID, FK connector, NOT NULL, `ON DELETE RESTRICT` |
| `ocpp_transaction_id` | String không rỗng, tối đa 255 ký tự |
| `status` | `pending \| active \| ending \| completed \| interrupted` |
| `started_at`, `ended_at` | UTC-aware; `started_at` bắt buộc sau Started, `ended_at` nullable |
| `last_event_at`, `last_meter_at` | UTC-aware nullable; không được lùi |
| `last_transaction_seq_no` | Integer không âm nullable; không được lùi |
| `meter_start_wh`, `meter_end_wh`, `energy_delivered_wh` | `NUMERIC(24,3)` nullable; các giá trị có mặt phải `>= 0` |
| `reconciliation_status` | `pending \| reconciled \| inconsistent \| unavailable` |
| `reconciliation_error` | String nullable, chỉ mô tả lỗi kỹ thuật đối soát |
| `created_at`, `updated_at` | UTC-aware, NOT NULL |

Unique `(station_id, ocpp_transaction_id)` bảo đảm reconnect không tạo session
mới. Index cho `(status, updated_at)`, `(station_id, status)`,
`(evse_id, status)`, `(connector_id, status)`, `started_at` và `ended_at`.
Session không có `deleted_at`, pricing, payment, authorization, driver hoặc
vehicle field trong MVP.

### 3.11. State machine và quy tắc idempotency

State machine operational của session:

```text
pending ──→ active ──→ ending ──→ completed
   │           │          └──────→ interrupted
   └───────────┴─────────────────→ interrupted

completed / interrupted là terminal; event mới chỉ có thể là duplicate/no-op
hoặc history bị out-of-order, không được mở lại session.
```

- `Started` lần đầu tạo session theo topology đã resolve. Nếu normalized
  `charging_state` là `pending` thì session ở `pending`; khi có trạng thái
  charging hoặc meter hợp lệ thì chuyển `active`.
- `Updated` và MeterValues không tạo session mới. Chúng append history; chỉ
  cập nhật aggregate nếu sequence/sample mới hơn theo quy tắc ordering.
- `Ended` append event, chuyển transient qua `ending` trong cùng transaction rồi
  chốt `completed` cho `end_reason=normal` hoặc `interrupted` cho
  `abnormal|offline|unknown`. Nếu meter reset/conflict, vẫn giữ history nhưng
  đặt `reconciliation_status=inconsistent`; không trừ ngược năng lượng.
- `mark_station_interrupted` chuyển mọi session `pending|active|ending` của
  station sang `interrupted`; session terminal không đổi.
- TransactionEvent logical key là `(station_id, ocpp_transaction_id, seq_no)`.
  Cùng key và cùng fingerprint là duplicate/no-op, có thể trả ACK thành công;
  cùng key nhưng payload khác là conflict, không cập nhật aggregate.
- Meter logical sample identity là
  `(ocpp_transaction_id, sampled_at, measurand, phase, context)`. Cùng identity
  và cùng giá trị là duplicate; cùng identity khác giá trị là conflict và đặt
  reconciliation inconsistent, không ghi đè sample cũ.
- Event/meter out-of-order vẫn được lưu nếu chưa duplicate, nhưng không được
  làm lùi `status`, `last_event_at`, `last_meter_at`, sequence hoặc meter end.
  Reconnect dùng cùng transaction identity để tiếp tục session; MeterValues của
  transaction chưa tồn tại bị từ chối/ghi nhận `unknown_transaction`, không tự
  tạo aggregate.
- Meter register giảm so với sample trước được lưu để audit và đánh dấu
  `meter_reset_or_decrease`; `energy_delivered_wh` không nhận giá trị âm.

### 3.12. Public service của `charging_sessions`

Đây là public boundary duy nhất để `charging_stations` gọi. Chữ ký dưới đây là
contract logic, không phải code implementation:

```text
ingest_transaction_event(
    db,
    station_id: UUID,
    evse_id: UUID,
    connector_id: UUID,
    transaction_id: str,
    event_type: Started | Updated | Ended,
    seq_no: int,
    event_occurred_at: datetime,
    received_at: datetime,
    charging_state: pending | active | None,
    end_reason: normal | abnormal | offline | unknown | None,
    meter_start_wh: Decimal | None,
    meter_end_wh: Decimal | None,
    idempotency_key: str,
    sanitized_raw_payload: JSON object | None,
) -> TransactionIngestResult

ingest_meter_values(
    db,
    station_id: UUID,
    evse_id: UUID,
    connector_id: UUID,
    transaction_id: str,
    sample: MeterSampleInput,
    received_at: datetime,
) -> MeterIngestResult

mark_station_interrupted(
    db,
    station_id: UUID,
    interrupted_at: datetime,
    received_at: datetime,
    reason: offline | connection_lost | unknown,
) -> InterruptionResult
```

`MeterSampleInput` chỉ là dữ liệu chuẩn hóa bằng kiểu standard library:
`sampled_at`, `measurand`, `phase`, `context`, `source_value`, `source_unit`,
`value_wh`, `seq_no` và `sample_idempotency_key`. Không nhận `ocpp.v201` type,
Pydantic model hay ORM object. Kết quả service là immutable standard-library
result chứa `accepted|duplicate|ignored_out_of_order|conflict|rejected`,
`session_id`, status hiện tại và số sample/event đã insert; không trả model DB.

Public service không `commit()`/`rollback()`. Caller ở entry boundary sở hữu
`AsyncSession` và transaction; một lần gọi ingest event hoặc một lần gọi
`ingest_meter_values` cho một sample là atomic. Repository được `flush()` để phát hiện FK/
unique conflict khi cần. DB timeout, serialization/deadlock hoặc lỗi bất ngờ
phải rollback và propagate; không retry trong service và không ACK OCPP thành
công trước khi transaction commit.

### 3.13. HTTP API topology/status/history và session monitoring

Prefix chung là `/api/v1`. API topology phục vụ pre-provision và soft-delete:

- `POST/GET /charging-stations` — tạo và liệt kê station; filter
  `administrative_status`, `connection_status`, `include_deleted` không mở cho
  API public MVP nếu chưa có authorization.
- `GET/PATCH/DELETE /charging-stations/{station_id}` — chi tiết, partial update,
  soft-delete station.
- `POST/GET /charging-stations/{station_id}/evses` và
  `GET/PATCH/DELETE /charging-evses/{evse_id}` — CRUD EVSE thuộc station.
- `POST/GET /charging-evses/{evse_id}/connectors` và
  `GET/PATCH/DELETE /charging-connectors/{connector_id}` — CRUD connector thuộc
  EVSE.

API technical status/history:

- `GET /charging-stations/{station_id}/status` trả snapshot station, EVSE và
  connector; không tạo topology nếu thiếu.
- `GET /charging-stations/{station_id}/status-history` hỗ trợ filter
  `evse_id`, `connector_id`, `source_action`, `technical_status`,
  `from/to` UTC-aware và `page/page_size`.

API session monitoring:

- `GET /charging-sessions`
- `GET /charging-sessions/active`
- `GET /charging-sessions/{session_id}`
- `GET /charging-sessions/{session_id}/meter-values`
- `GET /charging-sessions/{session_id}/events`

Session list filter theo `station_id`, `evse_id`, `connector_id`,
`ocpp_transaction_id`, `status`, `started_from/started_to` và
`updated_from/updated_to`; history filter theo UTC range. List dùng pagination
`page/page_size`, total và thứ tự ổn định có tie-breaker bằng UUID. Mặc định
session sort `updated_at DESC, session_id DESC`; event/meter sort theo thời điểm
phát sinh ASC rồi internal ID. Query phải tránh N+1.

Response public chỉ gồm topology/status/session/event/meter và thông tin
technical reconciliation. Không trả payment, authorization, driver, vehicle,
raw payload hoặc credential fields.

### 3.14. Timeout và raw payload redaction

- Các timeout phải là settings namespace charging được chốt khi làm Bước 2,
  tối thiểu gồm heartbeat/offline timeout, meter stale timeout, OCPP request
  timeout và giới hạn kích thước payload. Bước 1 không tự đặt giá trị vì
  heartbeat/sample interval và offline buffer còn thiếu.
- Station chỉ chuyển `connected` → `offline` khi clock vượt offline timeout từ
  `last_seen_at`; timeout không tự sinh session và không tự tạo topology.
  Gateway/lifecycle gọi `mark_station_interrupted` sau khi timeout được xác
  nhận. Dữ liệu reconnect replay được xử lý bằng idempotency.
- Adapter phải redact đệ quy các key không phân biệt hoa thường như
  `password`, `token`, `authorization`, `certificate`, `private_key`, `secret`,
  `credential`, `id_token` trước khi truyền `sanitized_raw_payload`. Payload
  vượt giới hạn cấu hình được bỏ qua phần raw, không làm mất event/meter đã
  chuẩn hóa; không lưu credential để phục vụ debug.
- Raw payload không xuất hiện trong response mặc định và public API MVP không có
  chế độ trả raw payload. Log chỉ ghi station/transaction/event identity và
  metadata đã sanitize, không ghi toàn bộ payload hoặc secret.

**Kết quả thực tế (rà soát ngày 2026-07-31):** Contract Bước 1 đã được chốt
trong các mục 3.7–3.14. Chưa sửa source code, dependency, config hoặc migration;
Bước 2 sẽ hiện thực contract này và phải rà lại các constraint TimescaleDB,
PostGIS, FK, index và kiểu enum trước khi merge.

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

**Kết quả thực tế (rà soát ngày 2026-07-31):** Bước 0 đã hoàn tất. Scope MVP
rút gọn được xác nhận như sau:

1. `charging_stations` sở hữu thiết bị vật lý (station/EVSE/connector),
   topology, trạng thái kỹ thuật, OCPP 2.0.1 và technical events. Gateway OCPP
   nằm trong domain này.
2. `charging_sessions` chỉ nhận dữ liệu đã chuẩn hóa từ
   `charging_stations` qua public service để tạo/cập nhật/lưu aggregate
   session, event history và meter history; domain này không sở hữu WebSocket
   hoặc OCPP adapter.
3. Authorization, RFID/`idToken`, policy driver/vehicle, remote start/stop và
   remote-control business, pricing, payment, webhook, overdue và debt đều nằm
   ngoài scope MVP. `docs/01-requirements/future.md` mục 26 đã ghi nhận các
   nghiệp vụ này là phần hoãn; không tạo source, migration hoặc placeholder cho
   chúng.

Các thông tin còn thiếu trước khi test với trụ thật, chưa tự đặt giá trị hoặc
business rule:

- identity/credential production và security profile xác thực thiết bị; dev
  isolated có thể dùng kết nối không TLS/không authentication theo quyết định
  hiện tại, nhưng không suy ra đây là cấu hình production;
- danh sách `EVSE ID` và `connector ID` thực tế của từng trụ;
- connector type, công suất định mức và capability tương ứng;
- heartbeat interval, meter/sample interval và timeout dùng để xác định mất
  kết nối;
- chính sách offline buffer: trụ có lưu và gửi bù dữ liệu khi reconnect hay
  chấp nhận mất dữ liệu trong thời gian offline.

Bằng chứng rà hiện trạng:

- `backend/app/domains/` hiện chỉ có `vehicles`, `telematics` và `telemetry`;
  chưa có `charging_stations`, `charging_sessions` hoặc `ocpp`.
- `backend/app/libs/db/migrations/versions/` chỉ có migration cho vehicles,
  telematics và `vehicle_telemetry`; chưa có bảng/migration charging.
- `backend/app/api/main.py` chỉ đăng ký router vehicles, telematics và
  telemetry; chưa có charging router.
- `backend/pyproject.toml` chưa khai báo `python-ocpp`; cấu hình chung và các
  file `.env.example` hiện chỉ có biến cho app, database và telemetry/MQTT,
  chưa có cấu hình charging/OCPP.
- `git ls-files` và tìm kiếm toàn repo không phát hiện source charging/OCPP;
  kết quả charging hiện tại chỉ là tài liệu planner, `AGENTS.md` và phần
  requirements/future liên quan.

Không thay đổi source code, dependency, config hoặc migration trong Bước 0.

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

**Kết quả thực tế (triển khai ngày 2026-08-02):** Bước 2 đã hoàn tất.

- Đã tạo package `charging_stations` và `charging_sessions` với `__init__.py`
  chỉ có module docstring; thêm `types.py`, `exceptions.py` và `models.py`
  theo contract. Metadata đã đăng ký đủ bảy bảng: station, EVSE, connector,
  technical status event, session, session event và meter value.
- Đã thêm `ocpp>=2.0.0` (tên package PyPI của thư viện MobilityHouse
  `python-ocpp`) và `geoalchemy2>=0.15.0`; `backend/uv.lock` đã được resolve
  với `ocpp` 2.1.0. Cấu hình charging dùng namespace `CHARGING_`, gồm timeout
  heartbeat/offline, meter stale, OCPP request và giới hạn raw payload.
- Đã tạo migration `b2c7d4e8f901_create_charging_domains.py`: UUID internal ID,
  UTC-aware timestamp, PostGIS `geography(POINT, 4326)`, Decimal/`NUMERIC`,
  enum contract value, FK `ON DELETE RESTRICT`, soft-delete topology, check/
  unique constraint và index phục vụ monitoring. `charging_sessions` là bảng
  thường; `charging_station_status_events`, `charging_session_events` và
  `charging_session_meter_values` là hypertable với partition key nằm trong
  mọi primary/unique index.
- Đã nạp các model charging vào Alembic metadata. Smoke test trên PostgreSQL
  + TimescaleDB dev đã chạy thành công: `upgrade head` → `downgrade
  5e7b1c9d2a44` → `upgrade head`; không tạo dữ liệu giả. Catalog xác nhận đúng
  bảy bảng, ba hypertable, PostGIS geography và các enum/index/FK tương ứng.
- Kiểm tra đạt: `compileall`, Black, isort, Ruff và mypy strict. `alembic
  check` vẫn phát hiện `spatial_ref_sys`, index nội bộ do TimescaleDB sinh và
  một số index legacy ngoài charging; đây là khác biệt introspection của
  schema hiện hữu, không phải lỗi upgrade/downgrade của migration mới.

### Bước 3 — CRUD station, EVSE, connector

**Prompt:**

```text
Thực hiện Bước 3. Tạo schemas/repository/service/router cho CRUD và soft-delete
station, EVSE, connector. Hỗ trợ N EVSE/N connector, PATCH theo convention,
validate identity/unique topology, không tự tạo topology từ OCPP. Đăng ký router,
chạy Swagger smoke test cho topology 2 EVSE × 1 connector và conflict.
```

**Kết quả thực tế (triển khai ngày 2026-08-02):** Đã hoàn tất implementation
Bước 3, chưa tạo commit mới.

- Đã thêm `schemas.py`, `repository.py`, `service.py` và `router.py` cho
  `charging_stations`; router được đăng ký dưới `/api/v1` với đầy đủ endpoint
  station, EVSE và connector theo internal UUID.
- CRUD hỗ trợ pagination, filter trạng thái station, PATCH theo
  `exclude_unset=True` và quy ước `None` là không cập nhật. Identity OCPP của
  station, `(station_id, ocpp_evse_id)` và `(evse_id, ocpp_connector_id)` được
  kiểm tra cả với record đã soft-delete; conflict trả HTTP 409.
- Parent phải active trước khi tạo/list topology con. Soft-delete station
  cascade xuống EVSE/connector; soft-delete EVSE cascade xuống connector;
  không physical-delete history/topology qua API.
- Swagger/OpenAPI smoke test đã xác nhận 6 route topology. API smoke test
  rollback/cleanup đã chạy topology 1 station, 2 EVSE, mỗi EVSE 1 connector,
  duplicate conflict, PATCH và soft-delete; database không còn record test.
- Kiểm tra đạt: Black, isort, Ruff, mypy strict, compileall và import FastAPI
  app. `httpx` chỉ được nạp tạm bằng `uv run --with` để smoke test, không thêm
  vào runtime dependency.

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

**Kết quả thực tế (triển khai ngày 2026-08-02):** Đã hoàn tất session ingestion
và persistence trong domain `charging_sessions`.

- Đã thêm `repository.py` và `service.py`, cùng các input/result immutable bằng
  dataclass trong `types.py`; boundary chỉ nhận UUID, enum, `datetime`,
  `Decimal`, `Sequence` và dict raw đã sanitize, không import
  `charging_stations` hoặc kiểu OCPP/Pydantic/ORM từ caller.
- `Started` tạo aggregate và event; `Updated`/`Ended` append event; Ended đi qua
  `ending` trong cùng transaction rồi chốt `completed` hoặc `interrupted`.
  Reconnect dùng lại `(station_id, transaction_id)` và không tạo session mới.
- TransactionEvent nhận diện duplicate/conflict bằng `(session_id, seq_no)` và
  fingerprint payload; event out-of-order vẫn lưu audit nhưng không làm lùi
  state, timestamp hoặc sequence. Terminal session không bị mở lại.
- MeterValues chỉ nhận hai measurand energy của MVP, normalize `Wh`/`kWh` bằng
  `Decimal`, append sample idempotent theo logical sample identity. Meter reset,
  giảm register hoặc payload conflict được lưu/đánh dấu
  `reconciliation_status=inconsistent` nhưng không làm `energy_delivered_wh`
  âm. Unknown transaction bị từ chối và không tự tạo aggregate.
- Đã implement `mark_station_interrupted`, chuyển các session
  `pending|active|ending` của station sang `interrupted` và append history;
  session terminal không đổi.
- Smoke test PostgreSQL dev đạt: hai EVSE có hai session đồng thời, duplicate
  seq, out-of-order, normalize Wh, meter reset, unknown transaction,
  interruption và rollback transaction. Dữ liệu test đã được dọn sạch.
- Kiểm tra phần thay đổi đạt: Black, isort, Ruff, mypy strict và compileall.
  Chưa thêm HTTP router/session monitoring hoặc OCPP bridge vì thuộc Bước 7–8;
  chưa thêm migration vì schema Bước 2 đã đủ cho contract này.

### Bước 5 — OCPP WebSocket gateway

**Prompt:**

```text
Thực hiện Bước 5 cho charging_stations/ocpp. Tạo server/entrypoint dùng
python-ocpp v201, chỉ negotiate ocpp2.0.1, validate identity, giữ một active
connection/station, xử lý reconnect và graceful shutdown. Dùng shared session
factory, structured logging, không tạo engine/logger riêng. Tạo simulator
connect/reject protocol tối thiểu.
```

**Kết quả thực tế (triển khai ngày 2026-08-02):** Đã hoàn tất gateway OCPP
2.0.1 tối thiểu và simulator handshake.

- Đã thêm `charging_stations/ocpp/ocpp_server.py` và `entrypoint.py`; gateway
  bind theo `CHARGING_OCPP_HOST`/`CHARGING_OCPP_PORT`, dùng shared
  `async_session_factory`, structured logging và `ocpp.v201.ChargePoint`.
- WebSocket chỉ accept subprotocol `ocpp2.0.1`, path `/ocpp/{ocpp_identity}`
  phải hợp lệ và identity phải là station đã pre-provision, chưa soft-delete.
  Identity lạ nhận HTTP 404; protocol sai nhận HTTP 426.
- Connection registry giữ tối đa một connection active cho mỗi identity.
  Reconnect đóng connection cũ trước khi handler mới tiếp tục; shutdown đóng
  listener và các connection active bằng close code 1001.
- Bước này chưa xử lý BootNotification, Heartbeat, StatusNotification,
  NotifyEvent, TransactionEvent hoặc MeterValues; các action đó vẫn chờ Bước
  6–7.
- Đã thêm `simulator/charging_session_simulator.py` để chạy session happy
  path với một identity đã pre-provision.
- Smoke test runtime với fake repository đạt: connect `ocpp2.0.1`, reconnect
  thay thế registry, unknown identity HTTP 404 và protocol sai HTTP 426. Docker
  daemon không truy cập được từ sandbox nên chưa chạy test với PostgreSQL/EMQX
  thật trong bước này.
- Kiểm tra đạt: Black, isort, Ruff, mypy strict và compileall.

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
