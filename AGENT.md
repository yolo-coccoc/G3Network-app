# AGENT.md — Hệ thống hỗ trợ tài xế lái xe tải điện

> Tài liệu định hướng cho AI coding agent (Claude Code) và developer khi làm việc trong repo này.
> Đây là dự án **mới, chưa có code**, tổ chức dạng **monorepo**. Nếu có mâu thuẫn giữa file này và
> code thực tế đã tồn tại, **code thực tế luôn đúng hơn** — hãy cập nhật lại AGENT.md khi phát hiện lệch.

- `docs/01-requirements/feature-list.md` - Đặc tả chức năng theo actor, và status

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
| Message broker (ingest dữ liệu IoT từ thiết bị telematics) | **EMQX** | đã chốt |
| OCPP Gateway (giao tiếp trụ sạc) | Nằm **trong domain `charging`** (thư mục `charging/ocpp/`), dùng thư viện `python-ocpp`; chạy container runtime riêng qua `entrypoint.py` riêng | vì là kết nối WebSocket dài hạn, khác REST API thường, nhưng vẫn chỉ phục vụ domain `charging` nên đặt code cạnh nhau |
| Reverse proxy / API Gateway | **Không dùng ở môi trường dev** (mỗi thành phần chạy port riêng trên host, gọi thẳng qua `localhost`) | cân nhắc lại (Traefik/Nginx) khi làm `docker-compose.prod.yml` |
| State management (Web) | TanStack Query (server state) + Zustand (client state) | đề xuất |
| UI kit (Web) | Tailwind CSS + shadcn/ui | đề xuất |
| State management (Vehicle App) | Riverpod | đề xuất |
| Môi trường phát triển | **Dev trực tiếp trên host**; chỉ Docker hoá phần hạ tầng không cần sửa code trực tiếp (`db`, `broker`) | xem Mục 4 |

---

## 2. Cấu trúc thư mục (Monorepo)

Cấu trúc chi tiết dưới đây tập trung vào **`backend/`** và **`web-portal/`** — 2 phần được tổ chức theo **domain nghiệp vụ** (bounded context), thay vì chia theo layer kỹ thuật chung. `vehicle-app/`, `infra/`, `docs/` giữ nguyên vị trí như một monorepo thông thường.

```
.
├── backend/
│   ├── app/                        # Source code chính (uv package layout)
│   │   ├── domains/
│   │   │   ├── identity/              # Auth & RBAC (AD-01) — domain nền tảng
│   │   │   │   ├── router.py  service.py  repository.py  schemas.py  models.py
│   │   │   │
│   │   │   ├── vehicles/              # Hồ sơ tĩnh, provisioning, kích hoạt/hủy kích hoạt (AD-05)
│   │   │   │   ├── router.py  service.py  repository.py  schemas.py  models.py
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
└── AGENT.md
```

---

## 3. Quy tắc ranh giới domain (backend)

- Mỗi thư mục trong `backend/domains/` là **1 bounded context**. Domain này chỉ được gọi sang domain khác qua **`service.py` công khai** của domain đó — **không** import/query chéo trực tiếp `repository.py`/`models.py` của domain khác.
  - Quy tắc này chỉ áp dụng **giữa các domain khác nhau**. Việc gọi trực tiếp giữa các file **trong cùng 1 domain** là hợp lệ (VD: `telemetry/ingestion/mqtt_consumer.py` gọi thẳng `telemetry/repository.py` — cùng nằm trong domain `telemetry`, không vi phạm quy tắc).
- **`identity`** là domain nền tảng: mọi domain khác được phép phụ thuộc vào nó (qua `service.py`), bản thân nó không phụ thuộc ngược lại domain nào.
- **`telemetry`** là domain dữ liệu thời gian thực: nhiều domain khác (`charging`, `fleet`, `notifications`, `scoring`) phụ thuộc vào nó để lấy dữ liệu realtime/lịch sử; bản thân `telemetry` chỉ phụ thuộc `vehicles` (để lấy `vehicle_id`/chủ sở hữu, phục vụ phân quyền theo đội).
- Các chiều phụ thuộc chi tiết khác giữa từng chức năng cụ thể **không liệt kê lại ở đây** — đã có đầy đủ trong cột "Phụ thuộc" của `docs/Chuc_nang_tong_hop.xlsx`; AGENT.md chỉ nêu nguyên tắc chung ở cấp domain.
- Domain mới được thêm vào phải tham chiếu đúng mã chức năng trong `docs/Chuc_nang_tong_hop.xlsx` (VD: `AD-03`, `D-05`).
- Dùng **`import-linter`** (Python) để chặn ở mức CI nếu domain A import trực tiếp vào nội bộ (`repository`/`models`) của domain B — biến quy tắc trên thành ràng buộc kỹ thuật, không chỉ là quy ước bằng lời.

---

## 4. Môi trường phát triển

**Nguyên tắc:** phát triển **trực tiếp trên host**. Chỉ Docker hoá các thành phần hạ tầng không cần chỉnh sửa trực tiếp thường xuyên.

### 4.1 Chạy trên host

| Thành phần | Công cụ |
|---|---|
| Backend (`api`, và các entrypoint trong `telemetry/ingestion`, `charging/ocpp`) | `uv` — `uv run uvicorn ...`, `uv run python -m domains.telemetry.ingestion.entrypoint`... |
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
- Format: **Black** (line length 88) + **isort** — chạy qua `uv run black .`, `uv run isort .` hoặc pre-commit.
- Lint: **Ruff** (`uv run ruff check`).
- Type checking: type hint bắt buộc cho function signature; dùng **mypy** hoặc **Pyright** (`uv run mypy .`).
- Mỗi domain trong `backend/domains/<ten_domain>/` tự có đủ layer riêng:
  - `router.py` — định nghĩa endpoint (FastAPI `APIRouter`), không chứa business logic.
  - `service.py` — business logic thuần Python; đây là **giao diện công khai duy nhất** để domain khác gọi vào.
  - `repository.py` — truy vấn DB (SQLAlchemy), không chứa business logic.
  - `schemas.py` — Pydantic models cho request/response.
  - `models.py` — SQLAlchemy models.
- Naming: `snake_case` cho biến/hàm/module, `PascalCase` cho class, hằng số `UPPER_SNAKE_CASE`.
- Toàn bộ I/O (DB, HTTP, MQTT) dùng **async/await**.
- Migration: **Alembic** (`uv run alembic ...`), mỗi migration có message rõ ràng, không sửa migration đã merge vào `main`.
- Docstring bắt buộc cho service function xử lý nghiệp vụ phức tạp (đối soát vi phạm, tính KPI...).

#### 5.1.1 SQLAlchemy Models — Primary Key Convention
- **Mọi bảng PHẢI có internal ID** (không dùng business key như license_plate, VIN làm PK):
  ```python
  # ✅ ĐÚNG: Internal auto-increment ID
  class Vehicle(Base):
      __tablename__ = "vehicles"
      
      vehicle_id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
      license_plate: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
      vin: Mapped[str] = mapped_column(String(17), unique=True, nullable=False, index=True)
  
  # ❌ SAI: Dùng business key làm PK
  class Vehicle(Base):
      __tablename__ = "vehicles"
      
      license_plate: Mapped[str] = mapped_column(String(20), primary_key=True)  # KHÔNG BAO GIỜ
  ```
- **Lý do:**
  - Business key có thể thay đổi (biển số xe đổi khi chuyển vùng, VIN có thể nhập sai cần sửa)
  - Internal ID bất biến, ổn định cho foreign key references
  - Auto-increment integer hiệu năng cao hơn UUID string
- **Foreign key** luôn tham chiếu đến internal ID:
  ```python
  # ✅ ĐÚNG
  charging_session.vehicle_id  # → Vehicle.vehicle_id (int)
  
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
- Khi thêm chức năng mới, đối chiếu lại mã chức năng tương ứng trong `docs/Chuc_nang_tong_hop.xlsx` (VD: `AD-03`, `D-05`) để giữ nhất quán giữa code và đặc tả.
- **KHÔNG tự ý bổ sung thành phần mới** (middleware, library, config, infrastructure...) mà **phải hỏi ý kiến bạn trước**. Chỉ triển khai những gì được yêu cầu rõ ràng trong planner hoặc prompt.

---

## 6. Database

- 1 instance PostgreSQL duy nhất, bật 2 extension: `timescaledb`, `postgis` (script khởi tạo ở `infra/db/init/`).
- Bảng dữ liệu time-series (telemetry xe, trạng thái trụ sạc, phiên sạc) tạo dưới dạng **hypertable** (TimescaleDB) để tối ưu truy vấn/nén dữ liệu lịch sử.
- Cột vị trí (GPS xe, vị trí trạm sạc, geofence) dùng kiểu `geometry`/`geography` (PostGIS).
- Naming bảng: số nhiều, `snake_case` (`vehicles`, `charging_sessions`, `alerts`, `policy_configs`...).
- Migration quản lý bằng Alembic, đặt trong `backend/libs/db/migrations/`.

---

## 7. Tài liệu đi kèm mã nguồn (bắt buộc)

- Mọi file source code **có logic đáng kể** (loại trừ file boilerplate/gần như rỗng như `__init__.py`, file config đơn giản) **bắt buộc** phải có 1 file `.md` mô tả nội dung đi kèm 1-1.
- File `.md` đặt **cùng thư mục, cùng tên** với file gốc (VD: `service.py` ⇄ `service.md`, `VehicleMap.tsx` ⇄ `VehicleMap.md`).
- Đây là **quy ước bằng lời**, hiện chưa có script/CI kiểm tra tự động — sẽ được rà soát thủ công định kỳ.
- Không có khung mẫu nội dung cố định; chỉ cần mô tả rõ được nội dung/vai trò của file nguồn tương ứng. Các file `.md` này thường do AI agent tự sinh khi tạo/sửa file nguồn.

---

## 8. Câu hỏi / quyết định cần bạn xác nhận thêm

1. **Giao thức kết nối cục bộ** giữa Thiết bị Telematics ↔ Màn hình xe (BLE/Wi-Fi Direct/CAN...) — vẫn đang "Cần thông tin" từ bảng chức năng, cần chốt trước khi code module `core/` trong `vehicle-app`.
2. **CI/CD**: dùng nền tảng nào (GitHub Actions, GitLab CI, Jenkins...)?
3. **Quản lý secrets khi lên môi trường thật** (Vault, AWS/GCP Secrets Manager, hay chỉ dùng `.env` + CI secrets)?

Phần nào bạn chưa quyết định, cứ để tôi giữ nguyên đề xuất tạm ở trên và triển khai theo, sau này đổi cũng không ảnh hưởng lớn tới cấu trúc thư mục.
