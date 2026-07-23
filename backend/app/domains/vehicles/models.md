# Vehicle SQLAlchemy Model

## Mục đích
Định nghĩa cấu trúc bảng `vehicles` trong PostgreSQL để lưu trữ thông tin hồ sơ xe tải điện.

## Các trường chính

| Trường | Kiểu | Mô tả |
|--------|------|-------|
| `id` | String(36) | UUID primary key |
| `license_plate` | String(20) | Biển số xe, unique, indexed |
| `vin` | String(17) | Số khung (Vehicle Identification Number), unique, indexed |
| `telematics_device_id` | String(50) | Mã thiết bị telematics gắn trên xe, unique, nullable, indexed |
| `make` | String(50) | Hãng xe (VD: VinFast, Hyundai...) |
| `model` | String(50) | Dòng xe (VD: eTruck 500) |
| `year` | Integer | Năm sản xuất |
| `status` | Enum | Trạng thái: active, inactive, maintenance, decommissioned |
| `fleet_id` | String(36) | FK đến bảng fleets (nullable - xe có thể chưa phân bổ đội) |
| `created_at` | DateTime | Thời gian tạo bản ghi |
| `updated_at` | DateTime | Thời gian cập nhật cuối |

## Enum VehicleStatus
- `ACTIVE`: Xe đang hoạt động bình thường
- `INACTIVE`: Xe tạm ngưng hoạt động
- `MAINTENANCE`: Xe đang bảo dưỡng
- `DECOMMISSIONED`: Xe đã thanh lý/ngừng sử dụng

## Indexes
- `license_plate`: Unique index (tìm kiếm theo biển số)
- `vin`: Unique index (tìm kiếm theo số khung)
- `telematics_device_id`: Unique index (liên kết dữ liệu telematics)
- `status`: Index (lọc theo trạng thái)

## Relationships
- `fleet`: Quan hệ với bảng `fleets` (sẽ được định nghĩa khi tạo Fleet model)

## Lưu ý
- `telematics_device_id` nullable vì xe mới có thể chưa gắn thiết bị
- `fleet_id` nullable vì xe có thể chưa được phân bổ vào đội
- Sử dụng `Mapped` type hints của SQLAlchemy 2.0 để type-safe
