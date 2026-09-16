# G3Network Backend

Backend FastAPI for the MVP driver assistance and electric truck fleet
management system.

## Current scope

- `vehicles` and `telematics` CRUD, including mapping devices to vehicles.
- Receiving telemetry over MQTT and storing it in PostgreSQL/TimescaleDB.
- `GET /api/v1/telemetry/vehicles/{vehicle_id}/latest` for reading a
  vehicle's latest telemetry.
- `charging_stations` → EVSE → connector topology CRUD, including station
  directory metadata (location, power rating, connector standard, operating
  hours, maintenance status).
- OCPP 2.0.1 gateway and the charging session happy-path lifecycle.
- API for reading charging sessions, events and meter values.

Telemetry history API, map, alerts, device health, policy, user/RBAC and
frontend are not yet part of the current source.

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

## Checks

```bash
uv run pytest
uv run ruff check .
uv run black --check .
uv run isort --check-only .
uv run mypy .
```

Two PostgreSQL integration tests are marked skip by default. Run them when
you want to test against a real database:

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
│   │   ├── vehicles/  # Vehicle management (F-F2)
│   │   ├── telematics/# Device profile and vehicle mapping (F-G1)
│   │   ├── telemetry/ # MQTT ingestion and latest telemetry query (F-A1)
│   │   ├── charging_stations/ # Topology and OCPP 2.0.1 (F-C1, F-G2)
│   │   └── charging_sessions/ # Session, event and meter lifecycle (F-B2)
│   ├── api/          # FastAPI application
│   │   └── main.py   # Entry point
│   └── libs/         # Shared utilities
│       └── db/       # Database configuration
├── pyproject.toml
└── uv.lock
```
