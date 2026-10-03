# Planner: Minimal automated tests for the current backend

> Feature code: F-A1, F-G2, F-F2, F-A5, F-B2
>
> Status: ✅ Done (MVP scope; closed 2026-10-04 when moved to `done/`).
> Everything left over is deferred in `docs/decisions/deferred.md`; unticked
> acceptance items below were never re-verified. Status before closing:
>
> 🚧 A minimal test suite has been implemented; PostgreSQL integration tests exist
> but are skipped by default unless the environment variable is enabled
>
> Goal: create a small, fast pytest suite that protects the important contracts
> of the current backend. This planner does not aim for high coverage.

## 1. Scope

The current backend consists of the following domains:

- `vehicles`: CRUD and soft delete.
- `telematics`: CRUD, VIN resolution, and device-vehicle mapping.
- `telemetry`: message validation, UTC normalization, latest query, and the ingestion worker.
- `charging_stations`: topology CRUD and OCPP topology resolution.
- `charging_sessions`: lifecycle `Started → Updated/MeterValues → Ended`.
- `charging_stations/ocpp`: parses handshake, timestamp, meter, and boundary into
  `charging_sessions`.
- `app.api.main`: health endpoint and router registration.

Does not test production reliability, retry, duplicate detection, reconnect,
authorization, payment, or UI; these groups are not yet part of the active MVP.

## 2. Principles of simplicity

- Use `pytest` and `pytest-asyncio`, already present in the dev dependency group.
- Prefer unit/smoke tests that do not need PostgreSQL, EMQX, or Docker.
- Do not create a complex fixture framework and do not add new test libraries.
- Each test checks one observable behavior; it does not check implementation detail.
- Database integration is only an optional group that runs when the dev infrastructure is ready.
- The current test suite has 16 unit/smoke tests and 2 PostgreSQL integration tests; the
  integration group is skipped by default.

## 3. Correctly understanding the test count

Tests are designed around business behaviors/flows and observable contracts, not
one test per file or one test per method.

Example with charging:

```text
OCPP/HTTP input → schema/adapter → service → fake repository → result
```

A single test can call multiple methods belonging to the same `Started` or `Ended` flow.
Conversely, a method only needs its own dedicated test when it has an important rule, e.g.
normalizing Wh/kWh, rejecting a timestamp without a timezone, or handling a worker error.

So 10-15 test cases is a count of behavior cases, not 10-15 files or 10-15
methods. Each test should check only one observable outcome; internal helpers
without their own rule do not need a direct test.

## 4. Proposed test structure

> **Update 2026-10-01 — current layout.** The single-file layout below grew to
> a 3,821-line `test_service_smoke.py`, so the suite was reorganized by domain
> (same 265 test functions, nothing added or removed):
>
> - `backend/tests/<domain>/test_<domain>_*_smoke.py` — service, schema and
>   OCPP tests per domain (`vehicles/`, `drivers/`, `fleet/`, `support/`,
>   `telematics/`, `telemetry/`, `notifications/`, `charging_stations/`,
>   `charging_sessions/`, plus `libs/` for shared helpers).
> - `backend/tests/builders.py` — shared in-memory ORM-record/message builders
>   (`build_vehicle_record()`, `fake_db_session()`...).
> - `backend/tests/fakes.py` — shared database test doubles
>   (`FakeSessionFactory`).
> - Top level: cross-cutting tests only — `test_api_smoke.py`,
>   `test_migrations_smoke.py`, `test_postgres_integration.py`.
>
> `tests/` and each domain folder are packages (docstring-only
> `__init__.py`), so helpers are imported as `from tests.builders import ...`.
> The file names in 4.1–4.5 and in the evidence below are historical.

### 4.1. `backend/tests/test_api_smoke.py`

About 2 tests:

1. The application can be created and the OpenAPI schema has `/health`, vehicles, telematics, telemetry,
   charging stations, and charging sessions.
2. The health endpoint returns `status=healthy` and the current version.

Does not call the database.

### 4.2. `backend/tests/test_schema_smoke.py`

About 4 tests:

1. `TelemetryMessage` normalizes a timestamp with a timezone to UTC.
2. `TelemetryMessage` rejects a timestamp without a timezone and a payload out of range.
3. Vehicle/telematic request schemas accept valid data correctly and reject data
   that violates the basic contract.
4. The OCPP meter canonicalizes Wh/kWh to `Decimal` Wh.

### 4.3. `backend/tests/test_service_smoke.py`

About 5 tests, using a fake repository or a small monkeypatch to cover the main
flows across the whole backend:

1. Vehicle create/update produces a valid record via the service.
2. A soft-deleted vehicle no longer appears in the active lookup/list.
3. Telematic resolves `vehicle_vin` correctly to `vehicle_id`; if the VIN does not exist,
   the mapping is `NULL` per contract.
4. Telemetry skips a message whose mapping cannot be resolved and persists a valid message
   exactly once.
5. Charging can run `Started → MeterValues/Updated → Ended`, transitioning the session
   to completed and computing the final meter reading/energy delivered.

Tests do not create a real `AsyncSession` unless necessary; the transaction boundary is checked
by verifying the service does not call `commit()`/`rollback()`.

### 4.4. `backend/tests/test_worker_smoke.py`

About 2 tests:

1. `MessageWorker` can start/stop when the queue is empty.
2. The worker stops and propagates a persistence error per the MVP policy.

### 4.5. `backend/tests/test_migrations_smoke.py`

Initially just 1-2 static tests:

1. Alembic has exactly one head, `0004_create_charging_mvp_schema`.
2. An offline upgrade has all four steps: reset, vehicles/telematics,
   vehicle telemetry, and charging MVP.
3. The reset migration only drops business tables/types in the allowlist, not
   `alembic_version` or the database extensions.

Once a dedicated test database is available, add an upgrade/downgrade/upgrade test on a
temporary database. Do not run a rollback on a dev database that holds real data.

## 5. Definition of done

- `pytest` collects test sources from Git; no more `0 tests` result.
- The test suite runs without needing Docker.
- All test cases in the scope above pass.
- `pytest` is added to the backend check command in the Makefile if it does not change
  the current convention.
- The following runs successfully:

```bash
cd backend
uv run pytest
uv run ruff check .
uv run black --check .
uv run isort --check-only .
uv run mypy .
# Run from backend so the app package can be imported when checking the simulator.
uv run mypy ../simulator
```

- Clearly note which tests need PostgreSQL/TimescaleDB and which run unit-only.

## 6. Implementation order

1. Create API/schema/worker tests that need no infrastructure.
2. Create a minimal fake repository for the telemetry and charging services.
3. Complete the migration reset/baseline, then add static migration checks.
4. Run all static checks and update the README/planner with the actual results.

## 7. Migration reset/baseline plan

### Decision adopted

Since the database is still at the initialization stage, the legacy migration
chain is no longer maintained. All old migrations have been replaced with a short graph:

1. `0001_reset_application_schema`: drops old business tables/types in the
   allowlist, keeping `alembic_version` and extensions.
2. `0002_vehicles_telematics`: creates the two record tables.
3. `0003_create_vehicle_telemetry`: creates the telemetry hypertable.
4. `0004_create_charging_mvp_schema`: creates the six active charging tables, of which
   events and meter values are hypertables.

### How to apply this to an existing local database

Since the old graph has been removed, a database still holding an old revision cannot recognize the
new graph on its own. On a local database where data loss is acceptable:

1. Stop the API/worker connected to the database.
2. Drop the `alembic_version` table or reset it to the `base` state via a confirmed
   database administration action.
3. Run `alembic upgrade head`; migration `0001` will drop the old business schema,
   then the remaining three migrations build the new baseline.
4. Verify the catalog has 9 application tables and 3 hypertables.

Do not run this process on a database with data that must be kept. The rollback of the
new graph is only for verifying structure; the reset is not intended to restore legacy
data.

## 8. Out of scope for this planner

- Does not write tests for the web portal or vehicle app, since these two components have no source yet.
- Does not build an end-to-end OCPP test harness from the start.
- Does not add a coverage tool, snapshot tool, factory library, or Docker test
  framework.

## 9. Implementation results

18 test cases have been created based on behavior/contract, split across 5 files; of these, 16
unit/smoke tests run by default and 2 PostgreSQL integration tests are skipped by default:

- `backend/tests/test_api_smoke.py`: health endpoint and router registration.
- `backend/tests/test_schema_smoke.py`: UTC, GPS validation, request schema, and
  meter conversion.
- `backend/tests/test_service_smoke.py`: vehicle, telematic, telemetry, and
  charging lifecycle.
- `backend/tests/test_worker_smoke.py`: worker start/stop and error propagation.
- `backend/tests/test_migrations_smoke.py`: migration head and reset allowlist.

Local run results:

- `cd backend && uv run pytest`: **16 passed, 2 skipped**.
- Ruff, Black, isort, and strict mypy on the backend: **pass**.
- Tests currently need no PostgreSQL, TimescaleDB, EMQX, or Docker.

Not done in this round: real upgrade/downgrade tests on a temporary database, repository queries
against PostgreSQL, and end-to-end OCPP over WebSocket. These are separate
integration tests that should only be added once an isolated test database is available.
