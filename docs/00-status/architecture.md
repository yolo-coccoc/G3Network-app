# G3Network Architecture — Current MVP

The current repo is a backend MVP monorepo. The diagram below describes the
components present in the source and local infrastructure; Kafka, Redis, API
Gateway, the portal and the vehicle app are only future directions, not
active components yet.

```mermaid
flowchart LR
    Vehicle["Vehicle telematic device"]
    Station["OCPP 2.0.1 charging station"]
    Broker["EMQX 5.5\nMQTT"]
    Ingestion["Telemetry ingestion\nMQTT consumer + worker"]
    OCPP["charging_stations/ocpp\nWebSocket gateway"]
    API["FastAPI API"]
    Domains["vehicles\ntelematics\ntelemetry\ncharging_stations\ncharging_sessions"]
    Notifications["notifications"]
    DB[("PostgreSQL 16\nTimescaleDB + PostGIS")]
    Portal["Admin web portal\n(polls, not built yet)"]

    Vehicle -->|MQTT telemetry| Broker
    Broker --> Ingestion
    Ingestion -->|vehicle_telemetry| DB
    Ingestion -->|SOC crossing -> alert, F-A2| Notifications
    Notifications -->|nearest station lookup| Domains
    Notifications -->|notifications| DB

    Station <-->|OCPP 2.0.1| OCPP
    OCPP -->|session event + meter| Domains
    Domains -->|charging data| DB

    API --> Domains
    API --> Notifications
    Domains -->|CRUD/query| DB
    Portal -.->|GET /api/v1/notifications| API
```

## Current components

### Backend API

FastAPI registers the following domains:

- `vehicles`: vehicle CRUD and soft delete.
- `telematics`: device CRUD and mapping devices to vehicles.
- `telemetry`: receiving data via the ingestion service and reading a
  vehicle's latest telemetry.
- `charging_stations`: Station → EVSE → Connector topology CRUD, station
  directory metadata (location, power rating, connector standard, operating
  hours, maintenance status), and the OCPP 2.0.1 gateway.
- `charging_sessions`: storing the session aggregate, lifecycle events and
  meter values.
- `notifications`: a generic, backend-storage notification table (F-A2)
  polled via `GET /api/v1/notifications?after_id=`; telemetry ingestion
  raises one when a vehicle's SOC crosses the 30/20/10% tiers, carrying the
  nearest operational charging station (resolved via `charging_stations`,
  the first PostGIS spatial query in the codebase). No push and no
  recipient scoping yet — there is no mobile app and no `identity` domain.

The API process runs separately via Uvicorn. Telemetry ingestion and the OCPP
gateway have their own entrypoints, sharing the same database/session
configuration.

### Database

Development uses a single PostgreSQL 16 container with the following
extensions:

- TimescaleDB for `vehicle_telemetry`, `charging_session_events` and
  `charging_session_meter_values`.
- PostGIS: used for `charging_stations.location` (F-C1) and
  `vehicle_telemetry.location` (both `geography(Point, 4326)` columns) —
  `charging_stations.location` has a GIST index, `vehicle_telemetry.location`
  deliberately doesn't (high-frequency write path, no spatial query need
  yet). F-A2's nearest-operational-station lookup is the first query to use
  that index (`ST_Distance` + the `<->` KNN operator). There's still no
  general-purpose map/geofence *search* API (radius query, filtering) beyond
  that one nearest-station lookup.
- `uuid-ossp` for the local database.

The current Alembic baseline consists of:

```text
0001_reset_application_schema
0002_vehicles_telematics
0003_create_vehicle_telemetry
0004_create_charging_mvp_schema
0005_station_directory_fields
0006_telemetry_location_geo
0007_telemetry_schema_version
0008_notifications
```

The charging MVP only supports pre-provisioned topology and the happy path:

```text
Started → Updated/MeterValues → Ended
```

### Local infrastructure

`infra/docker-compose.yml` only starts two services:

- `db`: PostgreSQL/TimescaleDB/PostGIS on port `5432`.
- `broker`: EMQX on port `1883`, dashboard on `18083`.

The backend runs directly on the host via `uv`; there is no API Gateway or
reverse proxy in the development environment.

## Not yet in the MVP

- User, authentication, RBAC and driver.
- Full telemetry history API, vehicle/station map and aggregate dashboard.
- Aggregate connector status and technical status history.
- Battery anomalies (F-A4) and their notifications; live station
  occupancy/online signal for a true "nearest *available*" (F-A2's lookup
  only reflects `deleted_at`/`maintenance_status` today).
- Push/multi-channel notification delivery (F-F3), recipient scoping, and
  the online/offline vehicle flag (F-A1) — `notifications` today is
  backend-storage-plus-portal-polling only.
- Geofence, device health, charging policy, payment and billing.
- Web portal, vehicle app, centralized observability and production
  reliability.

Items confirmed as needed in the future must be recorded in
[`docs/01-requirements/future.md`](docs/01-requirements/future.md); do not
create placeholders in active source.
