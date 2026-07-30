# Planner: Backend Charging Sessions (S-02)

> Mã chức năng: S-02; đối chiếu `feature-list.md` mục 3.1, 4.3 và 4.4  
> Trạng thái: 📋 Dự kiến  
> Ngày tạo: 2026-07-31

## 1. Mục tiêu

Xây dựng backend ghi nhận và giám sát vòng đời phiên sạc từ các
`TransactionEvent` OCPP 2.0.1 đã được domain `charging_stations` chuẩn hóa.
Domain `charging_sessions` sở hữu aggregate phiên, meter samples và API đọc
phiên đang hoạt động/lịch sử.

MVP chỉ quan sát phiên bắt đầu từ trụ. Remote start/stop, tính tiền và thanh
toán được hoãn và ghi trong `future.md`.

## 2. Ranh giới domain

- Package: `backend/app/domains/charging_sessions/`.
- Không chứa WebSocket, handler hoặc dataclass của `ocpp.v201`.
- Public ingestion function nhận event thuần Python đã normalize cùng internal
  identity station, EVSE và connector do OCPP adapter resolve trước đó.
- Chỉ gọi public service của `vehicles` khi có mapping xe đáng tin cậy. Không
  gọi ngược `charging_stations` và không query chéo repository/model, nhờ đó
  chiều phụ thuộc giữa hai domain sạc không tạo cycle.
- Mỗi OCPP event được xử lý atomic trong một transaction do OCPP entry boundary
  sở hữu; service/repository không commit/rollback.
- Billing/payment không được nhúng vào trạng thái phiên MVP.

## 3. Identity và state machine

OCPP 2.0.1 để Charging Station tạo `transactionId`, unique trong phạm vi station.
Database dùng:

- `charging_session_id`: UUID internal primary key;
- unique `(charging_station_id, ocpp_transaction_id)`.

State nghiệp vụ MVP:

```text
pending → active → ending → completed
                  ↘ interrupted
```

- `pending`: nhận event liên quan nhưng chưa đủ thông tin bắt đầu;
- `active`: nhận `TransactionEvent(eventType=Started)`;
- `ending`: đã nhận `Ended` từ trụ nhưng đang chờ dữ liệu cuối từ nguồn xe theo
  rule trong `feature-list.md`;
- `completed`: đủ dữ liệu bắt buộc hoặc hết reconciliation timeout;
- `interrupted`: phiên kết thúc bất thường/mất kết nối; vẫn giữ số đo cuối, không
  hoàn tác.

OCPP `triggerReason`, `chargingState` và `stoppedReason` được lưu riêng; không
đồng nhất trực tiếp với state nghiệp vụ. Event đến lặp hoặc out-of-order không
được làm state lùi. Contract idempotency dựa trên station, transaction,
`seqNo` và event type; duplicate phải không tạo session/sample lặp.

Rule “đợi telemetry cuối từ xe” chỉ bật khi có cách mapping chắc chắn transaction
với `vehicle_id`. Nếu MVP chưa có id token/mapping đáng tin cậy, session chốt
theo dữ liệu trụ và ghi rõ `reconciliation_status = station_only`; không đoán xe
từ connector hoặc session gần nhất.

## 4. Thiết kế dữ liệu

### 4.1. `charging_sessions`

Bảng aggregate quan hệ thông thường:

- `charging_session_id`: UUID primary key.
- `charging_station_id`, `charging_evse_id`, `charging_connector_id`: internal FK.
- `ocpp_transaction_id`: business ID từ station.
- `vehicle_id`: UUID nullable, FK internal ID khi resolve chắc chắn.
- `id_token_hash` hoặc reference phù hợp: không lưu credential thô.
- `status`, `trigger_reason`, `charging_state`, `stopped_reason`.
- `started_at`, `ended_at`, `completed_at`: UTC timezone-aware.
- `meter_start_wh`, `meter_end_wh`, `energy_delivered_wh`: decimal/integer với
  đơn vị canonical Wh; không dùng float cho tổng điện năng.
- `last_meter_at`, `last_event_seq_no`.
- `reconciliation_status`: `pending | matched | station_only | timed_out`.
- `created_at`, `updated_at`.

Session không soft delete trong MVP vì là lịch sử vận hành. Nếu cần sửa dữ liệu,
phải có contract audit riêng.

### 4.2. `charging_session_events`

Lưu event envelope để trace/idempotency:

- internal ID, session ID;
- OCPP event type, `seq_no`, timestamp;
- trigger/charging/stopped reason;
- received timestamp và raw payload JSONB.

Unique constraint phù hợp trên session/transaction + `seq_no` để chống xử lý lặp.
Nếu TimescaleDB bắt buộc partition key trong unique constraint, migration phải
review chiến lược idempotency trước khi tạo hypertable.

### 4.3. `charging_session_meter_values`

Hypertable time-series:

- identity chứa partition key theo convention TimescaleDB;
- `charging_session_id`, station/EVSE/connector internal IDs;
- `sampled_at`, `received_at` UTC timezone-aware;
- `measurand`, `phase?`, `location?`, `context?`;
- `value`, `unit`, `multiplier?`;
- giá trị canonical được normalize khi đủ contract;
- raw sampled value để trace.

Chỉ tính `energy_delivered_wh` từ measurand/counter điện năng phù hợp và cùng
unit đã normalize; không cộng dồn power samples để giả lập billing trong MVP.

## 5. API giám sát

Prefix: `/api/v1/charging-sessions`.

- `GET /charging-sessions`: filter station, EVSE, connector, vehicle, status và
  khoảng thời gian; pagination/cursor.
- `GET /charging-sessions/active`: danh sách phiên active/ending phục vụ dashboard.
- `GET /charging-sessions/{charging_session_id}`: aggregate, topology, thời gian,
  điện năng và reconciliation status.
- `GET /charging-sessions/{charging_session_id}/meter-values`: time range,
  measurand và pagination/downsampling contract rõ ràng.
- Không có POST/PATCH/DELETE session từ Admin trong MVP.

API không trả raw payload mặc định. Endpoint/operator tooling để xem raw event
nếu cần phải có phân quyền và planner riêng.

## 6. Xử lý sự kiện

Public service nhận tối thiểu:

- station/EVSE/connector identity đã resolve;
- transaction ID, event type, timestamp, `seqNo`;
- trigger reason, charging state, stopped reason;
- id token reference nếu có;
- meter values và raw envelope.

Quy tắc:

1. `Started`: create session hoặc idempotently cập nhật pending session.
2. `Updated`: cập nhật snapshot cuối và insert sample mới; không tạo session mồ
   côi trừ khi policy recovery đã được chốt.
3. `Ended`: lưu meter cuối, chuyển `ending`; không hoàn tác nếu mất kết nối.
4. Reconciliation worker chỉ chốt `completed` sau khi đủ nguồn dữ liệu hoặc
   timeout cấu hình; mỗi session transition phải idempotent.
5. Lỗi validation/message không hợp lệ được phản hồi/log tại OCPP boundary; lỗi
   DB rollback event và làm connection dừng theo policy gateway.

Không dùng `except Exception` ngoài task/process boundary. Structured logging
phải có station ID, transaction ID, seqNo và session ID khi đã resolve.

## 7. Phân rã triển khai

1. Chốt mapping vehicle/id token và reconciliation timeout.
2. Tạo types, exceptions, model và migration cho session/event/meter values.
3. Tạo event input contract thuần Python và public ingestion service.
4. Implement repository idempotency, state transition và meter normalization.
5. Nối handler TransactionEvent/MeterValues từ `charging_stations`.
6. Implement reconciliation worker bằng shared session factory.
7. Tạo schemas, query repository, service và read-only REST router.
8. Đăng ký router trong API.
9. Chạy Black, isort, Ruff, mypy, compile và review import boundary.
10. Smoke/integration test event normal, duplicate, out-of-order, reconnect,
    missing Ended, mất kết nối và hai phiên đồng thời trên hai EVSE.

## 8. Tiêu chí nghiệm thu

- [ ] Session được định danh bằng UUID nội bộ và unique theo station +
      OCPP transaction ID.
- [ ] State transition idempotent, không lùi khi event duplicate/out-of-order.
- [ ] Meter values gắn đúng session và lưu UTC/đơn vị có contract.
- [ ] Ended/mất kết nối không xóa hoặc hoàn tác phiên.
- [ ] Active/detail/history API lọc đúng station/EVSE/connector/time.
- [ ] Hai EVSE độc lập có thể có hai active session; cùng một EVSE không nhận
      phiên thứ hai nếu topology không cho phép.
- [ ] Không có import nội bộ từ `charging_stations`.
- [ ] Không có remote command, billing hoặc payment trong source MVP.
- [ ] Static checks và smoke/integration test được ghi nhận kết quả.

## 9. Thông tin cần chốt trước bước triển khai

- cách liên kết id token/OCPP transaction với `vehicle_id`;
- khoảng thời gian chờ telemetry cuối từ xe;
- danh sách measurand/unit mà firmware thực tế gửi;
- topology hai súng và khả năng chạy đồng thời;
- hành vi của trụ khi offline: có buffer và gửi lại event với `seqNo` cũ không.
