# Future Components — Resolved Items

> Entries from [future.md](./future.md) that were resolved, completed or
> superseded. Each keeps its original number, so existing references
> ("`future.md` item 9") still identify it. Kept for the decision history;
> nothing here is pending.

---

### 9. PostGIS Geography for telemetry location — Resolved

- **Short description**: Replace the two `latitude`/`longitude` columns with a `geography(Point, 4326)` column, or add a synced geography column.
- **Purpose/role in the system**: Supports spatial indexing, radius queries, geofencing, and efficient trip history.
- **Reason for deferral**: The telemetry MVP planner decided to store coordinates using two `DOUBLE PRECISION` columns to prove out the ingest flow first.
- **Related planner/feature**: `backend-telemetry-ingestion.md` (F-A1, F-A5)
- **Date recorded**: 2026-07-26
- **Additional notes**: Needs a migration of existing data and a decision between geometry and geography before implementation.
- **Resolution (2026-09-17)**: `vehicle_telemetry.location` is now `geography(Point, 4326)` (migration `0006_telemetry_location_geo`), matching `charging_stations.location` (F-C1) — settling the two open decisions this item recorded:
  - **Geography, not geometry** — same choice as `charging_stations`, correct for real-world GPS distance calculations.
  - **No spatial index** — unlike `charging_stations.location`, this column has no GIST index. `vehicle_telemetry` is a high-frequency hypertable write path (every 5-10s per vehicle) and nothing currently runs a spatial query against it (no map/geofence API yet); an index adds real write-side cost for a capability nothing uses yet. Add one later if/when an actual spatial query need shows up.
  - The MQTT wire contract and the HTTP response contract (`latitude`/`longitude`) are unchanged — only internal storage and the read/write mapping code changed. The conversion helpers moved to a shared `app/libs/common/geo.py` (`coordinates_to_location`/`location_to_coordinates`), used by both `charging_stations` and `telemetry` now.

---

### 10. Foreign key from vehicles to fleet — Superseded

- **Short description**: Convert `vehicles.fleet_id` to a UUID internal ID and create a foreign key to the table owned by the fleet domain.
- **Purpose/role in the system**: Ensures the integrity of vehicle-to-fleet assignment and complies with the rule that foreign keys must always reference internal IDs.
- **Reason for deferral**: The fleet domain and table haven't been implemented yet; no relationship placeholder should be added in source before the target model exists.
- **Related planner/feature**: F-A6, F-E1, F-E2, F-E3
- **Date recorded**: 2026-07-26
- **Additional notes**: When fleet is implemented, a migration is needed to convert the current `String(36)` data to UUID and add the constraint.
- **Resolution (2026-09-18)**: Superseded, not implemented as planned. This item predates the
  assignment-history-table pattern established by `drivers`/`driver_vehicle_assignments`
  (2026-09-18). Converting `fleet_id` to a UUID FK on `vehicles` would have forced `vehicles` to
  validate it on write — either a new `vehicles → fleet` edge (a real cycle against the
  unavoidable `fleet → vehicles` edge fleet needs for listing) or relying on the DB constraint as
  primary validation, which `backend-runtime-conventions.md` calls a last line of defense, not the
  real check. It also could never express membership history. Per CLAUDE.md's "most recent
  decision wins" rule, `fleet` instead owns a `fleet_vehicle_memberships` history table (mirroring
  `driver_vehicle_assignments`), and the dead `vehicles.fleet_id` column (`String(36)`, no FK, no
  index, never queried by any code) was dropped entirely in migration `0020_fleet` — per
  repo-conventions' rule that an old placeholder must be removed, not converted, once the real
  component exists. See `docs/02-planners/done/backend-crud-fleet.md`.

---

### 12. Alembic filter for objects managed by PostGIS/TimescaleDB — Resolved

- **Short description**: Add `include_object` to Alembic to skip `spatial_ref_sys` and internal indexes created by TimescaleDB.
- **Purpose/role in the system**: Makes `alembic check` and autogenerate reflect only the schema managed by the application, avoiding migrations that would drop extension-owned objects.
- **Reason for deferral**: The MVP hasn't settled on CI/CD yet and migrations are currently reviewed/run manually; the Alembic head is still correct.
- **Related planner/feature**: `backend-telemetry-ingestion.md` (F-A1), general database configuration.
- **Date recorded**: 2026-07-26
- **Additional notes**: Before enabling `alembic check` in CI or using autogenerate for a new migration, this item must be completed.
- **Resolution (2026-10-01)**: `backend/app/libs/db/migrations/env.py` now passes an `include_object` filter that skips, only when they exist in the database but not in the models, PostGIS's `spatial_ref_sys` table and TimescaleDB's single-column `<table>_<time column>_idx` hypertable indexes. The three `deleted_at` indexes that only the migration declared (drivers, fleets, telematics) are now declared in the models too. `alembic check` (`make db-check`) reports "No new upgrade operations detected" on a database built by `make db-reset`, and still detects real drift (verified by removing an index from a model). Server defaults are not compared (`compare_server_default` stays off). Not in CI, which is still undecided.

---

### 14. Making the `telematics.last_seen_at` update precise — Superseded

- **Short description**: Only update `last_seen_at`, `updated_at`, and the row count when the new timestamp is actually greater than the current value.
- **Purpose/role in the system**: Keeps `updated_at` semantically correct and makes the `telematics_updated` metric/log reflect the number of devices that actually changed.
- **Reason for deferral**: The current discrepancy only affects metadata/logs; it doesn't move `last_seen_at` backward and doesn't block the MVP ingest flow.
- **Related planner/feature**: `backend-telemetry-ingestion.md` (F-A1).
- **Date recorded**: 2026-07-26
- **Additional notes**: Need to consider a suitable batch SQL approach that still keeps a single update per batch.
- **Resolution (2026-10-01)**: Superseded. Commit `f4f718a` ("refactor(telematics): remove last seen metadata") dropped `telematics.last_seen_at` entirely; device liveness is now derived from telemetry rows (`telemetry.service.resolve_last_telemetry_at`, newest `received_at`). There is no column left to update.

---

### 18. API to assign/unassign a telematic device for a vehicle — Completed

- **Short description**: Add the provisioning use case allowing an Admin to
  assign, change, or remove a telematic from a vehicle; telemetry already has
  the `telematics` table, foreign key, and unique constraint, but the vehicles
  domain has no corresponding business API yet.
- **Purpose/role in the system**:
  - Completes the "assign device" part of the Vehicle Management feature.
  - Ensures a vehicle has at most one telematic and prevents a device from
    being assigned incorrectly.
  - Allows operating ingestion without having to insert/update the mapping via
    manual SQL.
  - Has a clear contract for replace/unassign, conflicts, and nonexistent
    device/vehicle.
- **Reason for deferral**: The initial vehicles CRUD MVP excluded
  provisioning from scope; telemetry ingestion currently only needs the
  mapping to exist for lookup, and no Admin workflow for device management has
  been built yet.
- **Related planner/feature**: `backend-crud-vehicles.md`,
  `backend-telemetry-ingestion.md` (F-F2, F-A1).
- **Date recorded**: 2026-07-27
- **Additional notes**: Since the telematic model/repository belongs to the
  telemetry domain, vehicles must not import these internal modules directly.
  Before implementing, decide the router/use-case owner; if vehicles
  orchestrates it, it must call the public API in `telemetry/service.py`. The
  operation must be atomic and must translate unique/FK `IntegrityError` into
  a clear domain conflict.
- **Resolution (2026-10-01)**: Done in the `telematics` domain, which owns the device table (not `telemetry`, as this item assumed, and not `vehicles`): `POST /telematics` with `vehicle_vin` assigns, `PATCH /telematics/{telematic_id}` with a `vehicle_vin` reassigns, and an explicit `vehicle_vin: null` unassigns; the vehicle is resolved through the existing `telematics → vehicles` edge. The contract is now complete: an unknown or soft-deleted device is a 404 (`TelematicNotFoundError`), a VIN matching no live vehicle is a 404 (`TelematicVehicleNotFoundError`, item 83), a vehicle that already has a live device is a 409 (`TelematicConflictError`, checked before the write and translated from the `IntegrityError` at flush), and a soft-deleted device no longer blocks a replacement (item 82). Each call is one transaction. See `docs/02-planners/backend-crud-telematics.md` and `docs/02-planners/done/backend-happy-path-completion.md` (D10).

---

### 24. Clear separation of configuration between `config.py` and `.env` — Completed

- **Short description**: Standardize the boundary between the schema/default configuration in `backend/app/libs/common/config.py` and the environment-specific runtime values in `backend/.env`.
- **Purpose/role in the system**:
  - `config.py` is the source of truth for variable names, data types, validation, default values, and how configuration is accessed via `Settings`.
  - `.env` only holds values that change per environment, such as the database URL, broker connection, credentials, and runtime tuning; it holds no business rules or application logic.
  - All backend source code and Alembic access configuration via `app.libs.common.config.settings`, without calling `os.getenv()` or `load_dotenv()` directly.
- **Reason for deferral**: The initial MVP already had a Pydantic-based `Settings`, but there was still a `DEBUG` variable that didn't match `APP_DEBUG`, an undeclared/unused `CORS_ORIGINS` in `.env.example`, Alembic had its own separate mechanism for reading `.env`, and some queue/batch/MQTT defaults were duplicated in source.
- **Related planner/feature**: Shared backend configuration; `backend/app/libs/common/config.py`, `backend/.env.example`, `backend/app/libs/db/migrations/env.py`, F-A1.
- **Date recorded**: 2026-07-30
- **Additional notes**: `DEBUG` was renamed to `APP_DEBUG`, the unused `CORS_ORIGINS` (no middleware consumed it) was removed, Alembic was switched to use the same `Settings`, the MQTT will and shared pagination policy were moved into configuration, the default password was removed from `config.py`, and the `.env.example` files were standardized. `DATABASE_URL` must now be provided from the environment; the remaining values have safe defaults in `config.py` and can be overridden in `.env`.
- **Date completed**: 2026-07-30

---

### 35. Online/offline status flag for F-A1 — Completed

- **Short description**: A per-vehicle/telematic online/offline status flag, derived from
  telemetry ingestion (e.g. "offline" if no message received within a configurable
  threshold).
- **Purpose/role in the system**: F-A1 names "online/offline flag maintained" as a stated
  constraint — fleet-facing screens need a live status signal beyond just "a latest record
  exists," since a stale latest record still looks like data if nothing marks it stale.
- **Reason for deferral**: Explicitly suspended on 2026-09-17 in favor of shipping schema
  versioning first, then confirmed out of scope under this backend's MVP/POC scope
  decision — no staleness-threshold config, computation (on-read vs. background sweep), or
  API field exists yet. `app/libs/common/config.py`'s
  `CHARGING_OFFLINE_TIMEOUT_SECONDS` (a charging station's derived
  `is_online`) is the precedent an equivalent
  `TELEMETRY_OFFLINE_THRESHOLD_SECONDS` could follow.
- **Related planner/feature**: F-A1, `telemetry` domain.
- **Date recorded**: 2026-09-17
- **Additional notes**: When resuming, decide with the user whether the flag is computed
  on read (compare `received_at`/`recorded_at` to now against a threshold, no new column)
  or maintained by a background job (needs its own state and a definition of "flap"
  handling); on-read is the simpler MVP-consistent default but hasn't been confirmed.
- **Partial resolution (2026-09-17)**: F-J1/F-J3's periodic device-health monitor
  (`telematics/monitoring/`) now computes silence the "background job" way this item
  described, using the same `received_at`-vs-threshold comparison the "on-read" option
  would have used, but it only produces a one-shot `DEVICE_OFFLINE_ALERT` notification per
  silence episode - it does **not** persist a queryable online/offline flag/field anywhere
  (no new column on `vehicles`/`telematics`, no API field). A fleet-facing screen wanting
  "is this vehicle online right now" as a stored, queryable value still has nothing to read
  - only the alert history via `GET /api/v1/notifications`. Resuming this item now means
  deciding whether that's sufficient or a real flag/field is still wanted.
- **Resolution (2026-10-01)**: Built as the "computed on read" option (decision D2 of `docs/02-planners/done/backend-happy-path-completion.md`); nothing is stored. `TELEMETRY_ONLINE_THRESHOLD_SECONDS` (default 300) decides: a vehicle is online while its newest telemetry `received_at` is within it - deliberately much shorter than the 180-minute `TELEMATICS_SILENT_THRESHOLD_MINUTES` that drives the silence alert. The flag is returned by `GET /telemetry/vehicles/{id}/latest` (with `received_at`), `GET /telemetry/fleets/{fleet_id}/vehicles/latest` (F-E1) and every telematics device response (F-J1), all through the public `telemetry.service.resolve_vehicle_live_status`. No flap handling is needed because no state transitions are stored.

---

### 47. Geofencing for F-A5 (boundary config + in/out-of-zone alerts) — Completed (fleet-scoped)

- **Short description**: F-A5's geofencing half - geofence boundary
  configuration (per vehicle/fleet, on `vehicles`) and in/out-of-zone
  entry/exit detection and alerting (on `telemetry`) - is entirely
  deferred. Only the location/history-query half of F-A5 was implemented.
- **Purpose/role in the system**: Lets fleet managers define a geographic
  boundary per vehicle/fleet and get alerted when a vehicle enters or
  exits it - named in F-A5's Output ("in/out-of-zone alerts") and cited as
  supporting the internal vehicle-repossession process.
- **Reason for deferral**: Deferred by explicit request when F-A5's backend
  was scoped, to ship the location-history half first. No geofence
  boundary table, no PostGIS containment query, and no event/notification
  wiring exist yet for this.
- **Related planner/feature**: F-A5, `vehicles`, `telemetry`,
  `docs/02-planners/done/backend-telemetry-query-api.md`.
- **Date recorded**: 2026-09-17
- **Additional notes**: When resuming, decide where geofence boundaries are
  stored (likely a `vehicles`-owned table storing a PostGIS `geography`
  polygon per vehicle/fleet), how entry/exit is detected (a `ST_Contains`/
  `ST_Within` check per incoming telemetry point vs. a periodic batch
  check), and whether in/out-of-zone events reuse the `notifications`
  domain the way F-A2/F-A4 do.
- **Resolution (2026-10-01)**: Built, with an ownership adaptation (decisions D6/D7 of `docs/02-planners/done/backend-happy-path-completion.md`). Boundaries live in a `fleet`-owned `geofences` table (`fleet_id`, `name`, `boundary geography(POLYGON, 4326)`, soft delete; `/fleets/{fleet_id}/geofences` CRUD with a GeoJSON `Polygon`), not on `vehicles`, and apply to the fleet's *current* member vehicles: the designed owner is the customer account (domain-model decision D1), which does not exist yet, so per-vehicle geofences were not built and `account_id` stays a planned column (item 86). Detection runs per incoming reading inside telemetry ingestion (`telemetry/geofencing.py`, same transaction as the insert): the geofences covering the previous and the current reading in the vehicle's current fleet are compared (`ST_Covers` on geography, a boundary point counts as inside, via `fleet.service.list_geofences_containing`) and one `GEOFENCE_ALERT` notification (severity `WARNING`) is raised per geofence entered or left. A vehicle's first reading or a vehicle in no fleet raises nothing. No spatial index: the check always filters by fleet first (`ix_geofences_fleet_id`). New edge `telemetry → fleet`.

---

### 49. Connector-status-aware availability for F-A2/F-D1 — Completed

- **Short description**: F-A2's nearest-operational-station lookup and
  F-D1's nearby-station search both approximate "available" as
  `deleted_at IS NULL AND maintenance_status = OPERATIONAL` — an admin-set
  directory field, not a live signal. F-C2 now gives this backend a real
  per-connector status (`Available`/`Occupied`/`Reserved`/`Unavailable`/
  `Faulted`), but neither query reads it yet.
- **Purpose/role in the system**: A station with every connector
  `Occupied` or `Faulted` still shows up as "available" today - refining
  both queries to also require at least one connector in `Available`
  status would make "available" mean something closer to what a driver
  actually needs.
- **Reason for deferral**: F-C2 was scoped as storing the status column
  only, not wiring it into other domains' queries - that's a second,
  separate change (a join from `charging_stations`/`charging_connectors`
  through `charging_evses`, filtered per station) that wasn't part of the
  F-C2/F-D1 round. Item 37 already flagged this exact resume path before
  F-C2 existed; this item narrows it now that the missing piece (the
  status column) is actually there.
- **Related planner/feature**: F-A2, F-D1, F-C2, item 37,
  `charging_stations/repository.py`.
- **Date recorded**: 2026-09-17
- **Additional notes**: When resuming, add a repository query joining
  `charging_connectors` (`status = 'Available'`) through `charging_evses`
  to `charging_stations`, and decide whether "available" should require
  *any* available connector or a minimum count - a business decision, not
  a technical one.

- **Update 2026-09-24 (decision D4)**: `ChargingConnectorStatus` now has ten values (2.0.1's five plus 1.6J's `Preparing`, `Charging`, `SuspendedEV`, `SuspendedEVSE`, `Finishing`). When this item is built use the busy rule: a connector is free only when `Available`; `Occupied`, `Preparing`, `Charging`, `SuspendedEV`, `SuspendedEVSE` and `Finishing` are busy (`Suspended*` are normal pauses, not faults); `Reserved`, `Unavailable` and `Faulted` are not free either.
- **Resolution (2026-10-01)**: Built (decision D3 of `docs/02-planners/done/backend-happy-path-completion.md`), answering the open business question with "any": a station is available when it is not deleted, `OPERATIONAL` and has at least one active connector whose last reported status is `Available` (the D4 busy rule above). `find_nearest_operational_station` (F-A2) now requires it; the nearby search (F-D1) returns `available_connector_count` and `is_online` per station and filters with `is_available_only=true` (default `false`, so the unfiltered search is unchanged); the station response also carries `available_connector_count`. The charger's `is_online` is deliberately not required - the connector status is the availability signal - so stale statuses after a charger goes offline remain item 76.

---

### 54. Fleet- and vehicle-group-scoped config push (F-J2) — Completed

- **Short description**: F-J2 says "push per vehicle/fleet"; today it is
  one device per HTTP call.
- **Purpose/role in the system**: Ops updating an interval fleet-wide
  today must call the endpoint once per device.
- **Reason for deferral**: Writing a fleet-wide push loop now would be a
  preemptive batched operation - the runtime conventions' "no premature
  batching" rule forbids building this ahead of a demonstrated need (same
  reasoning as items 5, 29, 34).
- **Related planner/feature**: F-J2.
- **Date recorded**: 2026-09-17
- **Additional notes**: A simple per-device loop calling the existing
  endpoint N times is the right shape when this is picked up - only batch
  the underlying publish if a benchmark shows the loop is too slow.
- **Resolution (2026-10-01)**: Built as the simple sequential loop this item asked for (decision D9 of `docs/02-planners/done/backend-happy-path-completion.md`): `POST /telematics/fleets/{fleet_id}/config` sends the single-push body to the device of every current fleet member through the same publish-then-record path, one device at a time, and answers 200 with `published_count`/`skipped_count`/`failed_count` and one result per vehicle. A vehicle that is soft-deleted, has no live device or whose device is not `ACTIVE` is skipped (stricter than the single push, which also accepts `MAINTENANCE`); a failed publish is reported and does not stop the loop; an unknown fleet is a 404. New edge `telematics → fleet`. A fleet is the only vehicle group this backend has, so there is no separate "vehicle group" push. The publish itself is not batched.

---

### 63. Fleet-level rollup and CSV export for F-A6 — Completed

- **Short description**: F-A6's stated output includes a multi-vehicle
  fleet view and CSV export. This round only ships the per-vehicle JSON
  endpoint.
- **Purpose/role in the system**: A fleet manager comparing vehicles or
  exporting a report for offline analysis needs more than one API call
  per vehicle.
- **Reason for deferral**: No `fleet` domain has active source in this
  backend yet (per `directory-structure.md`, a domain isn't created before
  a concrete task needs it), and no CSV export machinery exists anywhere
  in the codebase.
- **Related planner/feature**: F-A6, `docs/02-planners/done/backend-operating-energy-reports.md`.
- **Date recorded**: 2026-09-17
- **Additional notes**: When a `fleet` domain is justified by a concrete
  task, it should call `telemetry.get_vehicle_operating_report` per
  vehicle rather than duplicating the SOC-fold query - the aggregation
  belongs at the fleet layer, not inside `telemetry`.
- **Resolution (2026-10-01)**: Built. CSV: `format=csv` on `GET /telemetry/vehicles/{vehicle_id}/operating-report` (one row per period, or one for the whole window). Rollup: `GET /telemetry/fleets/{fleet_id}/operating-report[?format=csv]` returns one row per current member vehicle and fleet totals, rates recomputed from the summed distance/energy (the CSV ends with a `TOTAL` row). Adaptation: the rollup lives in `telemetry`, not at the fleet layer this item suggested, because `fleet → telemetry` would form a cycle with the `telemetry → fleet` edge geofencing needs (decision D7 of `docs/02-planners/done/backend-happy-path-completion.md`); it still reuses the per-vehicle fold (`repository.get_vehicle_window_summary` + `reports.build_operating_summary`) instead of duplicating the query, one vehicle at a time. The fleet report has no day/week/month breakdown.

---

### 82. A soft-deleted telematic device keeps blocking its vehicle — Completed

- **Short description**: Soft-deleting a telematic device
  (`telematics.service.soft_delete_telematic`) only sets `deleted_at`; the
  row keeps its `vehicle_id`, and `uq_telematics_vehicle_id` is a full
  unique constraint, not one scoped to live rows. Assigning a new device to
  that vehicle (create or update with its VIN) passes the service's
  live-device check (`repository.find_by_vehicle_id` ignores deleted rows)
  and then fails at flush with a `409 TelematicConflictError`.
- **Purpose/role in the system**: Replacing a broken or stolen device is the
  normal provisioning path (F-G1, F-F2); today the vehicle can never get a
  new device once its old one was deleted, short of editing the database.
- **Reason for deferral**: Found during the 2026-10-01 source refinement,
  which was kept behaviour-neutral apart from approved fixes; both
  candidate fixes change the device lifecycle contract and need a decision:
  (1) clear `vehicle_id` on soft delete (loses the "which vehicle did this
  device belong to" history on the row), or (2) replace the constraint with
  a partial unique index `WHERE deleted_at IS NULL`, the pattern
  `driver_vehicle_assignments`/`fleet_vehicle_memberships` already use
  (schema change: model, DBML, baseline migration).
- **Related planner/feature**: `docs/02-planners/backend-crud-telematics.md`,
  F-G1, F-F2; F-E4 in `feature-list.md` already names this as a known
  weakness of the `telematics.vehicle_id` pattern; item 83.
- **Date recorded**: 2026-10-01
- **Additional notes**: Option (2) keeps history and matches
  `database.md`'s open/close-history convention; whichever is chosen, add a
  PostgreSQL integration test (delete device, assign a new one to the same
  vehicle) since the bug only shows at flush time.
- **Resolution (2026-10-01)**: Option (2). `uq_telematics_vehicle_id` was replaced by the partial unique index `uq_telematics_active_vehicle` (`WHERE deleted_at IS NULL`) in the model, the DBML and the baseline migration, so a soft-deleted device keeps its `vehicle_id` as history and no longer blocks a replacement. `update_telematic` gained the same "vehicle already has a live device" pre-check as create. A PostgreSQL integration test covers replace-after-delete (201) and a second live device (409, both the pre-check and the flush-time path). See `docs/02-planners/done/backend-happy-path-completion.md` (phase A).

---

### 83. Telematics create/update silently ignore an unknown VIN — Completed

- **Short description**: `telematics.service.create_telematic` leaves the
  new device unassigned, and `update_telematic` *unassigns* the device,
  when the `vehicle_vin` sent matches no live vehicle, instead of
  rejecting the request (e.g. a 404 like fleet/driver assignment by VIN).
- **Purpose/role in the system**: A typo in the VIN produces a device that
  looks provisioned (201/200) but never maps telemetry to a vehicle;
  ingestion then skips every message from it, and on update an existing
  working assignment is lost.
- **Reason for deferral**: The behaviour is documented in both functions'
  docstrings ("Rule:") and is part of the current API contract; changing it
  is an API change that needs approval, which the 2026-10-01 refinement did
  not include.
- **Related planner/feature**: `docs/02-planners/backend-crud-telematics.md`,
  F-G1, F-F2; item 82.
- **Date recorded**: 2026-10-01
- **Additional notes**: When fixed, keep `vehicle_vin: null` on update as
  the explicit way to unassign, add a `NotFoundError`-based telematics
  exception (mapped centrally to 404 in `app/api/main.py`), and add smoke
  tests for the unknown-VIN case on both create and update.
- **Resolution (2026-10-01)**: Done as described (decision D10 of `docs/02-planners/done/backend-happy-path-completion.md`): a create/update `vehicle_vin` that matches no live vehicle raises `TelematicVehicleNotFoundError` (a `NotFoundError`, mapped to 404 in `app/api/main.py`); an explicit `vehicle_vin: null` on update still unassigns. Smoke tests cover the unknown VIN on create and on update.

---

### 84. Fleet membership of a soft-deleted vehicle cannot be closed — Completed

- **Short description**: `DELETE /fleets/{fleet_id}/vehicles/{vehicle_vin}`
  resolves the VIN through `vehicles.service.resolve_vehicle_reference_by_vin`,
  which excludes soft-deleted vehicles, so once a member vehicle is
  soft-deleted its open membership answers 404 and can never be removed
  through the API. Soft-deleting a vehicle does not close its memberships
  either: `vehicles` does not call `fleet` (the edge is `fleet → vehicles`
  only).
- **Purpose/role in the system**: Such a member stays in
  `GET /fleets/{id}/vehicles` forever (listed with `vin`/`license_plate`/
  `status` = null) and keeps counting toward the fleet's `vehicle_count`.
- **Reason for deferral**: Removal moved from `vehicle_id` to VIN on
  2026-10-01 (approved API change) to match how a vehicle is added; closing
  orphaned memberships needs a decision on who owns it - a lookup that
  includes soft-deleted vehicles, a removal by membership ID, or closing
  memberships when a vehicle is soft-deleted (a new `vehicles → fleet` edge
  would create a cycle, so that would need an event/hook design instead).
- **Related planner/feature**: `docs/02-planners/done/backend-crud-fleet.md`,
  F-E1.
- **Date recorded**: 2026-10-01
- **Additional notes**: `driver_vehicle_assignments` does not have this
  problem: a driver's assignment is closed by driver ID
  (`DELETE /drivers/{id}/assignment`).
- **Resolution (2026-10-01)**: Removal by membership ID (decision D8 of `docs/02-planners/done/backend-happy-path-completion.md`): `DELETE /fleets/{fleet_id}/memberships/{membership_id}` closes an open membership of that fleet, including one whose vehicle was soft-deleted; another fleet's, an already-closed or an unknown membership is a 404. Soft-deleting a vehicle still does not close its memberships automatically (no `vehicles → fleet` edge), so an orphan stays listed until it is closed this way. Covered by smoke tests and the fleet PostgreSQL integration test.

---

### 85. PostgreSQL integration tests for the drivers, fleet and support repositories — Completed

- **Short description**: `make backend-test-integration`
  (`backend/tests/test_postgres_integration.py`) covers the migration,
  the 1.6J transaction-ID sequence, telemetry, an OCPP 1.6J session end to
  end and notifications, but none of the `drivers`, `fleet` or `support`
  repository queries.
- **Purpose/role in the system**: Those domains rely on behaviour only
  PostgreSQL shows: the partial unique indexes that allow one open
  assignment/membership (`WHERE unassigned_at IS NULL` / `WHERE left_at IS
  NULL`), `IntegrityError` → conflict translation at flush, the
  paging/counting of open memberships behind the fleet vehicle list, and
  support's filters and SLA columns. The smoke tests use fakes and cannot
  catch a wrong query or index.
- **Reason for deferral**: Out of scope for the 2026-10-01 refinement,
  which only added integration tests for the bugs it fixed (telemetry
  last-seen, notifications insert/mark-read).
- **Related planner/feature**: `docs/02-planners/backend-automated-tests.md`,
  item 16, F-E4, F-E1, F-I1/F-I2.
- **Date recorded**: 2026-10-01
- **Additional notes**: Follow the existing pattern in
  `test_postgres_integration.py` (temporary database, skipped unless
  `RUN_DB_INTEGRATION=1`).
- **Resolution (2026-10-01)**: Added in commit `f9e1908` (with `docs/02-planners/done/backend-happy-path-completion.md`): `test_postgres_integration.py` now covers drivers (one open assignment per vehicle and per driver through the partial unique indexes, reopen after close, `q`/`vehicle_vin` filters), fleet (one active fleet per vehicle, list filters, the public lookups, geofence CRUD and `ST_Covers` containment on a real polygon, membership close by ID) and support (the `awaiting_response`/`sla_breached`/`channel`/`driver_id`/`category` filters agree with `is_sla_breached`; an SOS raises an `SOS_ALERT` that the filtered, newest-first notification list, the unread count and mark-all-read see). The suite has 18 tests, all passing with `RUN_DB_INTEGRATION=1`.
