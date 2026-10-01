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
