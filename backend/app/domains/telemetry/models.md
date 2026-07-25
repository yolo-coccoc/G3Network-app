# Telemetry Domain Models

File này định nghĩa các SQLAlchemy models cho domain telemetry.

## Telematic (Bảng `telematics`)

Quản lý thiết bị telematics (thiết bị thu thập dữ liệu) được gắn trên xe tải điện.

### Các trường dữ liệu

| Tên cột (DB) | Tên thuộc tính (Python) | Kiểu | Mô tả |
|---|---|---|---|
| `telematic_id` | `telematic_id` | UUID | Primary key |
| `telematic_serial` | `telematic_serial` | VARCHAR(50) | Mã vật lý trên thiết bị (unique) |
| `vehicle_id` | `vehicle_id` | UUID (FK) | ID xe được gán (nullable) |
| `status` | `status` | ENUM | Trạng thái: ACTIVE/INACTIVE/MAINTENANCE |
| `firmware_version` | `firmware_version` | VARCHAR(50) | Phiên bản firmware (nullable) |
| `last_seen_at` | `last_seen_at` | TIMESTAMPTZ | Thời điểm nhận message cuối cùng |
| `created_at` | `created_at` | TIMESTAMPTZ | Thời gian tạo |
| `updated_at` | `updated_at` | TIMESTAMPTZ | Thời gian cập nhật |

### Ràng buộc

- **Primary Key**: `telematic_id` (UUID)
- **Unique**: `telematic_serial` (mỗi thiết bị có 1 serial duy nhất)
- **Unique**: `vehicle_id` (mỗi xe chỉ có tối đa 1 telematic)
- **Foreign Key**: `vehicle_id` → `vehicles.vehicle_id` (ON DELETE SET NULL)
- **Index**: `telematic_serial`, `vehicle_id`, `status`

### Trạng thái thiết bị

- `ACTIVE`: Thiết bị đang hoạt động bình thường
- `INACTIVE`: Thiết bị đã ngưng hoạt động
- `MAINTENANCE`: Thiết bị đang bảo trì

### Lưu ý

- `vehicle_id` nullable: Thiết bị có thể được tạo trước khi gán cho xe
- `vehicle_id` kiểu UUID: Phù hợp với primary key của vehicles
- `last_seen_at` được cập nhật mỗi khi nhận message telemetry từ thiết bị
- Dùng UUID cho primary key để dễ đồng bộ giữa các hệ thống

## VehicleTelemetry (Bảng `vehicle_telemetry`)

Lưu dữ liệu telemetry time-series từ thiết bị telematics. Sử dụng TimescaleDB hypertable để tối ưu truy vấn thời gian.

### Các trường dữ liệu

| Tên cột (DB) | Tên thuộc tính (Python) | Kiểu | Mô tả |
|---|---|---|---|
| `message_id` | `message_id` | BIGINT | Primary key (auto-increment) |
| `message_uuid` | `message_uuid` | UUID | UUID do telematic tạo |
| `telematic_id` | `telematic_id` | UUID (FK) | ID thiết bị telematic |
| `telematic_serial` | `telematic_serial` | VARCHAR(50) | Serial number (audit/debug) |
| `vehicle_id` | `vehicle_id` | UUID (FK) | ID xe |
| `recorded_at` | `recorded_at` | TIMESTAMPTZ | Thời điểm telematic ghi nhận |
| `received_at` | `received_at` | TIMESTAMPTZ | Thời điểm backend nhận |
| `latitude` | `latitude` | DOUBLE | Vĩ độ GPS |
| `longitude` | `longitude` | DOUBLE | Kinh độ GPS |
| `speed` | `speed` | DOUBLE | Tốc độ (km/h, nullable) |
| `heading` | `heading` | DOUBLE | Hướng di chuyển (0-360°, nullable) |
| `soc` | `soc` | DOUBLE | State of Charge (%) |
| `battery_voltage` | `battery_voltage` | DOUBLE | Điện áp pin (V, nullable) |
| `battery_current` | `battery_current` | DOUBLE | Dòng điện pin (A, nullable) |
| `battery_temperature` | `battery_temperature` | DOUBLE | Nhiệt độ pin (°C, nullable) |
| `motor_temperature` | `motor_temperature` | DOUBLE | Nhiệt độ motor (°C, nullable) |
| `odometer` | `odometer` | DOUBLE | Số km đã đi (nullable) |
| `signal_strength` | `signal_strength` | INT | Cường độ tín hiệu (dBm, nullable) |
| `error_codes` | `error_codes` | JSONB | Mã lỗi (nullable) |
| `raw_payload` | `raw_payload` | JSONB | Dữ liệu gốc từ telematic |

### Ràng buộc

- **Primary Key**: `(message_id, recorded_at)` - composite PK chứa partition key
- **Unique**: `(telematic_id, recorded_at)` - một telematic chỉ có 1 message tại 1 thời điểm
- **Foreign Key**: `telematic_id` → `telematics.telematic_id` (ON DELETE CASCADE)
- **Foreign Key**: `vehicle_id` → `vehicles.vehicle_id` (ON DELETE CASCADE)
- **Index**: `message_uuid` (để trace, chưa unique trong MVP)
- **Index**: `(vehicle_id, recorded_at DESC)` - tối ưu query theo xe

### TimescaleDB Hypertable

- **Partition key**: `recorded_at`
- **Chunk interval**: 1 day
- **Lý do dùng BIGINT PK**: TimescaleDB khuyến nghị dùng BIGINT thay vì UUID cho performance

### Lưu ý

- `speed` nullable: không phải telematic nào cũng cung cấp tốc độ (có thể tính từ GPS hoặc cảm biến)
- `heading` nullable: không phải telematic nào cũng cung cấp hướng di chuyển
- `raw_payload` lưu toàn bộ JSON gốc để debug và reprocessing sau này
- `telematic_serial` được lưu lại (denormalized) để audit khi thiết bị bị xóa

## VehicleTelemetry (Bước 2)

Sẽ được thêm ở bước tiếp theo - bảng time-series lưu dữ liệu telemetry từ xe.
