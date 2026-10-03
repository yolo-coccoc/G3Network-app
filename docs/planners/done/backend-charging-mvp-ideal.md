# Planner: Charging backend MVP under ideal conditions

> Feature code: F-G2 and the basic lifecycle of F-B2
>
> Status: ✅ Done (MVP scope; closed 2026-10-04 when moved to `done/`).
> Everything left over is deferred in `docs/decisions/deferred.md`; unticked
> acceptance items below were never re-verified. Status before closing:
>
> In progress. Active source and the minimum automated smoke tests
> are complete; integration tests are still tracked in
> [`backend-automated-tests.md`](./backend-automated-tests.md)
>
> Last updated: 2026-08-02

> **Superseded in part (2026-09-24):** the assumption below that "the device is
> always online and the charging session always follows the correct flow" no
> longer holds for the OCPP 1.6J path — a real charger reboots, loses signal and
> sends vendor-specific values. See
> [`backend-ocpp16-charger-integration.md`](../backend-ocpp16-charger-integration.md),
> which adds the raw message log, charger liveness and reconnect-safe session
> lookup for 1.6J, while the 2.0.1 path described here is unchanged. The
> `charging_session_meter_values` table named in this planner was replaced by
> `charging_session_measurements` (migration `0025`).

This planner is the trimmed-down version of
[`backend-charging.md`](./backend-charging.md). The older planner is not
deleted, since it still describes the production branches. This planner is
only for the local/demo MVP, where the device is always online and the
charging session always follows the correct flow.

Each step below is an independent implementation unit. Do not move to the
next step until the current step's acceptance criteria have been checked.

## 1. Assumptions and fixed boundaries

### 1.1. Operating assumptions

- The station, EVSE, and connector have been pre-provisioned before the
  simulator runs.
- The device is always online, active, and keeps one stable WebSocket
  connection.
- A session always follows the order `Started → Updated/MeterValues →
  Ended`.
- There is no connection loss, reconnect, timeout, retry, duplicate,
  conflict, or out-of-order message.
- TransactionEvent and MeterValues are always valid; bad input may fail
  fast.
- There is no need for authorization, remote control, pricing, payment,
  debt, or driver/vehicle policy.

### 1.2. Domain boundary

```text
Station simulator ⇄ OCPP 2.0.1 gateway → charging_sessions
```

- `charging_stations` owns the station/EVSE/connector, the OCPP gateway, and
  identity resolution.
- `charging_sessions` owns the session aggregate, session events, and meter
  samples.
- The gateway only calls the public service of `charging_sessions`; it never
  imports that domain's `models.py` or `repository.py`.
- No table or source is added for authorization, remote command, pricing,
  payment, debt, or production reliability.

## 2. Active schema after trimming

The MVP keeps six active tables:

1. `charging_stations`: `station_id`, `ocpp_identity`, `display_name`,
   `location` (PostGIS geography), `power_rating_kw`, `connector_standard`,
   `operating_hours`, `maintenance_status`, and timestamps (F-C1, migration
   `0005_station_directory_fields`). `connector_count` is computed at read
   time, not stored.
2. `charging_evses`: `evse_id`, `station_id`, `ocpp_evse_id`, and timestamps.
3. `charging_connectors`: `connector_id`, `evse_id`, `ocpp_connector_id`,
   and timestamps.
4. `charging_sessions`: topology IDs, `ocpp_transaction_id`, `status`,
   start/end time, start/end meter, energy delivered, and timestamps.
5. `charging_session_events`: `event_id`, `event_occurred_at`, `session_id`,
   and `event_type` (`Started | Updated | Ended`).
6. `charging_session_meter_values`: `meter_value_id`, `sampled_at`,
   `session_id`, and `value_wh`.

`charging_station_status_events` is not in the active path since the
topology is assumed to always be online/active. The old models, enums,
helpers, and processing branches related to status history, interruption,
idempotency, ordering, retry, reconciliation, reconnect, and timeout must be
commented out in the source, not deleted; the reason for deferring them must
be recorded in `docs/decisions/deferred.md`.

### 2.1. Reading the six tables in plain terms

The first three tables describe "what the station has"; the next three
describe "how a single charge happens":

| Table | Plain-language role | Is it a hypertable? |
|---|---|---|
| `charging_stations` | The record for an entire charging station: its OCPP code, display name, and creation/edit time. | No |
| `charging_evses` | One charger/EVSE inside a station. A station can have multiple EVSEs. | No |
| `charging_connectors` | One plug belonging to an EVSE, used to identify exactly which port is charging. | No |
| `charging_sessions` | One aggregate row for a single charge, from start to finish. | No |
| `charging_session_events` | The log of the `Started`, `Updated`, `Ended` milestones of a charging session. | Yes |
| `charging_session_meter_values` | The energy readings over time for a charging session, stored in Wh. | Yes |

There are exactly three hypertables in the whole database:

1. `vehicle_telemetry`: the vehicle's real-time/historical data.
2. `charging_session_events`: the charging session's event history.
3. `charging_session_meter_values`: the charging session's energy reading
   history.

A hypertable is simply TimescaleDB's way of partitioning a table by time so
that querying large amounts of history is more efficient. So
`charging_sessions` remains a normal relational table: it holds the
session's "aggregate record," while the two history tables are the ones that
grow quickly over time.

## 3. Active business flow

### 3.1. `Started`

1. The gateway resolves `station_id`, `evse_id`, and `connector_id` from the
   OCPP identity.
2. It calls `charging_sessions.ingest_transaction_event(...)` with
   primitive values.
3. The service creates a session with `status = active`.
4. It stores the session's starting meter value if the message has one.
5. It appends a `Started` event.

### 3.2. `Updated` and `MeterValues`

1. Resolve the session by its existing `session_id`.
2. `Updated` appends an `Updated` event.
3. `MeterValues` appends each `value_wh` sample.
4. Update the ending meter value and energy delivered in the order the
   messages are received.

### 3.3. `Ended`

1. Resolve the currently active session.
2. Update `ended_at`, the ending meter value, and energy delivered.
3. Move status directly to `completed`.
4. Append an `Ended` event.

Each TransactionEvent or each MeterValues message runs inside one atomic
transaction.
`service.py` and `repository.py` never call `commit()`/`rollback()`; the
entry boundary owns the transaction. There is no retry, duplicate,
idempotency, interruption, or unknown-transaction branch in the active path.

## 4. Implementation order

### Step 0 — Review the current state and lock the MVP scope

**Prompt:**

```text
Read CLAUDE.md, feature-list.md, deferred.md, the backend-charging.md planner,
and the backend-charging-mvp-ideal.md planner. Review the current source of
charging_stations/charging_sessions.

Lock in that the MVP only supports provisioned topology, the device is
always online, and the flow is Started → Updated/MeterValues → Ended. List
the reliability, technical status history, authorization, remote control,
pricing, payment, and debt components that must be deferred. Only update
the planner/future doc if needed; do not change active logic yet.
```

**Expected outcome:**

- The active scope and the deferred items are clearly recorded in the
  planner and in `deferred.md`.
- No additional table is created for deferred items.
- Removed components no longer live in the active source; the scope for
  restoring them is clearly recorded in `deferred.md`.

**Actual result (reviewed on 2026-08-02):** Step 0 is complete. The MVP
scope was locked as follows:

1. The active path only supports pre-provisioned topology (`station`/
   `EVSE`/`connector`), the device is always online/active, and a session
   follows the order `Started → Updated/MeterValues → Ended`.
2. `charging_stations` owns the station, EVSE, connector, OCPP 2.0.1, and
   OCPP identity resolution. The gateway only forwards primitive values
   through the public service of `charging_sessions`.
3. `charging_sessions` owns the session aggregate, event lifecycle, and
   meter samples; it does not own the WebSocket/OCPP, does not call back
   into `charging_stations`, and contains no authorization business rule.
4. The following groups are deferred from the active path and have been
   recorded in `docs/decisions/deferred.md` items 26–27:
   reconnect/connection registry, offline detector and heartbeat/timeout;
   retry, duplicate/idempotency, out-of-order/conflict, and reconciliation;
   interruption/meter reset; technical status history and raw OCPP payload
   audit; authorization, RFID/`idToken`, driver/vehicle policy, remote
   start/stop, pricing, payment, webhook, overdue, and debt.

No table, source, or placeholder is added for the deferred groups. Per the
2026-08-02 decision, legacy source is not a contract that must be preserved
via comments, and the legacy charging blocks have been removed from the
active source. The scope, role, and restoration conditions are recorded in
`docs/decisions/deferred.md` items 27–28.

The `config.py` and `backend/.env.example` files only note the deferred
production settings; they create no active behavior for the MVP. This step
did not change source code, dependencies, config, migrations, or active
logic.

### Step 1 — Trim the active models, enums, and config

**Prompt:**

```text
Trim the charging models/types/config according to the schema in section 2.

Keep the six active tables and the minimum columns for topology, session,
event, and meter. Remove from the active source every class/enum/field/
helper that only serves status history, interruption, retry, idempotency,
ordering, reconciliation, reconnect, and timeout. Do not create a
placeholder; the reason for deferring it and the contract needed to restore
it must be recorded in `deferred.md`.

Comment out config no longer read in the active path. Do not add a new
placeholder or table. Update the Alembic metadata so it no longer loads the
technical status model.
```

**Main files/areas:**

- `backend/app/domains/charging_stations/models.py`
- `backend/app/domains/charging_stations/types.py`
- `backend/app/domains/charging_sessions/models.py`
- `backend/app/domains/charging_sessions/types.py`
- `backend/app/libs/common/config.py`
- `backend/app/libs/db/migrations/env.py`
- `backend/.env.example`

**Acceptance criteria:**

- The metadata has only the six active tables.
- No status history model import remains in Alembic.
- No legacy source or technical status import remains in the active path.
- Ruff, mypy, and compileall find no errors.

**Actual result (reviewed on 2026-08-02):** Step 1 has been implemented.

- The active metadata has exactly six tables: `charging_stations`,
  `charging_evses`, `charging_connectors`, `charging_sessions`,
  `charging_session_events`, and `charging_session_meter_values`.
- The three topology tables now only keep the internal ID, OCPP identity,
  topology FK, timestamps, and `deleted_at`; the location, capability,
  device metadata, administrative/technical/connection status, and status
  timestamp fields have been removed from the active model.
- `charging_sessions` now only keeps status `active|completed`; events only
  keep `Started|Updated|Ended`; meter values only keep `sampled_at`,
  `session_id`, and `value_wh`. The reliability fields are no longer part of
  the active contract; the legacy source is not an API or persistence
  contract that needs to be preserved.
- Alembic only imports the six active charging models; it no longer imports
  `ChargingStationStatusEvent` or the technical status history model.
- The active config now only has `CHARGING_OCPP_HOST` and
  `CHARGING_OCPP_PORT`; the heartbeat/offline/retry/raw-payload settings
  remain commented out. The topology API has been synced so it no longer
  references the deferred fields.
- No migration was created in Step 1; migrating the actual database schema
  belongs to Step 2. The 2026-08-02 decision allows deleting the legacy
  source that was removed from active; the details of the removed groups
  and the restoration conditions are recorded in
  `docs/decisions/deferred.md` item 28.

### Step 2 — Build the reset migration and baseline schema

**Prompt:**

```text
Since the database is still at an early bootstrap stage, delete the old
migration graph and rebuild a short one, starting with a migration that
resets the application schema. The reset only applies to a local database
that is allowed to lose data; never run it against a database that must be
preserved.

Create the migrations in this order: reset the old schema; vehicles/
telematics; telemetry hypertable; the six active charging tables. The two
charging history tables must be hypertables, while `charging_sessions` is a
relational table.

Review timezone, FK, check/unique constraints, indexes, and the drop/create
order. The baseline's downgrade only needs to drop the baseline schema; it
does not restore the legacy data that was reset.
```

**Main files:**

- `backend/app/libs/db/migrations/versions/0001_reset_application_schema.py`
- `backend/app/libs/db/migrations/versions/0002_create_vehicles_and_telematics.py`
- `backend/app/libs/db/migrations/versions/0003_create_vehicle_telemetry.py`
- `backend/app/libs/db/migrations/versions/0004_create_charging_mvp_schema.py`

**Acceptance criteria:**

- `alembic heads` shows only `0004_create_charging_mvp_schema`.
- `upgrade → downgrade → upgrade` runs successfully on a temporary database.
- The catalog has exactly the six active tables and no status history table.
- The reset migration only drops business tables/types, never an extension
  or `alembic_version`.

**Actual result (implemented on 2026-08-26):** The old migration graph was
replaced with four bootstrap migrations.

- `0001_reset_application_schema` drops the old business tables/types per an
  allowlist; the old local data is deleted per the bootstrap-stage decision.
- The next three migrations create vehicles/telematics, `vehicle_telemetry`,
  and the six active charging tables.
- The active schema has exactly the six tables `charging_stations`,
  `charging_evses`, `charging_connectors`, `charging_sessions`,
  `charging_session_events`, and `charging_session_meter_values`; status
  history is no longer in the catalog.
- `charging_session_events` and `charging_session_meter_values` were
  recreated as hypertables; `charging_sessions` remains a relational
  aggregate table.
- The new graph's downgrade drops the baseline schema; it does not pretend
  to restore the data that was reset.
- `alembic heads` now shows only `0004_create_charging_mvp_schema`.

### Step 3 — Implement the session happy path

**Prompt:**

```text
Rewrite the charging_sessions repository/service for the happy-path flow.

Implement ingest_transaction_event(...) for Started, Updated, and Ended:
- Started creates an active session and a Started event.
- Updated appends an Updated event.
- Ended updates the meter/time, moves to completed, and appends an Ended
  event.

Implement ingest_meter_values(...) to append one Wh sample per call and
update the ending meter value. The boundary only accepts UUID, enum,
datetime, Decimal, and one MeterSampleInput. The service/repository never
commit/rollback and never import charging_stations.

Keep the old reliability source as a comment; do not bring the
retry/idempotency, interruption/reconciliation, or unknown-transaction
branch into the active path.
```

**Main files:**

- `backend/app/domains/charging_sessions/repository.py`
- `backend/app/domains/charging_sessions/service.py`
- `backend/app/domains/charging_sessions/exceptions.py`
- `backend/app/domains/charging_sessions/types.py`

**Acceptance criteria:**

- A session can go fully through `Started → Updated/MeterValues → Ended`.
- A finished session has `status = completed`, the ending meter value, and
  energy delivered.
- Events and meter samples are stored in the same transaction as the
  aggregate.
- An exception rolls back the operation at the entry boundary.
- No active branch remains for retry, duplicate, ordering, or interruption.

**Actual result (implemented on 2026-08-03):** The happy-path implementation
in `charging_sessions` is complete.

- `ingest_transaction_event(...)` creates the `active` aggregate and
  `Started` event, appends `Updated`, or updates the meter/time, moves to
  `completed`, and appends `Ended`.
- `ingest_meter_values(...)` accepts exactly one `MeterSampleInput` per
  call, appends the canonical Wh sample, and updates the ending
  meter/energy delivered.
- The repository only calls `flush()` within the current transaction; the
  service and repository never call `commit()`/`rollback`, never import
  `charging_stations`, and never pass an ORM/Pydantic/OCPP object across the
  public boundary.
- Retry, duplicate/idempotency, out-of-order, interruption, reconciliation,
  and unknown transaction have no active branch; the reason for deferring
  them and the restoration contract are recorded in
  `docs/decisions/deferred.md` items 27–28.
- Ran a smoke check with a fake repository for the flow
  `Started → Updated/MeterValues → Ended`; the database integration check
  was not run in this step since it needs a running PostgreSQL/TimescaleDB
  instance.

### Step 4 — Trim the OCPP gateway

**Prompt:**

```text
Keep the OCPP gateway at a minimal handshake level.

The gateway only binds the host/port from config, accepts the WebSocket
path /ocpp/{ocpp_identity}, negotiates ocpp2.0.1, and rejects an identity
that has not been pre-provisioned. Keep one stable connection within the
process and forward primitive values to the charging_sessions service.

Comment out the advanced ConnectionRegistry, reconnect replacement, offline
detector, timeout, retry, and old shutdown recovery; do not delete the
source. Never auto-create a station/EVSE/connector from an OCPP message.
```

**Main files:**

- `backend/app/domains/charging_stations/ocpp/ocpp_server.py`
- `backend/app/domains/charging_stations/ocpp/entrypoint.py`
- `backend/app/domains/charging_stations/service.py`

**Acceptance criteria:**

- A valid identity can connect using the `ocpp2.0.1` subprotocol.
- An unprovisioned identity is rejected.
- The gateway contains no active reconnect/timeout/retry logic.
- The OCPP adapter never passes an ORM model, Pydantic schema, or OCPP
  object across the domain boundary.

**Actual result (implemented on 2026-08-03):** The minimal OCPP 2.0.1
gateway for the active path is complete.

- The gateway binds `CHARGING_OCPP_HOST`/`CHARGING_OCPP_PORT`, only accepts
  the path `/ocpp/{ocpp_identity}` and the `ocpp2.0.1` subprotocol; the
  identity must be an active, pre-provisioned station.
- The adapter resolves the OCPP station/EVSE/connector into primitive
  UUIDs, forwarding `TransactionEvent` and each energy `MeterValues` sample
  to the public `charging_sessions` service within the gateway's
  transaction boundary.
- The transaction/session mapping only exists within each WebSocket
  connection and is only updated after the transaction persists
  successfully; there is no production `ConnectionRegistry`, reconnect
  replacement, offline detector, timeout, retry, or shutdown recovery.
- The smoke test with `python-ocpp` payload dataclasses passed: Started,
  MeterValues, Ended, and verifying the boundary only accepts
  primitive/standard-library values.

### Step 5 — Write the handshake and happy-path simulator

**Prompt:**

```text
Create a local simulator for a pre-provisioned station.

The simulator must:
1. Connect over WebSocket with the ocpp2.0.1 subprotocol.
2. Send a TransactionEvent Started for a valid EVSE/connector.
3. Send each MeterValues message with one Wh sample value.
4. Send a TransactionEvent Updated if the flow needs an intermediate event.
5. Send a TransactionEvent Ended.
6. Close the connection after receiving a successful response.

Allow passing identity, EVSE ID, connector ID, and transaction ID via the
constructor/CLI. Do not add delay, retry, duplicate, reconnect, network
loss, random failure, or a reject case to the MVP simulator. The simulator
only runs the happy path with a pre-provisioned identity; handshake reject
is checked separately in the gateway smoke test.
```

**Main files/areas:**

- `simulator/`
- the related test/smoke script if the repo already has a convention

**Acceptance criteria:**

- One simulator command creates an `active` session after Started.
- The meter sample is stored with the correct `value_wh`.
- After Ended, the session moves to `completed` and has an Ended event.
- The simulator can connect with a valid, pre-provisioned identity.
- No new production dependency is created just to run the simulator.

**Actual result (implemented on 2026-08-04):** The OCPP happy-path simulator
at `simulator/charging_session_simulator.py` is complete.

- The simulator connects using the `ocpp2.0.1` subprotocol, sends
  `TransactionEvent Started`, each `MeterValues` with one Wh sample,
  `Updated`, and `Ended`; every CALL waits for a response before
  proceeding.
- Identity, EVSE ID, connector ID, and transaction ID can all be passed via
  `SimulatorConfig` or the CLI. The connection is closed after receiving
  the `Ended` ACK.
- The simulator only runs the happy path with a pre-provisioned identity;
  there is no delay, retry, duplicate, reconnect, reject, or random
  failure.
- The code lives outside the backend domain, at `simulator/`, using
  dependencies already present in the backend, with no new production
  dependency added.

### Step 6 — Minimal monitoring and integration check

**Prompt:**

```text
Add or complete the minimal monitoring API for the MVP session if the
current planner already requires a router.

Only expose the session, event, and meter data needed to check the happy
path; never expose payment, authorization, debt, or raw payload. Run a
smoke test from the simulator through to the DB and verify the
topology/session/event/meter data via the API or a read-only SQL query.
```

**Acceptance criteria:**

- A session can be viewed by ID and its stored event/meter data confirmed.
- No obvious N+1 in the added endpoints.
- The response contains none of the columns removed from the MVP.
- The transaction boundary still lives at the HTTP dependency/worker
  entrypoint.

**Actual result (implemented on 2026-08-04):** The monitoring API and the
happy-path integration smoke test are complete.

- Added the read-only endpoints:
  `GET /api/v1/charging-sessions`,
  `/api/v1/charging-sessions/{session_id}`, `/events`, and `/meter-values`;
  the list endpoint returns the newest session first so its `session_id`
  can be picked up, and the response contains only the session aggregate,
  lifecycle events, and canonical Wh meter values, with no raw
  OCPP/policy/payment field.
- Added stable pagination by timestamp + internal UUID. Event/meter history
  uses separate items/count queries and never eager-loads relationships, so
  it creates no N+1.
- Started PostgreSQL/TimescaleDB, the API, and the local OCPP gateway;
  pre-provisioned a test station/EVSE/connector, and ran the simulator
  `STEP6-TX-002` over a real WebSocket.
- DB result: session `completed`, starting meter `1000 Wh`, ending meter
  `1500 Wh`, energy delivered `500 Wh`; there were 3 events
  `Started → Updated → Ended` and 2 meter samples `1250 Wh`, `1500 Wh`. All
  3 monitoring endpoints returned the correct data.
- The integration test found and fixed a `python-ocpp` parser
  compatibility issue: the nested `transactionInfo`, `evse`, and
  `meterValue` can actually be a mapping instead of a dataclass. The
  adapter now canonicalizes both forms before ingesting.
- All test fixtures were cleaned out of the local PostgreSQL instance after
  the smoke test. The production broker/reliability path was not tested;
  those groups remain out of the MVP scope.

### Step 7 — Review, checks, and recording deferred items

**Prompt:**

```text
Run the final checks for all charging changes:

1. compileall, Black, isort, Ruff, and mypy.
2. Alembic upgrade/downgrade/upgrade on the dev DB if the environment
   allows it.
3. git diff --check.
4. Use rg to audit for cross-imports between models/repositories,
   commit/rollback inside service/repository, HTTPException outside the
   router, datetime.utcnow, and energy float.
5. Cross-check the commented-out source against the corresponding item in
   deferred.md.
6. Update the planner with the actual results and testing limits, and
   commit following Conventional Commits.
```

**Planner completion criteria:**

- The simulator can run the flow `Started → MeterValues/Updated → Ended`.
- The DB has exactly the six active tables per section 2.
- There is no retry, idempotency, reconnect, timeout, or interruption in
  the active path.
- No legacy/reliability source runs in the active path; limits that must be
  kept are recorded via docstring/comment wherever an invariant remains,
  while legacy source outside the MVP contract may be removed from the
  active source per the Step 0 decision.
- `deferred.md` fully describes the effect, the reason for deferring, and the
  planner that needs to reopen each reliability/production group.
- The check results and environment limits are recorded at the end of this
  step.

**Actual result (implemented on 2026-08-04):** The final review and checks
for the charging MVP are complete.

- `compileall`, Black, isort, Ruff, mypy strict, and `git diff --check` all
  passed. The minimum automated smoke test suite has now been added per
  [`backend-automated-tests.md`](./backend-automated-tests.md).
- The new Alembic graph has exactly the head `0004_create_charging_mvp_schema`;
  the offline SQL generated the full four reset/baseline steps and no
  longer references any old revision.
- Checked the offline catalog/DDL of the six active charging tables and the
  two history hypertables. A real upgrade/downgrade test on a temporary
  database now exists in `test_postgres_integration.py` and only runs when
  `RUN_DB_INTEGRATION=1` is set.
- After upgrading, the catalog has exactly the six active charging tables
  and no `charging_station_status_events`; Alembic has only the head
  `0004_create_charging_mvp_schema`.
- The audit found no cross-import of `models.py`/`repository.py` between
  domains, no `commit()`/`rollback()` in charging service/repository, no
  `HTTPException` outside the router, and no `datetime.utcnow()`. Energy
  values are canonicalized to `Decimal` before being passed into the
  session service/database; the adapter only uses the OCPP library's
  numeric types at the parsing layer.
- `deferred.md` has recorded the groups to reopen: authorization/remote
  control/pricing/payment/debt business logic, reliability/technical
  status, and operational error handling/observability of the OCPP
  gateway. The active path remains only
  `Started → Updated/MeterValues → Ended`, with no retry, idempotency,
  reconnect, timeout, or interruption.
- Remaining limits: real devices, DB/broker outages, production
  reliability, and automated OCPP WebSocket regression have not been
  tested. The backend smoke test suite exists; integration tests are
  enabled separately when the environment allows it. These items are
  outside the MVP planner's scope and require reopening the production
  planner before implementation.

## 5. Out of scope and the path back

When the system needs to run with real devices or under non-ideal
conditions, the old production planner and the corresponding item in
`docs/decisions/deferred.md` must be reopened. The groups that need to
be redesigned are:

- reconnect, connection registry, timeout, and offline detection;
- retry, idempotency, duplicate/conflict, and out-of-order events;
- interruption, reconciliation, and meter reset;
- technical status history;
- authorization, driver/vehicle policy, remote control, pricing, payment,
  and debt.

Do not reopen these groups on your own by gradually adding placeholders to
the MVP source.
