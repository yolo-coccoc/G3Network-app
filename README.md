# G3Network - Hệ thống hỗ trợ tài xế xe tải điện

## Quick Start

### 1. Khởi động hạ tầng (PostgreSQL)

```bash
make infra-up
```

### 2. Cài đặt backend dependencies

```bash
make backend-install
```

### 3. Chạy backend development server

```bash
make backend-dev
```

Server sẽ chạy tại: http://localhost:8000
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Health check: http://localhost:8000/health

### 4. Xem tất cả lệnh có sẵn

```bash
make help
```

## Các lệnh thường dùng

| Lệnh | Mô tả |
|------|-------|
| `make infra-up` | Khởi động PostgreSQL |
| `make infra-down` | Dừng PostgreSQL (giữ data) |
| `make infra-reset` | Xóa PostgreSQL và data |
| `make backend-install` | Cài đặt dependencies |
| `make backend-dev` | Chạy backend server |
| `make db-migrate` | Chạy database migrations |

## Cấu trúc dự án

```
.
├── backend/           # FastAPI backend
│   ├── app/          # Source code chính
│   │   ├── domains/  # Business domains
│   │   ├── api/      # FastAPI app
│   │   └── libs/     # Shared utilities
│   └── pyproject.toml
├── web-portal/        # React frontend (sau này)
├── vehicle-app/       # Flutter app (sau này)
├── infra/            # Docker compose files
└── docs/             # Documentation
```

## Tài liệu chi tiết

- [AGENTS.md](./AGENTS.md) - Hướng dẫn cho AI coding agent
- [docs/01-requirements/feature-list.md](./docs/01-requirements/feature-list.md) - Danh sách chức năng
- [docs/02-planners/](./docs/02-planners/) - Các planner triển khai
