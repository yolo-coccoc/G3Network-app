# AGENT.md — Hệ thống hỗ trợ tài xế lái xe tải điện

> Tài liệu định hướng cho AI coding agent (Claude Code) và developer khi làm việc trong repo này.
> Đây là dự án **mới, chưa có code**, tổ chức dạng **monorepo**. Nếu có mâu thuẫn giữa file này và
> code thực tế đã tồn tại, **code thực tế luôn đúng hơn** — hãy cập nhật lại AGENT.md khi phát hiện lệch.

Tài liệu tham chiếu (đặt tại `docs/`):
- Danh sách chức năng: `docs/01-requirements/features_list.md` - mô tả các chức năng và trạng thái

---

## 1. Quyết định kỹ thuật đã chọn (rà lại nếu thấy chưa phù hợp)

| Hạng mục | Lựa chọn | Ghi chú |
|---|---|---|
| Backend | **Python 3.12 + FastAPI** | đã chốt |
| Web Portal (Admin + Quản lý đội xe) | **React 18 + TypeScript + Vite** | đề xuất — phù hợp cho dashboard realtime nhiều bảng/biểu đồ, hệ sinh thái lớn, không cần SSR/SEO nên không chọn Next.js cho gọn |
| Vehicle App (màn hình trên xe) | **Flutter (Dart)**, build ra APK chạy trên **Android** (thiết bị đầu cuối) | đã chốt |
| Database | **PostgreSQL 16 + TimescaleDB (time-series) + PostGIS (địa lý)** | đã chốt — dùng 1 database, telemetry lưu dạng hypertable, vị trí/geofence dùng kiểu geometry |
| Message broker (ingest dữ liệu IoT từ thiết bị telematics) | **[Cần xác định]** — đề xuất tạm: MQTT broker (EMQX hoặc Mosquitto) | Xem mục 9 |
| OCPP Gateway (giao tiếp trụ sạc) | Tách thành service Python riêng dùng thư viện `python-ocpp` (WebSocket) | vì OCPP là kết nối WebSocket dài hạn, khác bản chất với REST API thường |
| Reverse proxy / API Gateway | **Traefik** (dev) hoặc **Nginx** | đề xuất — Traefik tiện cho local dev vì tự động discover container |
| State management (Web) | TanStack Query (server state) + Zustand (client state) | đề xuất |
| UI kit (Web) | Tailwind CSS + shadcn/ui | đề xuất |
| State management (Vehicle App) | Riverpod | đề xuất — ít boilerplate, dễ test hơn BLoC cho team mới |
| Container hóa | **Docker + Docker Compose ngay từ đầu**, container chia theo thành phần chính, mount source từ host để hot-reload | đã chốt |

---

## 2. Cấu trúc thư mục (Monorepo)

```
.
├── backend/
│   ├── apps/
│   │   ├── api/                  # FastAPI — REST API chính: auth/RBAC, đối soát sạc,
│   │   │                         # chính sách, hồ sơ xe/trạm, billing, CSKH, chấm điểm, báo cáo
│   │   ├── ocpp_gateway/         # WebSocket server giao tiếp trụ sạc qua OCPP (python-ocpp)
│   │   └── iot_ingestion/        # Worker nhận dữ liệu telematics (MQTT) → ghi DB, sinh cảnh báo
│   ├── libs/
│   │   ├── common/               # config, logging, exception handler dùng chung
│   │   ├── db/                   # SQLAlchemy models + Alembic migrations
│   │   ├── auth/                 # RBAC, JWT, permission dùng chung cho các app trên
│   │   └── schemas/              # Pydantic schemas dùng chung (request/response, event)
│   ├── tests/
│   │   ├── unit/
│   │   └── integration/
│   ├── pyproject.toml
│   ├── Dockerfile
│   └── Dockerfile.dev            # image cho dev (mount source, auto-reload)
│
├── web-portal/                   # React + TS — dùng chung cho Admin & Quản lý đội xe (phân quyền qua RBAC)
│   ├── src/
│   │   ├── features/             # chia theo tính năng: monitoring/, charging/, alerts/, fleet/, billing/...
│   │   ├── components/           # component dùng chung (design system)
│   │   ├── lib/                  # api client, hooks dùng chung
│   │   └── app/                  # routing, layout gốc
│   ├── package.json
│   ├── Dockerfile
│   └── Dockerfile.dev
│
├── vehicle-app/                  # Flutter — màn hình trên xe cho tài xế
│   ├── lib/
│   │   ├── features/             # checkin/, realtime_data/, alerts/, map/, support/, payment/...
│   │   ├── core/                 # network client, local storage, kết nối cục bộ tới thiết bị telematics
│   │   └── main.dart
│   ├── pubspec.yaml
│   └── Dockerfile.ci             # chỉ dùng để build/test trong CI, KHÔNG chạy trong docker-compose dev
│
├── infra/
│   ├── docker-compose.yml        # định nghĩa toàn bộ service cho môi trường dev
│   ├── docker-compose.prod.yml   # (khung sẵn cho sau này)
│   ├── reverse-proxy/            # cấu hình Traefik/Nginx
│   ├── db/
│   │   └── init/                 # script bật extension timescaledb, postgis khi khởi tạo DB
│   └── broker/                   # cấu hình MQTT broker
│
├── docs/                         # đặc tả chức năng, sơ đồ kiến trúc (đã có)
├── scripts/                      # script tiện ích (seed data, migrate, lint-all...)
├── .env.example
├── Makefile
└── AGENT.md
```

---

## 3. Môi trường phát triển bằng Docker

**Nguyên tắc:** mỗi thành phần chính chạy 1 container riêng; source code trên host được **mount** vào container tương ứng để hot-reload khi sửa code, không cần rebuild image.

### 3.1 Danh sách container (dev)

| Container | Thành phần | Mount source | Cổng (host) |
|---|---|---|---|
| `db` | PostgreSQL + TimescaleDB + PostGIS | `./infra/db/init` (chỉ script khởi tạo) | 5432 |
| `broker` | MQTT broker (nhận dữ liệu telematics) | `./infra/broker` (config) | 1883 |
| `api` | FastAPI — business logic chính | `./backend` | 8000 |
| `ocpp-gateway` | OCPP WebSocket server (trụ sạc) | `./backend` | 9000 |
| `iot-ingestion` | Worker nhận MQTT → ghi DB/cảnh báo | `./backend` | — (không expose) |
| `web-portal` | React dev server (Admin + Quản lý đội xe) | `./web-portal` | 5173 |
| `reverse-proxy` | Traefik — route `api`/`ocpp-gateway`/`web-portal` qua 1 cổng | `./infra/reverse-proxy` | 80 (dashboard: 8080) |

> `vehicle-app` (Flutter) **không** chạy trong `docker-compose up` vì đây là app biên dịch ra APK,
> không phải service chạy liên tục. Dùng container `Dockerfile.ci` riêng chỉ khi cần build/test trong CI,
> hoặc chạy Flutter trực tiếp trên máy dev (`flutter run`) và trỏ API tới `http://localhost` (qua reverse-proxy).

### 3.2 Lệnh thường dùng

```bash
# Khởi động toàn bộ môi trường dev
docker compose -f infra/docker-compose.yml up -d

# Xem log 1 service
docker compose -f infra/docker-compose.yml logs -f api

# Chạy migration
docker compose -f infra/docker-compose.yml exec api alembic upgrade head

# Chạy test backend
docker compose -f infra/docker-compose.yml exec api pytest

# Tắt toàn bộ + xoá volume (reset DB)
docker compose -f infra/docker-compose.yml down -v
```

### 3.3 Quy ước file compose
- Dùng **bind mount** (không dùng named volume) cho source code: `./backend:/app` — để sửa code trên host thấy hiệu lực ngay.
- `api`, `ocpp-gateway`, `iot-ingestion` dùng chung 1 image base (`backend/Dockerfile.dev`) nhưng khác lệnh khởi động (`command:` khác nhau trong compose), để tránh trùng lặp code giữa 3 app trong `backend/apps/`.
- Biến môi trường đọc từ `.env` ở root, KHÔNG hardcode trong `docker-compose.yml`.

---

## 4. Coding Convention

### 4.1 Backend — Python / FastAPI
- Format: **Black** (line length 88) + **isort** (import order) — chạy tự động qua pre-commit.
- Lint: **Ruff** (thay flake8, nhanh hơn).
- Type checking: type hint bắt buộc cho function signature; dùng **mypy** hoặc **Pyright** để check.
- Kiến trúc theo layer trong mỗi app (`api`, `ocpp_gateway`, `iot_ingestion`):
  - `routers/` — định nghĩa endpoint (FastAPI `APIRouter`), không chứa business logic.
  - `services/` — business logic thuần Python, không phụ thuộc FastAPI.
  - `repositories/` — truy vấn DB (SQLAlchemy), không chứa business logic.
  - `schemas/` — Pydantic models cho request/response.
- Naming: `snake_case` cho biến/hàm/module, `PascalCase` cho class, hằng số `UPPER_SNAKE_CASE`.
- Toàn bộ I/O (DB, HTTP, MQTT) dùng **async/await**.
- Migration: **Alembic**, mỗi migration có message rõ ràng, không sửa migration đã merge vào `main`.
- Docstring bắt buộc cho service function xử lý nghiệp vụ phức tạp (đối soát vi phạm, tính KPI...).

### 4.2 Web Portal — React / TypeScript
- Format: **Prettier**. Lint: **ESLint** (`@typescript-eslint/recommended` + `eslint-plugin-react-hooks`).
- Tổ chức theo **feature folder** (`src/features/<feature>/{components,hooks,api,types}.ts`), không chia theo loại file toàn cục.
- Naming: component `PascalCase.tsx`, hook `useXxx.ts`, biến/hàm `camelCase`, type/interface `PascalCase`.
- Toàn bộ gọi API qua 1 lớp `lib/api-client` dùng chung, không gọi `fetch` rải rác trong component.
- Không dùng `any`; bật `strict: true` trong `tsconfig.json`.

### 4.3 Vehicle App — Flutter / Dart
- Áp dụng **Effective Dart** + gói `flutter_lints`.
- Kiến trúc theo feature (`lib/features/<feature>/{presentation,domain,data}`), quản lý state bằng Riverpod.
- Naming: biến/hàm `lowerCamelCase`, class `UpperCamelCase`, tên file `snake_case.dart`.
- Kết nối cục bộ tới thiết bị telematics (giao thức **[Cần xác định]** — xem mục 9) tách riêng thành 1 package/module trong `core/` để dễ thay đổi giao thức sau này mà không ảnh hưởng UI.

### 4.4 Chung cho toàn repo
- Commit message theo **Conventional Commits**: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`, `perf:`.
  - Ví dụ: `feat(api): thêm endpoint đối soát vi phạm sạc (AD-03)`
- Branch: `feature/<mo-ta-ngan>`, `fix/<mo-ta-ngan>`, `chore/<mo-ta-ngan>`.
- Mọi PR phải qua review trước khi merge vào `main`; không push thẳng vào `main`.
- Không commit secrets — mọi config nhạy cảm qua `.env` (đã có `.env.example` làm mẫu, không chứa giá trị thật).
- Khi thêm chức năng mới, đối chiếu lại mã chức năng tương ứng trong `docs/Chuc_nang_tong_hop.xlsx` (VD: `AD-03`, `D-05`) để giữ nhất quán giữa code và đặc tả.

---

## 5. Database

- 1 instance PostgreSQL duy nhất, bật 2 extension: `timescaledb`, `postgis` (script khởi tạo ở `infra/db/init/`).
- Bảng dữ liệu time-series (telemetry xe, trạng thái trụ sạc, phiên sạc) tạo dưới dạng **hypertable** (TimescaleDB) để tối ưu truy vấn/nén dữ liệu lịch sử.
- Cột vị trí (GPS xe, vị trí trạm sạc, geofence) dùng kiểu `geometry`/`geography` (PostGIS).
- Naming bảng: số nhiều, `snake_case` (`vehicles`, `charging_sessions`, `alerts`, `policy_configs`...).
- Migration quản lý bằng Alembic, đặt trong `backend/libs/db/migrations/`.

---

## 6. Testing

| Thành phần | Công cụ | Ghi chú |
|---|---|---|
| Backend | `pytest` + `pytest-asyncio` + `httpx` (test client) | test service layer độc lập với FastAPI khi có thể |
| Web Portal | `Vitest` + `React Testing Library` | ưu tiên test hook/service logic hơn là snapshot UI |
| Vehicle App | `flutter test` | test riêng phần xử lý dữ liệu offline/local connection |

Mức coverage tối thiểu: **[Cần xác định]** (đề xuất tạm 70% cho `services/`, không bắt buộc 100%).

---

## 7. Câu hỏi / quyết định cần bạn xác nhận thêm

1. **Message broker cho IoT ingestion**: dùng MQTT (EMQX hay Mosquitto) hay nhận trực tiếp qua HTTPS từ thiết bị telematics (đơn giản hơn nhưng kém chuẩn IoT hơn)?
2. **Giao thức kết nối cục bộ** giữa Thiết bị Telematics ↔ Màn hình xe (BLE/Wi-Fi Direct/CAN...) — vẫn đang "Cần thông tin" từ bảng chức năng, cần chốt trước khi code module `core/` trong `vehicle-app`.
3. **CI/CD**: dùng nền tảng nào (GitHub Actions, GitLab CI, Jenkins...)? Chưa có mục này trong AGENT.md vì chưa biết.
4. **Coverage tối thiểu** cho test — có cần set cứng % không hay để linh hoạt giai đoạn đầu?
5. **Quản lý secrets khi lên môi trường thật** (Vault, AWS/GCP Secrets Manager, hay chỉ dùng `.env` + CI secrets)?

Phần nào bạn chưa quyết định, cứ để tôi giữ nguyên đề xuất tạm ở trên và triển khai theo, sau này đổi cũng không ảnh hưởng lớn tới cấu trúc thư mục.
