# G3Network

G3Network is a driver assistance and electric truck fleet management system.
This repo currently holds the backend MVP, the local infrastructure and the
simulators used to exercise the vehicle telemetry and charging flows. The web
portal and vehicle app have no source in this repo yet.

- Command reference: `make help` (the Makefile is the source of truth).
- AI coding agents: start with [CLAUDE.md](./CLAUDE.md).
- Documents: [docs/README.md](./docs/README.md) maps them all — progress in
  the [feature catalog](./docs/product/features/README.md), what exists today
  in [architecture.md](./docs/design/architecture.md), decisions in the
  [decision log](./docs/decisions/decision-log.md).

## Prerequisites

- Docker with the `docker compose` plugin (Docker Desktop or Docker Engine), running
- [`uv`](https://docs.astral.sh/uv/) — it also provisions Python 3.12, so no
  system Python is needed
- `make` and `git`

Only for working with Claude Code in this repo (optional):

- Node.js/npm — the Context7 MCP server runs through `npx`
- `npm install -g pyright` — the Pyright LSP plugin needs `pyright-langserver`
  on `PATH`

Shared VS Code settings live in `.vscode/` (ruff on save, mypy, pytest); the
recommended extensions are listed in `.vscode/extensions.json`.

All commands below run from the repository root.

## Quick start

```bash
make setup
```

`make setup` is safe to re-run. It:

1. creates `backend/.env` and `infra/.env` from their `.env.example` files
   (an existing `.env` is never overwritten);
2. runs `uv sync` in `backend/` — runtime dependencies plus the `dev`
   dependency group (ruff, mypy, pytest, import-linter, pytest-cov);
3. points git at `.githooks/`, so every commit runs `make check`;
4. starts PostgreSQL and EMQX (`infra/docker-compose.yml`) and waits until
   PostgreSQL accepts connections;
5. applies the database migration (`alembic upgrade head`);
6. runs `make check`.

Manual equivalent, if you prefer to run the steps yourself:

```bash
cp backend/.env.example backend/.env
cp infra/.env.example infra/.env
make backend-install     # uv sync
make install-hooks
make infra-up
make db-migrate          # once PostgreSQL is up
make check
```

## Daily development

Start the infrastructure if it isn't running (`make infra-up`), then run each
component you need in its own terminal and keep it running:

| Terminal | Command | What it runs |
|---|---|---|
| 1 | `make backend-dev` | API server on port 8000, auto-reload |
| 2 | `make telemetry-dev` | MQTT telemetry ingestion (consumer + worker) |
| 3 | `make charging-ocpp-dev` | OCPP gateway on port 9000 (2.0.1 and 1.6J) |
| 4 | `make telematics-monitor-dev` | Device-health monitor: alerts when a device stops reporting |
| 5 | `make telematics-status-dev` | T-Box status-report ingestion (health reports into `telematic_status_reports`) |
| 6 | `make driving-sessions-autoend-dev` | Ends driving sessions whose truck has not moved for the organization's auto-end time |

Once per database (after `make db-reset`), set `IDENTITY_BOOTSTRAP_ADMIN_PHONE` and `IDENTITY_BOOTSTRAP_ADMIN_PASSWORD` in `backend/.env` and run `make identity-bootstrap`: it creates the internal organization and the first HEAD_ADMIN, because no endpoint can.


Only the API is needed for plain CRUD work; start the others when you touch
their flow. The simulators (next section) run in further terminals.

Addresses once everything is up:

- API and Swagger: <http://localhost:8000/docs> (ReDoc: <http://localhost:8000/redoc>)
- API health check: <http://localhost:8000/health>
- OCPP endpoint: `ws://localhost:9000/ocpp/<station-code>`
- EMQX dashboard: <http://localhost:18083>

## Checks

```bash
make check                      # the gate: ruff lint + format check, import-linter,
                                # mypy (app + tests), smoke tests, domain-model and
                                # feature-catalog checks
make format                     # sort imports and format with ruff
make backend-test-integration   # PostgreSQL integration tests (needs make infra-up)
make coverage                   # smoke tests with a coverage report (backend/htmlcov/)
make audit                      # known-vulnerability check of locked dependencies (pip-audit)
make db-check                   # database built by the migration matches the models
```

There is no CI yet: the pre-commit hook enabled by `make setup` (or
`make install-hooks`) runs `make check` before every commit. Run
`make backend-test-integration` (and `make db-check` after `make db-reset`)
when you change the schema or a repository query.

The schema is a single baseline migration (`0001_baseline_schema`) during the
bootstrap phase: `make db-migrate` applies it to an empty database,
`make db-reset` wipes the database and rebuilds it from the baseline. See
[.claude/rules/database.md](.claude/rules/database.md).

## Current backend scope

- **Vehicles**: CRUD and soft delete, a device-activation state machine
  (`PENDING → DEVICE_ASSIGNED → ACTIVATED`) and a fleet-wide activation
  summary (`GET /api/v1/vehicles/activation-summary`).
- **Telematics devices**: CRUD and device-to-vehicle mapping with read-time
  health (`last_seen_at`, `is_online`, `is_silent`, signal strength); a
  telemetry publish-interval config push over MQTT to one device
  (`POST /api/v1/telematics/{id}/config`) or a whole fleet
  (`POST /api/v1/telematics/fleets/{fleet_id}/config`); a device-health
  monitor that raises a device-offline alert when a device goes silent.
- **Telemetry**: MQTT ingestion into TimescaleDB; latest record with an
  online flag, a bounded time-range history, a daily battery-health trend,
  SOC-based operating (day/week/month breakdown, CSV) and energy-usage
  reports per vehicle, and fleet views (member positions, operating rollup)
  under `/api/v1/telemetry/fleets/{fleet_id}/`.
- **Alerts and notifications**: battery-level, battery-health (SOH),
  anomaly (temperature, voltage drop, device error codes), device-offline,
  geofence entry/exit and SOS alerts, stored as notifications and read by
  polling (`GET /api/v1/notifications?after_id=`, filterable, newest-first
  on request), with unread count and mark-as-read.
- **Charging stations**: Station → EVSE → Connector topology CRUD with
  directory metadata (location, power rating, connector standard, operating
  hours, maintenance status, available-connector count), a nearby-station
  search with an availability filter
  (`GET /api/v1/charging-stations/nearby`), a per-station connector status
  view and the charger's latest configuration.
- **OCPP gateway**: OCPP 2.0.1 and OCPP 1.6J on one port, chosen per
  connection by the negotiated subprotocol. Every frame is stored verbatim
  and refreshes the station's liveness (`is_online` derived from
  `last_seen_at`); connector status (incl. 1.6J error codes) is stored as
  reported; both protocols record the charger's device info at boot; 1.6J
  chargers also report their configuration.
- **Charging sessions**: 2.0.1 `Started → Updated/MeterValues → Ended` and
  1.6J `StartTransaction → MeterValues → StopTransaction`; read APIs for
  sessions (with duration, SoC and peak-power summary, filters), events,
  meter values and all measurements, plus station energy totals and an
  hourly/daily energy series.
- **Drivers**: profile CRUD (search, current-vehicle filter) and vehicle
  assignment with full history.
- **Fleets**: CRUD and vehicle membership (added/removed by VIN) with
  history, list filters, and geofences that raise entry/exit alerts.
- **Support**: support case tickets and SOS intake (in-app or hotline) with
  a response SLA, list filters, and an SOS alert notification.

Not built yet: identity/RBAC, charging policy, pricing/billing/payment, push
or multi-channel notification delivery, KPI dashboards, OCPP remote
commands and reliability (retry/reconnect/TLS), and the frontends. The full
list of deferred items is in
[docs/product/future.md](./docs/decisions/deferred.md).

## Running the simulators

Simulators only generate test data; they are not needed with real devices or
chargers. All of them talk to the API, so `make backend-dev` must be running.

### Vehicle telemetry

Also needs `make telemetry-dev`. Create a sample vehicle and telematics device
once, then start the simulator:

```bash
cd backend
uv run python ../simulator/seed_simulator_devices.py
uv run python ../simulator/telematic_simulator.py
```

The simulator publishes location, speed and battery data over MQTT every
5 seconds until you press `Ctrl+C`.

### OCPP 2.0.1 charging session

Also needs `make charging-ocpp-dev`.

```bash
make charging-ocpp-seed    # once: station SIM-OCPP-001, EVSE 1, connector 1
make charging-ocpp-sim     # one Started -> Updated/MeterValues -> Ended session
```

A duplicate-code error from the seed means the topology already exists.
Results can be read through the charging APIs in Swagger.

### OCPP 1.6J charger

Also needs `make charging-ocpp-dev`. Simulates a dual-gun 1.6J charger such
as the Willdigits DC charger:

```bash
make charging-ocpp16-seed                    # once: station SIM-OCPP16-001, one EVSE per gun
make charging-ocpp16-sim                     # default scenario: boot (BootNotification + Heartbeats)
make charging-ocpp16-sim SCENARIO=status     # boot, then status reports incl. faults
make charging-ocpp16-sim SCENARIO=session    # boot, then a full charging session on gun 1
```

The `session` scenario authorizes, starts a transaction, sends meter values
(energy in kWh, SoC, power, voltage, current, temperature, `Power.Offered`
and a vendor-specific measurand) and stops it. The charger's boot info,
statuses, session, measurements and configuration can then be read in
Swagger. Further options are described in
`simulator/ocpp16_charge_point_simulator.py`.

## Connecting a real charging station

Register the station, EVSE and connector in Swagger first, with an OCPP
station code matching the device's configuration. Then configure the
charger:

```text
OCPP URL:   ws://<backend-host-address>:9000/ocpp/<station-code>
Protocol:   ocpp1.6 or ocpp2.0.1 (the gateway accepts both)
```

For an OCPP 1.6J charger register one EVSE per gun (EVSE `1` / connector `1`
for gun 1, EVSE `2` / connector `1` for gun 2, …): 1.6J connector `n` maps to
EVSE `n`. Connector `0` (the whole charger) needs no registration. Change the
charger's default HMI password before connecting it. The gateway currently
uses plain `ws://` without authentication (development mode), and the
charger must be able to reach the machine running the backend.

## Stopping

Press `Ctrl+C` in each terminal, then stop the containers:

```bash
make infra-down
```

Data is kept. `make infra-reset` (removes the volumes) and `make db-reset`
(rebuilds the schema) both delete all data.

## Repository layout

```text
backend/      FastAPI backend, Alembic migration, tests (see backend/README.md)
infra/        Docker Compose for PostgreSQL/TimescaleDB/PostGIS and EMQX
simulator/    Vehicle telemetry and OCPP charging simulators
docs/         Product, design, decisions, planners, reports (see docs/README.md)
.claude/      Rules, skills, agents and hooks for AI coding agents (see CLAUDE.md)
.githooks/    Pre-commit hook (runs make check)
.vscode/      Shared VS Code settings
```
