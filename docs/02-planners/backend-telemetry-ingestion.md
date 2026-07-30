# Planner: Backend Telemetry Ingestion (AD-02, FM-01, FM-02)

> Mã chức năng: AD-02 (Nhận dữ liệu thời gian thực), FM-01 (Dashboard realtime), FM-02 (Lịch sử vị trí/trạng thái)
> Trạng thái: 🚧 Đang thực hiện — bước 0-15 đã triển khai theo scope MVP; bước 16 chuyển luồng active sang xử lý từng message, còn batch path được giữ cho phase tương lai
> Ngày tạo: 2026-07-24
> Rà soát gần nhất: 2026-07-30

---

## Tổng quan

Xây dựng hệ thống ingest dữ liệu telemetry từ xe tải điện theo mô hình **xử lý từng message**:

**Luồng dữ liệu:**
```
Telematic Device → MQTT Broker (EMQX) → Backend Consumer → Message Queue → Message Worker → PostgreSQL (TimescaleDB)
```

**Nguyên lý MVP hiện tại:**
- Telematic publish message liên tục (mỗi 5-10 giây)
- Backend consume message và đưa vào in-memory queue
- Message worker lấy từng message và xử lý ngay khi có trong queue
- Mỗi message chạy trong một transaction riêng để giảm độ trễ và cô lập lỗi

**Batch path được giữ lại:**
- `batch_worker.py`, `process_batch()`, batch lookup và bulk insert không bị xóa
- Chưa dùng trong entrypoint MVP; việc bật lại phải được benchmark và chốt lại
  semantics transaction/backpressure trước

**Phạm vi:**
- Backend Python async (MQTT consumer + message worker + process entrypoint tối
  giản; không có HTTP runtime/health server trong MVP hiện tại)
- EMQX broker
- TimescaleDB hypertable
- Chưa bao gồm: API query telemetry, frontend dashboard

**Giả định và giới hạn MVP:**
- MQTT sử dụng QoS 0 (fire-and-forget)
- Giả định message được gửi và nhận một cách lý tưởng
- Không xử lý retry
- Không xử lý duplicate detection nâng cao
- Không có persistent queue
- Không có dead-letter queue (DLQ)
- Queue đầy được log warning và drop message; không retry/persist
- Không bảo đảm zero data loss khi process hoặc database gặp lỗi
- Không có metrics/counter trong ingestion MVP hiện tại; chỉ dùng structured log
- Structured log chỉ xuất `stderr`, chưa có log shipping/retention/alert
- Shutdown không drain queue; message còn trong RAM được phép mất
- Các lớp reliability sẽ được bổ sung ở phase sau

Phạm vi MVP tập trung vào việc chứng minh luồng:
```
Simulator → EMQX → MQTT consumer → asyncio.Queue → message worker → TimescaleDB
```

---

## Kiến trúc hiện tại theo AGENTS.md

```
backend/
├── app/
│   ├── domains/
│   │   ├── telemetry/
│   │   │   ├── models.py           # Telematic, VehicleTelemetry (TimescaleDB)
│   │   │   ├── repository.py       # Single insert/lookup và batch path tương lai
│   │   │   ├── schemas.py          # MQTT payload validation
│   │   │   ├── service.py          # Single-message và batch processing logic
│   │   │   └── ingestion/
│   │   │       ├── mqtt_consumer.py    # MQTT client, message handler
│   │   │       ├── message_worker.py   # Async single-message processor hiện tại
│   │   │       ├── batch_worker.py     # Batch processor giữ cho phase tương lai
│   │   │       └── entrypoint.py       # Process entrypoint tối giản
│   │   └── vehicles/
│   │       └── models.py           # Bảng vehicles được FK tham chiếu
│   ├── api/
│   │   └── main.py                 # API process riêng, không chạy MQTT consumer
│   └── libs/
│       ├── common/
│       │   ├── config.py           # Settings dùng chung
│       │   └── logging.py          # JSON structured logging
│       └── db/
│           ├── base.py
│           └── session.py          # Shared engine/session factory theo process
├── pyproject.toml
└── uv.lock

infra/
└── docker-compose.yml              # Chỉ db + broker trong development
```

**Lưu ý về ranh giới domain:**
- Ingestion, service, repository, schema và model đều thuộc cùng domain
  `telemetry`, nên được phép gọi/import trực tiếp nhau.
- Telemetry repository chỉ query model thuộc chính domain telemetry. Mapping
  `vehicle_id` được lấy từ bảng `telematics`; không query chéo vehicles
  repository/model trong luồng batch.
- Foreign key database từ `telematics`/`vehicle_telemetry` tới `vehicles` bảo vệ
  tính toàn vẹn ở persistence layer.
- Nếu sau này telemetry cần business validation từ vehicles, chỉ được gọi public
  API trong `vehicles/service.py`.

## Quy ước dùng planner này làm tài liệu mẫu

Mỗi bước phải ghi rõ:

1. **Mục tiêu và phạm vi**: kết quả cần đạt, phần không thuộc bước.
2. **Contract/quyết định**: schema, lifecycle, transaction, failure behavior và
   ownership của tài nguyên.
3. **File thay đổi**: không tạo placeholder chỉ để khớp cây thư mục.
4. **Kiểm tra**: tách static check, smoke test và integration/E2E.
5. **Kết quả thực tế**: implementation cuối cùng có thể khác prompt ban đầu;
   ghi rõ quyết định mới nhất và giới hạn còn lại.
6. **Future**: mọi thành phần chắc chắn cần nhưng hoãn phải ghi vào
   `docs/01-requirements/future.md`, không để TODO trong source.

---

## Danh sách bước thực hiện

### Bước 0: Nghiên cứu đặc tả chức năng

**Mục tiêu:** Hiểu rõ yêu cầu nghiệp vụ và kỹ thuật

**Prompt:**
```
Đọc và tóm tắt các file sau:
1. docs/01-requirements/feature-list.md - tìm các mục AD-02, FM-01, FM-02

Trả lời các câu hỏi:
- Dữ liệu telemetry gồm những trường nào? (GPS, SOC, speed, voltage...)
- Tần suất gửi dữ liệu từ xe?
- Có cần lưu trữ bao lâu?
- Có cần real-time alert không? (nếu có, sẽ làm ở phase sau)
```

**Kiểm tra:**
- [x] Đã chốt contract các trường dữ liệu cần thu thập trong `mqtt-spec.md`
- [x] Đã ghi rõ giả định tần suất 5-10 giây/message cho MVP
- [x] Đã xác định phụ thuộc persistence với vehicles và hạ tầng DB/EMQX
- [x] Đã ghi nhận những thông tin chưa có thay vì tự đặt yêu cầu

**Kết quả/Quyết định:**

- Payload MVP gồm định danh message/device, thời gian ghi nhận, GPS, trạng thái
  chuyển động, pin, động cơ, tín hiệu và mã lỗi.
- Tần suất 5-10 giây/message là giả định thiết kế của planner/MQTT spec, chưa
  phải SLA hoặc volume đã đo từ thiết bị thật.
- Chưa có yêu cầu retention, số lượng xe cực đại, peak throughput hoặc chính sách
  TimescaleDB compression. Không dùng các con số chưa xác nhận làm tiêu chí
  nghiệm thu.
- Realtime alert, API query và dashboard dùng dữ liệu telemetry nhưng nằm ngoài
  phạm vi ingestion MVP.
- `feature-list.md` hiện mô tả chức năng theo mục/actor nhưng không còn dùng trực
  tiếp các mã `AD-02`, `FM-01`, `FM-02`; planner giữ mã để tương thích với lịch
  sử tài liệu và cần đối chiếu theo nội dung chức năng thực tế.

---

### Bước 1: Thiết kế bảng telematics

**Mục tiêu:** Tạo bảng quản lý thiết bị telematics gắn trên xe

**Prompt:**
```
Thiết kế bảng telematics trong backend/app/domains/telemetry/models.py:

Bảng telematics:
- telematic_id: UUID primary key
- telematic_serial: VARCHAR(50), unique, not null (mã vật lý trên thiết bị)
- vehicle_id: UUID foreign key → vehicles.vehicle_id (nullable, có thể gán sau)
- status: ENUM ('active', 'inactive', 'maintenance') not null
- firmware_version: VARCHAR(50), nullable
- last_seen_at: TIMESTAMPTZ, nullable (cập nhật khi nhận message)
- created_at: TIMESTAMPTZ not null
- updated_at: TIMESTAMPTZ not null

Lưu ý:
- Dùng async SQLAlchemy 2.0 với Mapped và mapped_column
- Import base từ libs.db.base
- Thêm indexes cho serial, vehicle_id
- Thêm UNIQUE constraint cho vehicle_id (mỗi xe chỉ có tối đa 1 telematic)
- Thêm docstring
- Trong code Python, dùng tên rõ nghĩa: telematic_id, telematic_serial
```

**Kiểm tra:**
- [x] Model không có lỗi syntax
- [x] Có foreign key đến vehicles
- [x] Có index cho serial và vehicle_id
- [x] Có UNIQUE constraint cho vehicle_id
- [x] Timestamp dùng `DateTime(timezone=True)` và UTC timezone-aware
- [x] Model dùng shared `Base`, không tạo metadata/engine riêng

**Kết quả/Quyết định:**

- Model `Telematic` nằm trong domain telemetry vì thiết bị và mapping này phục vụ
  trực tiếp ingestion.
- Tên code/DB hiện dùng `telematic_id`, `telematic_serial` và
  `vehicles.vehicle_id`, thay cho tên `id`, `serial`, `vehicles.id` trong prompt
  ban đầu.
- Foreign key dùng `ON DELETE SET NULL`; unique constraint trên `vehicle_id` bảo
  đảm một xe có tối đa một telematic, còn nhiều row `NULL` vẫn hợp lệ.
- API provisioning/gán hoặc tháo thiết bị chưa được triển khai trong bước này.

---

### Bước 2: Thiết kế bảng vehicle_telemetry (TimescaleDB)

**Mục tiêu:** Tạo hypertable lưu dữ liệu telemetry time-series

**Prompt:**
```
Thiết kế bảng vehicle_telemetry trong backend/app/domains/telemetry/models.py:

Bảng vehicle_telemetry:
- message_id: BIGINT GENERATED BY DEFAULT AS IDENTITY (PK)
- message_uuid: UUID not null (do telematic tạo)
- telematic_id: UUID not null (foreign key → telematics.telematic_id)
- telematic_serial: VARCHAR(50) not null (lưu lại để debug, audit)
- vehicle_id: UUID not null (foreign key → vehicles.vehicle_id)
- recorded_at: TIMESTAMPTZ not null (thời điểm telematic ghi nhận)
- received_at: TIMESTAMPTZ not null (thời điểm backend nhận)
- latitude: DOUBLE PRECISION
- longitude: DOUBLE PRECISION
- speed: DOUBLE PRECISION (km/h)
- heading: DOUBLE PRECISION, nullable (độ, 0-360)
- soc: DOUBLE PRECISION (State of Charge, %)
- battery_voltage: DOUBLE PRECISION, nullable (V)
- battery_current: DOUBLE PRECISION, nullable (A)
- battery_temperature: DOUBLE PRECISION, nullable (°C)
- motor_temperature: DOUBLE PRECISION, nullable (°C)
- odometer: DOUBLE PRECISION, nullable (km)
- signal_strength: INTEGER, nullable (dBm)
- error_codes: JSONB, nullable
- raw_payload: JSONB not null (lưu dữ liệu gốc từ telematic)

Lưu ý:
- Dùng TimescaleDB hypertable (partition by recorded_at)
- Chunk interval: 1 day
- Primary key: (message_id, recorded_at) - phải chứa partition key
- Unique constraint: (telematic_id, recorded_at) - một telematic chỉ có 1 message tại 1 thời điểm
- Index trên (vehicle_id, recorded_at DESC)
- Index trên message_uuid (để trace, chưa unique trong MVP)
- Không dùng UUID làm PK (TimescaleDB khuyến nghị BIGINT)
- Thêm docstring giải thích từng trường
- heading nullable vì không phải telematic nào cũng cung cấp
- raw_payload lưu toàn bộ JSON gốc để debug và reprocessing
```

**Kiểm tra:**
- [x] Model không có lỗi syntax
- [x] Có docstring đầy đủ
- [x] Primary key chứa recorded_at
- [x] Unique constraint đúng nghiệp vụ
- [x] Có raw_payload JSONB
- [x] Có foreign key/index phục vụ trace và truy vấn theo xe/thời gian
- [x] Timestamp lưu UTC timezone-aware

**Kết quả/Quyết định:**

- Hypertable partition theo `recorded_at`, chunk interval một ngày.
- Composite primary key là `(message_id, recorded_at)` để chứa partition key.
- `message_uuid` chỉ có index, chưa unique trong MVP; duplicate detection nâng
  cao đã được ghi trong `future.md`.
- `latitude`/`longitude` dùng `DOUBLE PRECISION`; nâng cấp PostGIS được hoãn và
  ghi trong `future.md`.
- `raw_payload` giữ object JSON đã parse trước khi Pydantic normalize/drop field,
  phục vụ audit và reprocessing sau này.
- `speed` và `heading` nullable để chấp nhận thiết bị không gửi trạng thái chuyển
  động.

---

### Bước 3: Tạo Alembic migrations

**Mục tiêu:** Tạo migration cho `telematics`, `vehicle_telemetry` và hypertable
TimescaleDB

**Prompt:**
```
Tạo Alembic migration trong `backend/app/libs/db/migrations/versions/`:

Migration 1: Create telematics table
- Tạo bảng telematics với đầy đủ constraints, indexes
- Thêm foreign key đến vehicles
- Thêm UNIQUE constraint cho vehicle_id

Migration 2: Create vehicle_telemetry hypertable
- Tạo bảng vehicle_telemetry
- Chuyển thành hypertable: SELECT create_hypertable('vehicle_telemetry', 'recorded_at', chunk_time_interval => INTERVAL '1 day');
- Tạo indexes
- Thêm unique constraint (telematic_id, recorded_at)
- Tạo index cho message_uuid

Lưu ý:
- Import models trong env.py
- Dùng --autogenerate nhưng kiểm tra kỹ migration file
- Test trên database đang chạy
```

**Lệnh chạy:**
```bash
# Tạo migration
cd backend
uv run alembic revision --autogenerate -m "create telematics table"
uv run alembic revision --autogenerate -m "create vehicle_telemetry hypertable"

# Chạy migration
uv run alembic upgrade head

# Kiểm tra
docker exec g3network-db psql -U g3network -d g3network -c "\d telematics"
docker exec g3network-db psql -U g3network -d g3network -c "\d vehicle_telemetry"
docker exec g3network-db psql -U g3network -d g3network -c "SELECT hypertable_name FROM timescaledb_information.hypertables;"
```

**Kiểm tra:**
- [x] Migration chạy thành công
- [x] Bảng telematics được tạo
- [x] Bảng vehicle_telemetry là hypertable
- [x] Indexes được tạo đúng
- [x] Unique constraint (telematic_id, recorded_at) tồn tại
- [x] Upgrade/downgrade và timezone của bảng vehicles đã được rà soát

**Kết quả/Quyết định:**

- Migration thực tế không tách đúng hai file như prompt ban đầu:
  - `90df58f189f6_create_vehicles_and_telematics_tables.py` tạo `vehicles` và
    `telematics`.
  - `70cefd03d3d5_create_vehicle_telemetry_hypertable.py` tạo bảng time-series và
    chuyển thành hypertable.
  - `c0f4a8b6e2d1_use_timezone_aware_vehicle_timestamps.py` chuẩn hóa timestamp
    vehicles sang `TIMESTAMPTZ`.
- Alembic `env.py` import trực tiếp model cần thiết để metadata đầy đủ; các
  `__init__.py` không export code.
- Đã từng chạy migration và kiểm tra hypertable trên database thật. Khi audit lại
  planner ngày 2026-07-27 không chạy lại vì phiên làm việc không có quyền Docker
  daemon; trạng thái này không thay đổi kết quả nghiệm thu trước đó.
- `alembic check` với object do PostGIS/TimescaleDB quản lý vẫn được theo dõi
  trong `future.md`.

---

### Bước 4: Chốt MQTT topic và payload schema

**Mục tiêu:** Định nghĩa giao thức giao tiếp giữa telematic và backend

**Prompt:**
```
Tạo file docs/02-planners/mqtt-spec.md với nội dung:

1. MQTT Topics:
   - Publish từ telematic: `g3network/telematics/{telematic_serial}/telemetry`
   - Telematic status: `g3network/telematics/{telematic_serial}/status`
   - Backend command: `g3network/telematics/{telematic_serial}/command` (dành cho sau)

2. Payload Schema (JSON):
   {
     "message_uuid": "497f6eca-6276-4993-bfeb-53cbbbba6f08",
     "telematic_serial": "TBOX-VN-000123",
     "recorded_at": "2026-07-24T10:30:00Z",
     "location": {
       "latitude": 21.0285,
       "longitude": 105.8542
     },
     "vehicle_state": {
       "speed": 45.2,
       "heading": 90.0,
       "odometer": 12345.6
     },
     "battery": {
       "soc": 78.5,
       "voltage": 400.2,
       "current": -15.3,
       "temperature": 35.2
     },
     "motor": {
       "temperature": 42.1
     },
     "signal": {
       "strength": -75
     },
     "errors": ["E001"]
   }

3. QoS Level: 0 (fire-and-forget)

4. Retain: false

Lưu ý:
- Giải thích rõ từng trường
- Nêu rõ đơn vị đo
- Có ví dụ minh họa
- Payload KHÔNG chứa: message_id, telematic_id, vehicle_id, received_at (backend bổ sung sau)
- heading: hướng di chuyển theo góc (0°=Bắc, 90°=Đông, 180°=Nam, 270°=Tây)
```

**Kiểm tra:**
- [x] Topic sử dụng telematic_serial (không dùng vehicle_id)
- [x] Payload có message_uuid (không phải message_id)
- [x] Payload không chứa ID nội bộ (message_id, telematic_id, vehicle_id)
- [x] QoS được cấu hình là 0
- [x] Có ví dụ minh họa
- [x] Field bắt buộc/nullable, range, đơn vị và nguồn timestamp được mô tả

**Kết quả/Quyết định:**

- `docs/02-planners/mqtt-spec.md` là contract giao tiếp nguồn cho bước 5 và 7.
- `recorded_at` do thiết bị cung cấp; backend normalize UTC. `received_at` do
  backend bổ sung khi process batch theo contract MVP hiện tại.
- Topic status/command và ACL trong spec chỉ mô tả hướng mở rộng; consumer MVP chỉ
  subscribe telemetry topic.
- EMQX 5.x không dùng custom `acl.conf` như thiết kế EMQX 4.x cũ. Authentication,
  authorization và đối chiếu serial giữa topic/payload đã được hoãn trong
  `future.md`.
- Retain là `false`; QoS 0 nên không có delivery guarantee.

---

### Bước 5: Viết Pydantic validation schema

**Mục tiêu:** Validate message từ MQTT trước khi đưa vào queue

**Prompt:**
```
Tạo backend/app/domains/telemetry/schemas.py với Pydantic models:

1. LocationData:
   - latitude: float (range -90 to 90)
   - longitude: float (range -180 to 180)

2. VehicleState:
   - speed: float | None (range 0-200)
   - heading: float | None (range 0-360, nullable)
   - odometer: float | None

3. BatteryData:
   - soc: float (range 0-100)
   - voltage: float | None
   - current: float | None
   - temperature: float | None

4. MotorData:
   - temperature: float | None

5. SignalData:
   - strength: int | None

6. TelemetryMessage:
   - message_uuid: UUID
   - telematic_serial: str
   - recorded_at: datetime
   - location: LocationData
   - vehicle_state: VehicleState | None
   - battery: BatteryData
   - motor: MotorData | None
   - signal: SignalData | None
   - errors: list[str] | None

7. TelemetryEnvelope:
   - message: TelemetryMessage đã validate/normalize
   - raw_payload: dict JSON nguyên bản sau parse

Lưu ý:
- Dùng Pydantic v2
- Thêm validation cho các trường có range
- Thêm examples
- Thêm method `to_db_dict(telematic_id, vehicle_id, received_at, raw_payload)` để
  convert sang dict phù hợp với DB model
- heading nullable vì không phải telematic nào cũng cung cấp
```

**Kiểm tra:**
- [x] Schema không có lỗi syntax
- [x] Validation đúng range
- [x] heading có thể null
- [x] Examples hiển thị tốt trong docs
- [x] `recorded_at` bắt buộc có timezone và được normalize UTC
- [x] Raw payload gốc được bảo toàn sau validation

**Kết quả/Quyết định:**

- Dùng Pydantic v2 và `Annotated`/`Field` cho contract range.
- `TelemetryEnvelope` ghép `TelemetryMessage` đã validate với dict JSON gốc,
  tránh reconstruct `raw_payload` từ model đã normalize hoặc loại field.
- Validation diễn ra tại MQTT boundary trước khi queue nhận message. Service nhận
  envelope hợp lệ và chỉ xử lý mapping/conversion nghiệp vụ.
- `to_db_dict()` là phép chuyển đổi thuần, không query DB và không quản lý
  transaction.

---

### Bước 6: Dựng EMQX và kiểm tra publish/subscribe thủ công

**Mục tiêu:** Cài đặt EMQX 5.x broker và test kết nối với QoS 0

**Lưu ý quan trọng về EMQX 5.x:**
- EMQX 5.x **không dùng file `acl.conf`** như EMQX 4.x
- ACL được cấu hình qua Dashboard UI hoặc REST API
- File `acl.conf` mặc định vẫn tồn tại nhưng không được dùng để custom rules
- Đối với MVP, chúng ta sẽ bỏ qua ACL phức tạp và dùng default security

**Prompt:**
```
Cập nhật infra/docker-compose.yml:

1. Thêm service broker (EMQX 5.5):
   - Image: emqx/emqx:5.5
   - Ports: 1883 (MQTT), 18083 (Dashboard)
   - Environment: EMQX_NAME=g3network-broker, EMQX_HOST=0.0.0.0
   - Volumes: broker_data, broker_log (không mount ACL file)

2. Cập nhật `.env.example` ở root:
   - MQTT_HOST=localhost
   - MQTT_PORT=1883
   - MQTT_DASHBOARD_PORT=18083

3. Cập nhật backend/app/libs/common/config.py:
   - Thêm MQTT settings vào Settings class:
     * MQTT_HOST, MQTT_PORT, MQTT_CLIENT_ID
     * MQTT_USERNAME, MQTT_PASSWORD (nullable)
     * MQTT_QOS = 0 (default)
```

**Lệnh chạy:**
```bash
# Khởi động EMQX
docker compose -f infra/docker-compose.yml up -d broker

# Kiểm tra container
docker ps --filter "name=g3network-broker"

# Kiểm tra dashboard
open http://localhost:18083
# Default: admin / public

# Test subscribe (cài mosquitto-clients nếu chưa có)
sudo apt install mosquitto-clients
mosquitto_sub -h localhost -p 1883 -t "g3network/telematics/+/telemetry" -v

# Test publish với QoS 0
mosquitto_pub -h localhost -p 1883 -q 0 -t "g3network/telematics/TBOX-VN-000123/telemetry" -m '{"message_uuid":"497f6eca-6276-4993-bfeb-53cbbbba6f08","telematic_serial":"TBOX-VN-000123","recorded_at":"2026-07-24T10:00:00Z","location":{"latitude":10.76,"longitude":106.66},"battery":{"soc":50.0}}'
```

**Kiểm tra:**
- [x] EMQX service, port, volume và healthcheck đã có trong Compose
- [x] Dashboard được expose tại http://localhost:18083
- [x] Subscribe/publish thủ công với QoS 0 đã từng được xác nhận
- [x] MQTT settings đã được thêm vào config.py và `.env.example`

**Kết quả/Quyết định:**

- Development Compose dùng `emqx/emqx:5.5`, expose MQTT `1883` và dashboard
  `18083`, có persistent data/log volume và healthcheck.
- Không mount `acl.conf`; EMQX 5.x authorization sẽ cấu hình qua Dashboard/REST
  API khi triển khai security phase sau.
- Backend chạy trên host nên dùng `localhost:1883`; không dùng Docker service name
  trong `.env.example`.
- Kiểm tra runtime thủ công đã hoàn thành ở thời điểm triển khai bước 6. Audit
  tài liệu ngày 2026-07-27 không chạy lại container do không có quyền Docker
  daemon.
- Lệnh `sudo apt install` trong prompt chỉ là hướng dẫn môi trường, không phải
  thay đổi repo hoặc điều kiện để source compile.

---

### Bước 7: Viết MQTT client + consumer

**Mục tiêu:** Kết nối đến EMQX và nhận message với QoS 0

**Prompt:**
```
Tạo backend/app/domains/telemetry/ingestion/mqtt_consumer.py:

1. Class MQTTConsumer:
   - __init__(config: MQTTConfig, message_queue: asyncio.Queue)
   - async connect() - kết nối đến broker với QoS 0
   - async subscribe(topic_pattern: str) - subscribe topic
   - async start_consuming() - vòng lặp nhận message
   - async disconnect() - ngắt kết nối

2. Message handling:
   - Parse JSON payload
   - Validate bằng TelemetryMessage schema
   - Nếu hợp lệ: đưa vào asyncio.Queue
   - Nếu không hợp lệ: log warning, bỏ qua (MVP không có DLQ)

3. Error handling (MVP):
   - Log lỗi kết nối và để consumer dừng
   - Không reconnect/retry; ghi nhận reliability nâng cao trong `future.md`

4. Dùng thư viện:
   - gmqtt (async MQTT client) hoặc
   - aiomqtt (wrapper của paho-mqtt)

Lưu ý:
- Mọi I/O phải là async
- Không block trong message handler
- Subscribe với QoS 0
- Thêm docstring và type hints
```

**Kiểm tra:**
- [x] Consumer kết nối được đến EMQX
- [x] Message được parse và validate đúng
- [x] Message hợp lệ được đưa vào queue
- [x] Subscribe với QoS 0
- [x] JSON/Pydantic/MQTT error có failure behavior rõ ràng

**Kết quả/Quyết định:**

- Chọn `aiomqtt`; không cài đồng thời nhiều MQTT client library.
- `connect()` hiện chuẩn bị client/config, còn kết nối network và subscribe thật
  xảy ra khi vào async context trong `start_consuming()`.
- Payload hợp lệ được đóng gói thành `TelemetryEnvelope`; payload JSON hoặc schema
  không hợp lệ được log `WARNING` và skip.
- `MqttError` tại process/task boundary được log kèm traceback rồi raise; MVP
  không reconnect/retry.
- Client, topic, credential và QoS lấy từ settings/constructor.
- Quyết định cập nhật ngày 2026-07-28: bỏ `Metrics`, `is_consuming`,
  `subscribe()` public method và state `_consuming`. Consumer hiện chỉ còn
  `connect()`, `start_consuming()`, `disconnect()` và `_handle_message()`.

---

### Bước 8: Đưa message hợp lệ vào asyncio.Queue

**Mục tiêu:** Tạo queue trung gian giữa consumer và batch worker

**Prompt:**
```
Cập nhật backend/app/domains/telemetry/ingestion/mqtt_consumer.py:

1. Tạo module-level queue:
   message_queue: asyncio.Queue[TelemetryMessage] = asyncio.Queue(maxsize=10000)

2. Trong message handler:
   - Try: message_queue.put_nowait(validated_message)
   - Except QueueFull: log warning, increment metric, drop message

3. Thêm metrics (dùng prometheus-client hoặc simple counter):
   - messages_received_total
   - messages_valid_total
   - messages_invalid_total
   - messages_dropped_total (queue full)

4. Hỗ trợ graceful shutdown:
   - `disconnect()` dừng consuming
   - Entrypoint chịu trách nhiệm nhận SIGTERM, dừng consumer rồi đợi worker drain queue
```

**Kiểm tra:**
- [x] Queue hoạt động đúng
- [x] Queue full được drop có chủ đích và log warning
- [x] Consumer có primitive dừng; orchestration toàn process thuộc bước 14

**Kết quả/Quyết định:**

- Queue chứa `TelemetryEnvelope`, không chỉ `TelemetryMessage`, để giữ
  `raw_payload`.
- Quyết định cập nhật ngày 2026-07-28: bỏ toàn bộ metrics/counter trong
  `mqtt_consumer.py`. Không dùng Prometheus dependency và cũng không giữ counter
  process-local trong MVP.
- Queue đầy không block callback MQTT: message bị drop và ghi `WARNING`.
- Module-level queue hiện là default để các component cũ dùng chung. Bước 14 sẽ
  để entrypoint tạo một queue theo settings và inject cùng instance vào consumer
  và worker, giúp ownership/lifecycle rõ ràng.
- Consumer chỉ cung cấp primitive `disconnect`; entrypoint hủy task còn lại khi
  một task kết thúc trước. Không drain queue trong MVP hiện tại.

---

### Bước 9: Viết batch worker

**Mục tiêu:** Xử lý queue theo batch định kỳ

**Prompt:**
```
Tạo backend/app/domains/telemetry/ingestion/batch_worker.py:

1. Class BatchWorker:
   - __init__(queue: asyncio.Queue, batch_size: int = 100, flush_interval: float = 30.0)
   - async start() - bắt đầu worker
   - async stop() - dừng worker

2. Logic:
   - Mỗi flush_interval giây HOẶC khi queue có đủ batch_size messages:
     + Lấy tối đa batch_size messages từ queue
     + Gọi telemetry.service.process_batch(messages)
     + Log số lượng processed, time taken
   - Nếu DB lỗi: rollback toàn batch, log traceback và để worker dừng
   - MVP không retry và không có dead-letter queue

3. Metrics:
   - batches_processed_total
   - messages_processed_total
   - batch_processing_time_seconds
   - batch_errors_total

4. Graceful shutdown:
   - Khi stop(), xử lý nốt batch hiện tại
   - Đợi tối đa 60 giây

Lưu ý:
- Dùng asyncio.wait_for để timeout
- Thêm docstring và type hints
```

**Kiểm tra:**
- [x] Worker chạy định kỳ đúng interval
- [x] Batch được xử lý khi đủ size
- [x] DB error làm worker dừng, không retry
- [x] Worker không dùng `queue.join()`/`task_done()` trong MVP tối giản
- [x] Mỗi batch chạy trong một transaction atomic

**Kết quả/Quyết định:**

- Batch window bắt đầu khi nhận message đầu tiên và dùng monotonic event-loop
  clock; flush khi đủ size hoặc hết interval.
- Worker dùng `async_session_factory.begin()`: context commit khi thành công,
  rollback khi exception; service/repository không commit/rollback.
- Quyết định cập nhật ngày 2026-07-28: bỏ `BatchMetrics`, `is_running`,
  `wait()`, drain queue, `queue.join()` và `task_done()`.
- Khi shutdown, worker bị cancel ngay; transaction đang chạy rollback theo
  context manager và message còn trong queue RAM được phép mất.
- DB/service error được log bằng `logger.exception()`, raise lại và làm worker
  dừng theo policy MVP.

---

### Bước 10: Viết repository batch lookup và bulk insert

**Mục tiêu:** Insert batch telemetry vào database hiệu quả với batch lookup

**Prompt:**
```
Cập nhật backend/app/domains/telemetry/repository.py:

1. async def get_telematic_mappings(db: AsyncSession, serials: list[str]) -> dict[str, tuple[UUID, UUID]]:
   - Query bảng telematics một lần với WHERE serial IN (...)
   - Trả về dict: {telematic_serial: (telematic_id, vehicle_id)}
   - Dùng SQLAlchemy Core select

2. async def bulk_insert_telemetry(db: AsyncSession, messages: list[dict]) -> int:
   - Dùng SQLAlchemy Core insert (không phải ORM):
     stmt = insert(VehicleTelemetry).values(messages)
     result = await db.execute(stmt)
   - Trả về số rows inserted

3. async def update_telematic_last_seen(db: AsyncSession, telematic_data: list[tuple[UUID, datetime]]) -> None:
   - Update last_seen_at cho nhiều telematics cùng lúc
   - Chỉ update nếu timestamp mới lớn hơn giá trị hiện tại
   - Dùng batch update với CASE WHEN hoặc execute nhiều update

Lưu ý:
- Dùng async session
- Worker entry boundary sở hữu transaction và commit/rollback toàn batch
- Repository/service không gọi commit hoặc rollback
- Bulk insert phải dùng Core API, không dùng ORM add_all (chậm)
- Batch lookup: 1 query cho cả batch, không query từng message
- Thêm docstring
```

**Kiểm tra:**
- [x] Batch lookup hoạt động đúng
- [x] Bulk insert hoạt động
- [x] Cấu trúc query dùng một lookup, một bulk insert và một batch update
- [x] Transaction được commit đúng

**Kết quả/Quyết định:**

- `get_telematic_mappings()` thực hiện một `SELECT ... WHERE serial IN (...)` và
  chỉ trả thiết bị đã gán `vehicle_id`.
- `bulk_insert_telemetry()` dùng PostgreSQL Core `insert(...).values(messages)`,
  không dùng ORM `add_all`.
- `update_telematic_last_seen()` dùng một `UPDATE ... CASE` cho các thiết bị của
  batch.
- Repository không commit/rollback; transaction thực tế được commit bởi worker
  context sau khi service hoàn tất.
- Đã smoke test call count và dữ liệu truyền giữa service/repository. Chưa có
  benchmark đáng tin cậy để khẳng định throughput cho 100+ record hoặc
  messages/second; performance phải đo bằng workload ở môi trường đại diện.

---

### Bước 11: Viết service process_batch

**Mục tiêu:** Business logic xử lý batch message với batch lookup

**Prompt:**
```
Cập nhật backend/app/domains/telemetry/service.py:

1. async def process_batch(db: AsyncSession, messages: Sequence[TelemetryEnvelope]) -> BatchResult:
   - Lấy danh sách telematic_serial duy nhất từ batch
   - Gọi repository.get_telematic_mappings(serials) - 1 query cho cả batch
   - Với mỗi message:
     + Nếu telematic_serial không tồn tại: log warning, skip message
     + Nếu tồn tại: bổ sung telematic_id và vehicle_id
   - Tạo received_at = datetime.now(timezone.utc)
   - Convert message và raw payload sang DB dict
   - Gọi repository.bulk_insert_telemetry()
   - Gọi repository.update_telematic_last_seen() với MAX(received_at) của từng telematic
   - Trả về {"processed": count, "skipped": count, "errors": count}

2. Error handling:
   - Nếu DB error: raise để transaction rollback và batch worker dừng
   - Nếu validation error: log và skip message đó

3. Logging:
   - Log INFO khi batch processed thành công
   - Log WARNING khi có message bị skip
   - Log ERROR khi DB error

Lưu ý:
- Gọi repository, KHÔNG query trực tiếp
- Batch lookup: 1 query cho cả batch, không query từng message
- Không dùng cache trong MVP
- Thêm docstring
```

**Kiểm tra:**
- [x] Batch được xử lý đúng
- [x] Batch lookup hoạt động (1 query cho cả batch)
- [x] Telematic validation hoạt động
- [x] Logging đầy đủ

**Kết quả rà soát (2026-07-27):**
- `process_batch()` xử lý đúng message hợp lệ, skip telematic không tồn tại hoặc
  chưa gán xe, và trả về đủ các metric `processed`, `skipped`, `errors`.
- Danh sách `telematic_serial` được gom duy nhất và gọi
  `repository.get_telematic_mappings()` đúng một lần cho cả batch.
- Pydantic validation được thực hiện tại MQTT consumer trước khi message được đưa
  vào queue; service tiếp tục kiểm tra mapping telematic/vehicle và xử lý lỗi
  chuyển đổi từng message.
- Service ghi log `INFO` cho kết quả batch và `WARNING` cho message bị skip. Lỗi
  database được propagate tới transaction boundary và được batch worker ghi bằng
  `logger.exception()` trước khi dừng worker.
- Smoke test cô lập đã xác nhận batch gồm một message hợp lệ và một serial không
  tồn tại cho kết quả `processed=1`, `skipped=1`, `errors=0`, với đúng một lần
  lookup, bulk insert và update `last_seen_at`.
- Black, isort, Ruff và mypy đều pass trên các file liên quan. Chưa chạy lại
  integration test với PostgreSQL/TimescaleDB trong lượt rà soát này do không có
  quyền truy cập Docker daemon.
- `received_at` hiện được tạo một lần tại thời điểm service xử lý batch, nên mọi
  message trong batch dùng cùng timestamp. Đây là contract MVP hiện tại, không
  phải timestamp chính xác tại lúc MQTT callback nhận từng message.
- Mapping repository lọc thiết bị chưa gán xe, nên service không phân biệt được
  “serial không tồn tại” và “telematic tồn tại nhưng chưa gán”; giới hạn này đã
  được ghi trong `future.md`.

---

### Bước 12: Cập nhật telematic last_seen

**Mục tiêu:** Cập nhật thời điểm telematic hoạt động lần cuối

**Prompt:**
```
Cập nhật backend/app/domains/telemetry/service.py:

1. Trong process_batch(), sau khi bulk insert:
   - Nhóm message theo telematic_id
   - Lấy MAX(received_at) của từng telematic
   - Gọi repository.update_telematic_last_seen([(telematic_id, max_received_at), ...])

2. Repository update:
   - Chỉ update nếu timestamp mới lớn hơn giá trị hiện tại
   - Dùng batch update để tối ưu

Lưu ý:
- Không update từng message riêng lẻ
- Chỉ update với MAX(received_at) của batch
- Thêm docstring
```

**Kiểm tra:**
- [x] last_seen_at được cập nhật đúng
- [x] Chỉ update khi timestamp mới hơn
- [x] Một batch chỉ phát sinh một repository update cho các telematic liên quan

**Kết quả rà soát (2026-07-27):**
- `process_batch()` gom timestamp theo `telematic_id` và chỉ truyền một giá trị
  lớn nhất cho mỗi thiết bị sang `repository.update_telematic_last_seen()` sau
  khi bulk insert thành công.
- Repository dùng một câu `UPDATE` với `CASE WHEN` cho toàn bộ thiết bị trong
  batch; `last_seen_at` chỉ nhận timestamp mới khi giá trị mới lớn hơn giá trị
  hiện tại hoặc giá trị hiện tại là `NULL`.
- Smoke test cô lập với ba message của cùng một telematic xác nhận repository
  update chỉ được gọi một lần và chỉ nhận một tuple cho telematic đó.
- Giới hạn MVP đã được ghi tại mục 14 của `future.md`: câu `UPDATE` hiện vẫn đổi
  `updated_at` và row count có thể tính cả row khớp ID dù `last_seen_at` không
  đổi. Sai lệch này không làm `last_seen_at` lùi thời gian và không chặn bước 12.
- Chưa chạy lại integration/performance test với PostgreSQL/TimescaleDB trong
  lượt rà soát này do không có quyền truy cập Docker daemon.

---

### Bước 13: Thêm logging cơ bản (MVP không có retry/DLQ)

**Mục tiêu:** Logging đầy đủ để debug (MVP không có retry/DLQ)

**Prompt:**
```
Cập nhật backend/app/domains/telemetry/ingestion/batch_worker.py:

1. Logging:
   - Dùng Python logging module
   - Log format: JSON với timestamp, level, message, extra fields
   - Log level: INFO cho normal, WARNING cho skip, ERROR cho failure

2. Error handling (MVP):
   - Nếu DB error: log ERROR, raise exception (batch worker sẽ dừng)
   - Không có retry logic phức tạp trong MVP
   - Không có dead-letter queue trong MVP
   - Các lớp reliability sẽ được bổ sung ở phase sau

3. Metrics (optional):
   - messages_received_total
   - messages_processed_total
   - messages_skipped_total
   - batch_processing_time_seconds

Lưu ý:
- MVP tập trung vào việc chứng minh luồng hoạt động
- Retry, DLQ, persistent queue sẽ được ghi nhận vào future.md
- Thêm docstring
```

**Kiểm tra:**
- [x] Logging đầy đủ
- [x] Error được log đúng level
- [x] Không còn metrics/counter trong ingestion MVP tối giản

**Kết quả thực hiện (2026-07-27):**
- Bổ sung formatter JSON dùng Python standard library với các field chuẩn
  `timestamp`, `level`, `logger`, `message`, các structured `extra` fields và
  traceback khi có exception.
- Batch worker kích hoạt cấu hình logging theo cách idempotent khi bắt đầu chạy.
  Luồng bình thường dùng `INFO`, message bị skip/drop hoặc không hợp lệ dùng
  `WARNING`, lỗi database/process boundary dùng `logger.exception()` ở level
  `ERROR` rồi raise để worker dừng.
- Quyết định cập nhật ngày 2026-07-28: bỏ metrics/counter trong MQTT consumer và
  batch worker. Ingestion MVP chỉ log các event quan trọng và summary batch
  gồm `batch_size`, `processed`, `skipped`, `errors`.
- Không bổ sung retry, DLQ hoặc persistent queue trong phạm vi MVP; các hạng mục
  reliability này tiếp tục được theo dõi trong `future.md`.
- Tại thời điểm triển khai logging ngày 2026-07-27, Black, isort, Ruff và mypy
  đều pass. Sau các thay đổi tối giản ngày 2026-07-28, cần chạy lại static
  checks đầy đủ khi môi trường dependency có `ruff`.
- `configure_logging()` chỉ cấu hình cách xuất các `LogRecord` hiện có, không tự
  sinh business event mới. Handler ghi JSON một dòng ra `stderr`.
- `configure_logging()` hiện được gọi tại entrypoint thay vì trong worker để mọi
  log startup/MQTT/worker/shutdown dùng cùng format.
- Centralized observability và persistent metrics đã được chuyển sang
  `future.md`.

---

### Bước 14: Tách telemetry ingestion thành process riêng

> **Quyết định MVP cập nhật ngày 2026-07-28:** ingestion process được tối giản
> mạnh: bỏ `runtime.py`, `health.py`, HTTP health server, database startup probe,
> graceful drain, `BatchMetrics`, MQTT metrics, `BatchWorker.is_running`,
> `BatchWorker.wait()`, `MQTTConsumer.is_consuming` và `MQTTConsumer.subscribe()`.
> `entrypoint.py` trực tiếp khởi tạo queue RAM, consumer và worker. Khi một task
> kết thúc hoặc nhận SIGINT/SIGTERM, entrypoint cancel task còn lại rồi cleanup.
> Message còn trong queue RAM được phép mất trong phạm vi MVP.

**Mục tiêu:** Chạy telemetry ingestion độc lập với API server bằng process
entrypoint tối giản. Trong môi trường development, process chạy trực tiếp trên
host theo convention của `AGENTS.md`; đóng gói container production chưa nằm
trong phạm vi bước này.

#### 14.1. Phạm vi thay đổi

Tạo:

- `backend/app/domains/telemetry/ingestion/entrypoint.py`

Cập nhật:

- `backend/app/domains/telemetry/ingestion/batch_worker.py`
- `backend/app/domains/telemetry/ingestion/mqtt_consumer.py`
- `backend/app/libs/common/config.py`
- `.env.example`
- `Makefile`
- `README.md` nếu cần đồng bộ hướng dẫn chạy

Không thay đổi trong bước này:

- `infra/docker-compose.yml`: development Compose tiếp tục chỉ chạy `db` và
  `broker`.
- `infra/docker-compose.prod.yml` và Dockerfile production.
- Retry/reconnect, DLQ, persistent queue, metrics exporter hoặc health endpoint.
- Business logic trong telemetry service/repository.

#### 14.2. Entrypoint và ownership lifecycle

Entrypoint là điểm bắt đầu và component duy nhất điều phối lifecycle của
telemetry ingestion process:

```text
main()
  └─ asyncio.run(run())
       ├─ cấu hình JSON logging
       ├─ đăng ký SIGINT/SIGTERM
       ├─ tạo shared asyncio.Queue
       ├─ tạo MQTTConsumer và BatchWorker dùng cùng queue
       ├─ khởi động MQTT consumer và batch worker
       └─ chờ signal hoặc task đầu tiên kết thúc
```

Yêu cầu:

- Có `main()` đồng bộ gọi `asyncio.run(run())`.
- Không thực hiện network I/O tại import time.
- Entrypoint cancel các task còn lại khi một task kết thúc trước.
- MVP tối giản không còn phân biệt shutdown signal với consumer/worker tự dừng;
  process đi cleanup chung.
- Khi startup/runtime lỗi, cleanup vẫn phải đóng database pool và các component
  đã khởi động trước đó.

#### 14.3. Cấu hình logging tại process boundary

- Chuyển lời gọi `configure_logging()` từ `BatchWorker.start()` lên đầu
  entrypoint, trước MQTT connection.
- Entrypoint sở hữu cấu hình log cấp process; `BatchWorker` chỉ sở hữu batching
  và transaction.
- Mọi log startup, MQTT, worker và shutdown phải tuân theo cùng
  JSON output contract từ bước 13.
- `configure_logging()` tiếp tục idempotent nhưng không dựa vào worker để kích
  hoạt.

#### 14.4. Database lifecycle

- Không còn database startup probe trong entrypoint MVP.
- Batch worker dùng `async_session_factory.begin()` để mỗi batch nằm trong một
  transaction atomic.
- Không dùng `get_db()` vì đây là async-generator dependency dành cho HTTP
  request lifecycle của FastAPI.
- `close_db()` vẫn chạy trong cleanup để dispose shared engine/pool của process
  telemetry nếu worker đã mở kết nối.

Mỗi OS process vẫn có engine, pool và factory riêng trong bộ nhớ. “Shared
factory” ở đây có nghĩa mọi component **trong cùng telemetry process** dùng
factory chuẩn từ `app.libs.db.session`, không tự tạo pool thứ hai.

#### 14.5. Runtime configuration

Thêm các setting có namespace:

```env
TELEMETRY_QUEUE_SIZE=10000
TELEMETRY_BATCH_SIZE=100
TELEMETRY_FLUSH_INTERVAL=30
```

Ý nghĩa:

- `TELEMETRY_QUEUE_SIZE`: số envelope tối đa trong in-memory queue.
- `TELEMETRY_BATCH_SIZE`: số message tối đa trong một database transaction.
- `TELEMETRY_FLUSH_INTERVAL`: số giây tối đa chờ batch chưa đầy.
- Không còn `TELEMETRY_HEALTH_HOST`, `TELEMETRY_HEALTH_PORT` hoặc
  `TELEMETRY_SHUTDOWN_TIMEOUT` trong MVP hiện tại.

Entrypoint tạo đúng một queue và truyền cùng instance cho consumer và worker:

```text
MQTTConsumer ──put──▶ shared queue ──get──▶ BatchWorker
```

#### 14.6. API tối giản của BatchWorker và MQTTConsumer

`BatchWorker` hiện chỉ cung cấp:

```python
async def start() -> None: ...
async def stop() -> None: ...
```

`MQTTConsumer` hiện chỉ cung cấp:

```python
async def connect() -> None: ...
async def start_consuming() -> None: ...
async def disconnect() -> None: ...
```

Quyết định hiện hành:

- Không còn `BatchWorker.is_running`, `BatchWorker.wait()` hoặc
  `MQTTConsumer.is_consuming`.
- Không còn `MQTTConsumer.subscribe()` public method; subscribe telemetry topic
  diễn ra trực tiếp trong `start_consuming()`.
- Entrypoint hiện truy cập `worker._task` sau `start()` để đưa task worker vào
  danh sách chờ. Đây là trade-off được chấp nhận cho MVP tối giản; nếu sau này
  cần lifecycle API sạch hơn thì chuyển vào phase observability/runtime.

#### 14.7. Theo dõi task tối giản

Entrypoint chờ đồng thời:

- SIGINT/SIGTERM.
- MQTT consumer task.
- Background task của `BatchWorker`.

Dùng `asyncio.wait(..., return_when=FIRST_COMPLETED)` hoặc cơ chế tương đương.

Hành vi:

- Task nào hoàn thành trước cũng làm process đi vào cleanup.
- Các task còn lại bị cancel; queue không được drain.
- Exception từ task đã hoàn thành không còn được phân tích riêng trong entrypoint
  MVP tối giản. Nếu task lỗi trước khi `asyncio.wait()` trả về và exception không
  được retrieve, đây là giới hạn đã chấp nhận để giữ code ngắn; logging lỗi chính
  vẫn nằm trong consumer/worker boundary.

#### 14.8. Health check

Không có health endpoint trong ingestion MVP hiện tại. HTTP health/readiness cho
process riêng đã được chuyển sang `docs/01-requirements/future.md`.

#### 14.9. Shutdown tối giản

Thứ tự hiện tại:

```text
1. Một task kết thúc hoặc SIGINT/SIGTERM được nhận
2. Entrypoint cancel các task còn pending
3. Gọi consumer.disconnect()
4. Gọi worker.stop() để cancel worker task
5. Gọi close_db()
6. Gỡ signal handler và process thoát
```

Invariant:

- Không drain queue, không `queue.join()` và không `task_done()`.
- Transaction đang chạy rollback khi task bị cancel hoặc exception xảy ra.
- Message còn trong queue RAM được phép mất.
- MVP không retry/reconnect/DLQ.

#### 14.10. Lệnh development

Thêm Makefile target và cập nhật `make help`:

```bash
make telemetry-dev
```

Target chạy:

```bash
cd backend && uv run python -m app.domains.telemetry.ingestion.entrypoint
```

Lệnh trực tiếp tương đương:

```bash
cd backend
uv run python -m app.domains.telemetry.ingestion.entrypoint
```

#### 14.11. Kiểm tra và nghiệm thu

Static checks:

- Black, isort, Ruff và mypy toàn backend.
- `git diff --check`.
- Kiểm tra mọi docstring/comment mới bằng tiếng Việt theo `AGENTS.md`.

Smoke test lifecycle:

- Shared queue được truyền cho cả consumer và worker.
- `entrypoint.py` tạo consumer task, worker task và shutdown signal task.
- `asyncio.wait(..., FIRST_COMPLETED)` làm process đi cleanup khi task đầu tiên
  kết thúc.
- SIGTERM/SIGINT kích hoạt cleanup tối giản.
- `close_db()` luôn được gọi.
- JSON logging được cấu hình trước log startup đầu tiên.

Integration khi hạ tầng khả dụng:

```bash
make infra-up
make telemetry-dev
```

Test publish MQTT → queue → batch → TimescaleDB đầy đủ vẫn thuộc bước 15.

**Kiểm tra:**

- [x] Entrypoint chạy độc lập trên host
- [x] Logging được cấu hình tại process boundary
- [x] Không còn database startup probe trong MVP tối giản
- [x] Consumer và worker dùng chung một queue
- [x] Public lifecycle API thừa đã được bỏ
- [x] Health check đã được bỏ khỏi source và chuyển sang future
- [x] SIGINT/SIGTERM cleanup tối giản
- [x] Smoke check `compileall` pass cho các file ingestion
- [x] Makefile/README hướng dẫn chạy được đồng bộ

**Kết quả thực hiện (2026-07-28):**

- `entrypoint.py` là process boundary tối giản cho logging, signal, queue,
  MQTT consumer, batch worker và database cleanup.
- `health.py` và `runtime.py` đã bị xóa khỏi source.
- Entrypoint tạo một queue theo `TELEMETRY_QUEUE_SIZE` rồi inject cùng instance
  vào consumer/worker. Chỉ còn setting queue, batch size và flush interval.
- `BatchWorker` bỏ `BatchMetrics`, `is_running`, `wait()`, drain queue,
  `queue.join()` và `task_done()`.
- `MQTTConsumer` bỏ `Metrics`, `is_consuming`, `subscribe()` public method và
  `_consuming`; consumer hiện chỉ consume, validate và enqueue.
- Smoke check hiện tại đã chạy `compileall` cho các file ingestion. Ruff không
  chạy được trong môi trường hiện tại vì `uv` không tìm thấy binary/module
  `ruff`; cần chạy lại static checks đầy đủ khi môi trường dependency sẵn sàng.

---

### Bước 15: Test end-to-end

**Mục tiêu:** Chứng minh luồng MVP thực tế từ MQTT publish đến TimescaleDB, bao
gồm success path, skip/drop path, transaction failure và process lifecycle.

> Ghi chú: Các case batch trong bước này là bằng chứng của implementation trước
> Bước 16. Smoke end-to-end cho active single-message path được ghi ở Bước 16.

#### 15.1. Phạm vi và nguyên tắc

- Đây là integration/E2E test chạy với PostgreSQL/TimescaleDB và EMQX thật.
- Không thêm retry/DLQ/persistent queue chỉ để làm test pass.
- Mỗi case phải dùng `message_uuid` và `recorded_at` riêng để tránh unique
  constraint làm sai kết quả.
- Ghi lại lệnh, thời điểm, input, log liên quan, query xác minh và kết quả
  pass/fail. Không chỉ đánh dấu checkbox dựa trên quan sát chung.
- QoS 0 và queue RAM không cho phép khẳng định “không mất message” trong mọi
  failure. Shutdown MVP không drain queue; E2E chỉ cần chứng minh process cleanup
  được và dữ liệu còn trong RAM có thể bị bỏ qua.

#### 15.2. Preconditions

- `db` và `broker` healthy.
- Alembic đang ở `head`; `vehicle_telemetry` xuất hiện trong
  `timescaledb_information.hypertables`.
- Có một vehicle chưa soft-delete.
- Có một telematic active gán đúng vehicle đó.
- Có một serial không tồn tại để test skip.
- Telemetry entrypoint bước 14 đang chạy trên host.
- Biết rõ batch size/flush interval đang dùng trong `.env`.

Nếu chưa có seed script chính thức, có thể insert fixture bằng SQL thủ công nhưng
phải ghi rõ ID/serial và cleanup sau test; không đưa credential thật vào tài liệu.

#### 15.3. Baseline kiểm tra trước test

```bash
# Hạ tầng và migration
docker compose -f infra/docker-compose.yml ps
cd backend && uv run alembic current

# Hypertable
docker exec g3network-db psql -U g3network -d g3network \
  -c "SELECT hypertable_name FROM timescaledb_information.hypertables WHERE hypertable_name = 'vehicle_telemetry';"
```

#### 15.4. Ma trận test

##### Case A — Message hợp lệ tối thiểu

Publish payload hợp lệ với telematic đã gán xe:

```bash
mosquitto_pub -h localhost -p 1883 -q 0 \
  -t "g3network/telematics/TBOX-VN-000123/telemetry" \
  -m '{"message_uuid":"<uuid-case-a>","telematic_serial":"TBOX-VN-000123","recorded_at":"<utc-case-a>","location":{"latitude":10.76,"longitude":106.66},"battery":{"soc":50.0}}'
```

Pass khi:

- Consumer tăng received/valid và không log validation warning.
- Sau tối đa flush interval, có đúng một row theo `message_uuid`.
- Internal `telematic_id`/`vehicle_id` khớp fixture.
- `recorded_at` và `received_at` là timezone-aware; `received_at` không sớm hơn
  thời điểm test hợp lý.
- `raw_payload` bằng object JSON gửi vào, không chứa internal ID backend.
- `last_seen_at` của telematic tăng.

Query xác minh:

```bash
docker exec g3network-db psql -U g3network -d g3network \
  -c "SELECT message_id, message_uuid, telematic_id, vehicle_id, recorded_at, received_at, raw_payload FROM vehicle_telemetry WHERE message_uuid = '<uuid-case-a>';"

docker exec g3network-db psql -U g3network -d g3network \
  -c "SELECT telematic_id, telematic_serial, vehicle_id, last_seen_at FROM telematics WHERE telematic_serial = 'TBOX-VN-000123';"
```

##### Case B — Payload đầy đủ và raw payload

Publish đủ vehicle state, battery, motor, signal, errors và thêm một field chưa
được Pydantic model hóa.

Pass khi:

- Các field đã model hóa được flatten đúng sang column.
- Field chưa model hóa không trở thành column nhưng vẫn còn nguyên trong
  `raw_payload`.
- Error codes được lưu đúng JSON contract.

##### Case C — JSON/schema không hợp lệ

Thực hiện riêng:

- JSON malformed.
- Thiếu `battery.soc`.
- Latitude ngoài range.
- `recorded_at` không có timezone.

Pass khi mỗi message:

- Log `WARNING` có topic/error context.
- Không vào queue và không tạo row DB.
- Worker/process vẫn hoạt động.

##### Case D — Serial không có mapping hợp lệ

Publish payload hợp lệ với serial không tồn tại hoặc thiết bị chưa gán xe.

Pass khi:

- Message qua MQTT/Pydantic validation nhưng service skip.
- Không tạo row telemetry.
- Batch result tăng `skipped`.
- Transaction vẫn commit các message hợp lệ khác trong cùng batch.

MVP hiện không phân biệt log/metric giữa serial không tồn tại và telematic chưa
gán xe; giới hạn này đã nằm trong `future.md`.

##### Case E — Flush theo batch size

Publish đúng `TELEMETRY_BATCH_SIZE` message hợp lệ với UUID/timestamp khác nhau
trong thời gian ngắn hơn flush interval.

Pass khi:

- Batch xử lý ngay khi đủ size, không đợi hết interval.
- Số row insert bằng số message hợp lệ.
- Repository không phát sinh query mapping theo từng message.
- Log summary có đúng `batch_size`, `processed`, `skipped`, `errors`.

##### Case F — Flush theo interval

Publish ít hơn batch size rồi ngừng gửi.

Pass khi:

- Batch flush sau khoảng `TELEMETRY_FLUSH_INTERVAL` tính từ message đầu tiên.
- Không yêu cầu độ chính xác tuyệt đối theo milliseconds; ghi sai số quan sát.
- Không busy-loop khi queue rỗng.

##### Case G — Mixed batch

Trong cùng batch gửi message hợp lệ, serial không mapping và payload invalid.

Pass khi:

- Payload invalid bị loại trước queue.
- Message không mapping được service skip.
- Message hợp lệ vẫn insert.
- Counters phản ánh đúng định nghĩa, không dùng tổng received làm processed.

##### Case H — Database failure

Sau khi worker đã chạy, tạo một batch rồi làm database unavailable trước khi
flush hoặc dùng failure injection an toàn trong môi trường test.

Pass khi:

- Toàn batch rollback, không có partial insert/last_seen update.
- Log `ERROR` có traceback và batch context.
- Worker/process dừng theo MVP.
- Không retry và không đưa message vào DLQ.

Không chạy failure injection trên database chứa dữ liệu quan trọng.

##### Case I — Shutdown tối giản

Đưa một số message hợp lệ vào queue, sau đó gửi SIGTERM tới telemetry process.

Pass khi:

- Process nhận signal và đi vào cleanup.
- Consumer được yêu cầu disconnect.
- Worker task bị cancel; transaction đang chạy rollback nếu bị cancel giữa batch.
- Queue không drain; message còn trong RAM được phép mất.
- Database pool được đóng qua `close_db()`.

##### Case J — Queue full

Chỉ chạy với cấu hình test có queue size nhỏ và producer nhanh hơn worker.

Pass khi:

- Callback không block vô hạn.
- Message vượt capacity bị drop có chủ đích.
- Log `WARNING` khi queue đầy.
- Process tiếp tục chạy; không tuyên bố zero data loss.

#### 15.5. Cleanup

- Xóa fixture telemetry/telematic/vehicle theo đúng thứ tự foreign key hoặc dùng
  transaction/namespace test riêng.
- Khôi phục setting queue/batch/interval sau case queue full.
- Khởi động lại hạ tầng/process đã cố ý dừng ở failure test.
- Không dùng `docker compose down -v` trừ khi database test disposable và đã xác
  nhận rõ phạm vi xóa.

#### 15.6. Báo cáo nghiệm thu

Ghi vào planner:

- Ngày, môi trường, revision/commit được test.
- Phiên bản PostgreSQL/TimescaleDB/EMQX.
- Setting queue/batch/interval.
- Danh sách case pass/fail và bằng chứng ngắn.
- Known limitation hoặc case không chạy, kèm lý do.

**Kiểm tra:**

- [x] Preconditions và baseline hợp lệ
- [x] Message tối thiểu/đầy đủ được insert đúng
- [x] `raw_payload`, timezone và internal mapping đúng
- [x] Invalid payload không vào DB
- [x] Serial không mapping được skip
- [x] Flush theo size và interval đúng
- [ ] Mixed batch có log/result đúng
- [x] Database failure rollback và dừng process
- [x] Shutdown tối giản cleanup đúng, không drain queue
- [x] Queue full drop/log đúng
- [x] Báo cáo nghiệm thu có môi trường và bằng chứng

**Kết quả nghiệm thu (2026-07-28):**

- Môi trường: PostgreSQL/TimescaleDB container `g3network-db` healthy,
  EMQX 5.5 container `g3network-broker` healthy, Alembic DB revision
  `c0f4a8b6e2d1`, hypertable `vehicle_telemetry` tồn tại. Lệnh
  `uv run alembic current` bị treo trong phiên này nên baseline revision được
  xác minh trực tiếp qua bảng `alembic_version`.
- Runtime test: chạy `entrypoint.py` trên host với
  `TELEMETRY_BATCH_SIZE=2`, `TELEMETRY_FLUSH_INTERVAL=1`,
  `TELEMETRY_QUEUE_SIZE=10`. Process trong sandbox không mở được MQTT socket
  (`Operation not permitted`), nên E2E runtime được chạy ngoài sandbox.
- Fixture: tạo vehicle `E2E-AD02` và telematic `TBOX-E2E-AD02`; sau test đã
  cleanup thành công, xóa 5 row `vehicle_telemetry`, 1 row `telematics` và 1 row
  `vehicles`; xác minh còn 0 row fixture.
- Case A pass: publish payload tối thiểu qua `mosquitto_pub`; DB có đúng 1 row
  theo `message_uuid`, mapping đúng `vehicle_id`, `recorded_at` UTC, `soc=50`,
  `raw_payload.telematic_serial=TBOX-E2E-AD02`; `last_seen_at` của telematic tăng.
- Case B pass: payload đầy đủ flatten đúng các field `speed`, `heading`,
  `battery_voltage`, `battery_current`, `battery_temperature`,
  `motor_temperature`, `odometer`, `signal_strength`, `error_codes`; field
  `extra_field` vẫn còn trong `raw_payload`.
- Case C pass: JSON malformed và latitude ngoài range đều log `WARNING` tại MQTT
  consumer và không tạo row DB.
- Case D pass: serial `TBOX-E2E-MISSING` qua Pydantic validation nhưng service
  skip; log `processed=0`, `skipped=1`; DB không có row theo UUID test.
- Case E pass: gửi 2 message trong cùng phiên `mosquitto_pub -l`; worker xử lý
  batch `batch_size=2`, `processed=2`; DB có đủ 2 row.
- Case F pass gián tiếp: các message đơn lẻ flush sau khoảng
  `TELEMETRY_FLUSH_INTERVAL=1` và insert thành công theo interval.
- Case H pass: dừng DB container tạm thời rồi publish payload hợp lệ; worker log
  `ERROR` với traceback DB connection closed, process cleanup và dừng; sau khi
  start DB lại, UUID case H có 0 row.
- Case I pass: chạy process mới rồi gửi SIGINT; log cho thấy worker stop và
  `Telemetry ingestion stopped`, exit code 0. Chưa gửi SIGTERM riêng vì entrypoint
  dùng cùng signal path cho SIGINT/SIGTERM.
- Case J pass ở mức smoke logic: gọi trực tiếp `_handle_message()` với queue
  `maxsize=1`; message thứ hai log `Queue full, message dropped`, queue vẫn
  `qsize=1`. Chưa tái hiện queue-full qua EMQX vì worker consume nhanh và case
  này phụ thuộc timing.
- Chưa chạy mixed batch đầy đủ gồm valid + serial missing + invalid trong cùng
  batch; các hành vi thành phần đã được kiểm riêng ở Case A/C/D/E.

---

### Bước 16: Chuyển luồng active sang xử lý từng message

**Mục tiêu:** Bỏ batch window khỏi đường chạy MVP để message được xử lý ngay
theo thứ tự lấy ra khỏi queue, nhưng giữ toàn bộ implementation batch để có thể
bật lại khi workload thực tế cần tối ưu throughput.

#### 16.1. Contract và transaction

- Luồng active là:
  `MQTTConsumer → asyncio.Queue → MessageWorker → process_message → database`.
- `MessageWorker` lấy đúng một `TelemetryEnvelope` mỗi vòng lặp; không chờ đủ
  số lượng và không dùng `TELEMETRY_FLUSH_INTERVAL`.
- Mỗi message có một transaction riêng do worker sở hữu. Thành công thì commit;
  lỗi database rollback transaction của message hiện tại và dừng worker theo
  policy MVP không retry.
- Các message đã commit trước đó vẫn được giữ khi một message sau gặp lỗi.
  Message còn trong queue khi process dừng vẫn có thể mất vì queue là RAM và MVP
  không drain queue.
- `received_at` được tạo riêng cho từng message tại service boundary xử lý.
- Message không có mapping telematic/vehicle bị skip; lỗi chuyển đổi dữ liệu
  chỉ tăng `errors` cho message hiện tại. MQTT schema validation vẫn diễn ra ở
  consumer trước khi enqueue.

#### 16.2. File thay đổi

Tạo:

- `backend/app/domains/telemetry/ingestion/message_worker.py`

Cập nhật:

- `backend/app/domains/telemetry/service.py`: thêm `process_message()` và
  `MessageResult`.
- `backend/app/domains/telemetry/repository.py`: thêm singular mapping lookup và
  insert; giữ nguyên `get_telematic_mappings()`/`bulk_insert_telemetry()`.
- `backend/app/domains/telemetry/ingestion/entrypoint.py`: dùng
  `MessageWorker`, không truyền batch size/flush interval vào active worker.
- `backend/app/libs/common/config.py`, `backend/.env.example`: giữ setting batch
  cho code tương lai và ghi rõ chúng không thuộc active path.
- `docs/01-requirements/future.md`: ghi nhận batch processing là thành phần hoãn.

#### 16.3. Bảo toàn batch implementation

- Không xóa hoặc chuyển đổi `batch_worker.py` và `process_batch()` sang wrapper
  singular; batch path phải còn nguyên để tránh mất implementation đã có.
- Không xóa các repository batch API. Khi bật lại phải đánh giá lại benchmark,
  transaction atomicity, backpressure và failure semantics.
- Rà soát mục cache telematic mapping trong `future.md` để không còn mô tả rằng
  active MVP đang dùng batch lookup.

#### 16.4. Kiểm tra

- [x] Active entrypoint dùng `MessageWorker` và không còn batch window.
- [x] Singular service/repository API giữ transaction boundary ở worker.
- [x] Batch worker/service/repository path vẫn tồn tại và không bị gọi từ entrypoint.
- [x] `compileall`, Black, isort, Ruff và mypy pass trên các file thay đổi.
- [x] Smoke test message hợp lệ, mapping bị skip và lỗi database rollback.
- [x] Smoke test MQTT/entrypoint, shutdown và queue-full không hồi quy policy
  queue RAM MVP.

**Kết quả thực hiện (2026-07-30):**

- Tạo `MessageWorker` làm active worker; entrypoint không còn truyền hoặc log
  `TELEMETRY_BATCH_SIZE`/`TELEMETRY_FLUSH_INTERVAL`.
- `process_message()` thực hiện singular mapping lookup và insert trong
  transaction do worker sở hữu. Hai message smoke liên tiếp tạo hai transaction
  riêng; batch APIs vẫn import được và không bị entrypoint gọi.
- Targeted `compileall`, Black, isort, Ruff và mypy đều pass. Full-repository
  Black/isort vẫn báo lỗi formatting/import sorting tồn tại trước ở
  `app/domains/telematics/models.py`, `app/domains/telematics/repository.py` và
  `app/domains/telemetry/models.py`; không sửa các file ngoài phạm vi.
- PostgreSQL smoke pass với mapping thật `T00001`: insert, query, raw payload và
  cleanup đều thành công.
- MQTT E2E pass với EMQX 5.5: message UUID
  `0f8b7b0e-7b2f-4d12-9f15-4aa4d7b7a002` được persist với `soc=62.5`, log
  `processed=1`, sau đó đã xóa row test.
- Batch implementation vẫn nằm trong `batch_worker.py`, `process_batch()` và
  repository batch APIs; đã ghi nhận mục 25 trong `future.md`.

---

## Tổng kết

### Trạng thái hiện tại

- Bước 0-14: đã triển khai theo scope MVP tối giản; planner đã được đối chiếu
  lại với source ingestion ngày 2026-07-28.
- Bước 15: đã chạy E2E chính ngày 2026-07-28; còn mixed batch đầy đủ và
  queue-full qua broker chưa nghiệm thu ổn định.
- Bước 16: đã chuyển active ingestion sang xử lý từng message ngày 2026-07-30;
  batch path được giữ lại và ghi nhận trong `future.md`.

Sau khi hoàn thành bước 16, hệ thống có:

1. **Database**: 2 bảng `telematics` và `vehicle_telemetry` (TimescaleDB hypertable)
2. **MQTT Broker**: EMQX 5.5 chạy local, QoS 0; chưa cấu hình ACL production
3. **Backend**:
   - MQTT consumer nhận message từ broker với QoS 0
   - Message worker xử lý tuần tự từng message ngay sau khi lấy khỏi queue
   - Single lookup và insert cho active MVP
   - Batch worker, batch lookup và bulk insert được giữ cho phase tương lai
   - Lưu raw_payload JSONB để debug và reprocessing
   - Process entrypoint riêng, signal handling và cleanup tối giản
   - JSON structured logging, không còn process-local metrics

**Công nghệ sử dụng:**

- Python 3.12 + FastAPI
- SQLAlchemy 2.0 (async)
- TimescaleDB (PostgreSQL extension)
- EMQX 5.5
- Pydantic v2
- asyncio.Queue

**Performance/SLA:**

- Chưa có benchmark để cam kết throughput hoặc latency production.
- Batch flush interval không thuộc active path; chỉ có ý nghĩa khi bật lại batch
  worker trong phase tương lai.
- TimescaleDB hypertable đã dùng; compression/retention policy chưa được cấu hình.
- Chỉ công bố con số performance sau khi đo workload đại diện với số xe, tần suất,
  payload size và database resource đã chốt.

**Giới hạn MVP:**

- MQTT QoS 0 (không đảm bảo delivery)
- Không retry/reconnect
- Không có dead-letter queue
- Không có persistent queue
- Không có duplicate detection nâng cao
- Queue full sẽ drop message
- Không có metrics/counter trong ingestion MVP hiện tại
- Log chưa được thu thập tập trung
- Chưa có authentication/authorization MQTT production
- Chưa có automated backend test suite
- Các hạng mục hoãn được quản lý tại `docs/01-requirements/future.md`

**Phase tiếp theo:**

- API query telemetry (realtime + history)
- Tầng aggregate/throttle và cơ chế đẩy realtime cho dashboard
- Real-time alerting
- Dashboard frontend
- MQTT security và device identity
- Retry/reconnect và DLQ
- Persistent queue
- Duplicate detection nâng cao
- Observability tập trung và persistent metrics
- Health/readiness endpoint và graceful drain khi cần vận hành production
- Retention/compression/benchmark theo volume thật
