# Vehicle Router

## Mục đích
Định nghĩa API endpoints cho domain Vehicle. Router là layer tiếp nhận HTTP request và gọi xuống service layer.

## Các endpoints

### POST /vehicles
- Tạo xe mới
- Request body: `VehicleCreate`
- Response: `VehicleResponse` (201)
- Validation: license_plate và VIN phải unique

### GET /vehicles
- Lấy danh sách xe phân trang
- Query params:
  - `page` (default: 1, min: 1)
  - `page_size` (default: 10, min: 1, max: 100)
  - `status` (optional: ACTIVE, INACTIVE, MAINTENANCE, DECOMMISSIONED)
- Response: `VehicleListResponse` (200)

### GET /vehicles/{vehicle_id}
- Lấy chi tiết 1 xe
- Path param: `vehicle_id` (UUID)
- Response: `VehicleResponse` (200)
- Error: 404 nếu không tìm thấy

### PATCH /vehicles/{vehicle_id}
- Cập nhật thông tin xe
- Path param: `vehicle_id` (UUID)
- Request body: `VehicleUpdate` (partial update)
- Response: `VehicleResponse` (200)
- Error: 404 nếu không tìm thấy, 400 nếu license_plate trùng

### DELETE /vehicles/{vehicle_id}
- Soft delete xe
- Path param: `vehicle_id` (UUID)
- Response: `{"message": "Vehicle deleted successfully"}` (200)
- Error: 404 nếu không tìm thấy

## Quy tắc quan trọng

### Dependency Injection
- Database session được inject qua `Depends(get_db)`
- Mỗi request có session riêng, tự động đóng sau request

### Response Model
- Mỗi endpoint có `response_model` rõ ràng
- FastAPI tự động validate và serialize response

### Tags
- `tags=["vehicles"]` để nhóm endpoints trong Swagger UI

### Status Codes
- 201: Created (POST)
- 200: Success (GET, PATCH, DELETE)
- 400: Validation error
- 404: Not found

## Lưu ý
- Router KHÔNG chứa business logic
- Router chỉ định nghĩa interface và gọi service
- Mọi logic nghiệp vụ nằm ở `service.py`
