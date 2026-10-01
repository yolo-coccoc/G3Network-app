# Database

> Read this file when writing/reviewing a migration or changing the schema.
> Rationale for individual tables, columns and indexes lives in the DBML notes
> (`docs/01-requirements/domain-model/`) and the planners, not here.

## Design source

- **The design source is `docs/01-requirements/domain-model/domain-model.dbml`.** A schema change updates it in the same change (flip the table to `@status built`, make types exact, drop `@planned` from columns that now exist), regenerates the views, and passes the `domain-model` skill's `check`. New tables are designed there as `@status planned` before any migration is written.
- Don't create tables for a future domain (`alerts`, `policy_configs`, ...) before its contract is confirmed.

## Migrations — bootstrap phase (no real data yet)

The schema is **one** Alembic migration:
`backend/app/libs/db/migrations/versions/0001_baseline_schema.py`. There is no
migration history to preserve, so a schema change never adds a revision:

1. Change the SQLAlchemy model(s) and the `.dbml` (see above).
2. Edit `0001_baseline_schema.py` to match: hand-edit it, or regenerate the
   table section with `alembic revision --autogenerate` against an **empty**
   database that has only the extensions, then re-apply the hand-written parts
   listed in its module docstring (clear step, sequence, hypertables, and the
   indexes/server defaults the models don't declare) and delete the bogus
   `drop_table('spatial_ref_sys')` autogenerate emits.
3. Run `make db-reset`: `alembic stamp --purge base` forgets the old revision
   without running a downgrade, then `upgrade head` clears every application
   object and recreates the schema.
4. Run the tests, including `RUN_DB_INTEGRATION=1` for the PostgreSQL suite.

Invariants of the baseline:
- The clear step drops only tables, sequences and enum types in `public` that
  no extension owns. It never drops `alembic_version`, `spatial_ref_sys`, an
  extension or a schema (`backend/tests/test_migrations_smoke.py` guards this).
- `downgrade()` is the same clear step: there is no earlier schema.
- **Never run `make db-reset` (or the baseline) on a database whose data must
  be kept.** When real data first has to survive, freeze the baseline: from
  then on every change is a new, immutable migration and this section is
  rewritten.

General Alembic rules: the revision ID must stay ≤32 characters (the
`alembic_version.version_num` width); the migration is formatted by ruff like
any other file; every model module must be imported in `migrations/env.py` so
autogenerate sees it.

## Platform

- One PostgreSQL 16 instance with `timescaledb`, `postgis` and `uuid-ossp` (init script in `infra/db/init/`).
- **Hypertables** (TimescaleDB, 1-day chunks, time column part of the primary key): `vehicle_telemetry`, `charging_session_events`, `charging_session_measurements`, `charging_ocpp_messages`. Every other table is an ordinary relational table.

## Conventions

- Table names are plural `snake_case`; every table has an internal ID primary key (see `backend-runtime-conventions.md`).
- **Enums** store Python member names by default. An enum that mirrors a protocol (e.g. `chargingconnectorstatus`) uses `values_callable=enum_values` to store the protocol's values (`"Available"`, `"SuspendedEVSE"`) instead. PostgreSQL cannot drop an enum value; widening one is just an edit of the baseline in this phase.
- **Nullable without a server default** when an existing or historical row genuinely has no value — never fabricate one (e.g. `0` for an OCPP `seqNo` would collide with a real value).
- **GPS** is `geography(Point, 4326)`, declared with `Geography(..., spatial_index=False)`. Add an explicit GIST index only when a spatial query needs it (`charging_stations.location` has one; the high-frequency `vehicle_telemetry.location` and the input-only `support_cases.location` deliberately don't). Conversion helpers: `app/libs/common/geo.py`.
- **Open/close history tables** (`driver_vehicle_assignments`, `fleet_vehicle_memberships`) enforce "at most one open row" with a **partial unique index** (`WHERE unassigned_at IS NULL` / `WHERE left_at IS NULL`), so closed history rows are unlimited.
- **Device-reported writes** to a row an administrator also edits (`charging_stations` device/liveness columns) use `UPDATE … SET updated_at = updated_at`, so `updated_at` keeps meaning "last administrator edit".
- **Derived state is computed at read time**, not stored (e.g. a station's `is_online` from `last_seen_at`).
- **Alert-producing features share `notifications`** (JSONB `payload`): add a `notification_type` member and a payload shape, never a new table. `notification_id` (BIGINT) doubles as the poll cursor.
- **Energy** is stored as canonical Wh (unit `Wh`) in `charging_session_measurements`.
- `charging_ocpp_messages.raw_frame` contains RFID `idTag`s: never copy frames into application logs.
