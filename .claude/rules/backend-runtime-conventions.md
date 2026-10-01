# Backend runtime conventions — transaction, time, layer, worker, logging

> Scope: `backend/` — Python 3.12, FastAPI, async SQLAlchemy, and related
> background workers/entrypoints.
>
> This is the primary source of truth for transactions, database sessions,
> time handling, layer boundaries, background workers, and the checklist for
> finishing a backend task. Naming/DTO/mapping conventions live in
> [`backend-coding-conventions.md`](./backend-coding-conventions.md) — both
> documents apply together.

## Tooling

- Manage the environment/dependencies with **`uv`** (`pyproject.toml` + `uv.lock` are the single source of truth; don't use plain pip/poetry alongside it).
- **Ruff** is the only formatter and linter (line length 88; it replaced Black + isort): `make format` sorts imports and formats, `make lint` runs `ruff check`, `ruff format --check`, `lint-imports` (domain-boundary contracts in `backend/pyproject.toml`) and mypy on `app` and `tests`. `make check` = lint + smoke tests + the domain-model check; the git pre-commit hook (`.githooks/pre-commit`, enabled with `make install-hooks`) runs it on every commit. A Claude Code hook also ruff-formats every Python file the agent edits.
- Type checking: type hints are required on function signatures. **mypy** (strict, configured in `backend/pyproject.toml`) is the gate: `uv run mypy app tests`, run by `make lint`/`make check`. Pyright (the editor or the Claude Code Pyright plugin) is advisory only — when the two disagree, mypy wins.
- Each domain under `backend/app/domains/<domain_name>/` uses the following modules as needed; don't create an empty file as a placeholder:
  - `router.py` — defines endpoints (FastAPI `APIRouter`), contains no business logic.
  - `service.py` — pure Python business logic; this is the **only public interface** other domains call into.
  - `repository.py` — DB queries (SQLAlchemy), contains no business logic.
  - `schemas.py` — Pydantic models for requests/responses.
  - `models.py` — SQLAlchemy models.
  - `types.py` — enums/value objects shared across layers when needed; doesn't depend on FastAPI, Pydantic, or SQLAlchemy.
  - `exceptions.py` — pure Python domain exceptions; the router converts them into the appropriate transport-level error.
- Naming: `snake_case` for variables/functions/modules, `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants.
- All I/O (DB, HTTP, MQTT) uses **async/await**.
- Migrations: **Alembic** (`uv run alembic ...`). During the bootstrap phase
  there is exactly **one** migration, `0001_baseline_schema`, edited in place
  on every schema change and applied with `make db-reset` (clear + rebuild) —
  the full procedure is in [`database.md`](./database.md). Once real data
  must be preserved, the baseline is frozen and every later change becomes a
  new, immutable migration.

## Docstrings and comments

**Docstrings/comments in source code must be complete and explain intent**:

- Docstrings and comments MUST be written in **English**. Any Vietnamese
  text introduced going forward is a rule violation, not a tolerated legacy
  state.
- Every module MUST have a module docstring stating its responsibility, scope, and important limitations.
- Every class MUST have a docstring describing its role, lifecycle if any, and an `Attributes:` list covering all important state.
- Every function/method, including private and simple ones, MUST have a docstring describing its behavior; document `Args:`, `Returns:`, `Raises:`, and side effects where they exist. Complex business service functions must also describe rules, transaction boundaries, and partial-failure behavior.
- A comment is required at non-obvious logic: architectural decisions, race conditions, timeout/shutdown handling, transactions, batch algorithms, and library/framework workarounds. A comment must explain **why**, or the invariant that must be preserved — not restate the code line by line.
- Constants/module-level state with special meaning must have a comment explaining their purpose, ownership, and lifecycle scope.
- When modifying existing logic, update the related docstring/comment in the same change; an incorrect or stale docstring is treated as a bug.
- Don't add redundant comments for an assignment, import, or otherwise self-explanatory code.
- Example:
  ```python
  class VehicleState(BaseModel):
      """
      State of a vehicle reported by its telematic device.
      
      Attributes:
          speed: Speed in km/h (0-200)
          heading: Heading in degrees (0-360, nullable)
          odometer: Total distance traveled (km)
      """
      speed: float | None
      heading: float | None
      odometer: float | None
  
  def to_db_dict(self, telematic_id: UUID, vehicle_id: UUID) -> dict:
      """
      Convert the message into a dict matching the VehicleTelemetry model.
      
      Args:
          telematic_id: Telematic UUID (looked up from telematic_serial)
          vehicle_id: Vehicle UUID (looked up from telematic_id)
          
      Returns:
          Dict with all fields required to insert into the DB
      """
      ...
  ```

## Database session and transaction

- Only the **entry boundary** creates and closes an `AsyncSession`:
  - FastAPI uses `Depends(get_db)`.
  - A background worker/CLI uses `async_session_factory` from `app.libs.db.session`; it never creates its own engine or session factory.
- The entry boundary owns the transaction:
  - The HTTP dependency or the worker's unit-of-work performs the commit/rollback.
  - `service.py` and `repository.py` **never** call `commit()` or `rollback()`.
  - The repository may call `flush()` when it needs to detect a constraint error or retrieve a generated value.
- A business operation runs inside a single atomic transaction by default. If splitting the transaction is needed, it requires confirmation and a clear description of partial-failure behavior.
- `Base` is only defined and imported from `app.libs.db.base`; `session.py` only manages engine/session lifecycle.

## Time convention

- The entire backend and database use UTC, timezone-aware.
- "Now" comes from `app.libs.common.clock.utc_now()` — the single source of the current time in `app/` (ORM column defaults, services, repositories, workers). Never use `datetime.utcnow()`, and don't call `datetime.now(timezone.utc)` directly in app code. Tests and the standalone simulators may build timestamps with `datetime.now(timezone.utc)`.
- SQLAlchemy timestamps use `DateTime(timezone=True)`.
- A timestamp coming from the API/MQTT must carry a timezone and be normalized to UTC before being stored.
- Environment variables must be namespaced by component (`APP_`, `MQTT_`, `DB_`...); don't use a generic name prone to collision like `DEBUG`, `HOST`, or `PORT`.

## Layer boundary and exceptions

- The HTTP layer handles request/response/status codes: routers for the happy path, and `app/api/main.py`'s exception handlers for domain exceptions (one per shared base in `app/libs/common/errors.py`; see `backend-coding-conventions.md` §6).
- The service never imports FastAPI, never raises `HTTPException`, and only contains business logic.
- The repository only accesses the DB; it contains no HTTP/business policy and never commits/rolls back.
- A schema never imports a SQLAlchemy model. Shared enums/value objects live in `types.py`.
- Partial updates use `PATCH` together with `model_dump(exclude_unset=True)`.
- For `VehicleUpdateRequest`, both "field not sent" and "field sent as null" mean "don't update this field"; the service filters out `None` before calling the repository. If a feature genuinely needs to clear a nullable value, it must have its own confirmed contract instead of silently relying on `null`.
- Create/update validation must be consistent. A DB unique constraint is the last line of defense; an `IntegrityError` must be converted into the appropriate domain error.

## Query batching / premature optimization

- **Never write a batched/bulk query version of an operation during the MVP
  phase** — no grouped counts, bulk lookups, `IN (...)` batching to avoid an
  N+1 pattern, or similar. Always write the simple per-item version first
  (one query per row/request is fine), even where an N+1 pattern is obvious
  while writing it.
- Add a batched version only when real throughput needs it, and only after a
  benchmark confirms it — never preemptively "for scale."
- If a batched version already exists, revert it to the simple version and
  record the deferral in `docs/01-requirements/future.md` (per
  `repo-conventions.md`'s "when skipping a component" rule): what the
  batched version would look like, and why it's deferred for now.
- Precedent already applying this: `future.md` items 5 (telematic mapping
  cache), 29 (telematics list N+1), and 34 (charging station directory
  connector count) — all deferred for the same reason.

## Background worker, logging, and error handling

- A single component owns the connect/run/stop lifecycle of an external client.
- Topic, QoS, batch size, queue size, and timeout must come from settings/constructor arguments; don't hard-code them once config exists.
- The telemetry ingestion MVP doesn't use `queue.join()`/`task_done()` because the queue only lives in RAM and backlog data is allowed to be lost when the process exits.
- The telemetry ingestion MVP keeps its queue/task in RAM. On shutdown, cancel and await the worker immediately, roll back any running transaction, and drop any message still in the queue; it doesn't drain the queue.
- The telemetry MVP processes **one message per transaction** (`telemetry/ingestion/message_worker.py`), with QoS 0, no retry, and no DLQ: a DB error rolls back that message's transaction, logs the traceback, and stops the worker (the ingestion process then exits non-zero). Batched ingestion is deferred (`future.md` item 25).
- Use structured logging via `extra`; never use an f-string inside a logger call. Use `logger.exception()` for unexpected exceptions.
- Never use `except Exception` outside a process/task boundary.
- The `processed`, `skipped`, `errors`, `dropped` metrics must be clearly defined and must accurately reflect the outcome.

## Consistency checklist before finishing a backend task

1. Look for a similar pattern already existing in another domain/entrypoint.
2. Don't create a new engine, session factory, config, or logger if a shared implementation already exists.
3. Run `make check` (and `make backend-test-integration` when the schema or a repository query changed). The backend has smoke tests under `backend/tests/<domain>/` (shared helpers in `tests/builders.py` and `tests/fakes.py`) plus PostgreSQL integration tests in `tests/test_postgres_integration.py`, skipped unless `RUN_DB_INTEGRATION=1`; state clearly which test scope was run, and why, if the integration tests weren't run.
4. Check that `__init__.py` only contains a docstring.
5. Don't leave a placeholder/TODO for a component that's definitely needed later — move it to `docs/01-requirements/future.md` instead.
6. Review migrations for timezone handling, FK, index, constraint, PostGIS/TimescaleDB, upgrade, and downgrade correctness.
7. No batched/bulk query written preemptively — see "Query batching / premature optimization" above.
8. If new code needs a different convention, update the relevant convention document (this file, `backend-coding-conventions.md`, or `CLAUDE.md`) or get confirmation before implementing.

## SQLAlchemy Models — Primary Key Convention

- **Every table MUST have an internal ID** (never use a business key like license_plate, VIN as the PK):
  ```python
  # ✅ CORRECT: Internal ID (UUID or auto-increment, depending on the case)
  class VehicleModel(Base):
      __tablename__ = "vehicles"
      
      # Option 1: UUID (fits distributed systems)
      vehicle_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
      
      # Option 2: Auto-increment (fits a single database)
      # vehicle_id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
      
      license_plate: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
      vin: Mapped[str] = mapped_column(String(17), unique=True, nullable=False, index=True)
  
  # ❌ WRONG: using a business key as the PK
  class VehicleModel(Base):
      __tablename__ = "vehicles"
      
      license_plate: Mapped[str] = mapped_column(String(20), primary_key=True)  # NEVER
  ```
- **Choosing UUID vs. Auto-increment:**
  - **UUID**: fits distributed systems; telematic devices may generate IDs from multiple sources without central coordination.
  - **Auto-increment**: fits a single database; higher performance, easier to debug.
  - **Decision**: use UUID for the main tables (vehicles, telematics) since the system may scale into a distributed architecture.
- **Foreign keys** always reference the internal ID:
  ```python
  # ✅ CORRECT
  charging_session.vehicle_id  # → VehicleModel.vehicle_id (UUID or int)
  
  # ❌ WRONG
  charging_session.vehicle_license_plate  # → VehicleModel.license_plate (business key)
  ```
