# G3Network

G3Network is a driver assistance and electric truck fleet management system.
The current repo focuses on the backend MVP, local infrastructure and
simulators for testing the vehicle data and charging session flows. The web
portal and vehicle app have no source in this checkout yet.

## Prerequisites

- Docker Desktop is running
- `uv`
- `make`

All commands below are run from the project root directory:

```bash
cd /home/duc/Workspace/G3Network-app
```

## 1. Starting the whole system

### Run once for initial setup

```bash
cp backend/.env.example backend/.env
make backend-install
```

### Start the infrastructure

```bash
make infra-up
make db-migrate
```

`infra-up` starts PostgreSQL and EMQX via Docker. `db-migrate` creates/updates
the database tables.

### Current backend scope

- Vehicle and telematic device CRUD, including mapping devices to vehicles.
- Receiving telemetry over MQTT and storing it in TimescaleDB.
- API for reading a vehicle's latest telemetry.
- Station → EVSE → Connector topology CRUD, including station directory
  metadata (location, power rating, connector standard, operating hours,
  maintenance status).
- OCPP 2.0.1 and the charging session happy-path lifecycle
  `Started → Updated/MeterValues → Ended`.
- API for reading charging sessions, events and meter values.

Not yet in the current baseline: full telemetry history API, vehicle/station
map, aggregate connector status, battery/anomaly alerts, threshold alert
pushes, identity/RBAC, charging policy, payment and frontend.

### Running the application components

Each command below runs in its own Terminal. Keep these Terminals running.

Terminal 1 — Backend API:

```bash
make backend-dev
```

Terminal 2 — Receiving data from vehicle devices:

```bash
make telemetry-dev
```

Terminal 3 — OCPP 2.0.1 charging station gateway:

```bash
make charging-ocpp-dev
```

Once started, the system exposes these main addresses:

- API and Swagger: [http://localhost:8000/docs](http://localhost:8000/docs)
- API health check: [http://localhost:8000/health](http://localhost:8000/health)
- OCPP connection: `ws://localhost:9000/ocpp/<station-code>`
- EMQX Dashboard: [http://localhost:18083](http://localhost:18083)

## 2. Running the simulators

Simulators are only used to generate mock data and are not needed when
connecting real vehicle devices or charging stations. The backend and EMQX
must already be running; the charging station simulator additionally needs
the OCPP gateway running.

### Vehicle data simulator

Open a new Terminal. Run once to create a sample vehicle and telematics
device:

```bash
cd backend
uv run python ../simulator/seed_simulator_devices.py
```

Then run the simulator that sends location, speed and battery data over MQTT:

```bash
uv run python ../simulator/telematic_simulator.py
```

The simulator runs continuously every 5 seconds. Press `Ctrl+C` to stop.

### Charging station and charging session simulator

Open a new Terminal at the project root. Create a sample topology consisting
of one station, EVSE and connector:

```bash
make charging-ocpp-seed
```

The sample topology has station code `SIM-OCPP-001`, EVSE `1` and connector
`1`. This command only needs to run once; a duplicate-code error means the
topology already exists.

Run a simulated charging session:

```bash
make charging-ocpp-sim
```

The simulator connects to the gateway and sends the start-charging, meter
value, update and end-charging flow. Results can be viewed in the logs or via
the charging APIs in Swagger.

### Connecting a real charging station

A real charging station must first be registered as a station, EVSE and
connector in Swagger, with an OCPP code matching the device's configuration.
Then configure the charging station:

```text
OCPP URL:       ws://<backend-host-address>:9000/ocpp/<station-code>
Protocol:       ocpp2.0.1
```

In a local environment, the charging station must be able to reach the
machine running the backend.

## Stopping the system

Press `Ctrl+C` in the Terminals running the application, then stop Docker:

```bash
make infra-down
```

Data is preserved. Do not use `make infra-reset` or `make db-reset` unless
you intend to delete the data.

## Main structure

```text
backend/       Backend FastAPI and Alembic migrations
infra/         PostgreSQL/TimescaleDB/PostGIS and EMQX
simulator/     Vehicle and charging station data simulators
docs/          Status, requirements, architecture and implementation planning docs
  00-status/   Current repo status and architecture (source of truth for progress)
```

See also: [CLAUDE.md](./CLAUDE.md) and
[feature list](./docs/01-requirements/feature-list.md).
