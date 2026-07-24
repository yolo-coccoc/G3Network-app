# Vehicle Repository

## Mục đích
Xử lý tất cả truy vấn database cho domain Vehicle. Repository là layer duy nhất được phép truy vấn trực tiếp vào database.

## Các hàm chính

### Create
- `create_vehicle(db, vehicle_data)` - Tạo xe mới

### Read
- `get_vehicle_by_id(db, vehicle_id)` - Lấy xe theo ID
- `get_vehicle_by_plate(db, license_plate)` - Lấy xe theo biển số
- `get_vehicle_by_vin(db, vin)` - Lấy xe theo số khung
- `get_vehicles(db, skip, limit, status_filter)` - Lấy danh sách xe phân trang
- `count_vehicles(db, status_filter)` - Đếm tổng số xe

### Update
- `update_vehicle(db, vehicle_id, update_data)` - Cập nhật thông tin xe

### Delete
- `soft_delete_vehicle(db, vehicle_id)` - Xoá mềm (set deleted_at)

## Quy tắc quan trọng

### Soft Delete
- Tất cả hàm query đều có điều kiện `deleted_at IS NULL`
- `soft_delete_vehicle` chỉ set `deleted_at` và đổi status, không xoá record thật

### Async
- Tất cả hàm đều là `async`
- Sử dụng `AsyncSession` từ SQLAlchemy
- Dùng `await db.flush()` để persist thay đổi

### Type Hints
- Return type rõ ràng: `Vehicle | None`, `list[Vehicle]`, `int`
- Accept cả `UUID | str` cho vehicle_id

## Lưu ý
- Repository KHÔNG chứa business logic (validation, exception handling)
- Business logic nằm ở `service.py`
- Repository chỉ làm nhiệm vụ truy vấn/thao tác dữ liệu thuần túy
