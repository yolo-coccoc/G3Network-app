# Planner: Backend Telemetry Ingestion (F-A1, F-A5)

> Feature code: F-A1 (Real-time vehicle telemetry ingestion), F-A5 (Location, trip history & geofencing)
> Status: 🚧 Active MVP source and minimal automated smoke tests have been
> deployed; the batch path and integration tests are deferred to the next phase
> Date created: 2026-07-24
> Last reviewed: 2026-07-30

---

## Overview

Build a telemetry data ingestion system from electric trucks following the **per-message processing** model:

**Data flow:**
```
Telematic Device → MQTT Broker (EMQX) → Backend Consumer → Message Queue → Message Worker → PostgreSQL (TimescaleDB)
```

**Current MVP principle:**
- Telematic publishes messages continuously (every 5-10 seconds)
- Backend consumes messages and puts them into an in-memory queue
- The message worker takes each message and processes it as soon as it is in the queue
- Each message runs in its own transaction to reduce latency and isolate failures

**Batch path is deferred, its code removed (2026-10-01):**
- `batch_worker.py`, `process_batch()`, the batch lookup and the bulk insert
  were deleted under the no-preemptive-batching rule; their design is kept in
  `docs/01-requirements/future.md` item 25 (the steps below that build or
  preserve them are history)
- Building it again must be benchmarked and the transaction/backpressure
  semantics finalized first

The old acceptance results below may refer to previous Alembic revisions.
That is historical at the time it was recorded; the current migration graph is
the reset/baseline `0001` through `0004` in the charging MVP planner.

**Scope:**
- Backend Python async (MQTT consumer + message worker + minimal process
  entrypoint; no HTTP runtime/health server in the current MVP)
- EMQX broker
- TimescaleDB hypertable
- Does not describe telemetry query APIs; the active portion currently only has
  a separate latest endpoint in the `backend-telemetry-query-api.md` planner.
  The frontend dashboard has no source yet.

**MVP assumptions and limitations:**
- MQTT uses QoS 0 (fire-and-forget)
- Assumes messages are sent and received ideally
- No retry handling
- No advanced duplicate detection
- No persistent queue
- No dead-letter queue (DLQ)
- A full queue logs a warning and drops the message; no retry/persist
- No guarantee of zero data loss when the process or database fails
- No metrics/counters in the current ingestion MVP; only structured logging is used
- Structured logs are emitted to `stderr` only; no log shipping/retention/alerting yet
- Shutdown does not drain the queue; messages still in RAM are allowed to be lost
- Reliability layers will be added in a later phase

The MVP scope focuses on proving the flow:
```
Simulator → EMQX → MQTT consumer → asyncio.Queue → message worker → TimescaleDB
```

---

## Current architecture per CLAUDE.md

```
backend/
├── app/
│   ├── domains/
│   │   ├── telematics/
│   │   │   ├── models.py           # Physical Telematic device record
│   │   │   └── service.py          # Public serial → device/vehicle lookup
│   │   ├── telemetry/
│   │   │   ├── models.py           # VehicleTelemetry (TimescaleDB)
│   │   │   ├── repository.py       # Single insert/lookup and future batch path
│   │   │   ├── schemas.py          # MQTT payload validation
│   │   │   ├── service.py          # Single-message and batch processing logic
│   │   │   └── ingestion/
│   │   │       ├── mqtt_consumer.py    # MQTT client, message handler
│   │   │       ├── message_worker.py   # Current async single-message processor
│   │   │       ├── batch_worker.py     # Batch processor kept for a future phase
│   │   │       └── entrypoint.py       # Minimal process entrypoint
│   │   └── vehicles/
│   │       └── models.py           # vehicles table referenced by FK
│   ├── api/
│   │   └── main.py                 # Separate API process, does not run the MQTT consumer
│   └── libs/
│       ├── common/
│       │   ├── config.py           # Shared settings
│       │   └── logging.py          # JSON structured logging
│       └── db/
│           ├── base.py
│           └── session.py          # Shared engine/session factory per process
├── pyproject.toml
└── uv.lock

infra/
└── docker-compose.yml              # db + broker only in development
```

**Notes on domain boundaries:**
- Ingestion, service, repository, schema, and models for measurement data belong
  to the `telemetry` domain, and are allowed to call/import each other directly
  within that domain.
- `telemetry.service` calls the public `telematics.service` to resolve the
  `telematic_serial → (telematic_id, vehicle_id)` mapping. `telemetry.repository`
  only queries/writes models that belong to the `telemetry` domain itself, and
  does not import `telematics.models` or `telematics.repository` directly.
- The database foreign key from `telematics`/`vehicle_telemetry` to `vehicles`
  protects integrity at the persistence layer.
- If telemetry later needs business validation from vehicles, it must only call
  the public API in `vehicles/service.py`.

## Conventions for using this planner as a template document

Each step must clearly record:

1. **Goal and scope**: the outcome to achieve, and what is out of scope for the step.
2. **Contract/decisions**: schema, lifecycle, transaction, failure behavior, and
   resource ownership.
3. **Files changed**: do not create placeholders just to match the directory tree.
4. **Checks**: separate static checks, smoke tests, and integration/E2E tests.
5. **Actual outcome**: the final implementation may differ from the original
   prompt; record the latest decisions and remaining limitations.
6. **Future**: any component that is definitely needed but deferred must be
   recorded in `docs/01-requirements/future.md`, not left as a TODO in source.

---

## List of implementation steps

### Step 0: Research the feature specification

**Goal:** Clearly understand the business and technical requirements

**Prompt:**
```
Read and summarize the following files:
1. docs/01-requirements/feature-list.md - find the F-A1, F-A5 items

Answer the following questions:
- What fields does telemetry data include? (GPS, SOC, speed, voltage...)
- How frequently does the vehicle send data?
- How long does data need to be retained?
- Is a real-time alert needed? (if so, it will be done in a later phase)
```

**Checks:**
- [x] Finalized the contract for the data fields to collect in `mqtt-spec.md`
- [x] Clearly recorded the 5-10 second/message frequency assumption for the MVP
- [x] Identified the persistence dependency on vehicles and the DB/EMQX infrastructure
- [x] Recorded information that is not yet available instead of inventing requirements

**Outcome/Decisions:**

- The MVP payload includes message/device identifiers, recorded time, GPS,
  motion state, battery, motor, signal, and error codes.
- A frequency of 5-10 seconds/message is a design assumption of the planner/MQTT
  spec, not yet an SLA or a volume measured from real devices.
- There is no requirement yet for retention, maximum number of vehicles, peak
  throughput, or a TimescaleDB compression policy. Do not use unconfirmed
  numbers as acceptance criteria.
- Realtime alerts, query APIs, and the dashboard use telemetry data but are
  outside the scope of the ingestion MVP.

---

### Step 1: Design the telematics table

**Goal:** Create a table to manage telematics devices mounted on vehicles

**Prompt:**
```
Design the telematics table in backend/app/domains/telemetry/models.py:

telematics table:
- telematic_id: UUID primary key
- telematic_serial: VARCHAR(50), unique, not null (physical code on the device)
- vehicle_id: UUID foreign key → vehicles.vehicle_id (nullable, can be assigned later)
- status: ENUM ('active', 'inactive', 'maintenance') not null
- firmware_version: VARCHAR(50), nullable
- last_seen_at: TIMESTAMPTZ, nullable (updated when a message is received)
- created_at: TIMESTAMPTZ not null
- updated_at: TIMESTAMPTZ not null

Notes:
- Use async SQLAlchemy 2.0 with Mapped and mapped_column
- Import base from libs.db.base
- Add indexes for serial, vehicle_id
- Add a UNIQUE constraint for vehicle_id (each vehicle has at most 1 telematic)
- Add a docstring
- In Python code, use meaningful names: telematic_id, telematic_serial
```

**Checks:**
- [x] The model has no syntax errors
- [x] Has a foreign key to vehicles
- [x] Has an index for serial and vehicle_id
- [x] Has a UNIQUE constraint for vehicle_id
- [x] Timestamps use `DateTime(timezone=True)` and are UTC timezone-aware
- [x] The model uses the shared `Base`, without creating its own metadata/engine

**Outcome/Decisions:**

- The `Telematic` model belongs to the `telematics` domain; ingestion only
  resolves the mapping via the public `telematics.service`, while the
  `telemetry` domain owns the measurement data.
- The code/DB names currently used are `telematic_id`, `telematic_serial`, and
  `vehicles.vehicle_id`, instead of the `id`, `serial`, `vehicles.id` names in
  the original prompt.
- The foreign key uses `ON DELETE SET NULL`; a unique constraint on
  `vehicle_id` ensures a vehicle has at most one telematic, while multiple
  `NULL` rows are still valid.
- The provisioning API to assign or unassign a device has not been implemented
  in this step.

---

### Step 2: Design the vehicle_telemetry table (TimescaleDB)

**Goal:** Create a hypertable to store time-series telemetry data

**Prompt:**
```
Design the vehicle_telemetry table in backend/app/domains/telemetry/models.py:

vehicle_telemetry table:
- message_id: BIGINT GENERATED BY DEFAULT AS IDENTITY (PK)
- message_uuid: UUID not null (generated by the telematic)
- telematic_id: UUID not null (foreign key → telematics.telematic_id)
- telematic_serial: VARCHAR(50) not null (kept for debugging, auditing)
- vehicle_id: UUID not null (foreign key → vehicles.vehicle_id)
- recorded_at: TIMESTAMPTZ not null (the time the telematic recorded the data)
- received_at: TIMESTAMPTZ not null (the time the backend received the data)
- latitude: DOUBLE PRECISION
- longitude: DOUBLE PRECISION
- speed: DOUBLE PRECISION (km/h)
- heading: DOUBLE PRECISION, nullable (degrees, 0-360)
- soc: DOUBLE PRECISION (State of Charge, %)
- battery_voltage: DOUBLE PRECISION, nullable (V)
- battery_current: DOUBLE PRECISION, nullable (A)
- battery_temperature: DOUBLE PRECISION, nullable (°C)
- motor_temperature: DOUBLE PRECISION, nullable (°C)
- odometer: DOUBLE PRECISION, nullable (km)
- signal_strength: INTEGER, nullable (dBm)
- error_codes: JSONB, nullable
- raw_payload: JSONB not null (stores the raw data from the telematic)

Notes:
- Use a TimescaleDB hypertable (partition by recorded_at)
- Chunk interval: 1 day
- Primary key: (message_id, recorded_at) - must include the partition key
- Unique constraint: (telematic_id, recorded_at) - a telematic can only have 1 message at 1 point in time
- Index on (vehicle_id, recorded_at DESC)
- Index on message_uuid (for tracing, not unique in the MVP)
- Do not use UUID as PK (TimescaleDB recommends BIGINT)
- Add a docstring explaining each field
- heading is nullable because not every telematic provides it
- raw_payload stores the entire raw JSON for debugging and reprocessing
```

**Checks:**
- [x] The model has no syntax errors
- [x] Has a complete docstring
- [x] The primary key includes recorded_at
- [x] The unique constraint matches the business rule
- [x] Has raw_payload JSONB
- [x] Has foreign keys/indexes to support tracing and querying by vehicle/time
- [x] Timestamps are stored as UTC timezone-aware

**Outcome/Decisions:**

- The hypertable is partitioned by `recorded_at`, with a one-day chunk interval.
- The composite primary key is `(message_id, recorded_at)` to include the
  partition key.
- `message_uuid` only has an index, not unique in the MVP; advanced duplicate
  detection has been recorded in `future.md`.
- `latitude`/`longitude` used `DOUBLE PRECISION` at this step; a PostGIS
  upgrade was deferred and recorded in `future.md` item 9. **Update
  (2026-09-17): implemented** — the table now stores a single `location`
  `geography(Point, 4326)` column instead (migration
  `0006_telemetry_location_geo`); see `future.md` item 9's resolution note
  for the current schema. The MQTT wire contract above (`location.latitude`/
  `location.longitude`) is unaffected.
- `raw_payload` keeps the parsed JSON object before Pydantic normalizes/drops
  fields, to support later audit and reprocessing.
- `speed` and `heading` are nullable to accept devices that do not send motion
  state.

---

### Step 3: Create Alembic migrations

**Goal:** Create migrations for `telematics`, `vehicle_telemetry`, and the
TimescaleDB hypertable

**Prompt:**
```
Create Alembic migrations in `backend/app/libs/db/migrations/versions/`:

Migration 1: Create telematics table
- Create the telematics table with full constraints, indexes
- Add a foreign key to vehicles
- Add a UNIQUE constraint for vehicle_id

Migration 2: Create vehicle_telemetry hypertable
- Create the vehicle_telemetry table
- Convert it to a hypertable: SELECT create_hypertable('vehicle_telemetry', 'recorded_at', chunk_time_interval => INTERVAL '1 day');
- Create indexes
- Add a unique constraint (telematic_id, recorded_at)
- Create an index for message_uuid

Notes:
- Import models in env.py
- Use --autogenerate but carefully review the migration file
- Test against a running database
```

**Commands to run:**
```bash
# Create migration
cd backend
uv run alembic revision --autogenerate -m "create telematics table"
uv run alembic revision --autogenerate -m "create vehicle_telemetry hypertable"

# Run migration
uv run alembic upgrade head

# Verify
docker exec g3network-db psql -U g3network -d g3network -c "\d telematics"
docker exec g3network-db psql -U g3network -d g3network -c "\d vehicle_telemetry"
docker exec g3network-db psql -U g3network -d g3network -c "SELECT hypertable_name FROM timescaledb_information.hypertables;"
```

**Checks:**
- [x] Migration runs successfully
- [x] The telematics table is created
- [x] The vehicle_telemetry table is a hypertable
- [x] Indexes are created correctly
- [x] The unique constraint (telematic_id, recorded_at) exists
- [x] Upgrade/downgrade and the vehicles table timezone have been reviewed

**Outcome/Decisions:**

- The actual migrations were not split into exactly two files as in the
  original prompt:
  - `90df58f189f6_create_vehicles_and_telematics_tables.py` creates `vehicles`
    and `telematics`.
  - `70cefd03d3d5_create_vehicle_telemetry_hypertable.py` creates the
    time-series table and converts it to a hypertable.
  - `c0f4a8b6e2d1_use_timezone_aware_vehicle_timestamps.py` normalizes the
    vehicles timestamps to `TIMESTAMPTZ`.
- Alembic `env.py` directly imports the needed models so metadata is complete;
  the `__init__.py` files do not export code.
- The migration was previously run and the hypertable verified on a real
  database. When re-auditing the planner on 2026-07-27, it was not re-run
  because the work session had no Docker daemon access; this does not change
  the prior acceptance results.
- `alembic check` against objects managed by PostGIS/TimescaleDB is still
  tracked in `future.md`.

---

### Step 4: Finalize the MQTT topic and payload schema

**Goal:** Define the communication protocol between the telematic and the backend

**Prompt:**
```
Create the file docs/03-specifications/mqtt-spec.md with the following content:

1. MQTT Topics:
   - Published by the telematic: `g3network/telematics/{telematic_serial}/telemetry`
   - Telematic status: `g3network/telematics/{telematic_serial}/status`
   - Backend command: `g3network/telematics/{telematic_serial}/command` (for later)

2. Payload Schema (JSON):
   {
     "message_uuid": "497f6eca-6276-4993-bfeb-53cbbbba6f08",
     "telematic_serial": "TBOX-VN-000123",
     "recorded_at": "2026-07-24T10:30:00Z",
     "location": {
       "latitude": 21.0285,
       "longitude": 105.8542
     },
     "vehicle_state": {
       "speed": 45.2,
       "heading": 90.0,
       "odometer": 12345.6
     },
     "battery": {
       "soc": 78.5,
       "voltage": 400.2,
       "current": -15.3,
       "temperature": 35.2
     },
     "motor": {
       "temperature": 42.1
     },
     "signal": {
       "strength": -75
     },
     "errors": ["E001"]
   }

3. QoS Level: 0 (fire-and-forget)

4. Retain: false

Notes:
- Clearly explain each field
- State the unit of measurement
- Include an illustrative example
- The payload does NOT contain: message_id, telematic_id, vehicle_id, received_at (added later by the backend)
- heading: direction of travel as an angle (0°=North, 90°=East, 180°=South, 270°=West)
```

**Checks:**
- [x] The topic uses telematic_serial (not vehicle_id)
- [x] The payload has message_uuid (not message_id)
- [x] The payload does not contain internal IDs (message_id, telematic_id, vehicle_id)
- [x] QoS is configured as 0
- [x] Has an illustrative example
- [x] Required/nullable fields, ranges, units, and the timestamp source are described

**Outcome/Decisions:**

- `docs/03-specifications/mqtt-spec.md` is the source communication contract for
  steps 5 and 7.
- `recorded_at` is provided by the device; the backend normalizes it to UTC.
  `received_at` is added by the backend when processing a batch, per the
  current MVP contract.
- The status/command topics and ACL in the spec only describe a future
  direction; the MVP consumer only subscribes to the telemetry topic.
- EMQX 5.x does not use a custom `acl.conf` the way the old EMQX 4.x design
  did. Authentication, authorization, and cross-checking the serial between
  the topic and payload have been deferred in `future.md`.
- Retain is `false`; with QoS 0 there is no delivery guarantee.

---

### Step 5: Write the Pydantic validation schema

**Goal:** Validate MQTT messages before putting them into the queue

**Prompt:**
```
Create backend/app/domains/telemetry/schemas.py with Pydantic models:

1. LocationData:
   - latitude: float (range -90 to 90)
   - longitude: float (range -180 to 180)

2. VehicleState:
   - speed: float | None (range 0-200)
   - heading: float | None (range 0-360, nullable)
   - odometer: float | None

3. BatteryData:
   - soc: float (range 0-100)
   - voltage: float | None
   - current: float | None
   - temperature: float | None

4. MotorData:
   - temperature: float | None

5. SignalData:
   - strength: int | None

6. TelemetryMessage:
   - message_uuid: UUID
   - telematic_serial: str
   - recorded_at: datetime
   - location: LocationData
   - vehicle_state: VehicleState | None
   - battery: BatteryData
   - motor: MotorData | None
   - signal: SignalData | None
   - errors: list[str] | None

7. TelemetryEnvelope:
   - message: the validated/normalized TelemetryMessage
   - raw_payload: the original JSON dict after parsing

Notes:
- Use Pydantic v2
- Add validation for fields with a range
- Add examples
- Add a `to_db_dict(telematic_id, vehicle_id, received_at, raw_payload)` method
  to convert to a dict suitable for the DB model
- heading is nullable because not every telematic provides it
```

**Checks:**
- [x] The schema has no syntax errors
- [x] Validation ranges are correct
- [x] heading can be null
- [x] Examples render well in the docs
- [x] `recorded_at` is required to have a timezone and is normalized to UTC
- [x] The original raw payload is preserved after validation

**Outcome/Decisions:**

- Uses Pydantic v2 and `Annotated`/`Field` for the range contract.
- `TelemetryEnvelope` combines the validated `TelemetryMessage` with the
  original JSON dict, avoiding reconstructing `raw_payload` from the
  normalized model or a model that dropped fields.
- Validation happens at the MQTT boundary before the queue receives the
  message. The service receives a valid envelope and only handles
  mapping/conversion business logic.
- `to_db_dict()` is a pure conversion, does not query the DB, and does not
  manage transactions.

---

### Step 6: Stand up EMQX and manually verify publish/subscribe

**Goal:** Install the EMQX 5.x broker and test the connection with QoS 0

**Important note about EMQX 5.x:**
- EMQX 5.x **does not use the `acl.conf` file** like EMQX 4.x
- ACL is configured via the Dashboard UI or REST API
- The default `acl.conf` file still exists but is not used for custom rules
- For the MVP, we will skip complex ACL and use the default security

**Prompt:**
```
Update infra/docker-compose.yml:

1. Add a broker service (EMQX 5.5):
   - Image: emqx/emqx:5.5
   - Ports: 1883 (MQTT), 18083 (Dashboard)
   - Environment: EMQX_NAME=g3network-broker, EMQX_HOST=0.0.0.0
   - Volumes: broker_data, broker_log (do not mount an ACL file)

2. Update `.env.example` at the root:
   - MQTT_HOST=localhost
   - MQTT_PORT=1883
   - MQTT_DASHBOARD_PORT=18083

3. Update backend/app/libs/common/config.py:
   - Add MQTT settings to the Settings class:
     * MQTT_HOST, MQTT_PORT, MQTT_CLIENT_ID
     * MQTT_USERNAME, MQTT_PASSWORD (nullable)
     * MQTT_QOS = 0 (default)
```

**Commands to run:**
```bash
# Start EMQX
docker compose -f infra/docker-compose.yml up -d broker

# Check the container
docker ps --filter "name=g3network-broker"

# Check the dashboard
open http://localhost:18083
# Default: admin / public

# Test subscribe (install mosquitto-clients if not already installed)
sudo apt install mosquitto-clients
mosquitto_sub -h localhost -p 1883 -t "g3network/telematics/+/telemetry" -v

# Test publish with QoS 0
mosquitto_pub -h localhost -p 1883 -q 0 -t "g3network/telematics/TBOX-VN-000123/telemetry" -m '{"message_uuid":"497f6eca-6276-4993-bfeb-53cbbbba6f08","telematic_serial":"TBOX-VN-000123","recorded_at":"2026-07-24T10:00:00Z","location":{"latitude":10.76,"longitude":106.66},"battery":{"soc":50.0}}'
```

**Checks:**
- [x] The EMQX service, port, volume, and healthcheck are present in Compose
- [x] The dashboard is exposed at http://localhost:18083
- [x] Manual subscribe/publish with QoS 0 has been confirmed
- [x] MQTT settings have been added to config.py and `.env.example`

**Outcome/Decisions:**

- The development Compose uses `emqx/emqx:5.5`, exposes MQTT `1883` and
  dashboard `18083`, and has persistent data/log volumes and a healthcheck.
- No `acl.conf` is mounted; EMQX 5.x authorization will be configured via the
  Dashboard/REST API when security is implemented in a later phase.
- The backend runs on the host, so it uses `localhost:1883`; the Docker
  service name is not used in `.env.example`.
- Manual runtime verification was completed at the time step 6 was
  implemented. The 2026-07-27 documentation audit did not re-run the
  container because that session had no Docker daemon access.
- The `sudo apt install` command in the prompt is only environment guidance,
  not a repo change or a condition for the source to compile.

---

### Step 7: Write the MQTT client + consumer

**Goal:** Connect to EMQX and receive messages with QoS 0

**Prompt:**
```
Create backend/app/domains/telemetry/ingestion/mqtt_consumer.py:

1. MQTTConsumer class:
   - __init__(config: MQTTConfig, message_queue: asyncio.Queue)
   - async connect() - connect to the broker with QoS 0
   - async subscribe(topic_pattern: str) - subscribe to a topic
   - async start_consuming() - message receiving loop
   - async disconnect() - disconnect

2. Message handling:
   - Parse the JSON payload
   - Validate with the TelemetryMessage schema
   - If valid: put it into the asyncio.Queue
   - If invalid: log a warning, skip it (the MVP has no DLQ)

3. Error handling (MVP):
   - Log connection errors and let the consumer stop
   - No reconnect/retry; record advanced reliability in `future.md`

4. Use a library:
   - gmqtt (async MQTT client), or
   - aiomqtt (a wrapper around paho-mqtt)

Notes:
- All I/O must be async
- Do not block inside the message handler
- Subscribe with QoS 0
- Add docstrings and type hints
```

**Checks:**
- [x] The consumer can connect to EMQX
- [x] Messages are parsed and validated correctly
- [x] Valid messages are put into the queue
- [x] Subscribes with QoS 0
- [x] JSON/Pydantic/MQTT errors have clear failure behavior

**Outcome/Decisions:**

- Chose `aiomqtt`; do not install multiple MQTT client libraries at the same time.
- `connect()` currently prepares the client/config, while the actual network
  connection and subscription happen when entering the async context inside
  `start_consuming()`.
- A valid payload is packaged into a `TelemetryEnvelope`; an invalid JSON
  payload or schema is logged as `WARNING` and skipped.
- `MqttError` at the process/task boundary is logged with a traceback and then
  raised; the MVP does not reconnect/retry.
- The client, topic, credentials, and QoS come from settings/the constructor.
- Decision updated 2026-07-28: removed `Metrics`, `is_consuming`, the
  `subscribe()` public method, and the `_consuming` state. The consumer now
  only has `connect()`, `start_consuming()`, `disconnect()`, and
  `_handle_message()`.

---

### Step 8: Put valid messages into the asyncio.Queue

**Goal:** Create an intermediate queue between the consumer and the batch worker

**Prompt:**
```
Update backend/app/domains/telemetry/ingestion/mqtt_consumer.py:

1. Create a module-level queue:
   message_queue: asyncio.Queue[TelemetryMessage] = asyncio.Queue(maxsize=10000)

2. In the message handler:
   - Try: message_queue.put_nowait(validated_message)
   - Except QueueFull: log a warning, increment a metric, drop the message

3. Add metrics (using prometheus-client or a simple counter):
   - messages_received_total
   - messages_valid_total
   - messages_invalid_total
   - messages_dropped_total (queue full)

4. Support graceful shutdown:
   - `disconnect()` stops consuming
   - The entrypoint is responsible for receiving SIGTERM, stopping the
     consumer, then waiting for the worker to drain the queue
```

**Checks:**
- [x] The queue works correctly
- [x] A full queue drops messages intentionally and logs a warning
- [x] The consumer has a stop primitive; whole-process orchestration belongs to step 14

**Outcome/Decisions:**

- The queue holds `TelemetryEnvelope`, not just `TelemetryMessage`, to keep
  `raw_payload`.
- Decision updated 2026-07-28: removed all metrics/counters in
  `mqtt_consumer.py`. No Prometheus dependency is used, and no process-local
  counter is kept in the MVP either.
- A full queue does not block the MQTT callback: the message is dropped and a
  `WARNING` is logged.
- The module-level queue is currently the default so older components can
  share it. Step 14 will have the entrypoint create a queue based on settings
  and inject the same instance into the consumer and the worker, making
  ownership/lifecycle clearer.
- The consumer only provides the `disconnect` primitive; the entrypoint
  cancels the remaining task when one task finishes first. The queue is not
  drained in the current MVP.

---

### Step 9: Write the batch worker

**Goal:** Process the queue in periodic batches

**Prompt:**
```
Create backend/app/domains/telemetry/ingestion/batch_worker.py:

1. BatchWorker class:
   - __init__(queue: asyncio.Queue, batch_size: int = 100, flush_interval: float = 30.0)
   - async start() - start the worker
   - async stop() - stop the worker

2. Logic:
   - Every flush_interval seconds OR when the queue has enough batch_size messages:
     + Take up to batch_size messages from the queue
     + Call telemetry.service.process_batch(messages)
     + Log the number processed, time taken
   - If a DB error occurs: roll back the entire batch, log the traceback, and let the worker stop
   - The MVP has no retry and no dead-letter queue

3. Metrics:
   - batches_processed_total
   - messages_processed_total
   - batch_processing_time_seconds
   - batch_errors_total

4. Graceful shutdown:
   - On stop(), finish processing the current batch
   - Wait at most 60 seconds

Notes:
- Use asyncio.wait_for for timeouts
- Add docstrings and type hints
```

**Checks:**
- [x] The worker runs periodically at the correct interval
- [x] A batch is processed once it reaches the target size
- [x] A DB error stops the worker, no retry
- [x] The worker does not use `queue.join()`/`task_done()` in the minimal MVP
- [x] Each batch runs in one atomic transaction

**Outcome/Decisions:**

- The batch window starts when the first message is received and uses the
  monotonic event-loop clock; it flushes when full or when the interval elapses.
- The worker uses `async_session_factory.begin()`: the context commits on
  success and rolls back on exception; the service/repository does not
  commit/rollback.
- Decision updated 2026-07-28: removed `BatchMetrics`, `is_running`, `wait()`,
  queue draining, `queue.join()`, and `task_done()`.
- On shutdown, the worker is cancelled immediately; the in-flight transaction
  rolls back via the context manager, and messages still in the RAM queue are
  allowed to be lost.
- DB/service errors are logged with `logger.exception()`, re-raised, and stop
  the worker per MVP policy.

---

### Step 10: Write the batch lookup and bulk insert repository

**Goal:** Efficiently insert telemetry batches into the database with batch lookup

**Prompt:**
```
Update backend/app/domains/telemetry/repository.py:

1. Public `telematics.service.resolve_mappings_by_serial(db, serials)`:
   - The `telematics` domain queries the telematics table once with WHERE serial IN (...)
   - Returns a dict: {telematic_serial: (telematic_id, vehicle_id)}
   - Uses SQLAlchemy Core select

2. async def bulk_insert_telemetry(db: AsyncSession, messages: list[dict]) -> int:
   - Use SQLAlchemy Core insert (not the ORM):
     stmt = insert(VehicleTelemetry).values(messages)
     result = await db.execute(stmt)
   - Return the number of rows inserted

3. async def update_telematic_last_seen(db: AsyncSession, telematic_data: list[tuple[UUID, datetime]]) -> None:
   - Update last_seen_at for multiple telematics at once
   - Only update if the new timestamp is greater than the current value
   - Use a batch update with CASE WHEN or execute multiple updates

Notes:
- Use an async session
- The worker entry boundary owns the transaction and commits/rolls back the whole batch
- The repository/service must not call commit or rollback
- Bulk insert must use the Core API, not ORM add_all (slow)
- Batch lookup: 1 query for the whole batch, not one query per message
- Add a docstring
```

**Checks:**
- [x] The batch lookup works correctly
- [x] The bulk insert works
- [x] The query structure uses one lookup, one bulk insert, and one batch update
- [x] The transaction is committed correctly

**Outcome/Decisions:**

- `telematics.service.resolve_mappings_by_serial()` performs one
  `SELECT ... WHERE serial IN (...)` and only returns devices that already
  have a `vehicle_id` assigned.
- `bulk_insert_telemetry()` uses PostgreSQL Core `insert(...).values(messages)`,
  not ORM `add_all`.
- `update_telematic_last_seen()` uses a single `UPDATE ... CASE` for the
  devices in the batch.
- The repository does not commit/rollback; the transaction is actually
  committed by the worker context after the service finishes.
- Smoke tests have verified call counts and data passed between the
  service/repository. There is no reliable benchmark yet to claim throughput
  for 100+ records or messages/second; performance must be measured with a
  workload on a representative environment.

---

### Step 11: Write the process_batch service

**Goal:** Business logic to process a batch of messages with batch lookup

**Prompt:**
```
Update backend/app/domains/telemetry/service.py:

1. async def process_batch(db: AsyncSession, messages: Sequence[TelemetryEnvelope]) -> BatchResult:
   - Get the list of unique telematic_serial values from the batch
   - Call `telematics.service.resolve_mappings_by_serial(serials)` - 1 query for the whole batch
   - For each message:
     + If telematic_serial does not exist: log a warning, skip the message
     + If it exists: add telematic_id and vehicle_id
   - Create received_at = datetime.now(timezone.utc)
   - Convert the message and raw payload to a DB dict
   - Call repository.bulk_insert_telemetry()
   - Call repository.update_telematic_last_seen() with MAX(received_at) for each telematic
   - Return {"processed": count, "skipped": count, "errors": count}

2. Error handling:
   - If a DB error occurs: raise so the transaction rolls back and the batch worker stops
   - If a validation error occurs: log and skip that message

3. Logging:
   - Log INFO when a batch is processed successfully
   - Log WARNING when a message is skipped
   - Log ERROR on a DB error

Notes:
- Call the repository, do NOT query directly
- Batch lookup: 1 query for the whole batch, not one query per message
- No cache in the MVP
- Add a docstring
```

**Checks:**
- [x] The batch is processed correctly
- [x] The batch lookup works (1 query for the whole batch)
- [x] Telematic validation works
- [x] Logging is complete

**Review outcome (2026-07-27):**
- `process_batch()` correctly processes valid messages, skips telematics that
  do not exist or have no vehicle assigned, and returns the full set of
  `processed`, `skipped`, `errors` metrics.
- The list of `telematic_serial` values is deduplicated, and
  `telematics.service.resolve_mappings_by_serial()` is called exactly once
  for the whole batch.
- Pydantic validation is performed at the MQTT consumer before the message is
  put into the queue; the service continues by checking the
  telematic/vehicle mapping and handling per-message conversion errors.
- The service logs `INFO` for the batch result and `WARNING` for skipped
  messages. Database errors are propagated to the transaction boundary and
  logged by the batch worker with `logger.exception()` before the worker stops.
- An isolated smoke test confirmed that a batch with one valid message and one
  non-existent serial produces `processed=1`, `skipped=1`, `errors=0`, with
  exactly one lookup, bulk insert, and `last_seen_at` update.
- Black, isort, Ruff, and mypy all pass on the related files. The integration
  test with PostgreSQL/TimescaleDB was not re-run in this review pass due to
  lack of Docker daemon access.
- `received_at` is currently generated once at the time the service processes
  the batch, so every message in the batch uses the same timestamp. This is
  the current MVP contract, not the exact timestamp of when the MQTT callback
  received each message.
- The mapping repository filters out devices that have not been assigned a
  vehicle, so the service cannot distinguish between "serial does not exist"
  and "telematic exists but is not assigned"; this limitation has been
  recorded in `future.md`.

---

### Step 12: Update the telematic's last_seen

**Goal:** Update the telematic's last-active time

**Prompt:**
```
Update backend/app/domains/telemetry/service.py:

1. In process_batch(), after the bulk insert:
   - Group messages by telematic_id
   - Get MAX(received_at) for each telematic
   - Call repository.update_telematic_last_seen([(telematic_id, max_received_at), ...])

2. Repository update:
   - Only update if the new timestamp is greater than the current value
   - Use a batch update for efficiency

Notes:
- Do not update each message individually
- Only update with the MAX(received_at) of the batch
- Add a docstring
```

**Checks:**
- [x] last_seen_at is updated correctly
- [x] Only updates when the new timestamp is more recent
- [x] A batch produces exactly one repository update for the relevant telematics

**Review outcome (2026-07-27):**
- `process_batch()` groups timestamps by `telematic_id` and passes only the
  single maximum value for each device to
  `repository.update_telematic_last_seen()` after the bulk insert succeeds.
- The repository uses a single `UPDATE` statement with `CASE WHEN` for all
  devices in the batch; `last_seen_at` only receives the new timestamp when
  the new value is greater than the current value or the current value is
  `NULL`.
- An isolated smoke test with three messages from the same telematic confirmed
  the repository update is called only once and receives only one tuple for
  that telematic.
- An MVP limitation has been recorded in item 14 of `future.md`: the `UPDATE`
  statement still changes `updated_at`, and the row count may include rows
  matched by ID even when `last_seen_at` did not change. This discrepancy does
  not cause `last_seen_at` to move backward and does not block step 12.
- The integration/performance test with PostgreSQL/TimescaleDB was not re-run
  in this review pass due to lack of Docker daemon access.

---

### Step 13: Add basic logging (MVP has no retry/DLQ)

**Goal:** Complete logging for debugging (MVP has no retry/DLQ)

**Prompt:**
```
Update backend/app/domains/telemetry/ingestion/batch_worker.py:

1. Logging:
   - Use the Python logging module
   - Log format: JSON with timestamp, level, message, extra fields
   - Log level: INFO for normal, WARNING for skip, ERROR for failure

2. Error handling (MVP):
   - If a DB error occurs: log ERROR, raise the exception (the batch worker will stop)
   - No complex retry logic in the MVP
   - No dead-letter queue in the MVP
   - Reliability layers will be added in a later phase

3. Metrics (optional):
   - messages_received_total
   - messages_processed_total
   - messages_skipped_total
   - batch_processing_time_seconds

Notes:
- The MVP focuses on proving the flow works
- Retry, DLQ, persistent queue will be recorded in future.md
- Add a docstring
```

**Checks:**
- [x] Logging is complete
- [x] Errors are logged at the correct level
- [x] No metrics/counters remain in the minimal ingestion MVP

**Implementation outcome (2026-07-27):**
- Added a JSON formatter using the Python standard library with the standard
  fields `timestamp`, `level`, `logger`, `message`, structured `extra` fields,
  and a traceback when there is an exception.
- The batch worker enables the logging configuration idempotently at startup.
  The normal flow uses `INFO`, skipped/dropped or invalid messages use
  `WARNING`, and database/process boundary errors use `logger.exception()` at
  `ERROR` level and then raise to stop the worker.
- Decision updated 2026-07-28: removed metrics/counters in the MQTT consumer
  and batch worker. The ingestion MVP now only logs important events and a
  batch summary consisting of `batch_size`, `processed`, `skipped`, `errors`.
- No retry, DLQ, or persistent queue was added within the MVP scope; these
  reliability items continue to be tracked in `future.md`.
- At the time logging was implemented on 2026-07-27, Black, isort, Ruff, and
  mypy all passed. After the minimal changes on 2026-07-28, full static
  checks need to be re-run once the dependency environment has `ruff`.
- `configure_logging()` only configures how existing `LogRecord`s are
  emitted; it does not generate new business events on its own. The handler
  writes single-line JSON to `stderr`.
- `configure_logging()` is now called at the entrypoint instead of inside the
  worker so all startup/MQTT/worker/shutdown logs use the same format.
- Centralized observability and persistent metrics have been moved to
  `future.md`.

---

### Step 14: Split telemetry ingestion into a separate process

> **MVP decision updated 2026-07-28:** the ingestion process was heavily
> simplified: removed `runtime.py`, `health.py`, the HTTP health server, the
> database startup probe, graceful drain, `BatchMetrics`, MQTT metrics,
> `BatchWorker.is_running`, `BatchWorker.wait()`, `MQTTConsumer.is_consuming`,
> and `MQTTConsumer.subscribe()`. `entrypoint.py` directly initializes the RAM
> queue, the consumer, and the worker. When a task finishes or SIGINT/SIGTERM
> is received, the entrypoint cancels the remaining task and cleans up.
> Messages still in the RAM queue are allowed to be lost within the MVP scope.

**Goal:** Run telemetry ingestion independently of the API server using a
minimal process entrypoint. In the development environment, the process runs
directly on the host per the convention in `CLAUDE.md`; packaging a production
container is not within the scope of this step.

#### 14.1. Scope of changes

Create:

- `backend/app/domains/telemetry/ingestion/entrypoint.py`

Update:

- `backend/app/domains/telemetry/ingestion/batch_worker.py`
- `backend/app/domains/telemetry/ingestion/mqtt_consumer.py`
- `backend/app/libs/common/config.py`
- `.env.example`
- `Makefile`
- `README.md` if the run instructions need to be kept in sync

Not changed in this step:

- `infra/docker-compose.yml`: the development Compose continues to run only
  `db` and `broker`.
- `infra/docker-compose.prod.yml` and the production Dockerfile.
- Retry/reconnect, DLQ, persistent queue, metrics exporter, or health endpoint.
- Business logic in the telemetry service/repository.

#### 14.2. Entrypoint and lifecycle ownership

The entrypoint is the starting point and the only component that orchestrates
the lifecycle of the telemetry ingestion process:

```text
main()
  └─ asyncio.run(run())
       ├─ configure JSON logging
       ├─ register SIGINT/SIGTERM
       ├─ create a shared asyncio.Queue
       ├─ create MQTTConsumer and BatchWorker using the same queue
       ├─ start the MQTT consumer and the batch worker
       └─ wait for a signal or for the first task to finish
```

Requirements:

- Has a synchronous `main()` that calls `asyncio.run(run())`.
- No network I/O at import time.
- The entrypoint cancels the remaining tasks when one task finishes first.
- The minimal MVP no longer distinguishes a shutdown signal from the
  consumer/worker stopping on its own; the process goes through a shared cleanup.
- On startup/runtime error, cleanup must still close the database pool and
  any components already started.

#### 14.3. Logging configuration at the process boundary

- Move the `configure_logging()` call from `BatchWorker.start()` to the top of
  the entrypoint, before the MQTT connection.
- The entrypoint owns process-level log configuration; `BatchWorker` only owns
  batching and transactions.
- All startup, MQTT, worker, and shutdown logs must follow the same JSON
  output contract from step 13.
- `configure_logging()` remains idempotent but no longer relies on the worker
  to trigger it.

#### 14.4. Database lifecycle

- No database startup probe remains in the MVP entrypoint.
- The batch worker uses `async_session_factory.begin()` so each batch sits in
  one atomic transaction.
- `get_db()` is not used because it is an async-generator dependency meant for
  FastAPI's HTTP request lifecycle.
- `close_db()` still runs during cleanup to dispose of the telemetry process's
  shared engine/pool if the worker had opened a connection.

Each OS process still has its own engine, pool, and factory in memory. "Shared
factory" here means every component **within the same telemetry process**
uses the standard factory from `app.libs.db.session`, without creating a
second pool of its own.

#### 14.5. Runtime configuration

Add namespaced settings:

```env
TELEMETRY_QUEUE_SIZE=10000
TELEMETRY_BATCH_SIZE=100
TELEMETRY_FLUSH_INTERVAL=30
```

Meaning:

- `TELEMETRY_QUEUE_SIZE`: the maximum number of envelopes in the in-memory queue.
- `TELEMETRY_BATCH_SIZE`: the maximum number of messages in one database transaction.
- `TELEMETRY_FLUSH_INTERVAL`: the maximum number of seconds to wait for an
  incomplete batch.
- `TELEMETRY_HEALTH_HOST`, `TELEMETRY_HEALTH_PORT`, and
  `TELEMETRY_SHUTDOWN_TIMEOUT` no longer exist in the current MVP.

The entrypoint creates exactly one queue and passes the same instance to the
consumer and the worker:

```text
MQTTConsumer ──put──▶ shared queue ──get──▶ BatchWorker
```

#### 14.6. Minimal API of BatchWorker and MQTTConsumer

`BatchWorker` now only provides:

```python
async def start() -> None: ...
async def stop() -> None: ...
```

`MQTTConsumer` now only provides:

```python
async def connect() -> None: ...
async def start_consuming() -> None: ...
async def disconnect() -> None: ...
```

Current decisions:

- `BatchWorker.is_running`, `BatchWorker.wait()`, and
  `MQTTConsumer.is_consuming` no longer exist.
- The `MQTTConsumer.subscribe()` public method no longer exists; subscribing
  to the telemetry topic happens directly inside `start_consuming()`.
- The entrypoint now accesses `worker._task` after `start()` to add the
  worker task to the wait list. This is an accepted trade-off for the minimal
  MVP; if a cleaner lifecycle API is needed later, move it into the
  observability/runtime phase.

#### 14.7. Minimal task tracking

The entrypoint waits concurrently on:

- SIGINT/SIGTERM.
- The MQTT consumer task.
- The `BatchWorker`'s background task.

Uses `asyncio.wait(..., return_when=FIRST_COMPLETED)` or an equivalent mechanism.

Behavior:

- Whichever task completes first drives the process into cleanup.
- The remaining tasks are cancelled; the queue is not drained.
- An exception from an already-completed task is no longer analyzed
  separately in the minimal MVP entrypoint. If a task fails before
  `asyncio.wait()` returns and the exception is not retrieved, this is an
  accepted limitation to keep the code short; the primary error logging still
  lives at the consumer/worker boundary.

#### 14.8. Health check

There is no health endpoint in the current ingestion MVP. HTTP health/readiness
for the separate process has been moved to `docs/01-requirements/future.md`.

#### 14.9. Minimal shutdown

Current order:

```text
1. A task finishes, or SIGINT/SIGTERM is received
2. The entrypoint cancels the remaining pending tasks
3. Call consumer.disconnect()
4. Call worker.stop() to cancel the worker task
5. Call close_db()
6. Remove signal handlers and the process exits
```

Invariants:

- No queue draining, no `queue.join()`, and no `task_done()`.
- An in-flight transaction rolls back when a task is cancelled or an exception occurs.
- Messages still in the RAM queue are allowed to be lost.
- The MVP does not retry/reconnect/DLQ.

#### 14.10. Development command

Add a Makefile target and update `make help`:

```bash
make telemetry-dev
```

The target runs:

```bash
cd backend && uv run python -m app.domains.telemetry.ingestion.entrypoint
```

Equivalent direct command:

```bash
cd backend
uv run python -m app.domains.telemetry.ingestion.entrypoint
```

#### 14.11. Checks and acceptance

Static checks:

- Black, isort, Ruff, and mypy across the whole backend.
- `git diff --check`.
- Check that every new docstring/comment is in Vietnamese per `CLAUDE.md`.

Lifecycle smoke test:

- The shared queue is passed to both the consumer and the worker.
- `entrypoint.py` creates a consumer task, a worker task, and a shutdown
  signal task.
- `asyncio.wait(..., FIRST_COMPLETED)` drives the process into cleanup when
  the first task finishes.
- SIGTERM/SIGINT triggers minimal cleanup.
- `close_db()` is always called.
- JSON logging is configured before the first startup log.

Integration when infrastructure is available:

```bash
make infra-up
make telemetry-dev
```

The full MQTT publish → queue → batch → TimescaleDB test still belongs to step 15.

**Checks:**

- [x] The entrypoint runs independently on the host
- [x] Logging is configured at the process boundary
- [x] No database startup probe remains in the minimal MVP
- [x] The consumer and worker share the same queue
- [x] Redundant public lifecycle API has been removed
- [x] The health check has been removed from source and moved to future
- [x] Minimal SIGINT/SIGTERM cleanup
- [x] The `compileall` smoke check passes for the ingestion files
- [x] The Makefile/README run instructions are kept in sync

**Implementation outcome (2026-07-28):**

- `entrypoint.py` is the minimal process boundary for logging, signals, the
  queue, the MQTT consumer, the batch worker, and database cleanup.
- `health.py` and `runtime.py` have been removed from source.
- The entrypoint creates one queue based on `TELEMETRY_QUEUE_SIZE` and injects
  the same instance into the consumer/worker. Only the queue, batch size, and
  flush interval settings remain.
- `BatchWorker` removed `BatchMetrics`, `is_running`, `wait()`, queue
  draining, `queue.join()`, and `task_done()`.
- `MQTTConsumer` removed `Metrics`, `is_consuming`, the `subscribe()` public
  method, and `_consuming`; the consumer now only consumes, validates, and
  enqueues.
- The current smoke check has run `compileall` for the ingestion files. Ruff
  could not run in the current environment because `uv` could not find the
  `ruff` binary/module; full static checks need to be re-run once the
  dependency environment is ready.

---

### Step 15: End-to-end test

**Goal:** Prove the actual MVP flow from MQTT publish to TimescaleDB, covering
the success path, the skip/drop path, transaction failure, and process lifecycle.

> Note: The batch cases in this step are evidence of the implementation prior
> to Step 16. The end-to-end smoke test for the active single-message path is
> recorded in Step 16.

#### 15.1. Scope and principles

- This is an integration/E2E test run against real PostgreSQL/TimescaleDB and EMQX.
- Do not add retry/DLQ/persistent queue just to make the test pass.
- Each case must use its own `message_uuid` and `recorded_at` to avoid the
  unique constraint skewing the result.
- Record the command, the time, the input, relevant logs, the verification
  query, and the pass/fail result. Do not just check a box based on a general
  observation.
- QoS 0 and the RAM queue do not allow claiming "no message loss" under every
  failure. The MVP shutdown does not drain the queue; the E2E test only needs
  to prove the process can clean up, and data still in RAM may be disregarded.

#### 15.2. Preconditions

- `db` and `broker` are healthy.
- Alembic is at `head`; `vehicle_telemetry` appears in
  `timescaledb_information.hypertables`.
- There is a vehicle that has not been soft-deleted.
- There is an active telematic assigned to that vehicle.
- There is a non-existent serial to test the skip path.
- The step-14 telemetry entrypoint is running on the host.
- The batch size/flush interval currently in use in `.env` is known.

If there is no official seed script yet, fixtures may be inserted with manual
SQL, but the ID/serial must be recorded and cleaned up after the test; do not
put real credentials into the document.

#### 15.3. Baseline check before testing

```bash
# Infrastructure and migration
docker compose -f infra/docker-compose.yml ps
cd backend && uv run alembic current

# Hypertable
docker exec g3network-db psql -U g3network -d g3network \
  -c "SELECT hypertable_name FROM timescaledb_information.hypertables WHERE hypertable_name = 'vehicle_telemetry';"
```

#### 15.4. Test matrix

##### Case A — Minimal valid message

Publish a valid payload with a telematic already assigned to a vehicle:

```bash
mosquitto_pub -h localhost -p 1883 -q 0 \
  -t "g3network/telematics/TBOX-VN-000123/telemetry" \
  -m '{"message_uuid":"<uuid-case-a>","telematic_serial":"TBOX-VN-000123","recorded_at":"<utc-case-a>","location":{"latitude":10.76,"longitude":106.66},"battery":{"soc":50.0}}'
```

Passes when:

- The consumer increments received/valid and does not log a validation warning.
- After at most one flush interval, there is exactly one row matching `message_uuid`.
- The internal `telematic_id`/`vehicle_id` matches the fixture.
- `recorded_at` and `received_at` are timezone-aware; `received_at` is not
  earlier than a reasonable test time.
- `raw_payload` equals the JSON object sent, and does not contain internal
  backend IDs.
- The telematic's `last_seen_at` has increased.

Verification query:

```bash
docker exec g3network-db psql -U g3network -d g3network \
  -c "SELECT message_id, message_uuid, telematic_id, vehicle_id, recorded_at, received_at, raw_payload FROM vehicle_telemetry WHERE message_uuid = '<uuid-case-a>';"

docker exec g3network-db psql -U g3network -d g3network \
  -c "SELECT telematic_id, telematic_serial, vehicle_id, last_seen_at FROM telematics WHERE telematic_serial = 'TBOX-VN-000123';"
```

##### Case B — Full payload and raw payload

Publish full vehicle state, battery, motor, signal, errors, and add one field
not yet modeled by Pydantic.

Passes when:

- Fields that are modeled are flattened correctly to columns.
- The unmodeled field does not become a column but remains intact in
  `raw_payload`.
- Error codes are stored per the JSON contract.

##### Case C — Invalid JSON/schema

Run separately:

- Malformed JSON.
- Missing `battery.soc`.
- Latitude out of range.
- `recorded_at` without a timezone.

Passes when, for each message:

- A `WARNING` is logged with topic/error context.
- It does not enter the queue and no DB row is created.
- The worker/process keeps running.

##### Case D — Serial without a valid mapping

Publish a valid payload with a serial that does not exist, or a device not
yet assigned to a vehicle.

Passes when:

- The message passes MQTT/Pydantic validation but the service skips it.
- No telemetry row is created.
- The batch result increments `skipped`.
- The transaction still commits the other valid messages in the same batch.

The MVP currently does not distinguish log/metric between a non-existent
serial and a telematic without a vehicle assigned; this limitation is already
in `future.md`.

##### Case E — Flush by batch size

Publish exactly `TELEMETRY_BATCH_SIZE` valid messages with distinct
UUID/timestamp values within a time shorter than the flush interval.

Passes when:

- The batch is processed as soon as it reaches the target size, without
  waiting for the interval.
- The number of rows inserted equals the number of valid messages.
- The repository does not issue a mapping query per message.
- The summary log has the correct `batch_size`, `processed`, `skipped`, `errors`.

##### Case F — Flush by interval

Publish fewer than the batch size, then stop sending.

Passes when:

- The batch flushes after roughly `TELEMETRY_FLUSH_INTERVAL` from the first message.
- Millisecond-level precision is not required; record the observed deviation.
- No busy-looping while the queue is empty.

##### Case G — Mixed batch

Within the same batch, send a valid message, an unmapped serial, and an
invalid payload.

Passes when:

- The invalid payload is rejected before the queue.
- The unmapped message is skipped by the service.
- The valid message is still inserted.
- Counters reflect their exact definitions, not using total received as processed.

##### Case H — Database failure

After the worker is running, build up a batch and then make the database
unavailable before it flushes, or use safe failure injection in the test
environment.

Passes when:

- The whole batch rolls back, with no partial insert/last_seen update.
- An `ERROR` is logged with a traceback and batch context.
- The worker/process stops per the MVP policy.
- No retry, and the message is not sent to a DLQ.

Do not run failure injection against a database holding important data.

##### Case I — Minimal shutdown

Put several valid messages into the queue, then send SIGTERM to the telemetry process.

Passes when:

- The process receives the signal and goes into cleanup.
- The consumer is asked to disconnect.
- The worker task is cancelled; an in-flight transaction rolls back if
  cancelled mid-batch.
- The queue is not drained; messages still in RAM are allowed to be lost.
- The database pool is closed via `close_db()`.

##### Case J — Queue full

Only run with a test configuration that has a small queue size and a producer
faster than the worker.

Passes when:

- The callback does not block indefinitely.
- Messages beyond capacity are dropped intentionally.
- A `WARNING` is logged when the queue is full.
- The process keeps running; zero data loss is not claimed.

#### 15.5. Cleanup

- Delete telemetry/telematic/vehicle fixtures in the correct foreign-key
  order, or use a dedicated test transaction/namespace.
- Restore the queue/batch/interval settings after the queue-full case.
- Restart any infrastructure/process that was intentionally stopped during a
  failure test.
- Do not use `docker compose down -v` unless the test database is disposable
  and the scope of deletion has been clearly confirmed.

#### 15.6. Acceptance report

Record in the planner:

- The date, environment, and revision/commit tested.
- The PostgreSQL/TimescaleDB/EMQX versions.
- The queue/batch/interval settings.
- The list of cases pass/fail with brief evidence.
- Known limitations or cases not run, with the reason.

**Checks:**

- [x] Preconditions and baseline are valid
- [x] Minimal/full messages are inserted correctly
- [x] `raw_payload`, timezone, and internal mapping are correct
- [x] Invalid payloads do not enter the DB
- [x] Unmapped serials are skipped
- [x] Flush by size and by interval work correctly
- [ ] Mixed batch has correct log/result
- [x] Database failure rolls back and stops the process
- [x] Minimal shutdown cleans up correctly, without draining the queue
- [x] Queue full drop/log works correctly
- [x] The acceptance report has environment and evidence

**Acceptance outcome (2026-07-28):**

- Environment: PostgreSQL/TimescaleDB container `g3network-db` healthy,
  EMQX 5.5 container `g3network-broker` healthy, Alembic DB revision
  `c0f4a8b6e2d1`, hypertable `vehicle_telemetry` exists. The
  `uv run alembic current` command hung in this session, so the baseline
  revision was verified directly via the `alembic_version` table.
- Runtime test: ran `entrypoint.py` on the host with
  `TELEMETRY_BATCH_SIZE=2`, `TELEMETRY_FLUSH_INTERVAL=1`,
  `TELEMETRY_QUEUE_SIZE=10`. The process in the sandbox could not open an
  MQTT socket (`Operation not permitted`), so the E2E runtime was run outside
  the sandbox.
- Fixture: created vehicle `E2E-AD02` and telematic `TBOX-E2E-AD02`; after the
  test, cleanup succeeded, deleting 5 `vehicle_telemetry` rows, 1 `telematics`
  row, and 1 `vehicles` row; verified 0 fixture rows remain.
- Case A passed: published a minimal payload via `mosquitto_pub`; the DB has
  exactly 1 row for the `message_uuid`, correct `vehicle_id` mapping,
  `recorded_at` in UTC, `soc=50`,
  `raw_payload.telematic_serial=TBOX-E2E-AD02`; the telematic's `last_seen_at`
  increased.
- Case B passed: the full payload flattened correctly into the fields
  `speed`, `heading`, `battery_voltage`, `battery_current`,
  `battery_temperature`, `motor_temperature`, `odometer`, `signal_strength`,
  `error_codes`; the `extra_field` remained in `raw_payload`.
- Case C passed: malformed JSON and an out-of-range latitude both logged
  `WARNING` at the MQTT consumer and created no DB row.
- Case D passed: serial `TBOX-E2E-MISSING` passed Pydantic validation but was
  skipped by the service; logged `processed=0`, `skipped=1`; the DB has no row
  for the test UUID.
- Case E passed: sent 2 messages in the same `mosquitto_pub -l` session; the
  worker processed the batch with `batch_size=2`, `processed=2`; the DB has
  both rows.
- Case F passed indirectly: individual messages flushed after roughly
  `TELEMETRY_FLUSH_INTERVAL=1` and were inserted successfully per the interval.
- Case H passed: stopped the DB container temporarily then published a valid
  payload; the worker logged `ERROR` with a traceback of the DB connection
  closed, the process cleaned up and stopped; after restarting the DB, the
  case H UUID has 0 rows.
- Case I passed: started a fresh process then sent SIGINT; the logs show the
  worker stopping and `Telemetry ingestion stopped`, exit code 0. A separate
  SIGTERM was not sent since the entrypoint uses the same signal path for
  SIGINT/SIGTERM.
- Case J passed at the smoke-logic level: called `_handle_message()` directly
  with a queue of `maxsize=1`; the second message logged
  `Queue full, message dropped`, and the queue stayed at `qsize=1`. Queue-full
  was not reproduced via EMQX because the worker consumes quickly and this
  case is timing-dependent.
- The full mixed batch with valid + missing serial + invalid together has not
  been run; the individual behaviors have already been verified separately in
  Cases A/C/D/E.

---

### Step 16: Switch the active flow to per-message processing

**Goal:** Remove the batch window from the MVP run path so messages are
processed immediately in the order taken off the queue, while keeping the
entire batch implementation so it can be re-enabled when a real workload needs
throughput optimization.

#### 16.1. Contract and transaction

- The active flow is:
  `MQTTConsumer → asyncio.Queue → MessageWorker → process_message → database`.
- `MessageWorker` takes exactly one `TelemetryEnvelope` per loop iteration; it
  does not wait for a target count and does not use `TELEMETRY_FLUSH_INTERVAL`.
- Each message has its own transaction owned by the worker. On success it
  commits; a database error rolls back the current message's transaction and
  stops the worker per the MVP no-retry policy.
- Messages already committed earlier are kept when a later message fails.
  Messages still in the queue when the process stops may still be lost
  because the queue is in RAM and the MVP does not drain the queue.
- `received_at` is generated separately for each message at the service
  boundary that processes it.
- A message with no telematic/vehicle mapping is skipped; a data conversion
  error only increments `errors` for the current message. MQTT schema
  validation still happens at the consumer before enqueueing.

#### 16.2. Files changed

Created:

- `backend/app/domains/telemetry/ingestion/message_worker.py`

Updated:

- `backend/app/domains/telemetry/service.py`: added `process_message()` and
  `MessageResult`.
- `backend/app/domains/telemetry/repository.py`: keeps only telemetry
  data lookup/insert; singular/batch mapping belongs to the `telematics`
  public service.
- `backend/app/domains/telemetry/ingestion/entrypoint.py`: uses
  `MessageWorker`, no longer passes batch size/flush interval to the active worker.
- `backend/app/libs/common/config.py`, `backend/.env.example`: keep the batch
  settings for future code and clearly note they are not part of the active path.
- `docs/01-requirements/future.md`: records batch processing as a deferred component.

#### 16.3. Preserving the batch implementation

- Do not delete or convert `batch_worker.py` and `process_batch()` into a
  singular wrapper; the batch path must remain intact to avoid losing the
  existing implementation.
- Do not bring `telematics` model/repository queries back into telemetry. When
  re-enabling the batch path, it must call the public `telematics.service` and
  be evaluated for benchmarking, transaction atomicity, backpressure, and
  failure semantics.
- Review the telematic mapping cache item in `future.md` so it no longer
  describes the active MVP as using batch lookup.

#### 16.4. Checks

- [x] The active entrypoint uses `MessageWorker` and no longer has a batch window.
- [x] The singular service/repository API keeps the transaction boundary at the worker.
- [x] The batch worker/service/repository path still exists and is not called from the entrypoint.
- [x] `compileall`, Black, isort, Ruff, and mypy pass on the changed files.
- [x] Smoke test for a valid message, a skipped mapping, and a database error rollback.
- [x] Smoke test for MQTT/entrypoint, shutdown, and queue-full show no
  regression of the MVP RAM-queue policy.

**Implementation outcome (2026-07-30):**

- Created `MessageWorker` as the active worker; the entrypoint no longer
  passes or logs `TELEMETRY_BATCH_SIZE`/`TELEMETRY_FLUSH_INTERVAL`.
- `process_message()` performs a singular mapping lookup and insert within a
  transaction owned by the worker. Two consecutive smoke messages created two
  separate transactions; the batch APIs are still importable and are not
  called by the entrypoint.
- Targeted `compileall`, Black, isort, Ruff, and mypy all pass. Full-repository
  Black/isort still report pre-existing formatting/import-sorting errors in
  `app/domains/telematics/models.py`, `app/domains/telematics/repository.py`,
  and `app/domains/telemetry/models.py`; files outside this scope were not fixed.
- PostgreSQL smoke passed with the real mapping `T00001`: insert, query, raw
  payload, and cleanup all succeeded.
- MQTT E2E passed with EMQX 5.5: message UUID
  `0f8b7b0e-7b2f-4d12-9f15-4aa4d7b7a002` was persisted with `soc=62.5`, logged
  `processed=1`, and the test row was then deleted.
- The batch implementation still lives in `batch_worker.py`, `process_batch()`,
  and the repository batch APIs; recorded as item 25 in `future.md`.

---

## Summary

### Current status

- Steps 0-14: implemented per the minimal MVP scope; the planner was
  cross-checked against the ingestion source again on 2026-07-28.
- Step 15: the main E2E run was completed on 2026-07-28; the full mixed batch
  and queue-full-via-broker cases have not been reliably accepted yet.
- Step 16: switched active ingestion to per-message processing on 2026-07-30;
  the batch path is retained and recorded in `future.md`.

After completing step 16, the system has:

1. **Database**: 2 tables, `telematics` and `vehicle_telemetry` (TimescaleDB hypertable)
2. **MQTT Broker**: EMQX 5.5 running locally, QoS 0; no production ACL configured yet
3. **Backend**:
   - MQTT consumer receives messages from the broker with QoS 0
   - Message worker processes each message sequentially right after taking it off the queue
   - Single lookup and insert for the active MVP
   - Batch worker, batch lookup, and bulk insert kept for a future phase
   - Stores raw_payload JSONB for debugging and reprocessing
   - Separate process entrypoint, signal handling, and minimal cleanup
   - JSON structured logging, no more process-local metrics

**Technology used:**

- Python 3.12 + FastAPI
- SQLAlchemy 2.0 (async)
- TimescaleDB (PostgreSQL extension)
- EMQX 5.5
- Pydantic v2
- asyncio.Queue

**Performance/SLA:**

- No benchmark yet to commit to production throughput or latency.
- The batch flush interval is not part of the active path; it only matters if
  the batch worker is re-enabled in a future phase.
- The TimescaleDB hypertable is in use; the compression/retention policy has
  not been configured yet.
- Only publish performance numbers after measuring a representative workload
  with a finalized vehicle count, frequency, payload size, and database resources.

**MVP limitations:**

- MQTT QoS 0 (no delivery guarantee)
- No retry/reconnect
- No dead-letter queue
- No persistent queue
- No advanced duplicate detection
- A full queue drops messages
- No metrics/counters in the current ingestion MVP
- Logs are not yet collected centrally
- No production MQTT authentication/authorization yet
- Minimal automated smoke/unit tests exist; integration tests with the broker
  and database are deferred to the next phase
- Deferred items are tracked in `docs/01-requirements/future.md`

**Next phase:**

- Expanded telemetry query API (history, map, aggregate, and realtime dashboard)
- An aggregate/throttle layer and a realtime push mechanism for the dashboard
- Real-time alerting
- Frontend dashboard
- MQTT security and device identity
- Retry/reconnect and DLQ
- Persistent queue
- Advanced duplicate detection
- Centralized observability and persistent metrics
- Health/readiness endpoint and graceful drain for production operation
- Retention/compression/benchmark based on real volume
