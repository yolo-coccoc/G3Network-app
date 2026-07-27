# G3Network - Makefile
# Các lệnh thường dùng để phát triển

.PHONY: help infra-up infra-down infra-logs infra-reset backend-install backend-dev telemetry-dev backend-test db-migrate db-reset

# Mặc định hiển thị help
help:
	@echo "=== G3Network Development Commands ==="
	@echo ""
	@echo "Infrastructure:"
	@echo "  make infra-up       - Khởi động PostgreSQL và EMQX"
	@echo "  make infra-down    - Dừng PostgreSQL và EMQX (giữ data)"
	@echo "  make infra-logs    - Xem logs của PostgreSQL và EMQX"
	@echo "  make infra-reset   - Xóa PostgreSQL, EMQX và toàn bộ data"
	@echo ""
	@echo "Backend:"
	@echo "  make backend-install - Cài đặt dependencies"
	@echo "  make backend-dev     - Chạy development server (port 8000)"
	@echo "  make telemetry-dev   - Chạy telemetry ingestion (health port 8081)"
	@echo "  make backend-test    - Chạy tests"
	@echo ""
	@echo "Database:"
	@echo "  make db-migrate     - Chạy Alembic migrations"
	@echo "  make db-reset       - Reset database (xóa hết data)"
	@echo ""

# === INFRASTRUCTURE ===

infra-up:
	@echo "Khởi động PostgreSQL và EMQX..."
	docker compose -f infra/docker-compose.yml up -d
	@echo "✓ PostgreSQL: localhost:5432"
	@echo "✓ EMQX MQTT: localhost:1883"
	@echo "✓ EMQX Dashboard: http://localhost:18083"
	@echo "  Database: g3network"
	@echo "  User: g3network"
	@echo "  Password: g3network123"

infra-down:
	@echo "Dừng PostgreSQL và EMQX..."
	docker compose -f infra/docker-compose.yml down
	@echo "✓ Đã dừng (data vẫn được giữ)"

infra-logs:
	docker compose -f infra/docker-compose.yml logs -f db broker

infra-reset:
	@echo "⚠️  CẢNH BÁO: Thao tác này sẽ XÓA TOÀN BỘ DATA!"
	@echo "Nhấn Ctrl+C để hủy, hoặc Enter để tiếp tục..."
	@read confirm
	docker compose -f infra/docker-compose.yml down -v
	@echo "✓ Đã xóa container và volume"

# === BACKEND ===

backend-install:
	@echo "Cài đặt backend dependencies..."
	cd backend && uv sync
	@echo "✓ Đã cài đặt xong"

backend-dev:
	@echo "Khởi động backend server..."
	@echo "API Docs: http://localhost:8000/docs"
	cd backend && uv run uvicorn app.api.main:app --reload --port 8000

telemetry-dev:
	@echo "Khởi động telemetry ingestion..."
	@echo "Health check: http://localhost:8081/health"
	cd backend && uv run python -m app.domains.telemetry.ingestion.entrypoint

backend-test:
	@echo "Chạy backend tests..."
	cd backend && uv run pytest

# === DATABASE ===

db-migrate:
	@echo "Chạy Alembic migrations..."
	cd backend && uv run alembic upgrade head
	@echo "✓ Database đã được migrate"

db-reset:
	@echo "⚠️  Reset database về trạng thái ban đầu..."
	cd backend && uv run alembic downgrade base
	cd backend && uv run alembic upgrade head
	@echo "✓ Database đã được reset"
