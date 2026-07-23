# Vehicle Service

## Mục đích
Xử lý business logic cho domain Vehicle. Service là layer duy nhất chứa logic nghiệp vụ, validation, và exception handling.

## Các hàm chính

### create_vehicle
- Kiểm tra `license_plate` đã tồn tại chưa → 400 nếu trùng
- Kiểm tra `vin` đã tồn tại chưa (nếu có) → 400 nếu trùng
- Gọi repository để tạo xe mới
- Trả về `VehicleResponse`

### get_vehicle
- Gọi repository để lấy xe theo ID
- Nếu không tìm thấy → 404
- Trả về `VehicleResponse`

### list_vehicles
- Validate pagination (page >= 1, page_size 1-100)
- Gọi repository để lấy danh sách xe và đếm tổng số
- Trả về `VehicleListResponse`

### update_vehicle
- Kiểm tra xe tồn tại → 404 nếu không
- Nếu update `license_plate`, kiểm tra trùng → 400 nếu trùng
- Gọi repository để cập nhật
- Trả về `VehicleResponse`

### delete_vehicle
- Gọi repository để soft delete
- Nếu không tìm thấy → 404
- Trả về `{"message": "Vehicle deleted successfully"}`

## Quy tắc quan trọng

### Separation of Concerns
- Service KHÔNG query trực tiếp database
- Mọi I/O đều qua repository layer
- Service chỉ chứa business logic

### Exception Handling
- Dùng `HTTPException` từ FastAPI
- 400: Validation errors (trùng biển số, VIN)
- 404: Resource not found

### Validation
- Pagination: page >= 1, page_size 1-100
- Unique constraints: license_plate, vin

## Lưu ý
- Service được gọi từ `router.py`
- Service gọi xuống `repository.py`
- Service không biết về HTTP request/response (chỉ biết Pydantic schemas)
