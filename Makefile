# G3Network - Makefile
# Common commands used for development

.PHONY: help infra-up infra-down infra-logs infra-reset backend-install backend-dev telemetry-dev charging-ocpp-dev charging-ocpp-seed charging-ocpp-sim telematics-monitor-dev backend-test db-migrate db-reset

# Default: show help
help:
	@echo "=== G3Network Development Commands ==="
	@echo ""
	@echo "Infrastructure:"
	@echo "  make infra-up       - Start PostgreSQL and EMQX"
	@echo "  make infra-down    - Stop PostgreSQL and EMQX (keep data)"
	@echo "  make infra-logs    - View PostgreSQL and EMQX logs"
	@echo "  make infra-reset   - Remove PostgreSQL, EMQX, and all data"
	@echo ""
	@echo "Backend:"
	@echo "  make backend-install - Install dependencies"
	@echo "  make backend-dev     - Run the development server (port 8000)"
	@echo "  make telemetry-dev   - Run telemetry ingestion"
	@echo "  make charging-ocpp-dev - Run the OCPP 2.0.1 gateway (port 9000)"
	@echo "  make charging-ocpp-seed - Provision station/EVSE/connector simulator"
	@echo "  make charging-ocpp-sim - Run the OCPP session happy-path simulator"
	@echo "  make telematics-monitor-dev - Run the device-silence health monitor"
	@echo "  make backend-test    - Run tests"
	@echo ""
	@echo "Database:"
	@echo "  make db-migrate     - Run Alembic migrations"
	@echo "  make db-reset       - Reset the database (wipes all data)"
	@echo ""

# === INFRASTRUCTURE ===

infra-up:
	@echo "Starting PostgreSQL and EMQX..."
	docker compose -f infra/docker-compose.yml up -d
	@echo "✓ PostgreSQL: localhost:5432"
	@echo "✓ EMQX MQTT: localhost:1883"
	@echo "✓ EMQX Dashboard: http://localhost:18083"
	@echo "  Database: g3network"
	@echo "  User: g3network"
	@echo "  Password: g3network123"

infra-down:
	@echo "Stopping PostgreSQL and EMQX..."
	docker compose -f infra/docker-compose.yml down
	@echo "✓ Stopped (data is preserved)"

infra-logs:
	docker compose -f infra/docker-compose.yml logs -f db broker

infra-reset:
	@echo "⚠️  WARNING: This operation will DELETE ALL DATA!"
	@echo "Press Ctrl+C to cancel, or Enter to continue..."
	@read confirm
	docker compose -f infra/docker-compose.yml down -v
	@echo "✓ Containers and volumes removed"

# === BACKEND ===

backend-install:
	@echo "Installing backend dependencies..."
	cd backend && uv sync
	@echo "✓ Installation complete"

backend-dev:
	@echo "Starting backend server..."
	@echo "API Docs: http://localhost:8000/docs"
	cd backend && uv run uvicorn app.api.main:app --reload --port 8000

telemetry-dev:
	@echo "Starting telemetry ingestion..."
	cd backend && uv run python -m app.domains.telemetry.ingestion.entrypoint

charging-ocpp-dev:
	@echo "Starting OCPP 2.0.1 gateway..."
	cd backend && uv run python -m app.domains.charging_stations.ocpp.entrypoint

charging-ocpp-seed:
	@echo "Provisioning station/EVSE/connector for the OCPP simulator..."
	cd backend && uv run python ../simulator/seed_charging_topology.py

charging-ocpp-sim:
	@echo "Running the OCPP session happy-path simulator..."
	cd backend && uv run python ../simulator/charging_session_simulator.py

telematics-monitor-dev:
	@echo "Starting telematics device health monitor..."
	cd backend && uv run python -m app.domains.telematics.monitoring.entrypoint

backend-test:
	@echo "Running backend tests..."
	cd backend && uv run pytest

# === DATABASE ===

db-migrate:
	@echo "Running Alembic migrations..."
	cd backend && uv run alembic upgrade head
	@echo "✓ Database migrated"

db-reset:
	@echo "⚠️  Resetting database to its initial state..."
	cd backend && uv run alembic downgrade base
	cd backend && uv run alembic upgrade head
	@echo "✓ Database reset"
