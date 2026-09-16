# Planner: Backend for charging station management and charging session storage

> Feature code: AD-03 and the lifecycle part of S-02
>
> Status: In progress. Trimmed-down MVP implemented; automated smoke tests are
> in place, PostgreSQL/OCPP integration and the production path remain deferred
>
> Last updated: 2026-07-31

This planner describes the charging backend scope and is executed according to
the trimmed-down version in
[`backend-charging-mvp-ideal.md`](./backend-charging-mvp-ideal.md). The current
source only commits to pre-provisioned topology and the local happy path; the
production requirements in this document remain a scale-up plan.

The "Actual result" sections of the older steps below are kept as a decision
history and implementation evidence. Where the old content says "no source
yet" or "not yet implemented," that reflects the state at the time it was
recorded; the current state is taken from the top of this file and from the
`backend-charging-mvp-ideal.md` planner.

> **Source of truth for the current source:** only pre-provisioned topology,
> OCPP 2.0.1, the session aggregate, lifecycle events, meter values, and the
> session read APIs. The descriptions of technical status, capability,
> location, reconciliation, and production reliability further below are a
> scale-up design, not yet an active contract of the repo.

## 1. Boundary between the two domains

### 1.1. `charging_stations`: physical devices and OCPP

Owns:

- the Charging Station, EVSE, and Connector records;
- physical topology in the active MVP; capability and technical status are a
  scale-up item;
- the OCPP 2.0.1 WebSocket gateway and the lifecycle happy path; the
  connection registry and the reliability path are a scale-up item;
- receiving the OCPP messages needed for the `TransactionEvent` and
  `MeterValues` happy path;
- forwarding normalized OCPP data to the public service of
  `charging_sessions`.

This MVP does not implement remote start/stop or command transport to the
charging station.

### 1.2. `charging_sessions`: charging session data and lifecycle

Owns:

- the `charging_sessions` aggregate;
- the `charging_session_events` history;
- the `charging_session_meter_values` meter samples;
- creating/updating/ending sessions from data sent by `charging_stations`;
- the session, lifecycle event, and meter value read APIs; reconciliation is
  a scale-up item.

This domain does not implement authorization, pricing, payment, debt, or
remote-control business rules in the MVP. It also does not own a WebSocket/OCPP
adapter and does not call back into `charging_stations`.

### 1.3. Data direction and public boundary

```text
Charging station ⇄ OCPP ⇄ charging_stations → charging_sessions
```

- `charging_stations` calls the public service of `charging_sessions` to
  ingest events and meter data.
- `charging_sessions` returns a technical processing result if the caller
  needs it, but does not send OCPP or control commands back to the station.
- No cross-importing `models.py`/`repository.py`; the OCPP adapter only passes
  primitive/standard-library values and the internal IDs it needs.
- API orchestration may read both domains and assemble the response; that is
  not a reverse dependency between domains.

## 2. MVP scope and deferred items

### In scope

- Pre-provisioning station/EVSE/connector via the Admin API.
- OCPP 2.0.1 over WebSocket at `/ocpp/{ocpp_identity}`.
- Development may run without TLS/without authentication in an isolated
  environment; the production security profile is decided later.
- MVP topology test: `2 EVSE × 1 connector`; the schema supports N EVSE/N
  connector.
- Storing pre-provisioned topology, transaction events, and meter data.
- Storing the session aggregate, event history, meter history, and the
  session read API.
- UTC timezone-aware timestamps, canonical energy in Wh, `NUMERIC`/`Decimal`.
- `charging_sessions` is a regular relational table; event/meter history is a
  TimescaleDB hypertable. Location/PostGIS is not yet part of the active
  schema.

### Deferred from the MVP

- RFID/idToken authorization, driver/vehicle mapping, and start/stop
  permissioning.
- Remote start/stop, `charging_remote_commands`, and business guards.
- Tariff, pricing, payment, webhook, overdue, and debt.
- Reservation, smart charging, firmware management, alert delivery.

Deferred items must be recorded in `docs/01-requirements/future.md`; do not
create placeholder source or tables for them in the MVP.

## 3. Target data model in the MVP

### 3.1. `charging_stations`

- UUID internal ID, unique `ocpp_identity`, metadata, location, administrative
  status, connection snapshot, `last_seen_at`, timestamps, and soft delete.
- Does not auto-create an unknown station/EVSE/connector from
  `BootNotification`.

### 3.2. `charging_evses`

- UUID, station FK, positive `ocpp_evse_id`, availability/status, and
  capability.
- Unique `(station_id, ocpp_evse_id)`.

### 3.3. `charging_connectors`

- UUID, EVSE FK, positive `ocpp_connector_id`, connector type/status/
  capability.
- Unique `(evse_id, ocpp_connector_id)`.

### 3.4. `charging_station_status_events`

A hypertable keyed by `recorded_at`, storing station/EVSE/connector refs,
source action, status/event, OCPP message ID, received time, and sanitized
raw JSONB.

### 3.5. `charging_sessions`

A regular aggregate table, containing at minimum:

- UUID internal ID;
- station/EVSE/connector IDs;
- the OCPP transaction ID and start/end timestamps;
- operational status: `pending | active | ending | completed | interrupted`;
- meter start/end, energy delivered in Wh, reconciliation status/error;
- created/updated timestamps.

No pricing/payment/authorization columns are added in this MVP.

### 3.6. `charging_session_events` and `charging_session_meter_values`

Two hypertable/audit history tables, with an idempotency key matching
`transaction_id`, `seq_no`, the event timestamp, and the sampled value
identity. A duplicate or out-of-order event must not roll back state.

### 3.7. Step 1 schema contract — general conventions

- Every table has a UUID internal ID. For the three history tables that are
  hypertables, the primary key is the composite `(internal_id, time_column)`
  to satisfy TimescaleDB's unique index requirement; the UUID remains the
  internal ID used in the API and logs, and a business key is never used as
  the primary key.
- All timestamps are `TIMESTAMP WITH TIME ZONE`, normalized to UTC before
  validation/persistence. Every history record has both the time the device
  produced it (`recorded_at`/`event_occurred_at`/`sampled_at`) and the time
  the server received it (`received_at`).
- Energy and power values use `Decimal` in Python and `NUMERIC` in the DB; the
  canonical energy unit is Wh. `float` is never used to compute or compare
  energy.
- Soft-delete applies only to `charging_stations`, `charging_evses`, and
  `charging_connectors` via `deleted_at`. Sessions or history are never
  deleted; every topology/session foreign key uses `ON DELETE RESTRICT`, and
  normal CRUD operations never physically delete a record.
- Unique topology identity includes soft-deleted records too, to avoid
  reusing a business identity in a way that would blur history; reusing old
  topology requires restoring the old record under the later CRUD contract.
- `station_id`, `evse_id`, and `connector_id` passed across the boundary are
  always the internal UUID. The OCPP adapter resolves topology before calling
  the service; it never passes an `ocpp.v201` object, a Pydantic schema, or a
  SQLAlchemy model across the boundary.

### 3.8. Field/constraint/index contract for topology

#### `charging_stations`

| Field | Contract |
|---|---|
| `station_id` | UUID, primary key |
| `ocpp_identity` | Non-empty string, max 255 characters, unique across the table; case is preserved exactly to resolve the OCPP path correctly |
| `display_name` | Non-empty string, max 200 characters |
| `manufacturer`, `model`, `serial_number`, `firmware_version` | Nullable string, technical metadata only; never used as identity |
| `location` | PostGIS `geography(Point, 4326)`, nullable; the coordinates must fall within a valid latitude/longitude range |
| `administrative_status` | `active \| inactive \| maintenance`, required |
| `connection_status` | `unknown \| connected \| offline`, a technical snapshot, required |
| `last_seen_at`, `last_boot_at` | UTC-aware, nullable |
| `created_at`, `updated_at` | UTC-aware, required |
| `deleted_at` | UTC-aware, nullable; a soft-deleted station must not be resolved by OCPP or returned in the active list |

Minimum index/constraint: unique `ocpp_identity`; indexes on
`(administrative_status, deleted_at)`, `(connection_status, last_seen_at)`,
`deleted_at`; a spatial GiST index on `location` if location-based queries are
enabled.

#### `charging_evses`

| Field | Contract |
|---|---|
| `evse_id` | UUID, primary key |
| `station_id` | UUID, FK `charging_stations.station_id`, NOT NULL, `ON DELETE RESTRICT` |
| `ocpp_evse_id` | Positive integer (`> 0`), NOT NULL |
| `display_name` | Nullable string, max 100 characters |
| `administrative_status` | `active \| inactive`, required |
| `technical_status` | `unknown \| available \| occupied \| unavailable \| faulted`, required |
| `capabilities` | JSONB object, defaults to `{}`, holds only normalized capabilities, never a credential |
| `last_status_at` | UTC-aware, nullable |
| `created_at`, `updated_at`, `deleted_at` | UTC-aware; `deleted_at` nullable for soft delete |

Unique `(station_id, ocpp_evse_id)`; indexes on `(station_id, deleted_at)` and
`(technical_status, station_id)`. The service must verify the EVSE belongs to
the station passed in when processing an event; a single FK does not fully
express this cross-topology constraint.

#### `charging_connectors`

| Field | Contract |
|---|---|
| `connector_id` | UUID, primary key |
| `evse_id` | UUID, FK `charging_evses.evse_id`, NOT NULL, `ON DELETE RESTRICT` |
| `ocpp_connector_id` | Positive integer (`> 0`), NOT NULL |
| `connector_type` | Nullable string, max 100 characters; left nullable until real station data is available |
| `max_power_kw` | `NUMERIC(12,3)` nullable; if present, `> 0` |
| `administrative_status` | `active \| inactive`, required |
| `technical_status` | `unknown \| available \| occupied \| unavailable \| faulted`, required |
| `capabilities` | JSONB object, defaults to `{}`, never a credential |
| `last_status_at` | UTC-aware, nullable |
| `created_at`, `updated_at`, `deleted_at` | UTC-aware; `deleted_at` nullable for soft delete |

Unique `(evse_id, ocpp_connector_id)`; indexes on `(evse_id, deleted_at)` and
`(technical_status, evse_id)`. A connector must belong to the correct EVSE of
the station within the same operation; a connector is never auto-created from
an OCPP notification.

### 3.9. Field/constraint/index contract for technical history

#### `charging_station_status_events`

| Field | Contract |
|---|---|
| `status_event_id` | UUID internal ID |
| `recorded_at` | UTC-aware, NOT NULL, the hypertable's partition key |
| `station_id` | UUID, FK station, NOT NULL, `ON DELETE RESTRICT` |
| `evse_id` | UUID, FK EVSE, nullable for a station-level event |
| `connector_id` | UUID, FK connector, nullable; if present, `evse_id` is also required |
| `source_action` | `BootNotification \| Heartbeat \| StatusNotification \| NotifyEvent` |
| `technical_status` | Nullable string; the normalized status if the message carries one |
| `event_code` | Nullable string; the normalized event code for `NotifyEvent` |
| `ocpp_message_id` | Nullable string, max 255 characters |
| `idempotency_key` | Non-empty string, max 255 characters, stably generated by the adapter |
| `received_at` | UTC-aware, NOT NULL |
| `sanitized_raw_payload` | Nullable JSONB; always redacted before being stored |

The primary key is `(status_event_id, recorded_at)`. The minimum database
unique constraint is `(station_id, idempotency_key, recorded_at)` because a
hypertable requires the partition key to be part of the unique index; the
service still checks the logical `idempotency_key` to detect the same event
being resent with a different timestamp. Query indexes include
`(station_id, recorded_at DESC)`, `(evse_id, recorded_at DESC)`,
`(connector_id, recorded_at DESC)`, and `(source_action, recorded_at DESC)`.

#### `charging_session_events`

| Field | Contract |
|---|---|
| `event_id` | UUID internal ID |
| `event_occurred_at` | UTC-aware, NOT NULL, the hypertable's partition key |
| `session_id` | UUID, FK `charging_sessions.session_id`, NOT NULL, `ON DELETE RESTRICT` |
| `event_type` | `Started \| Updated \| Ended \| Interrupted` |
| `seq_no` | Non-negative integer, NOT NULL; the TransactionEvent's logical order |
| `end_reason` | `normal \| abnormal \| offline \| unknown`, nullable and only used for Ended/Interrupted |
| `charging_state` | `pending \| active`, nullable |
| `idempotency_key` | Non-empty string, stably derived from station/transaction/seq_no |
| `received_at` | UTC-aware, NOT NULL |
| `sanitized_raw_payload` | Nullable JSONB, redacted |

The primary key is `(event_id, event_occurred_at)`. Unique `(session_id,
seq_no, event_occurred_at)` is the minimum DB-level constraint; the service
uses the logical key `(session_id, seq_no)` plus a payload fingerprint to
handle differing timestamps. Indexes `(session_id, event_occurred_at,
event_id)` and `(session_id, seq_no, event_occurred_at)` support
history/idempotency.

#### `charging_session_meter_values`

| Field | Contract |
|---|---|
| `meter_value_id` | UUID internal ID |
| `sampled_at` | UTC-aware, NOT NULL, the hypertable's partition key |
| `session_id` | UUID, FK `charging_sessions.session_id`, NOT NULL, `ON DELETE RESTRICT` |
| `measurand` | Non-empty string, max 100 characters; the MVP accepts only normalized energy measurands (`energy_import_register` or `energy_import_interval`) |
| `phase`, `context` | Nullable string, max 50 characters |
| `source_value` | `NUMERIC(24,6)`, NOT NULL |
| `source_unit` | Non-empty string, max 20 characters |
| `value_wh` | `NUMERIC(24,3)`, NOT NULL, the value normalized to Wh and `>= 0` |
| `seq_no` | Non-negative integer, nullable if the MeterValues message has no sequence |
| `sample_idempotency_key` | Non-empty string, derived from the transaction/sample identity |
| `received_at` | UTC-aware, NOT NULL |
| `sanitized_raw_payload` | Nullable JSONB, redacted |

The MVP only stores meter energy for reconciliation purposes; power, current,
voltage, or temperature measurands do not go into this session meter table.
The primary key is `(meter_value_id, sampled_at)`. The minimum database
unique constraint is `(session_id, sample_idempotency_key, sampled_at)`; the
service uses the logical sample identity `(transaction_id, sampled_at,
measurand, phase, context)` to detect duplicates even when the partition
timestamp differs. Indexes `(session_id, sampled_at, meter_value_id)` and
`(session_id, measurand, sampled_at)`.

### 3.10. Field/constraint/index contract for the session aggregate

#### `charging_sessions`

| Field | Contract |
|---|---|
| `session_id` | UUID, primary key |
| `station_id` | UUID, FK station, NOT NULL, `ON DELETE RESTRICT` |
| `evse_id` | UUID, FK EVSE, NOT NULL, `ON DELETE RESTRICT` |
| `connector_id` | UUID, FK connector, NOT NULL, `ON DELETE RESTRICT` |
| `ocpp_transaction_id` | Non-empty string, max 255 characters |
| `status` | `pending \| active \| ending \| completed \| interrupted` |
| `started_at`, `ended_at` | UTC-aware; `started_at` is required after Started, `ended_at` is nullable |
| `last_event_at`, `last_meter_at` | UTC-aware, nullable; must never move backward |
| `last_transaction_seq_no` | Non-negative integer, nullable; must never move backward |
| `meter_start_wh`, `meter_end_wh`, `energy_delivered_wh` | `NUMERIC(24,3)` nullable; any present value must be `>= 0` |
| `reconciliation_status` | `pending \| reconciled \| inconsistent \| unavailable` |
| `reconciliation_error` | Nullable string, describing only a technical reconciliation error |
| `created_at`, `updated_at` | UTC-aware, NOT NULL |

Unique `(station_id, ocpp_transaction_id)` guarantees a reconnect never
creates a new session. Indexes on `(status, updated_at)`, `(station_id,
status)`, `(evse_id, status)`, `(connector_id, status)`, `started_at`, and
`ended_at`. A session has no `deleted_at`, pricing, payment, authorization,
driver, or vehicle field in the MVP.

### 3.11. State machine and idempotency rules

The session's operational state machine:

```text
pending ──→ active ──→ ending ──→ completed
   │           │          └──────→ interrupted
   └───────────┴─────────────────→ interrupted

completed / interrupted are terminal; any further event can only be a
duplicate/no-op or out-of-order history — a session is never reopened.
```

- `Started` creates the session for the first time against the resolved
  topology. If the normalized `charging_state` is `pending`, the session
  stays `pending`; once there is a valid charging state or meter value, it
  moves to `active`.
- `Updated` and MeterValues never create a new session. They append to
  history; they only update the aggregate if the sequence/sample is newer per
  the ordering rule.
- `Ended` appends an event, transitions through `ending` within the same
  transaction, and then finalizes to `completed` for `end_reason=normal` or
  `interrupted` for `abnormal|offline|unknown`. On a meter reset/conflict,
  history is still kept but `reconciliation_status=inconsistent` is set;
  energy is never subtracted back.
- `mark_station_interrupted` moves every `pending|active|ending` session of a
  station to `interrupted`; a terminal session is left unchanged.
- The TransactionEvent logical key is `(station_id, ocpp_transaction_id,
  seq_no)`. The same key with the same fingerprint is a duplicate/no-op and
  may return a successful ACK; the same key with a different payload is a
  conflict and the aggregate is not updated.
- The meter's logical sample identity is `(ocpp_transaction_id, sampled_at,
  measurand, phase, context)`. The same identity with the same value is a
  duplicate; the same identity with a different value is a conflict, marking
  reconciliation as inconsistent without overwriting the old sample.
- Out-of-order events/meter values are still stored if not a duplicate, but
  must never roll back `status`, `last_event_at`, `last_meter_at`, the
  sequence, or the meter end value. A reconnect uses the same transaction
  identity to continue the session; MeterValues for a transaction that does
  not yet exist are rejected/recorded as `unknown_transaction`, and no
  aggregate is auto-created.
- A meter register that decreases relative to the previous sample is still
  stored for audit and flagged `meter_reset_or_decrease`; `energy_delivered_wh`
  never takes a negative value.

### 3.12. Public service of `charging_sessions`

This is the only public boundary that `charging_stations` calls. The
signature below is a logical contract, not an implementation:

```text
ingest_transaction_event(
    db,
    station_id: UUID,
    evse_id: UUID,
    connector_id: UUID,
    transaction_id: str,
    event_type: Started | Updated | Ended,
    seq_no: int,
    event_occurred_at: datetime,
    received_at: datetime,
    charging_state: pending | active | None,
    end_reason: normal | abnormal | offline | unknown | None,
    meter_start_wh: Decimal | None,
    meter_end_wh: Decimal | None,
    idempotency_key: str,
    sanitized_raw_payload: JSON object | None,
) -> TransactionIngestResult

ingest_meter_values(
    db,
    station_id: UUID,
    evse_id: UUID,
    connector_id: UUID,
    transaction_id: str,
    sample: MeterSampleInput,
    received_at: datetime,
) -> MeterIngestResult

mark_station_interrupted(
    db,
    station_id: UUID,
    interrupted_at: datetime,
    received_at: datetime,
    reason: offline | connection_lost | unknown,
) -> InterruptionResult
```

`MeterSampleInput` is only normalized data using standard-library types:
`sampled_at`, `measurand`, `phase`, `context`, `source_value`, `source_unit`,
`value_wh`, `seq_no`, and `sample_idempotency_key`. It never accepts an
`ocpp.v201` type, a Pydantic model, or an ORM object. The service result is an
immutable standard-library result containing
`accepted|duplicate|ignored_out_of_order|conflict|rejected`, `session_id`,
the current status, and the number of samples/events inserted; it never
returns a DB model.

The public service never calls `commit()`/`rollback()`. The caller at the
entry boundary owns the `AsyncSession` and the transaction; one call to
ingest an event or one call to `ingest_meter_values` for a sample is atomic.
The repository calls `flush()` to detect FK/unique conflicts when needed. A
DB timeout, serialization/deadlock, or unexpected error must roll back and
propagate; the service never retries and never ACKs OCPP successfully before
the transaction commits.

### 3.13. HTTP API for topology/status/history and session monitoring

The common prefix is `/api/v1`. The topology API serves pre-provisioning and
soft delete:

- `POST/GET /charging-stations` — create and list stations; the
  `administrative_status`, `connection_status`, `include_deleted` filters are
  not exposed on the public MVP API until authorization exists.
- `GET/PATCH/DELETE /charging-stations/{station_id}` — station detail, partial
  update, soft delete.
- `POST/GET /charging-stations/{station_id}/evses` and
  `GET/PATCH/DELETE /charging-evses/{evse_id}` — CRUD for the station's EVSEs.
- `POST/GET /charging-evses/{evse_id}/connectors` and
  `GET/PATCH/DELETE /charging-connectors/{connector_id}` — CRUD for the
  EVSE's connectors.

Technical status/history API:

- `GET /charging-stations/{station_id}/status` returns a snapshot of the
  station, EVSE, and connector; it never creates topology if it is missing.
- `GET /charging-stations/{station_id}/status-history` supports filtering by
  `evse_id`, `connector_id`, `source_action`, `technical_status`, a
  UTC-aware `from/to` range, and `page/page_size`.

Session monitoring API:

- `GET /charging-sessions`
- `GET /charging-sessions/active`
- `GET /charging-sessions/{session_id}`
- `GET /charging-sessions/{session_id}/meter-values`
- `GET /charging-sessions/{session_id}/events`

The session list is filtered by `station_id`, `evse_id`, `connector_id`,
`ocpp_transaction_id`, `status`, `started_from/started_to`, and
`updated_from/updated_to`; history is filtered by a UTC range. Listing uses
`page/page_size` pagination, a total count, and a stable order with a UUID
tie-breaker. Sessions default-sort by `updated_at DESC, session_id DESC`;
events/meter values sort by their occurrence time ASC then internal ID.
Queries must avoid N+1.

The public response contains only topology/status/session/event/meter and
technical reconciliation information. It never returns payment,
authorization, driver, vehicle, raw payload, or credential fields.

### 3.14. Timeouts and raw payload redaction

- Timeouts must live in the charging settings namespace finalized during
  Step 2, covering at minimum the heartbeat/offline timeout, the meter stale
  timeout, the OCPP request timeout, and the payload size limit. Step 1 does
  not set actual values because the heartbeat/sample interval and offline
  buffer are still missing.
- A station only moves `connected` → `offline` once the clock exceeds the
  offline timeout from `last_seen_at`; a timeout never spawns a session and
  never creates topology on its own. The gateway/lifecycle calls
  `mark_station_interrupted` once the timeout is confirmed. Reconnect replay
  data is handled through idempotency.
- The adapter must recursively redact case-insensitive keys such as
  `password`, `token`, `authorization`, `certificate`, `private_key`,
  `secret`, `credential`, `id_token` before passing `sanitized_raw_payload`.
  A payload over the configured size limit has its raw portion dropped
  without losing the normalized event/meter data; credentials are never
  stored for debugging purposes.
- Raw payload never appears in the default response, and the public MVP API
  has no mode that returns raw payload. Logs record only
  station/transaction/event identity and sanitized metadata, never the full
  payload or a secret.

**Actual result (reviewed on 2026-07-31):** The Step 1 contract was finalized
across sections 3.7–3.14. No source code, dependency, config, or migration
has been changed yet; Step 2 will implement this contract and must re-review
the TimescaleDB constraints, PostGIS, FK, index, and enum types before
merging.

## 4. Execution order

Each step is an independent prompt. Only mark a step complete after running
its checks and recording the actual result in this file.

### Step 0 — Review the current state and lock the MVP scope

**Prompt:**

```text
Read CLAUDE.md, feature-list.md, future.md, and this planner. Review the
source/migrations/config to confirm no charging implementation exists yet.

Only carry out Step 0; do not write source code yet. Record clearly:
1. charging_stations owns the physical devices, OCPP, and technical events.
2. charging_sessions only receives/stores/updates sessions, events, and
   meter data from stations.
3. Authorization, RFID/driver/vehicle policy, remote control, pricing,
   payment, overdue, and debt are all out of the MVP scope.
4. Information still missing to test with a real station: production
   identity/credentials, EVSE/connector IDs, connector type/power, the
   heartbeat/sample interval, and the offline buffer.
Do not invent credentials or business rules outside the scope.
```

**Actual result (reviewed on 2026-07-31):** Step 0 is complete. The trimmed
MVP scope was confirmed as follows:

1. `charging_stations` owns the physical devices (station/EVSE/connector),
   topology, technical status, OCPP 2.0.1, and technical events. The OCPP
   gateway lives in this domain.
2. `charging_sessions` only receives normalized data from
   `charging_stations` through the public service, to create/update/store the
   session aggregate, event history, and meter history; this domain does not
   own a WebSocket or OCPP adapter.
3. Authorization, RFID/`idToken`, driver/vehicle policy, remote start/stop and
   remote-control business logic, pricing, payment, webhook, overdue, and debt
   are all out of the MVP scope. `docs/01-requirements/future.md` item 26 has
   recorded these as deferred business features; no source, migration, or
   placeholder is created for them.

Information still missing before testing with a real station, not yet
assigned a value or business rule:

- production identity/credentials and the device authentication security
  profile; an isolated dev environment may use a connection without
  TLS/without authentication per the current decision, but this must not be
  read as a production configuration;
- the actual list of `EVSE ID` and `connector ID` values for each station;
- connector type, rated power, and the corresponding capabilities;
- the heartbeat interval, meter/sample interval, and the timeout used to
  determine a lost connection;
- the offline buffer policy: whether a station buffers and backfills data on
  reconnect, or data loss during an offline period is accepted.

Evidence from the current-state review:

- `backend/app/domains/` currently only has `vehicles`, `telematics`, and
  `telemetry`; there is no `charging_stations`, `charging_sessions`, or
  `ocpp` yet.
- `backend/app/libs/db/migrations/versions/` only has migrations for
  vehicles, telematics, and `vehicle_telemetry`; there is no charging
  table/migration yet.
- `backend/app/api/main.py` only registers the vehicles, telematics, and
  telemetry routers; there is no charging router yet.
- `backend/pyproject.toml` does not yet declare `python-ocpp`; the shared
  config and the `.env.example` files currently only have variables for the
  app, database, and telemetry/MQTT, with no charging/OCPP config yet.
- `git ls-files` and a repo-wide search find no charging/OCPP source; the
  only charging-related results today are the planner documents,
  `CLAUDE.md`, and the related requirements/future sections.

No source code, dependency, config, or migration was changed in Step 0.

### Step 1 — Lock the schema contract and the public service

**Prompt:**

```text
Carry out Step 1, changing only the planner/contract.
1. Lock the fields/constraints/index/FK/soft-delete for the 5 table groups
   in section 3.
2. Lock the session state machine and idempotency rules for
   TransactionEvent/MeterValues.
3. Design the public service of charging_sessions:
   ingest_transaction_event(...), ingest_meter_values(...),
   mark_station_interrupted(...) if needed.
4. Specify that the OCPP adapter only passes primitive/standard-library
   values.
5. Lock the HTTP API for topology/status/history and session monitoring.
6. Lock the transaction boundary, duplicate/out-of-order handling, timeouts,
   and raw payload redaction.
Do not add tables for remote commands, tariffs, payment, or debt.
```

### Step 2 — Create the package, dependency/config, and migration

**Prompt:**

```text
Carry out Step 2 per the Step 1 contract.
1. Create the charging_stations and charging_sessions packages with
   __init__.py containing only a docstring, plus the required
   types/exceptions/models.
2. Add python-ocpp via uv, syncing pyproject.toml/uv.lock.
3. Create the Alembic migration for station/EVSE/connector/status event/
   session/session event/meter value.
4. Use the shared Base/session; UUID, UTC, PostGIS, and TimescaleDB per the
   contract.
5. Review the hypertable partition key, unique/idempotency constraints,
   FK/soft-delete, upgrade/downgrade, and the monitoring query indexes.
6. Run format/lint/type/import checks and a migration smoke test; do not
   create fake data.
```

**Actual result (implemented on 2026-08-02):** Step 2 is complete.

- Created the `charging_stations` and `charging_sessions` packages with
  `__init__.py` containing only a module docstring; added `types.py`,
  `exceptions.py`, and `models.py` per the contract. The metadata now
  registers all seven tables: station, EVSE, connector, technical status
  event, session, session event, and meter value.
- Added `ocpp>=2.0.0` (the PyPI package name of the MobilityHouse
  `python-ocpp` library) and `geoalchemy2>=0.15.0`; `backend/uv.lock` was
  resolved with `ocpp` 2.1.0. The charging config uses the `CHARGING_`
  namespace, covering heartbeat/offline timeouts, meter stale timeout, OCPP
  request timeout, and the raw payload size limit.
- Created the migration `b2c7d4e8f901_create_charging_domains.py`: UUID
  internal ID, UTC-aware timestamps, PostGIS `geography(POINT, 4326)`,
  Decimal/`NUMERIC`, contract enum values, FK `ON DELETE RESTRICT`,
  soft-delete topology, check/unique constraints, and indexes to support
  monitoring. `charging_sessions` is a regular table;
  `charging_station_status_events`, `charging_session_events`, and
  `charging_session_meter_values` are hypertables with the partition key
  included in every primary/unique index.
- Loaded the charging models into the Alembic metadata. The smoke test on a
  dev PostgreSQL + TimescaleDB instance ran successfully: `upgrade head` →
  `downgrade 5e7b1c9d2a44` → `upgrade head`; no fake data was created. The
  catalog confirmed exactly seven tables, three hypertables, PostGIS
  geography, and the corresponding enums/indexes/FKs.
- Checks passed: `compileall`, Black, isort, Ruff, and mypy strict. `alembic
  check` still flags `spatial_ref_sys`, internal indexes generated by
  TimescaleDB, and a few legacy indexes outside charging; this is an
  introspection difference from the existing schema, not an upgrade/downgrade
  bug in the new migration.

### Step 3 — CRUD for station, EVSE, connector

**Prompt:**

```text
Carry out Step 3. Create schemas/repository/service/router for CRUD and
soft delete of station, EVSE, connector. Support N EVSE/N connector, PATCH
following convention, identity/unique topology validation, and never
auto-create topology from OCPP. Register the router, run a Swagger smoke
test for a 2 EVSE × 1 connector topology and for conflicts.
```

**Actual result (implemented on 2026-08-02):** The Step 3 implementation is
complete; no new commit has been created yet.

- Added `schemas.py`, `repository.py`, `service.py`, and `router.py` for
  `charging_stations`; the router is registered under `/api/v1` with the full
  set of station, EVSE, and connector endpoints keyed by internal UUID.
- CRUD supports pagination, filtering by station status, PATCH following
  `exclude_unset=True` with the convention that `None` means no update. The
  station's OCPP identity, `(station_id, ocpp_evse_id)`, and `(evse_id,
  ocpp_connector_id)` are checked against soft-deleted records too; a
  conflict returns HTTP 409.
- The parent must be active before child topology can be created/listed.
  Soft-deleting a station cascades to its EVSEs/connectors; soft-deleting an
  EVSE cascades to its connectors; the API never physically deletes
  history/topology.
- The Swagger/OpenAPI smoke test confirmed 6 topology routes. The API smoke
  test with rollback/cleanup ran a topology of 1 station, 2 EVSEs, 1
  connector per EVSE, a duplicate conflict, PATCH, and soft delete; no test
  record remains in the database.
- Checks passed: Black, isort, Ruff, mypy strict, compileall, and importing
  the FastAPI app. `httpx` was only loaded temporarily via `uv run --with`
  for the smoke test and was not added as a runtime dependency.

### Step 4 — Session ingestion service and persistence

**Prompt:**

```text
Carry out Step 4 for charging_sessions. Implement a repository/service that
accepts TransactionEvent Started/Updated/Ended and MeterValues via primitive
values. Started creates the session; Updated/MeterValues append an
event/sample; Ended transitions through ending then to
completed/interrupted per the contract. Duplicate/out-of-order/reconnect
handling is idempotent, state never moves backward, and energy is normalized
to Wh using Decimal. The service and repository never commit/rollback and
never import charging_stations. Test two concurrent EVSEs, a duplicate
seqNo, a meter reset, an unknown transaction, and a rollback.
```

**Actual result (implemented on 2026-08-02):** Session ingestion and
persistence in the `charging_sessions` domain is complete.

- Added `repository.py` and `service.py`, along with immutable
  input/result dataclasses in `types.py`; the boundary only accepts UUID,
  enum, `datetime`, `Decimal`, `Sequence`, and a sanitized raw dict — it never
  imports `charging_stations` or an OCPP/Pydantic/ORM type from the caller.
- `Started` creates the aggregate and event; `Updated`/`Ended` append an
  event; Ended passes through `ending` within the same transaction and then
  finalizes to `completed` or `interrupted`. A reconnect reuses
  `(station_id, transaction_id)` and never creates a new session.
- TransactionEvent detects duplicates/conflicts via `(session_id, seq_no)`
  and a payload fingerprint; an out-of-order event is still stored for audit
  but never rolls back state, timestamps, or the sequence. A terminal session
  is never reopened.
- MeterValues only accepts the two MVP energy measurands, normalizes
  `Wh`/`kWh` using `Decimal`, and appends samples idempotently by logical
  sample identity. A meter reset, a decreasing register, or a payload
  conflict is stored/flagged as `reconciliation_status=inconsistent` without
  making `energy_delivered_wh` negative. An unknown transaction is rejected
  and no aggregate is auto-created.
- Implemented `mark_station_interrupted`, moving a station's
  `pending|active|ending` sessions to `interrupted` and appending history; a
  terminal session is left unchanged.
- The dev PostgreSQL smoke test passed: two EVSEs with two concurrent
  sessions, duplicate seq, out-of-order, Wh normalization, meter reset,
  unknown transaction, interruption, and transaction rollback. Test data was
  fully cleaned up.
- Checks on the changed code passed: Black, isort, Ruff, mypy strict, and
  compileall. The HTTP router/session monitoring and OCPP bridge were not
  added yet, since they belong to Steps 7–8; no migration was added since the
  Step 2 schema already covers this contract.

### Step 5 — OCPP WebSocket gateway

**Prompt:**

```text
Carry out Step 5 for charging_stations/ocpp. Create a server/entrypoint using
python-ocpp v201, negotiating only ocpp2.0.1, validating identity, keeping
one active connection per station, and handling reconnect and graceful
shutdown. Use the shared session factory and structured logging, without
creating a separate engine/logger. Create a minimal connect/reject protocol
simulator.
```

**Actual result (implemented on 2026-08-02):** The minimal OCPP 2.0.1 gateway
and the handshake simulator are complete.

- Added `charging_stations/ocpp/ocpp_server.py` and `entrypoint.py`; the
  gateway binds to `CHARGING_OCPP_HOST`/`CHARGING_OCPP_PORT`, using the
  shared `async_session_factory`, structured logging, and
  `ocpp.v201.ChargePoint`.
- The WebSocket only accepts the `ocpp2.0.1` subprotocol; the
  `/ocpp/{ocpp_identity}` path must be valid, and the identity must be a
  pre-provisioned station that has not been soft-deleted. An unknown identity
  gets HTTP 404; a mismatched protocol gets HTTP 426.
- The connection registry keeps at most one active connection per identity. A
  reconnect closes the old connection before the new handler proceeds;
  shutdown closes the listener and every active connection with close code
  1001.
- This step does not yet handle BootNotification, Heartbeat,
  StatusNotification, NotifyEvent, TransactionEvent, or MeterValues; those
  actions are still pending Steps 6–7.
- Added `simulator/charging_session_simulator.py` to run the happy-path
  session with a pre-provisioned identity.
- The runtime smoke test with a fake repository passed: connecting with
  `ocpp2.0.1`, a reconnect replacing the registry entry, an unknown identity
  returning HTTP 404, and a mismatched protocol returning HTTP 426. The
  Docker daemon was not reachable from the sandbox, so testing against a real
  PostgreSQL/EMQX was not run in this step.
- Checks passed: Black, isort, Ruff, mypy strict, and compileall.

### Step 6 — Boot, heartbeat, status, and technical history

**Prompt:**

```text
Carry out Step 6. Implement BootNotification, Heartbeat, StatusNotification,
and NotifyEvent. Boot only updates a pre-provisioned station; unknown
topology is never auto-created. The snapshot and status event write is
atomic; duplicates/out-of-order never roll back the timestamp/status; offline
detection reads its timeout from config. Create the status and status-history
API with UTC filtering/pagination, never returning raw payload by default.
```

### Step 7 — Bridge OCPP events into sessions

**Prompt:**

```text
Carry out Step 7. The OCPP adapter resolves internal station/EVSE/connector
IDs, carries the transactionId/seqNo/timestamp/meter, and calls the public
charging_sessions.service. Never pass an ocpp.v201 or SQLAlchemy model across
the boundary. Only respond to OCPP after the persistence operation succeeds,
per the transaction contract. Test Started, Updated, Ended, MeterValues,
duplicate, out-of-order, unknown transaction, and a DB rollback. Do not
implement authorize or remote command.
```

### Step 8 — Session monitoring API

**Prompt:**

```text
Carry out Step 8 for charging_sessions. Implement:
GET /api/v1/charging-sessions
GET /api/v1/charging-sessions/active
GET /api/v1/charging-sessions/{session_id}
GET /api/v1/charging-sessions/{session_id}/meter-values
GET /api/v1/charging-sessions/{session_id}/events

Filter by station/EVSE/connector/transaction/status/UTC range, with stable
pagination and no N+1. The response contains only
session/event/meter/technical reconciliation data, with no
payment/authorization fields. Run an empty/filter/pagination smoke test.
```

### Step 9 — Simulator and MVP acceptance

**Prompt:**

```text
Carry out Step 9 and accept the MVP planner.
1. The simulator models a station with two EVSEs, one connector per EVSE;
   run two concurrent sessions, Boot/Heartbeat/Status/Notify/Transaction/
   Meter, delay, disconnect/reconnect, duplicate, and out-of-order.
2. E2E: OCPP Started → session active → MeterValues → Ended → completed or
   interrupted → API monitoring.
3. Run compileall, Black, isort, Ruff, mypy, and migration
   upgrade/downgrade/upgrade.
4. Use rg to audit for cross-imports between models/repositories,
   HTTPException outside the router, commit/rollback outside the boundary,
   datetime.utcnow, float for energy, and raw secrets. Record the
   command/evidence/limits of testing with a real station.
5. Do not add authorization, remote control, pricing, payment, or debt;
   these items can only be opened up by a separate future planner.
```

## 5. Completion criteria

- Station/EVSE/connector CRUD and topology work correctly.
- The OCPP 2.0.1 gateway receives technical events reliably.
- Sessions are created/updated/ended from station events idempotently.
- Meter/status/event history is stored correctly as a hypertable and can be
  queried.
- Two EVSEs can have two concurrent sessions in the simulator.
- There is no reverse dependency or internal cross-import between the two
  domains.
- Authorization, remote-control business logic, pricing, payment, and debt
  do not appear in the MVP source/migration.

## 6. Reference protocol documentation

- [OCPP 2.0.1 JSON schemas](https://ocpp-spec.org/schemas/v2.0.1/)
- [OCPP 2.x numbering](https://ocpp-spec.org/docs/ocpp_2_0/architecture/numbering/)
- [`python-ocpp`](https://github.com/mobilityhouse/ocpp)
