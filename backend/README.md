# G3Network Backend

Backend FastAPI cho MVP hệ thống hỗ trợ tài xế và quản lý đội xe tải điện.

## Phạm vi hiện tại

- CRUD `vehicles` và `telematics`, gồm mapping thiết bị với xe.
- Nhận telemetry qua MQTT và lưu vào PostgreSQL/TimescaleDB.
- `GET /api/v1/telemetry/vehicles/{vehicle_id}/latest` để đọc telemetry mới
  nhất của xe.
- CRUD topology `charging_stations` → EVSE → connector.
- OCPP 2.0.1 gateway và lifecycle charging session happy path.
- API đọc charging session, event và meter value.

Các API lịch sử telemetry, bản đồ, alert, device health, policy, user/RBAC và
frontend chưa thuộc source hiện tại.

## Development

```bash
# Install dependencies
uv sync

# Run development server
uv run uvicorn app.api.main:app --reload

# Run with specific host/port
uv run uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --reload

# Run the OCPP 2.0.1 gateway in a separate process
uv run python -m app.domains.charging_stations.ocpp.entrypoint

# Run the OCPP session happy-path simulator from the repository root
uv run python ../simulator/charging_session_simulator.py

# Provision station, EVSE and connector for the simulator
uv run python ../simulator/seed_charging_topology.py
```

The OCPP gateway listens on `CHARGING_OCPP_HOST` and
`CHARGING_OCPP_PORT` (default `0.0.0.0:9000`) and accepts only the
`ocpp2.0.1` WebSocket subprotocol. The station identity in
`/ocpp/{ocpp_identity}` must already exist in the charging-stations API.

## Kiểm tra

```bash
uv run pytest
uv run ruff check .
uv run black --check .
uv run isort --check-only .
uv run mypy .
```

Hai test PostgreSQL integration được đánh dấu skip mặc định. Chạy chúng khi
muốn kiểm tra database tạm:

```bash
RUN_DB_INTEGRATION=1 uv run pytest tests/test_postgres_integration.py
```

## Configuration

Copy `.env.example` to `.env` before running the backend. The shared schema,
types, validation and safe defaults live in
`app/libs/common/config.py`; `.env` only supplies values that vary by runtime
environment, credentials and operational tuning. `DATABASE_URL` is required
and must not be placed in source code.

## API Documentation

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Project Structure

```
backend/
├── app/               # Source code (uv package)
│   ├── domains/       # Business domains (bounded contexts)
│   │   ├── vehicles/  # Vehicle management (AD-05)
│   │   ├── telematics/# Device profile and vehicle mapping (AD-02/AD-05)
│   │   ├── telemetry/ # MQTT ingestion and latest telemetry query (AD-02/FM-01)
│   │   ├── charging_stations/ # Topology and OCPP 2.0.1 (AD-03)
│   │   └── charging_sessions/ # Session, event and meter lifecycle (S-02)
│   ├── api/          # FastAPI application
│   │   └── main.py   # Entry point
│   └── libs/         # Shared utilities
│       └── db/       # Database configuration
├── pyproject.toml
└── uv.lock
```
