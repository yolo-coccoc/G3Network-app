# Architecture — what exists today

What is actually built in this repository right now: the running components,
the technology stack, each backend domain, the database, local infrastructure
and the directory layout, then what is not built yet. Progress per product
feature lives in the [feature catalog](../product/features/README.md); why a
choice was made lives in the [decision log](../decisions/decision-log.md).

Contents: [Components](#current-components) ·
[Technology stack](#technology-stack) · [Database](#database) ·
[Local infrastructure](#local-infrastructure) ·
[Directory structure](#directory-structure) · [Not built yet](#not-yet-in-the-mvp)

The repo is a backend MVP monorepo. The diagram below describes the
components present in the source and local infrastructure; Kafka, Redis, API
Gateway, the portal and the vehicle app are only future directions, not
active components yet.

```mermaid
flowchart LR
    Vehicle["Vehicle telematic device"]
    Station["OCPP charging station\n(1.6J or 2.0.1)"]
    Broker["EMQX 5.5\nMQTT"]
    Ingestion["Telemetry ingestion\nMQTT consumer + worker"]
    OCPP["charging_stations/ocpp\nWebSocket gateway"]
    Monitor["telematics/monitoring\nperiodic device-health check"]
    API["FastAPI API"]
    Domains["vehicles\ntelematics\ntelemetry\ncharging_stations\ncharging_sessions\ndrivers\nsupport\nfleet"]
    Notifications["notifications"]
    DB[("PostgreSQL 16\nTimescaleDB + PostGIS")]
    Portal["Admin web portal\n(polls, not built yet)"]

    Vehicle -->|MQTT telemetry| Broker
    Broker --> Ingestion
    Ingestion -->|vehicle_telemetry| DB
    Ingestion -->|battery/SOH/anomaly/geofence alerts, F-A2-A5| Notifications
    Ingestion -->|nearest available station, fleet geofences| Domains
    Notifications -->|notifications| DB

    Station <-->|OCPP 1.6J / 2.0.1| OCPP
    OCPP -->|raw frames, status, session + measurements| Domains
    Domains -->|charging data| DB

    Monitor -->|last-seen check, F-J1/F-J3| DB
    Monitor -->|silence -> alert| Notifications

    API --> Domains
    API --> Notifications
    Domains -->|SOS -> SOS_ALERT, F-I2| Notifications
    API -->|F-J2 command publish, per device or fleet| Broker
    Broker -.->|command topic, not consumed by any device yet| Vehicle
    Domains -->|CRUD/query| DB
    Portal -.->|GET /api/v1/notifications| API
```

## Current components

### Backend API

FastAPI registers the following domains:

- `vehicles`: vehicle CRUD and soft delete (list filterable by `status`;
  a vehicle has an owning organization, a model from the `vehicle_models`
  catalog, change history and the `vehicle_ownership_periods` view) and the
  catalog endpoints `/vehicle-models`. The pack capacity other domains read
  (F-A6/F-C6) is the model's nominal capacity. The device-activation state
  machine is gone (computed later, VH-06). `batteries` (battery models,
  batteries, the `battery_installation_periods` view) and `warranties` have
  models only until WP3 (VH-20).
- `telematics`: device CRUD and mapping devices to vehicles (an unknown
  VIN is a 404; at most one *live* device per vehicle, so a soft-deleted
  device no longer blocks its replacement; a device on a soft-deleted
  vehicle maps nothing), a periodic device-health monitor (F-J1/F-J3,
  partial) - this backend's first non-event-driven background process,
  which judges silence from the newest backend `received_at`
  (`telemetry.service.resolve_last_telemetry_at`), so a skewed device
  clock can't fake silence - and a backend-to-device MQTT command
  publisher (`telematics/commands/`, F-J2 partial) - this backend's only
  MQTT *publish* path, pushing a telemetry publish-interval change to
  `g3network/telematics/{serial}/command`, for one device or for every
  current member of a fleet (sequential, one result per vehicle). Device
  responses carry read-time health (`last_seen_at`, `is_online`,
  `is_silent`, `last_signal_strength_dbm`) through
  `telemetry.service.resolve_vehicle_live_status`; `is_silent` and the
  monitor share one rule (`monitoring/silence_rule.py`).
- `telemetry`: receiving data via the ingestion service, reading a
  vehicle's latest telemetry (with `received_at` and a read-time
  `is_online`, `TELEMETRY_ONLINE_THRESHOLD_SECONDS`) and history
  (including F-A3's `soh_percent`/`cycle_count`), a daily F-A3
  battery-health trend, and two SOC-based aggregate reports (F-A6
  operating performance with an optional day/week/month breakdown and CSV,
  F-C6 energy usage) computed from the same telemetry history via a
  window-function query. Fleet-wide views are served here through a
  one-directional `telemetry → fleet` edge (`fleet` never calls
  `telemetry`): member positions/online flags
  (`/telemetry/fleets/{fleet_id}/vehicles/latest`) and the fleet operating
  rollup (`/telemetry/fleets/{fleet_id}/operating-report`, rates
  recomputed from summed distance/energy). `service.py` is the public,
  I/O-orchestrating API; the pure work lives in internal modules
  (`time_windows.py` window validation, `mappers.py` responses,
  `reports.py` F-A3/F-A6/F-C6 calculations and CSV, `detection.py` alert
  detectors), `alerting.py` writes the battery/SOH/anomaly notifications
  (it owns the `notifications`/`charging_stations` edges) and
  `geofencing.py` raises `GEOFENCE_ALERT`s (it owns the `fleet` edge used
  by ingestion). Report days/weeks/months are cut in
  `APP_REPORT_TIMEZONE`; timestamps stay UTC. Ingestion
  (`telemetry/ingestion/`: MQTT consumer -> in-memory queue -> message
  worker) stores one message per transaction - the alerts and geofence
  checks run in the same transaction, after the insert; a payload that
  isn't UTF-8, JSON or the schema is logged and dropped, and the process
  exits non-zero when the consumer or worker stops on its own (only a
  shutdown signal is a clean exit).
- `charging_stations`: Station → EVSE → Connector topology CRUD, station
  directory metadata (location, power rating, connector standard, operating
  hours, maintenance status, read-time `available_connector_count`), a
  nearby-station radius search with an `is_available_only` filter (F-D1),
  a per-station status view (`GET /charging-stations/{id}/connectors`:
  the whole charger plus every gun), the all-stations energy ranking
  (`GET /charging-sessions/stations/energy`, F-C5, served here because this
  domain owns the station directory), and the OCPP gateway. "Available"
  (F-A2/F-D1) = not deleted, `OPERATIONAL` and at least one connector whose
  last status is `Available`; `is_online` is reported but not required.
  The gateway serves **OCPP 2.0.1 and OCPP 1.6J**: it
  negotiates the WebSocket subprotocol (`ocpp2.0.1` preferred, `ocpp1.6`
  accepted) and uses one adapter class per protocol
  (`ocpp201_charge_point.py::OCPP201ChargePoint`, `ocpp16_charge_point.py::OCPP16ChargePoint`);
  `ocpp_server.py` only does the handshake, negotiation and connection
  handling. The adapters write through the internal `ocpp_state_service.py`
  / `ocpp_state_repository.py` (identity/topology resolution, frame log,
  boot info, charger/connector status, configuration captures); the public
  `service.py`/`repository.py` keep topology CRUD, the directory and geo
  searches and the configuration read.
  A `RecordingConnection` wrapper stores every frame, both directions,
  verbatim in `charging_ocpp_messages` before it is parsed, and every
  inbound frame of either protocol refreshes `last_seen_at` (the station's
  `is_online` is derived from it). For 1.6J the adapter handles
  `BootNotification` (device info, firmware-change warning), `Heartbeat`,
  `StatusNotification` (F-C2, incl. connector `0` = the whole charger,
  stored on the station), `Authorize`, `StartTransaction`,
  `StopTransaction` and `MeterValues`; and after each boot the gateway asks
  the charger for `GetConfiguration` in a separate task and stores the
  answer (`GET /charging-stations/{id}/configuration`). The 2.0.1 adapter
  handles `BootNotification` (device info and `last_boot_at`, the same
  heartbeat interval), `Heartbeat`, `TransactionEvent`, `MeterValues`
  (energy only) and `StatusNotification`.
  1.6J is verified against a simulator; a real charger has not been connected.
- `charging_sessions`: storing the session aggregate, lifecycle events and
  the session's measurements, plus F-C5's station-level energy total over a
  window and an hourly/daily energy series (energy-register deltas between
  consecutive readings, each booked in the bucket of its later reading,
  buckets cut in `APP_REPORT_TIMEZONE`). The session detail adds a
  read-time summary (`duration_seconds`, first/last SoC, `max_power_kw`)
  and the list filters by station, connector, status and start window;
  `resolve_station_energy_total` is the DTO `charging_stations` uses for the
  all-stations ranking. All measurements (the energy register that drives the session
  total, and SoC/power/voltage/current/temperature/`Power.Offered` and
  vendor-specific measurands) live in one `charging_session_measurements`
  table; `/meter-values` is the energy-only view and `/measurements` shows
  everything. The 1.6J backend assigns the integer `transactionId` from a
  database sequence and stores the `idTag`, stop reason and the charger's
  own `meterStop`. F-B2's four correctness fixes on the happy path still hold:
  persisted OCPP `seqNo`, a guard refusing any event on an already-`COMPLETED`
  session, a time-ordering watermark stopping a stale `MeterValues` from
  overwriting a newer reading, and unit-aware energy normalization in each
  OCPP adapter (still no retry/out-of-order recovery/DLQ/dedup).
- `notifications`: a generic, backend-storage notification table (F-A2)
  polled via `GET /api/v1/notifications?after_id=` (ascending cursor;
  `order=desc` returns the newest page instead), filterable by vehicle,
  type and severity, with a single read, an unread count and
  mark-all-read. Telemetry ingestion raises one when a vehicle's SOC
  crosses the 30/20/10% tiers (carrying the nearest available charging
  station and its coordinates, resolved via `charging_stations`, the first
  PostGIS spatial query in the codebase), SOH drops below its threshold
  (F-A3), an anomaly is detected (F-A4) or a vehicle enters/leaves a fleet
  geofence (F-A5); the device-health monitor raises one when a vehicle goes
  silent (F-J1/F-J3); `support` raises a `CRITICAL` `SOS_ALERT` for every
  new SOS (F-I2). No push and no recipient scoping yet — there is no mobile
  app and no `identity` domain.
- `drivers`: this backend's first brand-new domain since the initial
  baseline (F-E4). Driver profile CRUD plus a `driver_vehicle_assignments`
  assignment-history table (`assigned_at`/`unassigned_at`) enforcing "one
  active vehicle per driver, one active driver per vehicle" via this
  backend's first partial unique indexes, with smooth reassignment
  (auto-closes the driver's previous active assignment) and full
  per-driver assignment history; the driver list searches name/phone/license
  (`q`) and finds the driver currently assigned to a VIN (`vehicle_vin`).
  Depends one-directionally on `vehicles`' public service to
  resolve/validate a VIN — the same shape as `telematics → vehicles`. F-A9 (empty-trip detection) is suspended, not
  built here — see `deferred.md` item 67.
- `support`: support case tickets (F-I1) and SOS intake (F-I2). One
  `support_cases` table discriminated by `case_type` rather than two
  tables. Depends one-directionally on `vehicles` (resolve/validate a VIN)
  and `drivers` (validate a driver ID, enrich `driver_name`) - deliberately
  not wired to `telemetry`, since vehicle context is client-supplied at
  case-creation time rather than fetched live. A response-SLA deadline is
  copied onto each row at creation so a later config change never rewrites
  a past case's SLA; `first_responded_at`/`resolved_at` are stamped only
  the first time a status implies them, and a case cancelled before any
  response is judged at `closed_at`. A CLOSED/CANCELLED case refuses
  further updates. An SOS records its `channel` (hotline fallback allowed,
  coordinates required only in-app) and raises an `SOS_ALERT` through
  `notifications` in the same transaction (`support → notifications`
  edge). The case list filters by category, channel, driver,
  `awaiting_response` and `sla_breached` (the SQL form of the breach rule).
  F-I4 (partner directory/dispatch) and F-I3 (booking) are deferred.
- `fleet`: this backend's second brand-new domain (F-E1; FLT-01/FLT-02 in
  the feature catalog). Fleet CRUD plus a `fleet_vehicle_memberships`
  open/close table (`added_at`/`removed_at`) mirroring
  `driver_vehicle_assignments`'s shape, with one difference: no partial
  unique index on `fleet_id` (a fleet holds many vehicles at once). A fleet
  has no status (it exists or is soft-deleted) and needs a `name`, a
  `fleet_code` or both (`ck_fleets_name_or_code`; the code is unique per
  organization among fleets not deleted, FL-08). A fleet is owned by an
  organization (`organization_id`, required until authentication lets the
  API take it from the caller); a parent must belong to the same one. Fleets
  keep a change history (`fleet_history`).
  Fleets nest through `parent_fleet_id` (FL-02): a parent must be a live
  fleet, a move under the fleet itself or one of its sub-fleets is refused
  (400), and so is deleting a fleet that still has live sub-fleets (409).
  Nothing rolls up the tree yet: `vehicle_count`, the fleet views in
  `telemetry`, geofences and the fleet-wide config push all cover only a
  fleet's own members, not those of its sub-fleets.
  Depends one-directionally on `vehicles` to resolve/validate a VIN on
  membership add/remove (both by VIN) and to enrich F-E1's vehicle list
  (`vin`/`license_plate`/`status`, via a `VehicleSummary` DTO; null for a
  member whose vehicle was soft-deleted; such a membership is closed by its
  ID). Replaces the dead `vehicles.fleet_id` column. Also owns
  **geofences** (F-A5): polygons per fleet (`/fleets/{id}/geofences`
  CRUD), applied to the fleet's current members. Its public service gives
  other domains the current member vehicle IDs, a vehicle's current fleet
  and the geofences covering a point (`ST_Covers` on geography); `telemetry`
  (fleet views, geofence alerts) and `telematics` (fleet-wide config push)
  call them, never the reverse. F-E2 (KPI dashboard) is deferred.

The API process runs separately via Uvicorn. Telemetry ingestion, the OCPP
gateway, and the telematics device-health monitor each have their own
entrypoint, sharing the same database/session configuration.

Domain exceptions are mapped to HTTP once: each inherits one base in
`app/libs/common/errors.py` (`NotFoundError` 404, `ConflictError` 409,
`InvalidInputError` 400, `UpstreamUnavailableError` 502) and
`app/api/main.py` registers one handler per base, answering
`{"detail": ...}`. Shared, business-free helpers live in `app/libs/`:
`common/clock.utc_now`, `common/pagination.normalize_page_window` (used by
every paginated list), `common/geo`, and `db/enums.enum_values`. Between
domains, import-linter (`backend/pyproject.toml`) allows importing only
another domain's `service`, `types` and `exceptions` modules, and
`tests/test_import_contracts_smoke.py` fails when a new internal module is
missing from its domain's contract. The current cross-domain edges are
listed in `.claude/rules/domain-boundaries.md`.

## Technology stack

Facts only; the reasoning and alternatives are in the
[decision log](../decisions/decision-log.md) under the IDs shown.

| Part | Choice | Decision |
|---|---|---|
| Backend | Python 3.12, FastAPI, async SQLAlchemy, Pydantic v2 | TT-01 |
| Environment and dependencies | `uv` (`pyproject.toml` + `uv.lock`) | TT-02 |
| Database | PostgreSQL 16 + TimescaleDB (time series) + PostGIS (geo) | DM-01 |
| Schema migrations | Alembic, one baseline migration edited in place until real data must be kept | DM-03 |
| Vehicle data broker | EMQX 5.5 (MQTT); ACLs are set through its Dashboard/REST API, not `acl.conf` | IS-06 |
| Charger protocol | OCPP 1.6J and 2.0.1 through the `ocpp` library, one adapter per protocol, inside `charging_stations` | CO-01 |
| Formatting and lint | Ruff only (line length 88) | TT-03 |
| Types | mypy strict on `app` and `tests` (Pyright advisory) | TT-04 |
| Domain boundaries | import-linter, one contract per domain | CV-01 |
| Quality gate | local pre-commit hook running `make check`; no CI yet | TT-05 |
| Reverse proxy / API gateway | none in development | IS-02 |
| Web portal, vehicle app | proposed only (React + Vite, Flutter); decided when their first task starts | TT-20 |

## Database

Development uses a single PostgreSQL 16 container with the following
extensions:

- TimescaleDB for five hypertables: `vehicle_telemetry`,
  `charging_session_events`, `charging_session_measurements`,
  `charging_ocpp_messages` (the append-only raw OCPP message log) and
  `access_audit_logs` (identity).
  `vehicle_telemetry` is indexed on `(vehicle_id, recorded_at DESC)` for the
  latest/history reads and on `(vehicle_id, received_at DESC)` for the
  device-health monitor's last-seen lookup.
- PostGIS: used for `charging_stations.location` (F-C1),
  `vehicle_telemetry.location` and `support_cases.location` (all
  `geography(Point, 4326)` columns) and `geofences.boundary`
  (`geography(POLYGON, 4326)`, F-A5). Only `charging_stations.location`
  has a GIST index: `vehicle_telemetry.location` is a high-frequency write
  path that is never searched spatially, and a geofence check always
  filters by fleet first (`ix_geofences_fleet_id`) before `ST_Covers`.
  F-A2's nearest-available-station lookup uses that index (`ST_Distance` +
  the `<->` KNN operator, filtered on connector status); F-D1's nearby-station
  search (`GET /charging-stations/nearby`) uses `ST_DWithin` for the radius
  filter with the same KNN ordering.
- "One live row" constraints are partial unique indexes: one open driver
  assignment per driver and per vehicle (`WHERE unassigned_at IS NULL`),
  one open fleet membership per vehicle (`WHERE removed_at IS NULL`), and one
  live telematic device per vehicle (`uq_telematics_active_vehicle`,
  `WHERE deleted_at IS NULL`).
- `uuid-ossp` for the local database.

The schema is a single Alembic migration, `0001_baseline_schema`. During the
bootstrap phase (no data worth keeping) a schema change edits that migration
instead of adding a revision, and `make db-reset` clears the database and
rebuilds it; see `.claude/rules/database.md`.

The charging MVP only supports pre-provisioned topology and the happy path
(2.0.1: `Started → Updated/MeterValues → Ended`; 1.6J: `StartTransaction →
MeterValues → StopTransaction`). The OCPP 1.6J work added an append-only raw
OCPP message log, the charger's device/liveness fields, the widened connector
status, session `idTag`/stop reason/`meterStop`, the unified measurements
table and configuration snapshots, but **not** reconnect/offline recovery,
fault alerting, remote commands or TLS/authentication (`deferred.md` items 27,
73-76).

## Local infrastructure

`infra/docker-compose.yml` only starts two services:

- `db`: PostgreSQL/TimescaleDB/PostGIS on port `5432`.
- `broker`: EMQX on port `1883`, dashboard on `18083`.

The backend runs directly on the host via `uv`; there is no API Gateway or
reverse proxy in the development environment.

## Directory structure

The layout of active source (`backend/`, `infra/`, `simulator/`) and of the
project tooling. Where a new file belongs is decided here; how domains may
call each other is in
[domain-boundaries.md](../../.claude/rules/domain-boundaries.md).

```
.
├── backend/
│   ├── app/                        # Main source code (uv package layout)
│   │   ├── domains/
│   │   │   ├── vehicles/              # Vehicle profile, owner, model catalog (F-F2)
│   │   │   │   ├── router.py  service.py  repository.py  schemas.py  models.py  types.py  exceptions.py
│   │   │   │
│   │   │   ├── telematics/            # Telematic device profile and mapping to vehicles (F-G1)
│   │   │   │   ├── router.py  service.py  repository.py  schemas.py  models.py  types.py  exceptions.py
│   │   │   │   ├── commands/          # mqtt_publisher.py: F-J2 config-push publisher (the only MQTT publish path)
│   │   │   │   └── monitoring/        # device_health_monitor.py + entrypoint.py for "make telematics-monitor-dev" (F-J1/F-J3);
│   │   │   │                          # silence_rule.py: pure silence rule shared by the monitor and the API's is_silent
│   │   │   │
│   │   │   ├── telemetry/             # Real-time & historical vehicle data (F-A1)
│   │   │   │   ├── router.py  service.py  repository.py  schemas.py  models.py  types.py  exceptions.py
│   │   │   │   ├── time_windows.py    # internal, pure: history/report time-window validation
│   │   │   │   ├── mappers.py         # internal, pure: ORM row/MQTT message -> responses, F-A4 snapshot
│   │   │   │   ├── reports.py         # internal, pure: F-A3/F-A6/F-C6 report calculations, fleet rollup, CSV
│   │   │   │   ├── detection.py       # internal, pure: F-A2/F-A3/F-A4 alert detectors
│   │   │   │   ├── alerting.py        # internal, I/O: writes the alert notifications (owns the notifications/charging_stations edges)
│   │   │   │   ├── geofencing.py      # internal, I/O: F-A5 geofence entry/exit per reading -> GEOFENCE_ALERT (owns the ingestion's fleet edge)
│   │   │   │   └── ingestion/         # Receives telematics data via MQTT (EMQX)
│   │   │   │       ├── mqtt_consumer.py  message_worker.py
│   │   │   │       └── entrypoint.py  # entrypoint for "make telemetry-dev" (runs on the host, not a container — see dev-environment.md)
│   │   │   │
│   │   │   ├── charging_stations/     # Station/EVSE/connector topology and OCPP (F-C1, F-G2)
│   │   │   │   ├── router.py  service.py  repository.py  schemas.py  models.py  types.py  exceptions.py
│   │   │   │   │   # service.py/repository.py: topology CRUD, directory and geo searches, configuration read
│   │   │   │   ├── ocpp_state_service.py     # internal (ocpp/ only): identity/topology resolution, frame log, boot info, charger/connector status, GetConfiguration captures
│   │   │   │   ├── ocpp_state_repository.py  # the matching queries
│   │   │   │   └── ocpp/              # WebSocket server for charging station communication (OCPP 2.0.1 and 1.6J)
│   │   │   │       ├── ocpp_server.py       # handshake, subprotocol negotiation, connection handling (no protocol handler)
│   │   │   │       ├── ocpp201_charge_point.py # OCPP 2.0.1 adapter (OCPP201ChargePoint): Boot/Heartbeat/TransactionEvent/MeterValues/StatusNotification + payload helpers
│   │   │   │       ├── ocpp16_charge_point.py  # OCPP 1.6J adapter (OCPP16ChargePoint): Boot/Heartbeat/Status/Authorize/Start/Stop/MeterValues + post-boot GetConfiguration
│   │   │   │       ├── ocpp16_measurements.py  # pure 1.6J MeterValues -> energy samples + measurements (never shares code with the 2.0.1 normalizer)
│   │   │   │       ├── parsing.py           # protocol-neutral helpers shared by both adapters (OcppPayload, timestamp parsing/formatting)
│   │   │   │       ├── raw_log.py           # RecordingConnection: verbatim, append-only log of every OCPP frame
│   │   │   │       └── entrypoint.py  # entrypoint for "make charging-ocpp-dev" (runs on the host, not a container)
│   │   │   │
│   │   │   ├── charging_sessions/     # Stores events, meter data, and charging session lifecycle (F-B2)
│   │   │   │   └── router.py  service.py  repository.py  schemas.py  models.py  types.py  exceptions.py
│   │   │   │
│   │   │   ├── notifications/         # Generic operator-facing notification storage/polling (F-A2)
│   │   │   │   └── router.py  service.py  repository.py  schemas.py  models.py  types.py  exceptions.py
│   │   │   │
│   │   │   ├── drivers/               # Driver profile and vehicle assignment history (F-E4)
│   │   │   │   └── router.py  service.py  repository.py  schemas.py  models.py  types.py  exceptions.py
│   │   │   │       # models.py has 2 tables: DriverModel, DriverVehicleAssignmentModel
│   │   │   │
│   │   │   ├── support/               # Support case tickets and SOS intake (F-I1, F-I2)
│   │   │   │   └── router.py  service.py  repository.py  schemas.py  models.py  types.py  exceptions.py
│   │   │   │
│   │   │   ├── fleet/                 # Fleet CRUD, vehicle membership and geofences (F-E1, F-A5)
│   │   │   │   └── router.py  service.py  repository.py  schemas.py  models.py  types.py  exceptions.py
│   │   │   │       # models.py has 3 tables: FleetModel, FleetVehicleMembershipModel, GeofenceModel
│   │   │   │
│   │   │   ├── batteries/             # Battery models and batteries; models and enums only until WP3 (BAT-01)
│   │   │   ├── warranties/            # Warranties of trucks, batteries, T-Boxes, chargers; models and enums only until WP3 (WAR-01)
│   │   │   └── identity/              # Organizations, users, memberships, roles, credentials, sessions, consent, audit log (F-F1); models only until WP2
│   │   │       └── models.py  types.py
│   │   │           # models.py has 12 tables (OrganizationModel ... AccessAuditLogModel, OrganizationSettingModel); no service, router or API yet
│   │   │
│   │   ├── api/
│   │   │   └── main.py                # FastAPI app that merges routers from every domains/*/router.py; run via "make backend-dev" (host, not a container)
│   │   │
│   │   └── libs/
│   │       ├── common/                 # shared, NO business logic: config, logging, errors (domain-exception bases),
│   │       │                           # clock (utc_now), pagination (normalize_page_window), geo
│   │       └── db/                     # SQLAlchemy base, session, enums (enum_values), Alembic migrations
│   │
│   ├── tests/                      # one package per domain (tests/<domain>/test_*_smoke.py) plus tests/libs/;
│   │                               # shared builders.py/fakes.py; cross-cutting tests at the top level
│   │                               # (API, migration smoke, PostgreSQL integration)
│   ├── alembic.ini
│   ├── pyproject.toml              # dependencies + dev group, ruff, mypy, import-linter contracts
│   ├── uv.lock
│   ├── README.md
│   └── .env.example                # every setting with its default; no production Dockerfile at this stage yet
│
├── infra/
│   ├── docker-compose.yml          # defines exactly 2 services: db, broker (see dev-environment.md)
│   ├── .env.example
│   └── db/
│       └── init/                   # 01-extensions.sql: enables timescaledb, postgis, uuid-ossp on DB init
│
├── simulator/                      # Vehicle telemetry + OCPP charging session simulators (manual smoke testing only)
│   ├── seed_simulator_devices.py     telematic_simulator.py           # vehicle telemetry over MQTT
│   ├── seed_charging_topology.py     charging_session_simulator.py    # OCPP 2.0.1 (seed also does the 1.6J layout)
│   └── ocpp16_charge_point_simulator.py                               # OCPP 1.6J charger (--scenario boot|status|session)
│
├── docs/                           # see docs/README.md for what each folder is
│   ├── product/                      # WHAT we build: features/ (catalog), inputs/ (business files)
│   ├── design/                       # HOW it is built: this file, domain-model/, specifications/
│   ├── decisions/                    # WHY: decision-log.md, deferred.md (+ deferred-resolved.md)
│   ├── planners/                     # build logs (active; done/)
│   ├── reports/                      # point-in-time analyses and progress reports
│   └── archive/                      # superseded documents
├── .githooks/pre-commit            # runs `make check`; enabled per clone with `make install-hooks`
├── .claude/                        # Claude Code: rules/, skills/, agents/, hooks/, settings.json (settings.local.json is untracked)
├── .mcp.json                       # project MCP servers (read-only PostgreSQL, Context7)
├── .vscode/                        # shared VS Code settings (settings.json) + recommended extensions (extensions.json)
├── Makefile                        # command source of truth (`make help`; first run: `make setup`)
├── README.md
└── CLAUDE.md
```

Not present yet: `backend/Dockerfile`, `infra/docker-compose.prod.yml`, a
`scripts/` directory, and a single shared `.env.example` at the repo root —
each part (`backend/`, `infra/`) currently keeps its own `.env.example`.
Don't create these files/directories before a concrete task needs them.

Domains that have feature codes in `docs/product/feature-list.md`
but **no source yet**: `policy`, `billing`, `scoring` (`identity` has its tables only, no service or API). Don't
create empty directories/files for them before a concrete task exists; when
creating one, apply [domain-boundaries.md](../../.claude/rules/domain-boundaries.md) and
reference the correct feature code.

Some built domains are intentionally partial — `notifications` (no push, no
recipient scoping), `support` (no partner directory/dispatch), `fleet` (no KPI
dashboard; its live-position and operating-rollup views live in `telemetry`,
which depends on `fleet`, not the reverse). Their planners in
`docs/planners/done/` record what was left out.

`web-portal/` (React/TS) and `vehicle-app/` (Flutter) are planned monorepo
components with no source yet; their directory structure and coding
convention will be written once the first task for that part starts.

## Not yet in the MVP

- User, authentication and RBAC (the `identity` domain has its tables only:
  no service, login or API yet). `drivers` now has a profile-CRUD/assignment slice (F-E4),
  but no login/auth of its own and no empty-trip detection (F-A9,
  suspended — no trip concept exists in this backend).
- True trip segmentation (start/end detection, idle-gap grouping): F-A5's
  trip replay is a bounded time-range history query
  (`GET /telemetry/vehicles/{id}/history`). Geofences are owned by a fleet,
  not yet by a customer account or a single vehicle (`deferred.md` item 86).
- Fleet audit: the acting user is not passed yet to `added_by`/`removed_by`
  on memberships or as the change reason of fleet edits (waits for
  authentication, WP2 of the refactor plan);
  `PATCH /fleets/{fleet_id}` cannot clear a name or code, nor move a fleet
  back to the top level (`deferred.md` item 98).
- Technical status history and stale-status handling for chargers: an
  offline charger keeps its last connector statuses, and "available" does
  not require `is_online` (`deferred.md` item 76).
- Push/multi-channel notification delivery (F-F3) and recipient scoping —
  `notifications` today is backend-storage-plus-portal-polling only.
- A device error-code catalog (cell/module vs. motor fault classification),
  vendor-validated F-A4 anomaly thresholds, re-alert/escalation for a
  persisting anomaly, and a motor-temperature anomaly detector.
- F-J1's SIM/power status fields and F-J3's power-loss-vs-signal-loss
  distinction (no such data in the MQTT contract), charging policy,
  payment and billing.
- F-J2's confirmation-of-applied-config and rollback (no MQTT ack topic
  exists, so a successful publish only proves the broker accepted the
  message) and local alert thresholds (only the telemetry publish interval
  is implemented).
- Time-of-use or per-tenant electricity pricing for F-A6 (one flat
  `TELEMETRY_ENERGY_COST_PER_KWH_VND` setting today), and vendor-confirmed
  battery capacity (falls back to a documented default when a vehicle has
  none recorded). F-C6 specifically cannot satisfy NF-10's 3-way
  reconciliation with its current SOC-based method - that needs the
  vehicle-linkage `charging_sessions` still lacks.
- F-E2's fleet KPI dashboard (the km/kWh/cost half exists as the fleet
  operating report; SOH, alert counts and utilization are missing -
  `deferred.md` item 72), F-E3's charging & warranty report, and F-A8's
  per-driver charging-efficiency report - the latter two are hard-blocked
  on `charging_sessions` having no vehicle/driver linkage at all (same
  blocker as F-C6/NF-10 above), plus F-E3 also needs the `policy` domain.
- F-I4's repair/rescue partner directory and dispatch routing, and F-I3's
  maintenance-scheduling booking - both considered alongside F-I1/F-I2 when
  `support` was built but deferred (`deferred.md` items 68-69). An
  SLA-breach monitor/escalation for support cases and support-case
  ownership scoped to an authenticated driver also don't exist yet
  (`deferred.md` items 70-71).
- Web portal, vehicle app, centralized observability and production
  reliability.

Items confirmed as needed in the future must be recorded in
[`docs/decisions/deferred.md`](../decisions/deferred.md); do not
create placeholders in active source.
