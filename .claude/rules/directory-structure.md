# Directory structure (backend & infra as it exists)

> Read this file when you need to know where a domain/module belongs, or
> before creating a new file/directory. General rules in
> [`../../CLAUDE.md`](../../CLAUDE.md); communication boundaries between
> domains in [domain-boundaries.md](./domain-boundaries.md).

The tree below reflects **active source**: `backend/`, `infra/`, `docs/`.
`web-portal/` and `vehicle-app/` don't exist in the repo yet — don't create
placeholders for either part; their structure will be defined once real
implementation starts.

```
.
├── backend/
│   ├── app/                        # Main source code (uv package layout)
│   │   ├── domains/
│   │   │   ├── vehicles/              # Static profile, provisioning, activate/deactivate (F-F2)
│   │   │   │   ├── router.py  service.py  repository.py  schemas.py  models.py  types.py  exceptions.py
│   │   │   │
│   │   │   ├── telematics/            # Telematic device profile and mapping to vehicles (F-G1)
│   │   │   │   ├── router.py  service.py  repository.py  schemas.py  models.py  types.py  exceptions.py
│   │   │   │   ├── commands/          # mqtt_publisher.py: F-J2 config-push publisher (the only MQTT publish path)
│   │   │   │   └── monitoring/        # device_health_monitor.py + entrypoint.py for "make telematics-monitor-dev" (F-J1/F-J3)
│   │   │   │
│   │   │   ├── telemetry/             # Real-time & historical vehicle data (F-A1)
│   │   │   │   ├── router.py  service.py  repository.py  schemas.py  models.py  types.py  exceptions.py
│   │   │   │   ├── time_windows.py    # internal, pure: history/report time-window validation
│   │   │   │   ├── mappers.py         # internal, pure: ORM row/MQTT message -> responses, F-A4 snapshot
│   │   │   │   ├── reports.py         # internal, pure: F-A6/F-C6 report calculations
│   │   │   │   ├── detection.py       # internal, pure: F-A2/F-A3/F-A4 alert detectors
│   │   │   │   ├── alerting.py        # internal, I/O: writes the alert notifications (owns the notifications/charging_stations edges)
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
│   │   │   │       ├── ocpp201_charge_point.py # OCPP 2.0.1 adapter (OCPP201ChargePoint): TransactionEvent/MeterValues/StatusNotification + payload helpers
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
│   │   │   └── fleet/                 # Fleet CRUD and vehicle membership (F-E1)
│   │   │       └── router.py  service.py  repository.py  schemas.py  models.py  types.py  exceptions.py
│   │   │           # models.py has 2 tables: FleetModel, FleetVehicleMembershipModel
│   │   │
│   │   ├── api/
│   │   │   └── main.py                # FastAPI app that merges routers from every domains/*/router.py; run via "make backend-dev" (host, not a container)
│   │   │
│   │   └── libs/
│   │       ├── common/                 # shared, NO business logic: config, logging, errors (domain-exception bases),
│   │       │                           # clock (utc_now), pagination (normalize_page_window), geo
│   │       └── db/                     # SQLAlchemy base, session, enums (enum_values), Alembic migrations
│   │
│   ├── tests/                      # one package per domain (tests/<domain>/test_*_smoke.py);
│   │                               # shared builders.py/fakes.py; cross-cutting tests at the top level
│   ├── pyproject.toml
│   ├── uv.lock
│   └── .env.example                # no production Dockerfile at this stage yet
│
├── infra/
│   ├── docker-compose.yml          # defines exactly 2 services: db, broker (see dev-environment.md)
│   ├── .env.example
│   └── db/
│       └── init/                   # script that enables the timescaledb, postgis extensions on DB init
│
├── simulator/                      # Vehicle telemetry + OCPP charging session simulators (manual smoke testing only)
│   ├── seed_simulator_devices.py     seed_charging_topology.py
│   └── telematic_simulator.py        charging_session_simulator.py
│
├── docs/                           # feature specs, architecture diagrams (already present)
├── .githooks/pre-commit            # runs `make check`; enabled per clone with `make install-hooks`
├── .mcp.json                       # project MCP servers (read-only PostgreSQL, Context7)
├── Makefile
└── CLAUDE.md
```

Not present yet: `backend/Dockerfile`, `infra/docker-compose.prod.yml`, a
`scripts/` directory, and a single shared `.env.example` at the repo root —
each part (`backend/`, `infra/`) currently keeps its own `.env.example`.
Don't create these files/directories before a concrete task needs them.

## Out of current scope

Domains that have feature codes in `docs/01-requirements/feature-list.md`
but **no source yet**: `identity`, `policy`, `billing`, `scoring`. Don't
create empty directories/files for them before a concrete task exists; when
creating one, apply [domain-boundaries.md](./domain-boundaries.md) and
reference the correct feature code.

Some built domains are intentionally partial — `notifications` (no push, no
recipient scoping), `support` (no partner directory/dispatch), `fleet` (no KPI
rollup). Their planners in `docs/02-planners/done/` record what was left out.

`web-portal/` (React/TS) and `vehicle-app/` (Flutter) are planned monorepo
components with no source yet; their directory structure and coding
convention will be written once the first task for that part starts.
