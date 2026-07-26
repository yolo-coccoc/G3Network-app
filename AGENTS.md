# AGENTS.md — Hệ thống hỗ trợ tài xế lái xe tải điện

> Tài liệu định hướng cho AI coding agent và developer khi làm việc trong repo monorepo này.
> Dự án đã có code đang phát triển. Khi code, planner, requirements và tài liệu này không khớp,
> phải xác định quyết định mới nhất; không mặc định sao chép pattern hiện có nếu pattern đó vi phạm
> convention. Cập nhật lại AGENTS.md sau khi quyết định được xác nhận.

- `docs/01-requirements/feature-list.md` - Đặc tả chức năng theo actor, và status
- `docs/01-requirements/future.md` - Thành phần hoãn lại (bỏ qua tạm thời để sớm hoàn thành MVP)

---

## 1. Quyết định kỹ thuật đã chọn (rà lại nếu thấy chưa phù hợp)

| Hạng mục | Lựa chọn | Ghi chú |
|---|---|---|
| Backend | **Python 3.12 + FastAPI** | đã chốt |
| Quản lý môi trường/dependency Backend | **`uv`** (`pyproject.toml` + `uv.lock`) | đã chốt |
| Web Portal (Admin + Quản lý đội xe) | **React 18 + TypeScript + Vite** | đề xuất — không cần SSR/SEO nên không chọn Next.js |
| Package manager Web Portal | **pnpm** | đã chốt |
| Vehicle App (màn hình trên xe) | **Flutter (Dart)**, build ra APK chạy trên **Android** | đã chốt |
| Database | **PostgreSQL 16 + TimescaleDB (time-series) + PostGIS (địa lý)** | đã chốt |
| Message broker (ingest dữ liệu IoT từ thiết bị telematics) | **EMQX 5.5** | đã chốt - Lưu ý: EMQX 5.x không dùng file `acl.conf` như EMQX 4.x, ACL được cấu hình qua Dashboard UI hoặc REST API |
| OCPP Gateway (giao tiếp trụ sạc) | Nằm **trong domain `charging`** (thư mục `charging/ocpp/`), dùng thư viện `python-ocpp`; chạy container runtime riêng qua `entrypoint.py` riêng | vì là kết nối WebSocket dài hạn, khác REST API thường, nhưng vẫn chỉ phục vụ domain `charging` nên đặt code cạnh nhau |
| Reverse proxy / API Gateway | **Không dùng ở môi trường dev** (mỗi thành phần chạy port riêng trên host, gọi thẳng qua `localhost`) | cân nhắc lại (Traefik/Nginx) khi làm `docker-compose.prod.yml` |
| State management (Web) | TanStack Query (server state) + Zustand (client state) | đề xuất |
| UI kit (Web) | Tailwind CSS + shadcn/ui | đề xuất |
| State management (Vehicle App) | Riverpod | đề xuất |
| Môi trường phát triển | **Dev trực tiếp trên host**; chỉ Docker hoá phần hạ tầng không cần sửa code trực tiếp (`db`, `broker`) | xem Mục 4 |

---

## 2. Cấu trúc thư mục (Monorepo)

Cấu trúc mục tiêu dưới đây tập trung vào **`backend/`** và **`web-portal/`** — 2 phần được tổ chức theo **domain nghiệp vụ** (bounded context), thay vì chia theo layer kỹ thuật chung. File/domain thuộc planner chưa hoàn thành có thể chưa tồn tại trong source hiện tại; không tạo placeholder chỉ để khớp cây thư mục. `vehicle-app/`, `infra/`, `docs/` giữ nguyên vị trí như một monorepo thông thường.

```
.
├── backend/
│   ├── app/                        # Source code chính (uv package layout)
│   │   ├── domains/
│   │   │   ├── identity/              # Auth & RBAC (AD-01) — domain nền tảng
│   │   │   │   ├── router.py  service.py  repository.py  schemas.py  models.py  types.py  exceptions.py
│   │   │   │
│   │   │   ├── vehicles/              # Hồ sơ tĩnh, provisioning, kích hoạt/hủy kích hoạt (AD-05)
│   │   │   │   ├── router.py  service.py  repository.py  schemas.py  models.py  types.py  exceptions.py
│   │   │   │
│   │   │   ├── telemetry/             # Dữ liệu thời gian thực & lịch sử của xe (AD-02, FM-01, FM-02, AD-08)
│   │   │   │   ├── router.py  service.py  repository.py  schemas.py  models.py
│   │   │   │   └── ingestion/         # Nhận dữ liệu telematics qua MQTT (EMQX)
│   │   │   │       ├── mqtt_consumer.py
│   │   │   │       └── entrypoint.py  # container "telemetry-ingestion" trỏ vào đây
│   │   │   │
│   │   │   ├── charging/              # Trụ sạc, phiên sạc, đối soát vi phạm (S-02, AD-03)
│   │   │   │   ├── router.py  service.py  repository.py  schemas.py  models.py
│   │   │   │   └── ocpp/              # WebSocket server giao tiếp trụ sạc (OCPP)
│   │   │   │       ├── ocpp_server.py
│   │   │   │       └── entrypoint.py  # container "charging-ocpp" trỏ vào đây
│   │   │   │
│   │   │   ├── policy/                # Chính sách sạc/bảo hành (AD-04)
│   │   │   ├── notifications/         # Cấu hình ngưỡng & kênh thông báo (AD-06)
│   │   │   ├── drivers/                # Hồ sơ & phân công tài xế (FM-04)
│   │   │   ├── fleet/                  # Dashboard KPI, báo cáo theo đội xe (FM-01…FM-07)
│   │   │   ├── billing/                # Gói dịch vụ, thuê bao (AD-09)
│   │   │   ├── support/                # CSKH/ticket (AD-07)
│   │   │   └── scoring/                 # Chấm điểm hành vi lái (AD-08)
│   │   │
│   │   ├── api/
│   │   │   └── main.py                # FastAPI app gộp router từ tất cả domains/*/router.py → container "api"
│   │   │
│   │   └── libs/
│   │       ├── common/                 # config, logging — dùng chung, KHÔNG chứa nghiệp vụ
│   │       └── db/                     # SQLAlchemy base, Alembic migrations dùng chung
│   │
│   ├── pyproject.toml
│   ├── uv.lock
│   └── Dockerfile                  # dùng cho production, chưa cần hoàn thiện ở giai đoạn này
│
├── web-portal/                     # Admin + Quản lý đội xe (RBAC phân quyền trong cùng 1 app)
│   ├── src/
│   │   ├── features/
│   │   │   ├── vehicles/           # ánh xạ domain vehicles
│   │   │   ├── charging/           # ánh xạ domain charging + policy
│   │   │   ├── fleet/              # ánh xạ domain fleet + drivers
│   │   │   ├── billing/            # ánh xạ domain billing
│   │   │   ├── support/            # ánh xạ domain support
│   │   │   ├── scoring/            # ánh xạ domain scoring
│   │   │   └── settings/           # ánh xạ domain identity (user/role) + notifications
│   │   ├── components/             # component dùng chung (design system)
│   │   ├── lib/                    # api client, hooks dùng chung
│   │   └── app/                    # routing, layout gốc
│   ├── package.json
│   ├── pnpm-lock.yaml
│   └── Dockerfile
│
├── vehicle-app/                    # Flutter — màn hình trên xe cho tài xế
│   ├── lib/
│   │   ├── features/               # checkin/, realtime_data/, alerts/, map/, support/, payment/
│   │   │   # ⚠️ Lưu ý: cấu trúc feature ở đây được giữ nguyên từ bản đầu, CHƯA được rà soát lại
│   │   │   # cho khớp với domain mới bên backend (vehicles/telemetry/charging...).
│   │   │   # Cần rà soát khi triển khai tới phần vehicle-app.
│   │   ├── core/                   # network client, local storage, kết nối cục bộ tới thiết bị telematics
│   │   └── main.dart
│   ├── pubspec.yaml
│   └── Dockerfile.ci               # chỉ dùng để build/test trong CI, KHÔNG chạy trong docker-compose dev
│
├── infra/
│   ├── docker-compose.yml          # chỉ định nghĩa 2 service: db, broker (xem Mục 4)
│   ├── docker-compose.prod.yml     # (khung sẵn cho sau này)
│   └── db/
│       └── init/                   # script bật extension timescaledb, postgis khi khởi tạo DB
│
├── docs/                           # đặc tả chức năng, sơ đồ kiến trúc (đã có)
├── scripts/                        # script tiện ích (seed data, migrate, lint-all...)
├── .env.example
├── Makefile
└── AGENTS.md
```

---

## 3. Quy tắc ranh giới domain (backend)

- Mỗi thư mục trong `backend/app/domains/` là **1 bounded context**. Domain này chỉ được gọi sang domain khác qua **`service.py` công khai** của domain đó — **không** import/query chéo trực tiếp `repository.py`/`models.py` của domain khác.
  - Quy tắc này chỉ áp dụng **giữa các domain khác nhau**. Việc gọi trực tiếp giữa các file **trong cùng 1 domain** là hợp lệ (VD: `telemetry/ingestion/mqtt_consumer.py` gọi thẳng `telemetry/repository.py` — cùng nằm trong domain `telemetry`, không vi phạm quy tắc).
- **`identity`** là domain nền tảng: mọi domain khác được phép phụ thuộc vào nó (qua `service.py`), bản thân nó không phụ thuộc ngược lại domain nào.
- **`telemetry`** là domain dữ liệu thời gian thực: nhiều domain khác (`charging`, `fleet`, `notifications`, `scoring`) phụ thuộc vào nó để lấy dữ liệu realtime/lịch sử; bản thân `telemetry` chỉ phụ thuộc `vehicles` (để lấy `vehicle_id`/chủ sở hữu, phục vụ phân quyền theo đội).
- Các chiều phụ thuộc chi tiết khác giữa từng chức năng cụ thể **không liệt kê lại ở đây** — đã có đầy đủ trong cột "Phụ thuộc" của `docs/01-requirements/feature-list.md`; AGENTS.md chỉ nêu nguyên tắc chung ở cấp domain.
- Domain mới được thêm vào phải tham chiếu đúng mã chức năng trong `docs/01-requirements/feature-list.md` (VD: `AD-03`, `D-05`).
- Khi nền tảng CI/CD được chốt, bổ sung **`import-linter`** để chặn domain A import trực tiếp nội bộ (`repository`/`models`) của domain B. Hiện package/config này chưa được cài đặt, nên review và tìm kiếm import là bước bắt buộc.

---

## 4. Môi trường phát triển

**Nguyên tắc:** phát triển **trực tiếp trên host**. Chỉ Docker hoá các thành phần hạ tầng không cần chỉnh sửa trực tiếp thường xuyên.

### 4.1 Chạy trên host

| Thành phần | Công cụ |
|---|---|
| Backend (`api`, và các entrypoint trong `telemetry/ingestion`, `charging/ocpp`) | `uv` — `uv run uvicorn app.api.main:app`, `uv run python -m app.domains.telemetry.ingestion.entrypoint`... |
| Web Portal | `pnpm dev` |
| Vehicle App | `flutter run` |

### 4.2 Chạy trong Docker (`infra/docker-compose.yml`)

| Container | Thành phần | Cổng (host) |
|---|---|---|
| `db` | PostgreSQL + TimescaleDB + PostGIS | 5432 |
| `broker` | EMQX | 1883 (MQTT), 18083 (dashboard) |

Backend/web-portal chạy trên host kết nối vào 2 container này qua `localhost:5432` / `localhost:1883`.

### 4.3 Lệnh thường dùng

Sử dụng Makefile làm nguồn chân lý. Xem danh sách lệnh đầy đủ:

```bash
make help
```

Các lệnh cơ bản:
- `make infra-up` — Khởi động hạ tầng (PostgreSQL)
- `make backend-install` — Cài đặt dependencies
- `make backend-dev` — Chạy backend server
- `make db-migrate` — Chạy database migrations

Chi tiết cài đặt và chạy nhanh xem tại [README.md](./README.md).

### 4.4 Biến môi trường
- `.env.example` trỏ tới `localhost` (không dùng tên service Docker nội bộ), VD: `DATABASE_URL=postgresql://...@localhost:5432/...`, `MQTT_HOST=localhost`.

---

## 5. Coding Convention

### 5.1 Backend — Python / FastAPI
- Quản lý môi trường/dependency bằng **`uv`** (`pyproject.toml` + `uv.lock` là nguồn chân lý duy nhất; không dùng pip/poetry thuần song song).
- Format: **Black** (line length 88) + **isort**. Không để formatter sửa migration đã merge; khi format toàn repo phải exclude `app/libs/db/migrations/versions/`, còn migration mới phải được format trước khi merge.
- Lint: **Ruff** (`uv run ruff check`).
- Type checking: type hint bắt buộc cho function signature; dùng **mypy** hoặc **Pyright** (`uv run mypy .`).
- Mỗi domain trong `backend/app/domains/<ten_domain>/` dùng các module sau theo nhu cầu chức năng; không tạo file rỗng làm placeholder:
  - `router.py` — định nghĩa endpoint (FastAPI `APIRouter`), không chứa business logic.
  - `service.py` — business logic thuần Python; đây là **giao diện công khai duy nhất** để domain khác gọi vào.
  - `repository.py` — truy vấn DB (SQLAlchemy), không chứa business logic.
  - `schemas.py` — Pydantic models cho request/response.
  - `models.py` — SQLAlchemy models.
  - `types.py` — enum/value object dùng chung giữa các layer khi cần; không phụ thuộc FastAPI, Pydantic hoặc SQLAlchemy.
  - `exceptions.py` — domain exception thuần Python; router chuyển exception này thành lỗi transport tương ứng.
- Naming: `snake_case` cho biến/hàm/module, `PascalCase` cho class, hằng số `UPPER_SNAKE_CASE`.
- Toàn bộ I/O (DB, HTTP, MQTT) dùng **async/await**.
- Migration: **Alembic** (`uv run alembic ...`), mỗi migration có message rõ ràng, không sửa migration đã merge vào `main`.
- Docstring bắt buộc cho service function xử lý nghiệp vụ phức tạp (đối soát vi phạm, tính KPI...).
- **Comment/Docstring chi tiết cho mọi class và method**:
  - Mọi class PHẢI có docstring mô tả vai trò và danh sách `Attributes:` (nếu có).
  - Mọi method PHẢI có docstring mô tả ngắn gọn, danh sách `Args:` và `Returns:` (nếu có tham số/giá trị trả về).
  - Ví dụ:
    ```python
    class VehicleState(BaseModel):
        """
        Trạng thái xe từ telematic.
        
        Attributes:
            speed: Tốc độ (0-200 km/h)
            heading: Hướng di chuyển (0-360 độ, nullable)
            odometer: Tổng quãng đường đã đi (km)
        """
        speed: float | None
        heading: float | None
        odometer: float | None
    
    def to_db_dict(self, telematic_id: UUID, vehicle_id: UUID) -> dict:
        """
        Convert message thành dict phù hợp với VehicleTelemetry model.
        
        Args:
            telematic_id: UUID của telematic (lookup từ telematic_serial)
            vehicle_id: UUID của xe (lookup từ telematic_id)
            
        Returns:
            Dict với đầy đủ trường để insert vào DB
        """
        ...
    ```

#### 5.1.1 Database session và transaction

- Chỉ **entry boundary** được tạo và đóng `AsyncSession`:
  - FastAPI dùng `Depends(get_db)`.
  - Background worker/CLI dùng `async_session_factory` từ `app.libs.db.session`; không tự tạo engine hoặc session factory.
- Entry boundary sở hữu transaction:
  - HTTP dependency hoặc worker unit-of-work thực hiện commit/rollback.
  - `service.py` và `repository.py` **không** gọi `commit()` hoặc `rollback()`.
  - Repository được phép `flush()` khi cần phát hiện constraint error hoặc lấy generated value.
- Một business operation mặc định chạy trong một transaction atomic. Nếu cần chia transaction, phải xin xác nhận và mô tả rõ hành vi partial failure.
- `Base` chỉ được định nghĩa và import từ `app.libs.db.base`; `session.py` chỉ quản lý engine/session lifecycle.

#### 5.1.2 Quy ước thời gian

- Toàn backend và database dùng UTC timezone-aware.
- Python dùng `datetime.now(timezone.utc)`; không dùng `datetime.utcnow()`.
- SQLAlchemy timestamp dùng `DateTime(timezone=True)`.
- Timestamp từ API/MQTT phải có timezone và được normalize về UTC trước khi lưu.
- Biến môi trường phải có namespace theo component (`APP_`, `MQTT_`, `DB_`...); không dùng tên chung dễ va chạm như `DEBUG`, `HOST` hoặc `PORT`.

#### 5.1.3 Ranh giới layer và exception

- Router xử lý HTTP request/response/status code và chuyển domain exception thành `HTTPException`.
- Service không import FastAPI, không ném `HTTPException`, và chỉ chứa business logic.
- Repository chỉ truy cập DB; không chứa HTTP/business policy và không commit/rollback.
- Schema không import SQLAlchemy model. Enum/value object dùng chung được đặt trong `types.py`.
- Partial update dùng `PATCH` cùng `model_dump(exclude_unset=True)`.
- Với `VehicleUpdate`, cả “không gửi field” và “gửi field = null” đều có nghĩa không cập nhật field đó; service lọc `None` trước khi gọi repository. Nếu một chức năng cần xóa giá trị nullable, phải có contract riêng được xác nhận thay vì ngầm dùng `null`.
- Validation create/update phải nhất quán. Unique constraint DB là bảo vệ cuối cùng; `IntegrityError` phải được chuyển thành domain error phù hợp.

#### 5.1.4 Background worker, logging và error handling

- Một component duy nhất sở hữu lifecycle connect/run/stop của external client.
- Topic, QoS, batch size, queue size và timeout phải lấy từ settings/constructor; không hard-code khi đã có config.
- Với `asyncio.Queue`, mỗi `get()` thành công phải có đúng một `task_done()`.
- Graceful shutdown phải ngừng nhận message mới, drain queue theo policy, hoàn tất/rollback transaction hiện tại, rồi cancel và await task nếu quá timeout.
- Telemetry MVP dùng QoS 0, không retry và không DLQ; DB error phải rollback batch, log traceback và làm worker dừng.
- Dùng structured logging qua `extra`; không dùng f-string trong logger call. Exception bất ngờ dùng `logger.exception()`.
- Không dùng `except Exception` ngoài process/task boundary.
- Các metric `processed`, `skipped`, `errors`, `dropped` phải có định nghĩa và phản ánh đúng kết quả.

#### 5.1.5 Kiểm tra tính nhất quán trước khi hoàn thành backend task

1. Tìm pattern tương tự đang tồn tại ở domain/entrypoint khác.
2. Không tạo engine, session factory, config hoặc logger riêng nếu shared implementation đã có.
3. Chạy Black, isort, Ruff và mypy. Trong MVP hiện chưa có automated test suite; phải chạy smoke test phù hợp cho phần thay đổi và ghi rõ phạm vi đã kiểm tra. Khi test suite được bổ sung, mọi test liên quan phải chạy trước khi hoàn thành task.
4. Kiểm tra `__init__.py` chỉ chứa docstring.
5. Không để placeholder/TODO cho thành phần chắc chắn cần về sau; chuyển sang `docs/01-requirements/future.md`.
6. Review migration về timezone, FK, index, constraint, PostGIS/TimescaleDB, upgrade và downgrade.
7. Nếu code mới cần convention khác, phải cập nhật AGENTS.md hoặc xin xác nhận trước khi triển khai.

#### 5.1.6 SQLAlchemy Models — Primary Key Convention
- **Mọi bảng PHẢI có internal ID** (không dùng business key như license_plate, VIN làm PK):
  ```python
  # ✅ ĐÚNG: Internal ID (UUID hoặc auto-increment tùy trường hợp)
  class Vehicle(Base):
      __tablename__ = "vehicles"
      
      # Option 1: UUID (phù hợp cho distributed systems)
      vehicle_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
      
      # Option 2: Auto-increment (phù hợp cho single database)
      # vehicle_id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
      
      license_plate: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
      vin: Mapped[str] = mapped_column(String(17), unique=True, nullable=False, index=True)
  
  # ❌ SAI: Dùng business key làm PK
  class Vehicle(Base):
      __tablename__ = "vehicles"
      
      license_plate: Mapped[str] = mapped_column(String(20), primary_key=True)  # KHÔNG BAO GIỜ
  ```
- **Lựa chọn UUID vs Auto-increment:**
  - **UUID**: Phù hợp cho distributed systems, telematic devices có thể tạo ID từ nhiều nguồn, không cần central coordination
  - **Auto-increment**: Phù hợp cho single database, hiệu năng cao hơn, dễ debug
  - **Quyết định**: Dùng UUID cho các bảng chính (vehicles, telematics) vì hệ thống có thể mở rộng sang distributed architecture
- **Foreign key** luôn tham chiếu đến internal ID:
  ```python
  # ✅ ĐÚNG
  charging_session.vehicle_id  # → Vehicle.vehicle_id (UUID hoặc int)
  
  # ❌ SAI
  charging_session.vehicle_license_plate  # → Vehicle.license_plate (business key)
  ```

### 5.2 Web Portal — React / TypeScript
- Package manager: **pnpm**.
- Format: **Prettier**. Lint: **ESLint** (`@typescript-eslint/recommended` + `eslint-plugin-react-hooks`).
- Tổ chức theo **feature folder** (`src/features/<feature>/{components,hooks,api,types}.ts`), tên feature ánh xạ theo tên domain backend tương ứng (xem Mục 2).
- Naming: component `PascalCase.tsx`, hook `useXxx.ts`, biến/hàm `camelCase`, type/interface `PascalCase`.
- Toàn bộ gọi API qua 1 lớp `lib/api-client` dùng chung, không gọi `fetch` rải rác trong component.
- Không dùng `any`; bật `strict: true` trong `tsconfig.json`.

### 5.3 Vehicle App — Flutter / Dart
- Áp dụng **Effective Dart** + gói `flutter_lints`.
- Kiến trúc theo feature (`lib/features/<feature>/{presentation,domain,data}`), quản lý state bằng Riverpod.
- Naming: biến/hàm `lowerCamelCase`, class `UpperCamelCase`, tên file `snake_case.dart`.
- Kết nối cục bộ tới thiết bị telematics (giao thức **[Cần xác định]** — xem Mục 8) tách riêng thành 1 package/module trong `core/` để dễ thay đổi giao thức sau này mà không ảnh hưởng UI.

### 5.4 Chung cho toàn repo
- Commit message theo **Conventional Commits**: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`, `perf:`.
  - Ví dụ: `feat(charging): thêm endpoint đối soát vi phạm sạc (AD-03)`
- Branch: `feature/<mo-ta-ngan>`, `fix/<mo-ta-ngan>`, `chore/<mo-ta-ngan>`.
- Mọi PR phải qua review trước khi merge vào `main`; không push thẳng vào `main`.
- Không commit secrets — mọi config nhạy cảm qua `.env` (đã có `.env.example` làm mẫu, không chứa giá trị thật).
- Khi thêm chức năng mới, đối chiếu lại mã chức năng tương ứng trong `docs/01-requirements/feature-list.md` (VD: `AD-03`, `D-05`) để giữ nhất quán giữa code và đặc tả.
- **File `__init__.py` KHÔNG chứa code**: Mọi file `__init__.py` trong repo chỉ được chứa docstring mô tả module, KHÔNG được import hay export bất kỳ thứ gì. Các file khác cần import từ nhau phải import trực tiếp từ module (VD: `from app.domains.telemetry.models import Telematic` thay vì `from app.domains.telemetry import Telematic`).
- **KHÔNG tự ý bổ sung thành phần mới** (middleware, library, config, infrastructure...) mà **phải hỏi ý kiến bạn trước**. Chỉ triển khai những gì được yêu cầu rõ ràng trong planner hoặc prompt.
- **Khi bỏ qua/xóa thành phần** với lý do "hiện tại chưa cần, nhưng sau này chắc chắn phải thêm" (VD: middleware giữa frontend và backend, caching layer, rate limiting...):
  - **KHÔNG** đặt placeholder trong source code.
  - **PHẢI** ghi nhận vào `docs/01-requirements/future.md` với mô tả đầy đủ về: thành phần, tác dụng, vai trò trong hệ thống, lý do hoãn, liên quan đến planner/feature nào.
  - Nếu đã có placeholder cũ trong source, xóa sạch và chuyển thông tin sang future.md.

---

## 6. Database

- 1 instance PostgreSQL duy nhất, bật 2 extension: `timescaledb`, `postgis` (script khởi tạo ở `infra/db/init/`).
- Bảng dữ liệu time-series (telemetry xe, trạng thái trụ sạc, phiên sạc) tạo dưới dạng **hypertable** (TimescaleDB) để tối ưu truy vấn/nén dữ liệu lịch sử.
- Cột vị trí (GPS xe, vị trí trạm sạc, geofence) dùng kiểu `geometry`/`geography` (PostGIS), trừ ngoại lệ MVP đã được planner chốt và ghi trong `future.md`.
- Naming bảng mặc định là số nhiều, `snake_case` (`vehicles`, `charging_sessions`, `alerts`, `policy_configs`...). Ngoại lệ phải được planner hoặc migration đã chốt ghi rõ; telemetry MVP hiện dùng `vehicle_telemetry`.
- Migration quản lý bằng Alembic, đặt trong `backend/app/libs/db/migrations/`.

---

## 7. Câu hỏi / quyết định cần bạn xác nhận thêm

1. **Giao thức kết nối cục bộ** giữa Thiết bị Telematics ↔ Màn hình xe (BLE/Wi-Fi Direct/CAN...) — vẫn đang "Cần thông tin" từ bảng chức năng, cần chốt trước khi code module `core/` trong `vehicle-app`.
2. **CI/CD**: dùng nền tảng nào (GitHub Actions, GitLab CI, Jenkins...)?
3. **Quản lý secrets khi lên môi trường thật** (Vault, AWS/GCP Secrets Manager, hay chỉ dùng `.env` + CI secrets)?

Phần nào bạn chưa quyết định, cứ để tôi giữ nguyên đề xuất tạm ở trên và triển khai theo, sau này đổi cũng không ảnh hưởng lớn tới cấu trúc thư mục.
