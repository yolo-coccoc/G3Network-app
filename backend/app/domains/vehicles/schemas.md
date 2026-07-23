# Vehicle Pydantic Schemas

## Mục đích
Định nghĩa các Pydantic models cho request/response trong Vehicle API.

## Các schema chính

### VehicleBase
Schema cơ sở chứa các trường chung:
- `license_plate`: Biển số xe (bắt buộc, 1-20 ký tự)
- `vin`: Số khung (optional, đúng 17 ký tự)
- `telematics_device_id`: Mã thiết bị telematics (optional, max 50 ký tự)
- `make`: Hãng xe (bắt buộc)
- `model`: Dòng xe (bắt buộc)
- `year`: Năm sản xuất (bắt buộc, 1900-2100)
- `status`: Trạng thái (mặc định: ACTIVE)

### VehicleCreate (extends VehicleBase)
Schema tạo xe mới:
- Kế thừa tất cả từ VehicleBase
- Thêm `fleet_id`: ID đội xe (optional)

### VehicleUpdate
Schema cập nhật xe:
- Tất cả trường đều optional
- Cho phép cập nhật từng phần (PATCH)

### VehicleResponse (extends VehicleBase)
Schema trả về cho client:
- Kế thừa từ VehicleBase
- Thêm: `id`, `fleet_id`, `created_at`, `updated_at`
- Sử dụng `ConfigDict(from_attributes=True)` để map từ SQLAlchemy model

### VehicleListResponse
Schema danh sách xe phân trang:
- `items`: Danh sách VehicleResponse
- `total`: Tổng số xe
- `page`: Trang hiện tại (≥1)
- `page_size`: Số item per trang (1-100)

## Validation
- `license_plate`: min_length=1, max_length=20
- `vin`: đúng 17 ký tự (nếu có)
- `telematics_device_id`: max_length=50
- `year`: 1900 ≤ year ≤ 2100
- `page_size`: 1 ≤ page_size ≤ 100

## Examples
Mỗi schema có examples để hiển thị trong Swagger UI.

## Lưu ý
- Sử dụng Pydantic v2 syntax
- `ConfigDict(from_attributes=True)` thay cho v1's `class Config: orm_mode = True`
