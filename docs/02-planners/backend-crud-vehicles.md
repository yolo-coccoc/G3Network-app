# Planner: Backend CRUD Vehicles (AD-05)

> Mã chức năng: AD-05 (Quản lý xe - CRUD, gán thiết bị)
> Trạng thái: 📋 Dự kiến
> Ngày tạo: 2026-07-23

---

## Tổng quan

Xây dựng backend API cho quản lý xe (vehicles) với các thao tác CRUD cơ bản:
- Create: Tạo xe mới
- Read: Xem danh sách xe, chi tiết 1 xe
- Update: Cập nhật thông tin xe
- Delete: Xoá xe (soft delete)

**Phạm vi:**
- Chỉ backend (FastAPI)
- Test qua Swagger UI
- Chưa bao gồm: frontend, gán thiết bị telematics, tích hợp với domain khác

---

## Kiến trúc theo AGENTS.md

```
backend/
├── app/                        # Source code chính (uv package layout)
│   ├── domains/
│   │   └── vehicles/
│   │       ├── router.py      # FastAPI endpoints
│   │       ├── service.py     # Business logic
│   │       ├── repository.py  # Database queries
│   │       ├── schemas.py     # Pydantic models
│   │       └── models.py      # SQLAlchemy models
│   ├── api/
│   │   └── main.py           # Mount router
│   └── libs/
│       └── db/
│           └── base.py        # SQLAlchemy base
├── pyproject.toml
└── uv.lock
```

---

## Danh sách bước thực hiện

### Bước 0: Khởi tạo hạ tầng database

**Mục tiêu:** Tạo PostgreSQL container với TimescaleDB + PostGIS extensions

**Prompt:**
```
Tạo hạ tầng database trong thư mục infra/:
1. Tạo docker-compose.yml với service db (PostgreSQL 16)
2. Tạo thư mục db/init/ với script bật extensions (TimescaleDB, PostGIS, uuid-ossp)
3. Tạo .env.example với POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
4. Cập nhật backend/.env.example với DATABASE_URL khớp với infra/.env.example
```

**Lưu ý quan trọng:**
- Image `postgres:16` mặc định **không có** TimescaleDB và PostGIS
- Cho development cơ bản (CRUD vehicles), image này đủ dùng
- Khi cần dùng TimescaleDB/PostGIS (domain telemetry, charging), cần chuyển sang:
  - `timescale/timescaledb-ha:pg16` (có TimescaleDB)
  - `postgis/postgis:16-3.4` (có PostGIS)
  - Hoặc build custom image có cả 2 extensions

**Lệnh chạy:**
```bash
# Copy .env.example to .env
cp infra/.env.example infra/.env

# Khởi động container
docker compose -f infra/docker-compose.yml up -d

# Kiểm tra container đang chạy
docker ps

# Xem logs (nếu cần)
docker compose -f infra/docker-compose.yml logs -f db

# Dừng và xóa container (giữ volume)
docker compose -f infra/docker-compose.yml down

# Dừng và xóa cả volume (reset database)
docker compose -f infra/docker-compose.yml down -v
```

**Kiểm tra:**
- [ ] Container `g3network-db` đang chạy
- [ ] Port 5432 accessible
- [ ] Extensions đã được bật trong database

---

### Bước 1: Thiết lập môi trường backend

**Mục tiêu:** Khởi tạo project backend với FastAPI + uv

**Prompt:**
```
Khởi tạo project backend trong thư mục backend/ với:
- Python 3.12
- FastAPI
- uv để quản lý dependency (pyproject.toml + uv.lock)
- Cấu trúc thư mục theo AGENTS.md:
  - backend/app/domains/vehicles/
  - backend/app/api/main.py
  - backend/app/libs/db/
- File .env.example với DATABASE_URL
- File .gitignore cho Python
```

**Kiểm tra:**
- [ ] `uv sync` chạy thành công
- [ ] `uv run uvicorn api.main:app --reload` khởi động được server
- [ ] Truy cập `http://localhost:8000/docs` thấy Swagger UI

---

### Bước 2: Thiết lập database connection

**Mục tiêu:** Kết nối PostgreSQL, tạo base model

**Prompt:**
```
Thiết lập kết nối database trong backend/app/libs/db/:
1. Tạo base.py với SQLAlchemy declarative_base
2. Tạo session management (async session)
3. Cấu hình database URL từ environment variable
4. Tạo hàm get_db dependency để inject vào router
```

**Kiểm tra:**
- [ ] Server khởi động không lỗi khi có DATABASE_URL hợp lệ
- [ ] Connection pool hoạt động

---

### Bước 3: Tạo models.py (SQLAlchemy)

**Mục tiêu:** Định nghĩa bảng `vehicles` trong database

**Prompt:**
```
Tạo backend/app/domains/vehicles/models.py với SQLAlchemy model Vehicle:

Bảng vehicles:
- id: UUID primary key
- plate_number: string, unique, not null (biển số xe)
- make: string, not null (hãng xe: VinFast, Hyundai...)
- model: string, not null (dòng xe)
- year: integer (năm sản xuất)
- vin: string, unique (số khung)
- battery_capacity_kwh: decimal (dung lượng pin)
- max_range_km: integer (quãng đường tối đa khi đầy pin)
- status: enum (active, inactive, maintenance)
- team_id: UUID foreign key (nullable, để sau tích hợp fleet)
- created_at: timestamp
- updated_at: timestamp
- deleted_at: timestamp (nullable, soft delete)

Lưu ý:
- Dùng async SQLAlchemy
- Import base từ libs.db.base
- Thêm docstring mô tả bảng
```

**Kiểm tra:**
- [ ] Model không có lỗi syntax
- [ ] Các trường đúng kiểu dữ liệu
- [ ] Có docstring đầy đủ

---

### Bước 4: Tạo schemas.py (Pydantic)

**Mục tiêu:** Định nghĩa request/response schemas

**Prompt:**
```
Tạo backend/app/domains/vehicles/schemas.py với Pydantic models:

1. VehicleBase:
   - plate_number: str
   - make: str
   - model: str
   - year: int | None
   - vin: str | None
   - battery_capacity_kwh: Decimal | None
   - max_range_km: int | None
   - status: VehicleStatus (enum)

2. VehicleCreate (extends VehicleBase):
   - team_id: UUID | None

3. VehicleUpdate:
   - Tất cả fields optional
   - Không cho update plate_number (hoặc cho phép nếu business yêu cầu)

4. VehicleResponse (extends VehicleBase):
   - id: UUID
   - team_id: UUID | None
   - created_at: datetime
   - updated_at: datetime

5. VehicleListResponse:
   - items: list[VehicleResponse]
   - total: int
   - page: int
   - page_size: int

Lưu ý:
- Dùng Pydantic v2
- Thêm examples cho mỗi schema
- Thêm docstring
```

**Kiểm tra:**
- [ ] Các schema không có lỗi syntax
- [ ] Examples hiển thị đúng trong Swagger
- [ ] Docstring đầy đủ

---

### Bước 5: Tạo repository.py

**Mục tiêu:** Xử lý truy vấn database

**Prompt:**
```
Tạo backend/app/domains/vehicles/repository.py với các hàm async:

1. create_vehicle(db, vehicle_data) -> Vehicle
2. get_vehicle_by_id(db, vehicle_id) -> Vehicle | None
3. get_vehicle_by_plate(db, plate_number) -> Vehicle | None
4. get_vehicles(db, skip, limit, status_filter) -> list[Vehicle]
5. count_vehicles(db, status_filter) -> int
6. update_vehicle(db, vehicle_id, update_data) -> Vehicle | None
7. soft_delete_vehicle(db, vehicle_id) -> Vehicle | None

Lưu ý:
- Dùng async session
- Soft delete: set deleted_at thay vì xoá thật
- Filter: chỉ lấy record có deleted_at is None
- Thêm docstring cho mỗi hàm
```

**Kiểm tra:**
- [ ] Các hàm không có lỗi syntax
- [ ] Type hints đầy đủ
- [ ] Docstring đầy đủ

---

### Bước 6: Tạo service.py

**Mục tiêu:** Business logic layer

**Prompt:**
```
Tạo backend/app/domains/vehicles/service.py với các hàm:

1. create_vehicle(db, vehicle_data) -> VehicleResponse
   - Kiểm tra plate_number đã tồn tại chưa
   - Nếu trùng, raise HTTPException 400

2. get_vehicle(db, vehicle_id) -> VehicleResponse
   - Nếu không tìm thấy, raise HTTPException 404

3. list_vehicles(db, page, page_size, status) -> VehicleListResponse
   - Validate page, page_size
   - Gọi repository để lấy data

4. update_vehicle(db, vehicle_id, update_data) -> VehicleResponse
   - Kiểm tra vehicle tồn tại
   - Nếu update plate_number, kiểm tra trùng

5. delete_vehicle(db, vehicle_id) -> dict
   - Soft delete
   - Trả về {"message": "Vehicle deleted successfully"}

Lưu ý:
- Mọi I/O qua repository, KHÔNG query trực tiếp trong service
- Xử lý business exception
- Thêm docstring
```

**Kiểm tra:**
- [ ] Các hàm không có lỗi syntax
- [ ] Exception handling đúng
- [ ] Docstring đầy đủ

---

### Bước 7: Tạo router.py

**Mục tiêu:** Định nghĩa API endpoints

**Prompt:**
```
Tạo backend/app/domains/vehicles/router.py với FastAPI APIRouter:

Endpoints:
1. POST /vehicles
   - Tạo xe mới
   - Request: VehicleCreate
   - Response: VehicleResponse (201)

2. GET /vehicles
   - Danh sách xe (phân trang)
   - Query params: page, page_size, status
   - Response: VehicleListResponse (200)

3. GET /vehicles/{vehicle_id}
   - Chi tiết 1 xe
   - Response: VehicleResponse (200)

4. PATCH /vehicles/{vehicle_id}
   - Cập nhật thông tin xe
   - Request: VehicleUpdate
   - Response: VehicleResponse (200)

5. DELETE /vehicles/{vehicle_id}
   - Soft delete xe
   - Response: {"message": "..."} (200)

Lưu ý:
- Dùng async def
- Inject db session qua Depends(get_db)
- Thêm tags=["vehicles"] cho Swagger grouping
- Thêm response_model cho mỗi endpoint
- Thêm docstring cho mỗi endpoint
```

**Kiểm tra:**
- [ ] Router không có lỗi syntax
- [ ] Swagger hiển thị đúng endpoints
- [ ] Docstring hiển thị trong Swagger

---

### Bước 8: Mount router vào main.py

**Mục tiêu:** Kết nối router vào app chính

**Prompt:**
```
Cập nhật backend/app/api/main.py:
1. Import router từ domains.vehicles.router
2. Tạo FastAPI app với title, description
3. Include router với prefix="/api/v1"
4. Thêm health check endpoint GET /health
5. Thêm CORS middleware (cho phép tất cả origins trong dev)
```

**Kiểm tra:**
- [ ] Server khởi động không lỗi
- [ ] Truy cập `/docs` thấy tất cả endpoints
- [ ] Health check trả về 200

---

### Bước 9: Tạo Alembic migration

**Mục tiêu:** Tạo migration để tạo bảng vehicles

**Prompt:**
```
Thiết lập Alembic và tạo migration đầu tiên:
1. Init Alembic trong backend/
2. Cấu hình alembic.ini với database URL từ env
3. Cấu hình env.py để hỗ trợ async
4. Import Vehicle model trong env.py
5. Tạo migration: alembic revision --autogenerate -m "create vehicles table"
6. Chạy migration: alembic upgrade head
```

**Kiểm tra:**
- [ ] Migration chạy thành công
- [ ] Bảng vehicles được tạo trong database
- [ ] Các cột đúng kiểu dữ liệu

---

### Bước 10: Test CRUD qua Swagger

**Mục tiêu:** Kiểm tra tất cả endpoints hoạt động

**Prompt:**
```
Không cần code, chỉ test thủ công qua Swagger UI:

Test case 1: Tạo xe mới
- POST /api/v1/vehicles
- Body: {"plate_number": "51A-12345", "make": "VinFast", "model": "e34", "year": 2024, "status": "active"}
- Kiểm tra: 201, trả về vehicle với id

Test case 2: Lấy danh sách xe
- GET /api/v1/vehicles
- Kiểm tra: 200, trả về danh sách có xe vừa tạo

Test case 3: Lấy chi tiết xe
- GET /api/v1/vehicles/{id}
- Kiểm tra: 200, trả về đúng xe

Test case 4: Cập nhật xe
- PATCH /api/v1/vehicles/{id}
- Body: {"year": 2025}
- Kiểm tra: 200, year đã đổi

Test case 5: Xoá xe
- DELETE /api/v1/vehicles/{id}
- Kiểm tra: 200
- GET lại danh sách: xe không còn trong danh sách

Test case 6: Tạo xe trùng biển số
- POST với plate_number đã tồn tại
- Kiểm tra: 400, message lỗi rõ ràng
```

**Kiểm tra:**
- [ ] Tất cả test cases pass
- [ ] Error responses đúng format
- [ ] Soft delete hoạt động

---

### Bước 11: Tạo file .md đi kèm

**Mục tiêu:** Tài liệu mô tả cho domain vehicles

**Prompt:**
```
Tạo file backend/app/domains/vehicles/vehicles.md mô tả:
- Mục đích của domain vehicles
- Các endpoints và cách sử dụng
- Các trường trong model Vehicle
- Lưu ý khi sử dụng (soft delete, validation...)
- Link đến mã chức năng AD-05 trong feature-list
```

**Kiểm tra:**
- [ ] File .md đầy đủ nội dung
- [ ] Đặt đúng vị trí

---

## Thứ tự thực hiện khuyến nghị

```
Bước 1 → Bước 2 → Bước 3 → Bước 4 → Bước 5 → Bước 6 → Bước 7 → Bước 8 → Bước 9 → Bước 10 → Bước 11
```

**Lưu ý:**
- Mỗi bước nên làm riêng biệt, test kỹ trước khi sang bước tiếp
- Nếu gặp lỗi, dừng và sửa ngay
- Commit code sau mỗi bước hoàn thành

---

## Checklist tổng kết

Sau khi hoàn thành tất cả bước:

- [ ] Backend chạy ổn định trên `localhost:8000`
- [ ] Swagger UI hiển thị đầy đủ endpoints tại `/docs`
- [ ] Database có bảng `vehicles` với đúng schema
- [ ] CRUD operations hoạt động qua Swagger
- [ ] Soft delete hoạt động đúng
- [ ] Validation (plate_number unique) hoạt động
- [ ] Error responses đúng format
- [ ] Code có docstring đầy đủ
- [ ] File .md đi kèm đã tạo

---

## Ghi chú

- **Chưa bao gồm:** Gán thiết bị telematics, tích hợp với domain fleet/teams, authentication/authorization
- **Mở rộng sau:** API gán thiết bị, API lấy telemetry của xe, filter theo team_id
