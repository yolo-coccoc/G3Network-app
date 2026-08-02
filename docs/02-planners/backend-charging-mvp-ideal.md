# Planner: Charging backend MVP trong điều kiện lý tưởng

> Mã chức năng: AD-03 và lifecycle cơ bản của S-02
>
> Trạng thái: 📋 Planner rút gọn thay thế cho phạm vi triển khai MVP tiếp theo
>
> Ngày cập nhật: 2026-08-02

Planner này được viết lại từ `backend-charging.md` cho một MVP local/demo có
điều kiện vận hành lý tưởng. Planner cũ vẫn được giữ nguyên để tham chiếu đầy
đủ các nhánh production đã từng được thiết kế.

## 1. Giả định cố định của MVP lý tưởng

- Station, EVSE và connector đã được pre-provision trước khi chạy simulator.
- Tất cả topology luôn `active` và luôn online trong suốt phiên chạy.
- Một station chỉ có một WebSocket ổn định trong một process gateway.
- Một phiên luôn đi theo đúng thứ tự `Started → Updated/MeterValues → Ended`.
- Không có mất kết nối, reconnect, timeout, retry, duplicate, conflict hoặc
  out-of-order message.
- Mọi TransactionEvent và MeterValues hợp lệ; input sai có thể fail-fast.
- Không cần authorization, remote control, pricing, payment, debt hoặc driver/
  vehicle policy.

Các giả định này chỉ dành cho local MVP. Khi bất kỳ giả định nào không còn đúng,
phải mở lại phần tương ứng trong `future.md` và planner production cũ.

## 2. Ranh giới active

```text
Station simulator ⇄ OCPP 2.0.1 gateway → charging_sessions
```

`charging_stations` chỉ giữ topology tối thiểu để resolve:

- `station_id` ↔ `ocpp_identity`;
- `evse_id` ↔ `ocpp_evse_id`;
- `connector_id` ↔ `ocpp_connector_id`.

Gateway chỉ validate handshake, resolve identity và chuyển primitive values sang
public service của `charging_sessions`. Gateway không sở hữu retry, registry
reconnect hoặc offline detector.

## 3. Sáu bảng active

MVP giữ sáu bảng; bảng technical status history bị loại khỏi luồng active vì
thiết bị được giả định luôn online/active.

### `charging_stations`

Giữ `station_id`, `ocpp_identity`, `display_name` và timestamps phục vụ
pre-provision/resolve. Metadata nhà sản xuất, vị trí, firmware và trạng thái
connection không tham gia lifecycle phiên.

### `charging_evses`

Giữ `evse_id`, `station_id`, `ocpp_evse_id` và timestamps.

### `charging_connectors`

Giữ `connector_id`, `evse_id`, `ocpp_connector_id` và timestamps.

### `charging_sessions`

Giữ:

- internal IDs của station/EVSE/connector;
- `ocpp_transaction_id`;
- `status`: chỉ `active | completed`;
- `started_at`, `ended_at`;
- `meter_start_wh`, `meter_end_wh`, `energy_delivered_wh`;
- timestamps tạo/cập nhật.

Không lưu state `pending`, `ending`, `interrupted`, reconciliation status,
ordering cache hoặc error message.

### `charging_session_events`

Chỉ lưu `event_id`, `event_occurred_at`, `session_id` và `event_type` với ba
giá trị `Started | Updated | Ended`. Không lưu `seq_no`, end reason,
idempotency key, received time hoặc raw payload.

### `charging_session_meter_values`

Chỉ lưu `meter_value_id`, `sampled_at`, `session_id` và `value_wh`. MVP chỉ có
một measurand năng lượng canonical là Wh.

## 4. Luồng nghiệp vụ duy nhất

### Started

`TransactionEvent Started` tạo một `charging_sessions` với status `active`,
ghi meter đầu phiên nếu có và append một event `Started`.

### Updated/MeterValues

`Updated` append event. `MeterValues` append sample và cập nhật
`meter_end_wh`/`energy_delivered_wh` theo thứ tự nhận được.

### Ended

`Ended` cập nhật `ended_at`, meter cuối phiên, energy delivered, chuyển status
thẳng sang `completed` và append event `Ended`.

Không có nhánh xử lý duplicate, conflict, retry, interruption hoặc unknown
transaction. Nếu input không hợp lệ, operation fail và transaction boundary
rollback toàn bộ operation.

## 5. Public service contract rút gọn

```text
ingest_transaction_event(
    db,
    station_id,
    evse_id,
    connector_id,
    transaction_id,
    event_type,
    event_occurred_at,
    meter_start_wh,
    meter_end_wh,
)

ingest_meter_values(
    db,
    session_id,
    samples: Sequence[{sampled_at, value_wh}],
)
```

Service và repository không commit/rollback. Caller vẫn sở hữu một transaction
atomic cho từng TransactionEvent hoặc batch MeterValues.

## 6. OCPP gateway và simulator

- Gateway chỉ negotiate `ocpp2.0.1`, validate `/ocpp/{ocpp_identity}` và
  station đã provision.
- Không giữ `ConnectionRegistry`, không thay connection cũ khi reconnect và
  không có offline timeout.
- Simulator chỉ cần connect một station hợp lệ rồi giữ connection trong lúc
  test handshake. Simulator đầy đủ TransactionEvent/MeterValues sẽ được bổ sung
  trong bước E2E sau khi public service rút gọn hoàn tất.

## 7. Thứ tự triển khai

1. Tạo migration rút gọn schema, giữ migration cũ bất biến.
2. Comment model/enum/helper phục vụ status history, interruption,
   idempotency, ordering và reconciliation.
3. Implement service/repository happy path cho Started, Updated, MeterValues,
   Ended.
4. Comment nhánh reconnect/failure trong OCPP gateway, giữ handshake cơ bản.
5. Viết simulator E2E happy path và API monitoring tối thiểu.
6. Chạy compile, Black, isort, Ruff, mypy và migration upgrade/downgrade.

## 8. Ngoài phạm vi và đường quay lại

Toàn bộ reliability/production path của planner cũ không bị xóa. Source bị
loại khỏi active path phải giữ lại dưới dạng comment có lý do; chi tiết các phần
hoãn nằm trong `docs/01-requirements/future.md`.
