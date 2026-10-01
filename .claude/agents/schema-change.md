---
name: schema-change
description: Carry out a database schema change in this repo end to end during the bootstrap phase — SQLAlchemy model, DBML design source, the single baseline migration, database rebuild and verification. Use whenever a table, column, index, constraint, enum value, hypertable or sequence must be added, changed or removed. Give it the change and the feature code.
tools: Read, Grep, Glob, Bash, Edit, Write
model: inherit
---

You make schema changes for this backend. Read `.claude/rules/database.md`
(the procedure and invariants), `.claude/rules/backend-runtime-conventions.md`
and `.claude/skills/domain-model/SKILL.md` before touching anything. The
local database holds no data worth keeping; `make db-reset` wipes it.

## Procedure

1. **Design first** — edit `docs/01-requirements/domain-model/domain-model.dbml`
   (new tables start as `@status planned`; flip to `@status built` and make
   types exact once implemented). Regenerate the views with the skill's
   `generate` command. Never hand-edit a generated view (a hook blocks it).
2. **Model** — change the SQLAlchemy model in `backend/app/domains/<domain>/models.py`
   following the naming/PK/enum/geography conventions. A new model module
   must be imported in `backend/app/libs/db/migrations/env.py`.
3. **Migration** — edit `backend/app/libs/db/migrations/versions/0001_baseline_schema.py`
   in place; never add a revision. Keep the hand-written parts its docstring
   lists (clear step, `charging_ocpp16_transaction_id_seq`, the hypertable
   loop, the server defaults and `deleted_at` indexes the models don't
   declare). For a large change you may regenerate the table section with
   `alembic revision --autogenerate` against an **empty** database that has
   only the extensions (`infra/db/init/01-extensions.sql`), then re-apply the
   hand-written parts and delete the bogus `drop_table('spatial_ref_sys')`.
   A new time-series table that must be a hypertable goes into `_HYPERTABLES`
   and needs its time column in the primary key.
4. **Rebuild** — `make db-reset`.
5. **Verify** — all must pass:
   - `cd backend && uv run alembic check`. On the current baseline it reports
     exactly these known, expected differences — anything **else** is drift
     between the model and the migration that you must fix:
     `remove_table spatial_ref_sys` (PostGIS); `remove_index` for the four
     TimescaleDB time indexes `charging_ocpp_messages_occurred_at_idx`,
     `charging_session_events_event_occurred_at_idx`,
     `charging_session_measurements_sampled_at_idx`,
     `vehicle_telemetry_recorded_at_idx`; and `remove_index` for
     `ix_drivers_deleted_at`, `ix_fleets_deleted_at`, `ix_telematics_deleted_at`
     (declared only in the migration). Update this list in this file if the
     change legitimately alters it.
   - `make check` (includes the domain-model check against the models).
   - `make backend-test-integration` (builds, tears down and rebuilds the
     baseline on a temporary database and checks the hypertables).
6. **Docs** — update `.claude/rules/database.md` only if a rule changed
   (rules, not history); record the rationale for the change in the DBML
   notes and the feature's planner.

## Report

Do not commit. Reply with: files changed, the `alembic check` result (and
whether only the known differences remain), and the results of `make check`
and `make backend-test-integration`.
