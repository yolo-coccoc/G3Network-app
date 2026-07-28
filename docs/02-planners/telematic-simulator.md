# Planner: Telematic Simulator (AD-02, AD-05)

> Mã chức năng: AD-02 (Nhận dữ liệu telemetry), AD-05 (Quản lý xe và thiết bị)
> Trạng thái: 📋 Dự kiến
> Ngày tạo: 2026-07-28

## 1. Mục tiêu

Tạo simulator tối giản để kiểm thử end-to-end luồng dữ liệu:

```text
POST vehicles + POST telematics
              ↓
        Telematic Simulator
              ↓ MQTT
             EMQX
              ↓
     telemetry-ingestion → PostgreSQL
```

Trong planner này:

- **Telematic** là thiết bị vật lý giả lập, được định danh bằng
  `telematic_serial`.
- **Telemetry** là bản tin dữ liệu mà thiết bị gửi liên tục qua MQTT.
- Simulator không tạo dữ liệu trực tiếp trong database và không bypass API/MQTT.

## 2. Phạm vi

Tạo đúng hai script:

```text
scripts/seed_simulator_devices.py   # chạy một lần để tạo vehicles và telematics
scripts/telematic_simulator.py      # chạy liên tục để publish telemetry
```

Không bao gồm UI, Docker image riêng, mô phỏng OCPP, mô phỏng command từ backend,
hoặc mô phỏng lỗi mạng nâng cao.

## 3. Tài liệu và source contract bắt buộc tham chiếu

Simulator phải bám các source sau, không tự định nghĩa schema khác:

- Vehicle request: `backend/app/domains/vehicles/schemas.py`, class
  `VehicleCreate`.
- Telematic request: `backend/app/domains/telematics/schemas.py`, class
  `TelematicCreate`.
- Telematic status: `backend/app/domains/telematics/types.py`, enum
  `TelematicStatus`.
- Telemetry payload: `backend/app/domains/telemetry/schemas.py`, các class
  `TelemetryMessage`, `LocationData`, `VehicleState`, `BatteryData`,
  `MotorData`, `SignalData`.
- Database mapping: `backend/app/domains/telematics/models.py`, class
  `Telematic`, và `backend/app/domains/vehicles/models.py`, class `Vehicle`.
- MQTT topic/payload/QoS: `docs/02-planners/mqtt-spec.md`.
- MQTT runtime settings: `backend/app/libs/common/config.py`, các biến
  `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_QOS`.

Nếu source schema thay đổi, simulator phải được cập nhật theo source đó trước khi
đổi payload mẫu trong script.

## 4. Script 1 — seed vehicles và telematics

### 4.1. Trách nhiệm

`seed_simulator_devices.py` chạy một lần và thực hiện tuần tự:

1. Tạo một hoặc nhiều vehicle bằng `POST /api/v1/vehicles/`.
2. Đọc `vin` và `vehicle_id` từ response.
3. Tạo một Telematic tương ứng bằng `POST /api/v1/telematics/`, gửi
   `vehicle_vin` để backend resolve thành `vehicle_id`.
4. In ra bảng mapping `telematic_serial → vehicle_id → VIN` để dùng khi chạy
   simulator.

### 4.2. Dữ liệu vehicle

Mỗi request phải có đủ field theo `VehicleCreate`/`VehicleBase`:

```json
{
  "license_plate": "51D-123.45",
  "vin": "SIMULATORVIN00001",
  "make": "G3Network",
  "model": "E-Truck Simulator",
  "year": 2026,
  "status": "ACTIVE",
  "fleet_id": null
}
```

VIN phải dài đúng 17 ký tự theo `VehicleCreate`. Script cần tạo VIN và biển số
không trùng nhau giữa các lần chạy hoặc hỗ trợ prefix/index cấu hình được.

### 4.3. Dữ liệu Telematic

Mỗi request phải theo `TelematicCreate`:

```json
{
  "telematic_serial": "TBOX-SIM-000001",
  "vehicle_vin": "SIMULATORVIN00001",
  "status": "ACTIVE",
  "firmware_version": "simulator-1.0.0"
}
```

`vehicle_vin` phải đúng VIN vừa tạo. Không dùng `vehicle_id` trong request vì API
CRUD Telematic nhận VIN và tự resolve FK.

### 4.4. Tính idempotency tối thiểu

- Mặc định script fail-fast nếu API trả `409`, để tránh âm thầm tạo mapping sai.
- Có tham số `--prefix` hoặc `--start-index` để tạo bộ serial/VIN mới khi chạy lại.
- Không xóa dữ liệu cũ và không gọi DELETE tự động.
- Để dễ chạy cho developer, cấu hình MVP được hard-code tập trung trong một block
  `CONFIG` ở đầu script, có comment tiếng Việt cho từng giá trị; không bắt buộc
  truyền command-line arguments.
- Các giá trị cần sửa trực tiếp gồm `API_BASE_URL`, số lượng thiết bị,
  `TELEMATIC_SERIAL_PREFIX`, `VIN_PREFIX`, firmware và request timeout. Mặc định
  dùng `http://localhost:8000`.

## 5. Script 2 — publish telemetry liên tục

### 5.1. Trách nhiệm

`telematic_simulator.py` đọc danh sách serial đã seed và mở một MQTT client để
publish liên tục. MVP có thể dùng một process và một task async cho mỗi thiết bị.

Topic của mỗi thiết bị:

```text
g3network/telematics/{telematic_serial}/telemetry
```

Thiết lập QoS `0`, retain `false`, đúng `docs/02-planners/mqtt-spec.md`.

### 5.2. Payload phải khớp TelemetryMessage

Mỗi bản tin có cấu trúc:

```json
{
  "message_uuid": "497f6eca-6276-4993-bfeb-53cbbbba6f08",
  "telematic_serial": "TBOX-SIM-000001",
  "recorded_at": "2026-07-28T10:30:00Z",
  "location": {
    "latitude": 10.762622,
    "longitude": 106.660172
  },
  "vehicle_state": {
    "speed": 42.5,
    "heading": 90.0,
    "odometer": 12500.5
  },
  "battery": {
    "soc": 78.0,
    "voltage": 650.0,
    "current": -120.0,
    "temperature": 32.0
  },
  "motor": {
    "temperature": 45.0
  },
  "signal": {
    "strength": -70
  },
  "errors": []
}
```

Quy tắc sinh dữ liệu:

- `message_uuid`: UUID mới cho mỗi message.
- `telematic_serial`: giữ nguyên serial của task.
- `recorded_at`: UTC timezone-aware, tăng theo thời gian thực.
- latitude/longitude: dao động nhỏ quanh tọa độ cấu hình, luôn trong range schema.
- speed: dao động trong `0..200` km/h.
- heading: dao động trong `0..360` hoặc `null`.
- odometer: không giảm giữa các message của cùng thiết bị.
- battery.soc: dao động trong `0..100`.
- `errors`: mặc định `[]`; không mô phỏng lỗi kỹ thuật trong MVP.

Script nên validate payload bằng `TelemetryMessage.model_validate()` trước khi
serialize JSON để simulator phát hiện sớm lỗi contract.

### 5.3. Tham số chạy

Đề xuất cấu hình tập trung ở đầu script:

```python
CONFIG = {
    "mqtt_host": "localhost",
    "mqtt_port": 1883,
    "mqtt_topic_prefix": "g3network/telematics",
    "device_count": 3,
    "publish_interval_seconds": 5,
}
```

Developer chỉ cần sửa block `CONFIG`, sau đó chạy script không tham số:

```bash
uv run python scripts/telematic_simulator.py
```

Có thể hỗ trợ đọc serial từ file JSON do script seed xuất ra, nhưng không bắt buộc
ở MVP. `Ctrl+C` phải dừng task, disconnect MQTT và thoát sạch.

## 6. Thứ tự chạy và điều kiện trước

1. Khởi động PostgreSQL, EMQX và API.
2. Chạy migration mới nhất.
3. Khởi động telemetry ingestion.
4. Chạy `seed_simulator_devices.py`.
5. Chạy `telematic_simulator.py` với các serial đã seed.
6. Kiểm tra log ingestion và số row trong `vehicle_telemetry`.

Nếu Telematic chưa được gán vehicle, ingestion sẽ skip message theo business rule
hiện tại; vì vậy phải seed vehicle trước và gửi đúng VIN khi tạo Telematic.

## 7. Tiêu chí nghiệm thu

- [ ] Seed script tạo được vehicle qua API.
- [ ] Seed script tạo được Telematic qua API bằng `vehicle_vin`.
- [ ] Seed script in đúng mapping serial/VIN/vehicle_id.
- [ ] Simulator publish đúng topic của từng thiết bị.
- [ ] Payload validate được bằng `TelemetryMessage`.
- [ ] Mỗi thiết bị phát message theo interval cấu hình.
- [ ] Ingestion nhận, enrich và lưu được telemetry vào PostgreSQL.
- [ ] Ctrl+C dừng simulator không để task MQTT chạy nền.
- [ ] Không thêm field ngoài contract nếu chưa cập nhật schema/spec tương ứng.
