# G3Network Backend

Backend for G3Network - Electric truck driver support system.

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
│   ├── domains/      # Business domains (bounded contexts)
│   │   └── vehicles/ # Vehicle management (AD-05)
│   ├── api/          # FastAPI application
│   │   └── main.py   # Entry point
│   └── libs/         # Shared utilities
│       └── db/       # Database configuration
├── pyproject.toml
└── uv.lock
```
