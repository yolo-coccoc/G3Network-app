# G3Network Backend

FastAPI backend for the electric truck driver support and fleet management
MVP. First-time setup, the daily `make` targets, the simulators and the full
feature scope are in the [root README](../README.md); `make help` lists every
command.

## Current scope

Nine domains: vehicles (incl. device-activation state), telematics devices
(vehicle mapping, MQTT config push, device-health monitor), telemetry (MQTT
ingestion, latest/history, operating and energy reports, battery/SOH/anomaly
alerts), notifications (poll-only), charging stations (topology, directory,
nearby search, OCPP gateway), charging sessions, drivers, fleets and support
(tickets and SOS). Not built yet: identity/RBAC, policy, billing/payment and
the frontends. See [docs/00-status/overview.md](../docs/00-status/overview.md).

## Development

The `make` targets are the normal way to run things (from the repository
root). The raw commands, from this `backend/` directory:

```bash
# Install runtime dependencies plus the `dev` group (ruff, mypy, pytest,
# import-linter, pytest-cov) - plain `uv sync` includes the dev group
uv sync

# API server (make backend-dev)
uv run uvicorn app.api.main:app --reload --port 8000

# Telemetry ingestion (make telemetry-dev)
uv run python -m app.domains.telemetry.ingestion.entrypoint

# OCPP gateway, 2.0.1 + 1.6J (make charging-ocpp-dev)
uv run python -m app.domains.charging_stations.ocpp.entrypoint

# Device-health monitor (make telematics-monitor-dev)
uv run python -m app.domains.telematics.monitoring.entrypoint

# Simulators live in ../simulator; run them from here so they use this venv
uv run python ../simulator/seed_charging_topology.py
uv run python ../simulator/charging_session_simulator.py
```

The OCPP gateway listens on `CHARGING_OCPP_HOST`:`CHARGING_OCPP_PORT`
(default `0.0.0.0:9000`) and accepts the `ocpp2.0.1` and `ocpp1.6` WebSocket
subprotocols; the negotiated one picks the adapter. The station identity in
`/ocpp/{ocpp_identity}` must already exist in the charging-stations API.

## Checks

From the repository root:

```bash
make check                      # ruff, import-linter, mypy, smoke tests, domain-model check
make format                     # sort imports + format with ruff
make backend-test-integration   # PostgreSQL integration tests (needs make infra-up)
```

The PostgreSQL integration tests in `tests/test_postgres_integration.py` are
skipped unless `RUN_DB_INTEGRATION=1`, which `make backend-test-integration`
sets.

## Configuration

`make setup` copies `.env.example` to `.env`. Every setting is defined, typed
and validated in `app/libs/common/config.py`; `.env.example` documents each
one, grouped by component, with its default. Only `DATABASE_URL` is required,
and it must never be placed in source code.

## API documentation

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Project structure

```
backend/
├── app/                      # Source code (uv package)
│   ├── domains/              # Business domains (bounded contexts)
│   │   ├── vehicles/         # Vehicle profile and activation state (F-F2)
│   │   ├── telematics/       # Devices, vehicle mapping, config push, health monitor (F-G1, F-J1-J3)
│   │   ├── telemetry/        # MQTT ingestion, history, reports, alerts (F-A1-A6, F-C6)
│   │   ├── charging_stations/# Topology, directory, nearby search, OCPP 2.0.1 + 1.6J (F-C1, F-C2, F-D1, F-G2)
│   │   ├── charging_sessions/# Session, event and measurement lifecycle (F-B2, F-C5)
│   │   ├── notifications/    # Notification storage and polling (F-A2)
│   │   ├── drivers/          # Driver profile and vehicle assignments (F-E4)
│   │   ├── fleet/            # Fleets and vehicle membership (F-E1)
│   │   └── support/          # Support tickets and SOS (F-I1, F-I2)
│   ├── api/
│   │   └── main.py           # FastAPI app: routers + domain-exception handlers
│   └── libs/
│       ├── common/           # config, logging, errors, clock, pagination, geo
│       └── db/               # SQLAlchemy base, session, enums, Alembic migration
├── tests/                    # tests/<domain>/ smoke tests, shared builders/fakes,
│                             # cross-cutting and PostgreSQL integration tests
├── alembic.ini
├── pyproject.toml
└── uv.lock
```

Where each module belongs and which cross-domain imports are allowed:
[`.claude/rules/directory-structure.md`](../.claude/rules/directory-structure.md)
and [`.claude/rules/domain-boundaries.md`](../.claude/rules/domain-boundaries.md).
