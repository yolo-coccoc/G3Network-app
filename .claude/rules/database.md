# Database

> Read this file when writing/reviewing a migration or changing the schema.
> Rationale for individual tables, columns and indexes lives in the DBML notes
> (`docs/design/domain-model/`) and the planners, not here.

## Design source

- **The design source is `docs/design/domain-model/domain-model.dbml`.** A schema change updates it in the same change (flip the table to `@status built`, make types exact, drop `@planned` from columns that now exist), regenerates the views, and passes the `domain-model` skill's `check`. New tables are designed there as `@status planned` before any migration is written.
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
   server defaults the models don't declare). `env.py`'s `include_object`
   filter keeps autogenerate away from PostGIS/TimescaleDB-owned objects.
3. Run `make db-reset`: `alembic stamp --purge base` forgets the old revision
   without running a downgrade, then `upgrade head` clears every application
   object and recreates the schema.
4. Run `make db-check` (`alembic check`: must say "No new upgrade operations
   detected" — anything else is drift between the models and the migration),
   then `make check` and `make backend-test-integration`.

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
- **"At most one live row" is a partial unique index**, never a full unique constraint, so closed or soft-deleted rows are unlimited: open/close history tables (`driver_vehicle_assignments`, `fleet_vehicle_memberships`: `WHERE unassigned_at IS NULL` / `WHERE left_at IS NULL`) and soft-deleted rows that keep a reference (`telematics.vehicle_id`, `uq_telematics_active_vehicle`: `WHERE deleted_at IS NULL`, so a deleted device keeps its vehicle as history without blocking a replacement).
- **Area polygons** (`geofences.boundary`) are `geography(POLYGON, 4326)`; containment uses `ST_Covers` (a boundary point is inside). No GIST index while every check filters by an owner column first (`ix_geofences_fleet_id`).
- **Profile vs state (owner decision, 2026-10-03)** — one table answers one business question:
  - The line is **decision vs observation**, not person vs machine. The **profile** table holds what the thing is and the **decisions** about it — made by a person or by a business rule acting for the company (e.g. an automatic suspension after unpaid invoices) — which are rare and need *who* and *why* (change history, `status_reason`). So a **business status** (`organizations.status`, `users.status`, a vehicle's operating status) is profile.
  - Its **state** companion, `<singular>_state` (1:1, keyed by the profile's ID, e.g. `user_state`, `charging_station_state`), holds **observations**: facts recorded by devices or by activity (heartbeats, logins, device-reported status, boot-reported firmware), written often, needing no explanation. No change history.
  - Store only raw facts in a state table (`last_seen_at`, `last_login_at`); anything derivable (`is_online`, "last online") is computed at read time (next bullet).
  - Design first, refactor in bulk: built tables that still mix the two (`charging_stations`, `charging_connectors`, `telematics`) get their target split designed in the DBML during the design review and are refactored together once the design is done.
- **People: user, organization, role, profile (owner decisions, 2026-10-03):**
  - A **user** is the person, one account across every organization (login: phone + password via `user_credentials`; contact). Everyone who uses or receives anything from the system is a user; a message type belongs to a service (feature) and reaches a user when the service is in their role and plan, then on every channel (app, fleet portal, and e-mail when an address is on file) - no per-user channel choice.
  - An **organization** owns the data and decides **data reach**: users of an internal organization (`is_internal`) see every organization; everyone else only their own. Each truck and driver profile has exactly one owning organization; cooperating parties declare the owner themselves.
  - A **role** is a job title = a bundle of features, the same list for every organization; it holds **permissions only** (plus settings of that permission, e.g. a fleet limit in `fleet_user_assignments`). Features = role ∩ plan for customers, the whole role for internal users; internal-only features are never put in any plan. `HEAD_ADMIN`/`CO_ADMIN` exist only in internal organizations; `ORG_ADMIN` manages one organization's own users.
  - A **profile** stores **facts only** about one membership (one person in one organization) in one job (at most one per job type; a person may have several types). A profile table is added only when the system must act on that job's data (rules, searches, reports, alerts); display-only fields go on the shared record. A role that needs a profile requires an active one (`DRIVER` → driver profile with a valid licence); a profile may outlive the role (a former driver's history stays).
  - **One person, several organizations (owner decision, 2026-10-03):** a user is one person with one account; `memberships` (user × organization, `INVITED`/`ACTIVE`/`LOCKED`, `joined_at`/`left_at`) says where they belong. Roles (`user_role_assignments`), fleet limits (`fleet_user_assignments`) and job profiles (`drivers`) point to a **membership**, not to the user; organization-owned rows still carry `organization_id`. After login a person with several organizations picks one, and every query filters by it.
- **Device-reported writes** to a row an administrator also edits (`charging_stations` device/liveness columns) use `UPDATE … SET updated_at = updated_at`, so `updated_at` keeps meaning "last administrator edit". Interim only: this goes away when those columns move to a `_state` table (bullet above).
- **Derived state is computed at read time**, not stored (e.g. a station's `is_online` from `last_seen_at`, a vehicle's or device's `is_online`/`is_silent` from the newest telemetry `received_at`, a station's `available_connector_count`).
- **Alert-producing features share `notifications`** (JSONB `payload`, e.g. `SOS_ALERT`, `GEOFENCE_ALERT`): add a `notification_type` member and a payload shape, never a new table. `notification_id` (BIGINT) doubles as the poll cursor.
- **Energy** is stored as canonical Wh (unit `Wh`) in `charging_session_measurements`.
- **Change history (owner decision, 2026-10-02)** — only for tables whose changes must be audited (e.g. profile details), never for every table:
  - History is decided **per table, on or off** (owner decision, 2026-10-03: deciding column by column was too much work). When on, a change to **any** column writes a history row. The only exception is a short list of excluded columns that a machine updates constantly (e.g. a heartbeat time), which would otherwise flood the history; for most tables the list is empty.
  - A history row is a copy of the **whole old row** — every column of the table, not just the changed or tracked ones — plus when it was replaced and by whom. It is written in the same transaction as the change.
  - Append-only/time-series tables (rows never edited) and tables that are already open/close histories (assignments, memberships) need none.
  - History tables follow a fixed pattern and are **not reviewed one by one** in the design review — only a special case is discussed (a column that changes very often → `@untracked`, or anything unusual). Whether to add a `change_reason` column to every history table is still to be decided.
  - One dedicated history table per tracked table, named `<singular>_history` (e.g. `organization_history`), holding every source column (nullable, no unique constraint) plus `history_id`, `changed_at`, `changed_by`. Filled by a database trigger (`AFTER UPDATE OF <tracked columns> … WHEN` a tracked value differs); the app sets the acting user for the transaction so the trigger can record `changed_by`.
  - In the DBML: the source table's tag line says `@tracked *` (plus `@untracked col1,col2` for the rare excluded columns). The history table is **generated by the `domain-model` tool** from that tag and is never written by hand; it appears in the views numbered `<N>.h`.
- `charging_ocpp_messages.raw_frame` contains RFID `idTag`s: never copy frames into application logs.
