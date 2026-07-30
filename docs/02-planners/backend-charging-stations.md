# Planner: Backend Charging Stations (AD-03)

> Mã chức năng: AD-03; đối chiếu `feature-list.md` mục 3.4 và 4.2  
> Trạng thái: 📋 Dự kiến  
> Ngày tạo: 2026-07-31

## 1. Mục tiêu

Xây dựng backend quản lý hồ sơ trụ sạc và giám sát trạng thái thiết bị qua OCPP
2.0.1. Domain `charging_stations` sở hữu Charging Station, EVSE, Connector,
kết nối WebSocket và lịch sử trạng thái; không sở hữu vòng đời phiên sạc.

Phạm vi MVP:

- CRUD và soft delete Charging Station;
- quản lý topology `ChargingStation → EVSE → Connector`;
- OCPP 2.0.1 CSMS nhận kết nối và các message tối thiểu phục vụ đăng ký, heartbeat,
  trạng thái và dữ liệu đo;
- API đọc trạng thái hiện tại, topology và lịch sử trạng thái;
- phát sự kiện đã chuẩn hóa sang public service của `charging_sessions`.

Không thuộc MVP: Web Portal, OCPP 1.6/2.1, remote start/stop, smart charging,
reservation, firmware, certificate management, billing và payment.

## 2. Quyết định kiến trúc

### 2.1. Ranh giới domain

- Package: `backend/app/domains/charging_stations/`.
- OCPP gateway đặt tại `charging_stations/ocpp/` và chạy process riêng.
- REST router và OCPP entrypoint đều là entry boundary, tự sở hữu session và
  transaction; service/repository không commit hoặc rollback.
- Payload/dataclass từ `ocpp.v201` chỉ tồn tại trong adapter `ocpp/`. Public
  service nhận command/event thuần Python để tránh làm lan contract giao thức.
- OCPP adapter resolve internal identity rồi gọi public function trong
  `charging_sessions/service.py`. Domain phiên không gọi ngược và không import
  model, repository hoặc adapter OCPP nội bộ của domain trụ.

### 2.2. Phiên bản OCPP

- Chỉ negotiate WebSocket subprotocol `ocpp2.0.1`.
- Dùng module `ocpp.v201` của `python-ocpp`; pin dependency bằng `uv` tại bước
  triển khai sau khi kiểm tra compatibility với Python 3.12.
- Không xây abstraction đa phiên bản trong MVP. Nếu đổi OCPP, thay adapter và
  mapping event; schema nghiệp vụ/API phải giữ ổn định nếu semantics tương đương.

### 2.3. Hai súng sạc và topology

Không hard-code số súng vào schema Charging Station. Mô hình hỗ trợ:

```text
ChargingStation (OCPP identity)
└── EVSE 1..N
    └── Connector 1..N
```

Hai topology phần cứng có thể cùng được mô tả là “trụ có 2 súng”:

1. Hai EVSE độc lập, mỗi EVSE có một connector: có thể hỗ trợ hai xe sạc đồng
   thời nếu phần công suất cho phép.
2. Một EVSE có hai connector: thường là hai lựa chọn đầu nối của cùng một điểm
   cấp điện; không mặc định cho phép hai transaction đồng thời.

Database hỗ trợ cả hai. Trước integration test với thiết bị thật phải lấy tài
liệu nhà sản xuất hoặc OCPP device report để xác nhận `evseId`, `connectorId`,
khả năng sạc đồng thời và cách chia công suất. Dữ liệu seed ban đầu không được
tự suy diễn topology chỉ từ số lượng súng.

## 3. Thiết kế dữ liệu

### 3.1. `charging_stations`

- `charging_station_id`: UUID internal primary key.
- `ocpp_identity`: business key unique dùng trong WebSocket URL.
- `name`, `serial_number`, `vendor`, `model`, `firmware_version`: metadata.
- `location`: `geography(Point, 4326)` theo convention PostGIS; API nhận/trả
  latitude/longitude và adapter schema chịu trách nhiệm chuyển đổi.
- `administrative_status`: `active | inactive | maintenance`.
- `connection_status`: `online | offline | unknown`, là snapshot dẫn xuất.
- `last_seen_at`: UTC timezone-aware, nullable.
- `created_at`, `updated_at`, `deleted_at`: UTC timezone-aware.

Soft delete không xóa EVSE, connector, trạng thái lịch sử hoặc phiên sạc.
OCPP identity của bản ghi đã xóa không được tự động tái sử dụng nếu chưa có
quyết định provisioning/audit riêng.

### 3.2. `charging_evses`

- `charging_evse_id`: UUID internal primary key.
- `charging_station_id`: FK đến internal ID của station.
- `ocpp_evse_id`: số nguyên dương, unique trong một station.
- `status`: trạng thái hiện tại đã chuẩn hóa từ OCPP.
- `availability`: trạng thái vận hành do hệ thống quản lý.
- `max_power_kw`: nullable, không tự suy diễn từ meter value.
- timestamps timezone-aware.

Unique constraint: `(charging_station_id, ocpp_evse_id)`.

### 3.3. `charging_connectors`

- `charging_connector_id`: UUID internal primary key.
- `charging_evse_id`: FK đến internal ID của EVSE.
- `ocpp_connector_id`: số nguyên dương, unique trong một EVSE.
- `connector_type`: kiểu đầu nối theo contract OCPP/thiết bị.
- `status`: snapshot hiện tại.
- timestamps timezone-aware.

Unique constraint: `(charging_evse_id, ocpp_connector_id)`.

### 3.4. `charging_station_status_events`

Bảng time-series/hypertable lưu lịch sử trạng thái:

- internal identity phù hợp constraint TimescaleDB;
- `charging_station_id`, `charging_evse_id?`, `charging_connector_id?`;
- `source_action`: `StatusNotification | NotifyEvent`;
- `status?`: connector status đã chuẩn hóa;
- `event_id?`, `severity?`, `component?`, `variable?`, `event_code?`: thông tin
  sự cố khi nguồn là `NotifyEvent`;
- `recorded_at`, `received_at` UTC timezone-aware;
- `raw_payload` JSONB phục vụ trace.

Snapshot trên bảng station/EVSE/connector và status event phải được cập nhật
atomic trong cùng transaction của một OCPP message.

## 4. OCPP 2.0.1 contract MVP

Gateway tối thiểu xử lý:

- `BootNotification`: nhận diện station, cập nhật metadata và trả interval;
- `Heartbeat`: cập nhật `last_seen_at`;
- `StatusNotification`: cập nhật connector/EVSE snapshot và ghi event;
- `NotifyEvent`: lưu sự kiện kỹ thuật/sự cố để giám sát; việc sinh alert thuộc
  domain `notifications`, nằm ngoài planner này;
- `TransactionEvent`: chuyển event đã validate/normalize sang domain
  `charging_sessions`;
- meter values nằm trong `TransactionEvent` và `MeterValues` khi thiết bị gửi:
  chuyển sample gắn transaction sang `charging_sessions`, sample cấp station
  không thuộc transaction được lưu theo contract telemetry trụ nếu có nhu cầu
  đã xác nhận;
- phản hồi unsupported cho message ngoài scope theo đúng OCPP, không âm thầm bỏ.

Quy tắc kết nối:

- WebSocket path chứa `ocpp_identity`; identity không tồn tại, inactive hoặc
  soft deleted bị từ chối theo chính sách provisioning đã triển khai;
- một process sở hữu connect/run/stop và registry connection in-memory;
- reconnect cùng identity phải có policy thay thế connection cũ rõ ràng;
- offline được suy ra từ mất WebSocket hoặc heartbeat timeout cấu hình được,
  không hard-code;
- lỗi DB rollback message hiện tại, log traceback và đóng connection để thiết bị
  reconnect; không retry/DLQ trong MVP nếu chưa được chốt riêng.

## 5. REST API

Prefix: `/api/v1/charging-stations`.

- `POST /charging-stations`: tạo station và topology khai báo ban đầu.
- `GET /charging-stations`: pagination; filter administrative/connection status.
- `GET /charging-stations/{charging_station_id}`: metadata, EVSE và connector.
- `PATCH /charging-stations/{charging_station_id}`: partial update, lọc `None`
  theo convention; không đổi internal ID.
- `DELETE /charging-stations/{charging_station_id}`: soft delete, trả `204`;
  từ chối nếu có phiên active theo public service của `charging_sessions`.
- `GET /charging-stations/{charging_station_id}/status`: snapshot hiện tại.
- `GET /charging-stations/{charging_station_id}/status-history`: filter khoảng
  UTC bắt buộc, pagination/cursor theo `recorded_at`.

Endpoint chi tiết topology có thể nằm trong create/update station hoặc các
endpoint EVSE/connector riêng; phải chốt contract trước khi code, không cho
update topology làm mồ côi session/history.

## 6. Phân rã triển khai

1. Rà schema hiện có, xác nhận topology thiết bị thật và OCPP identity.
2. Thêm dependency `python-ocpp` bằng `uv` sau khi kiểm tra Python 3.12.
3. Tạo types, models và migration cho station/EVSE/connector/status events.
4. Tạo schemas, repository, exceptions, service và CRUD/status REST router.
5. Tạo OCPP 2.0.1 server, connection registry và graceful shutdown.
6. Implement BootNotification, Heartbeat, StatusNotification và NotifyEvent.
7. Định nghĩa event contract thuần Python và nối TransactionEvent/MeterValues
   sang `charging_sessions.service`.
8. Đăng ký REST router và tạo entrypoint process riêng.
9. Chạy Black, isort, Ruff, mypy, compile và review import boundary.
10. Smoke/integration test với OCPP client simulator rồi với trụ thật.

Mỗi bước triển khai phải cập nhật kết quả thực tế vào planner; thành phần chắc
chắn cần nhưng hoãn phải ghi vào `future.md`, không để TODO trong source.

## 7. Tiêu chí nghiệm thu

- [ ] CRUD station dùng UUID nội bộ và soft delete đúng contract.
- [ ] Một station quản lý được N EVSE và N connector, không hard-code hai súng.
- [ ] Chỉ client negotiate `ocpp2.0.1` và identity hợp lệ mới kết nối được.
- [ ] Boot/heartbeat/status/fault event cập nhật snapshot và history đúng
      transaction.
- [ ] Mất kết nối chuyển station offline theo timeout đã cấu hình.
- [ ] TransactionEvent được chuyển qua public service, không import chéo nội bộ.
- [ ] Query status/history tận dụng index/hypertable và timestamp UTC.
- [ ] Topology hai súng, khả năng sạc đồng thời và chia công suất đã được xác nhận
      với thiết bị thật.
- [ ] Static checks và smoke/integration test được ghi nhận kết quả.

## 8. Thông tin cần có trước khi tích hợp thiết bị thật

- OCPP WebSocket identity và cơ chế credential/TLS của từng trụ;
- topology chính xác: số EVSE, connector trên mỗi EVSE và ID do firmware gửi;
- hai súng có sạc đồng thời không, tổng/công suất riêng từng súng;
- danh sách connector type và measurand/unit thực tế;
- heartbeat interval, sample interval và hành vi buffer khi mất mạng.

## 9. Tài liệu giao thức tham chiếu

- [OCPP 2.0.1 JSON schemas](https://ocpp-spec.org/schemas/v2.0.1/)
- [OCPP 2.x numbering và transaction identity](https://ocpp-spec.org/docs/ocpp_2_0/architecture/numbering/)
- [`python-ocpp` và các phiên bản được hỗ trợ](https://github.com/mobilityhouse/ocpp)
