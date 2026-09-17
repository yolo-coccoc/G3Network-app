# Planner: Driver CRUD & Vehicle Assignment (F-E4), F-A9 Suspended

> Feature code: F-E4 (Driver management & assignment); F-A9 (empty-trip
> detection) considered alongside it, suspended this round
> Status: ✅ Done at MVP/POC scope (F-E4); F-A9 stays 📋 Planned, blocked
> Created: 2026-09-18

## 1. Goal

Build this backend's first brand-new domain since the initial baseline:
`drivers`. Cover F-E4's full scope — add/edit drivers, assign/reassign
vehicles, and per-driver activity history — using patterns already
established by `vehicles`/`telematics`. F-A9 (empty-trip detection) was
considered for the same round since it also touches driver identity, but
research showed this backend has no trip concept, no driver auth, and no
per-model consumption curve anywhere, so it was suspended rather than
half-built.

## 2. Scope decisions

- **New domain-boundary edge: `drivers → vehicles`, one-directional.** Via
  `vehicles`' existing public `resolve_vehicle_reference_by_vin`/
  `resolve_vehicle_reference_by_id` — the same shape as the existing
  `telematics → vehicles` edge. `vehicles` never calls back into
  `drivers`. Recorded in `domain-boundaries.md`.
- **"Per-driver activity history" = a real assignment-history table**,
  not a single current-vehicle column. `driver_vehicle_assignments`
  (`assigned_at`/`unassigned_at`, open/close semantics) is a deliberate
  improvement over the pre-existing `telematics.vehicle_id` pattern,
  which has two real bugs: its `UniqueConstraint("vehicle_id")` isn't
  scoped to `deleted_at IS NULL`, so a soft-deleted telematic still
  blocks reassignment; and reassigning never auto-frees the old row
  (`ondelete="SET NULL"` only fires on hard delete, which never happens
  in this backend). Both are avoided here by construction. This was an
  explicit user decision between "assignment history table" and "single
  current-vehicle column" — history table was chosen.
- **One active vehicle per driver, one active driver per vehicle**,
  enforced via **two partial unique indexes** (`WHERE unassigned_at IS
  NULL`) — this backend's first use of a partial index anywhere. This is
  a stated assumption: the PRD doesn't explicitly forbid a driver holding
  two vehicles at once, but "assign/reassign" (singular framing) plus
  normal single-fleet-driver operation makes 1:1 the sensible MVP
  default. `assign_vehicle_to_driver` smoothly reassigns (auto-closes the
  driver's previous active assignment) rather than requiring today's
  telematics-style two-step "unassign, then assign" dance.
- **`ondelete="RESTRICT"` on both `driver_vehicle_assignments` foreign
  keys** (matching `charging_sessions`' pattern, not telematics'
  `SET NULL`) — this is a history table, not a mutable current-state
  pointer, so a driver/vehicle should never disappear out from under its
  own history. Moot in practice since hard delete never happens in this
  backend, only soft-delete.
- **Driver fields are a stated assumption**, since the PRD only says
  "add/edit drivers": `full_name`, `phone_number` (unique),
  `license_number` (unique — the same two-natural-key shape as
  `vehicles`' `license_plate`+`vin`), `status` (`ACTIVE`/`INACTIVE`).
- **Soft-deleting a driver auto-closes any active assignment first**, in
  the same transaction, and sets `status = INACTIVE` (mirrors `vehicles`'
  soft-delete setting `status = DECOMMISSIONED`) — otherwise a deleted
  driver could hold a vehicle hostage against the partial unique index
  forever.
- **`assign_vehicle_to_driver` distinguishes idempotent no-op from
  conflict.** Assigning a vehicle already actively held by *this* driver
  is a no-op returning the existing row. Assigning a vehicle already
  actively held by *another* driver is a hard `DriverAssignmentConflictError`
  — it never silently steals the assignment.
- **Assigning by an unknown VIN raises `DriverVehicleNotFoundError`**,
  unlike telematics' precedent of silently accepting a `None` vehicle
  reference — wrong for an explicit assign action where the caller needs
  to know the VIN didn't resolve.
- **F-A9 suspended entirely, not built at any partial scope.** Research
  confirmed: no `trip_id`/`driver_id`/ignition signal anywhere in
  `vehicle_telemetry`; no authentication anywhere in this backend (zero
  hits for `auth|user_id|current_user|JWT|token` across routers); no
  per-model consumption-curve data. Two existing `future.md` deferrals
  (items 38, 46) already reserve "inventing a trip concept" for whenever
  F-A9 is actually built, warning against a second, conflicting
  definition. Three options were presented (declaration-only endpoint,
  declaration + proxy-inference from telemetry, full trip-model first);
  per explicit user choice, none were built — recorded as `future.md`
  item 67 instead.
- **No auth on any endpoint here** — matches every other domain in this
  backend today; not a gap specific to this feature.

## 3. What was built

### 3.1 New domain `backend/app/domains/drivers/`

Standard module layout (`router.py`/`service.py`/`repository.py`/
`schemas.py`/`models.py`/`types.py`/`exceptions.py`), file-for-file
mirroring `vehicles/` as the CRUD template.

- `types.py`: `DriverStatus` (`ACTIVE`/`INACTIVE`), `DriverReference`
  (frozen dataclass — the standard cross-domain DTO shape, unused by any
  caller yet, established for consistency with every other CRUD domain).
- `models.py`: `DriverModel` (`drivers` table — `driver_id` PK,
  `full_name`, unique `phone_number`, unique `license_number`, `status`,
  `created_at`/`updated_at`/`deleted_at`) and
  `DriverVehicleAssignmentModel` (`driver_vehicle_assignments` table —
  `assignment_id` PK, `driver_id`/`vehicle_id` FKs (`ondelete="RESTRICT"`),
  `assigned_at`, nullable `unassigned_at`, `created_at`/`updated_at`),
  with the two partial unique indexes plus
  `ix_driver_vehicle_assignments_driver_time` (`driver_id`, `assigned_at`)
  for the history-listing query.
- `exceptions.py`: `DriverError`, `DriverNotFoundError`,
  `DriverConflictError` (duplicate phone/license),
  `DriverVehicleNotFoundError` (VIN doesn't resolve),
  `DriverAssignmentConflictError` (vehicle/driver already actively
  assigned elsewhere), `DriverAssignmentNotFoundError` (unassign with
  nothing active).
- `schemas.py`: `_DriverInputFields` (private base), `DriverCreateRequest`,
  `DriverUpdateRequest` (PATCH semantics), `DriverResponse` (enriched with
  `current_vehicle_id`/`current_vehicle_vin`, same idiom as
  `TelematicResponse.vehicle_vin`), `DriverListResponse`,
  `DriverVehicleAssignRequest` (`vehicle_vin: str`, 17 chars),
  `DriverVehicleAssignmentResponse` (`vehicle_vin: str | None` — nullable
  since a historical vehicle might be soft-deleted),
  `DriverAssignmentHistoryResponse`.
- `repository.py`: driver CRUD (`insert`, `get_by_id`,
  `find_by_phone_number`, `find_by_license_number`, `list_all`, `count`,
  `update_fields`, `soft_delete`) mirroring `vehicles/repository.py`
  exactly, plus assignment functions with no existing precedent to copy
  verbatim: `get_active_assignment_by_vehicle`,
  `get_active_assignment_by_driver`, `list_assignments_by_driver`,
  `count_assignments_by_driver`, `insert_assignment`, `close_assignment`.
- `service.py`: standard CRUD (`create_driver`, `get_driver`,
  `list_drivers`, `update_driver`, `soft_delete_driver` — unique-field
  pre-checks → `DriverConflictError`, `IntegrityError` fallback, PATCH's
  "null means don't update" rule); `soft_delete_driver` additionally
  closes any active assignment first. `build_driver_response` (async
  enrichment via `vehicle_service.resolve_vehicle_reference_by_id`).
  `assign_vehicle_to_driver` (fetch driver → resolve VIN → conflict/
  idempotent/auto-close logic described above → insert, wrapped in
  `try/except IntegrityError → DriverAssignmentConflictError`).
  `unassign_vehicle_from_driver` (fetch driver → find active assignment
  or 404 → close it). `list_driver_assignment_history` (verify driver
  exists → list + count → enrich each row's `vehicle_vin`, one lookup per
  row, no batching per the repo's no-premature-batching convention).
  `resolve_driver_reference_by_id` (standard cross-domain resolver).
- `router.py`: 8 endpoints —
  `POST /`, `GET /`, `GET /{driver_id}`, `PATCH /{driver_id}`,
  `DELETE /{driver_id}`, `POST /{driver_id}/assignment`,
  `DELETE /{driver_id}/assignment`, `GET /{driver_id}/assignments`.

### 3.2 Wiring

- `backend/app/api/main.py`: registers `drivers_router` at
  `/api/v1/drivers`.
- `backend/app/libs/db/migrations/env.py`: imports `DriverModel`,
  `DriverVehicleAssignmentModel` so `Base.metadata` picks them up.

### 3.3 Migration `0018_drivers`

`down_revision = "0017_charging_ingest_fields"`. Creates `drivers` and
`driver_vehicle_assignments` with all indexes described above, including
the two partial unique indexes
(`postgresql_where=sa.text("unassigned_at IS NULL")`). `downgrade()`
drops both tables (assignments first, FK order) then the `driverstatus`
enum type.

## 4. Verification

- `black`, `isort`, `ruff check`, `mypy .` — all clean (mypy: "Success: no
  issues found in 106 source files").
- Full unit suite: 154 passed, 2 skipped (the two PostgreSQL integration
  tests, gated by `RUN_DB_INTEGRATION`); 15/15 driver-specific tests
  passing, covering: create success, duplicate-phone conflict, get/list/
  update, soft-delete auto-closing an active assignment; assign succeeds
  and enriches the response; assign to an already-actively-assigned
  vehicle raises `DriverAssignmentConflictError`; assigning a second
  vehicle to a driver who already has one auto-closes the first
  (asserting both the closed and the new row); assigning the same vehicle
  twice is idempotent; an unknown VIN raises `DriverVehicleNotFoundError`;
  unassign succeeds and closes the row; unassign with nothing active
  raises `DriverAssignmentNotFoundError`; history listing enriches each
  row; `get_driver` 404s for an unknown driver.
- Migration `0018` verified via `upgrade head` → `downgrade -1` →
  `upgrade head` against the live local Postgres; both tables and both
  partial unique indexes confirmed present via `\d drivers` and
  `\d driver_vehicle_assignments`.
- **Live end-to-end** against the running API: created 2 vehicles + 2
  drivers; assigned driver A to vehicle 1; attempted assigning driver B
  to vehicle 1 (confirmed 409 `DriverAssignmentConflictError`); confirmed
  enriched `GET /drivers/{A}` showed `current_vehicle_id`/
  `current_vehicle_vin`; reassigned driver A to vehicle 2 (confirmed the
  vehicle-1 assignment auto-closed, a new active row on vehicle 2);
  confirmed `GET /drivers/{A}/assignments` showed both rows (one closed,
  one active); unassigned driver A (204); confirmed a second unassign
  404s (`DriverAssignmentNotFoundError`); confirmed vehicle 2 was free for
  reassignment; soft-deleted a driver holding an active assignment and
  confirmed the assignment was auto-closed (checked via direct `psql`
  query, since the driver becomes invisible via `GET` after soft-delete).
  All test data (`DRVLIC-%` drivers, 4 test VINs) cleaned up via direct
  SQL `DELETE`s afterward; API server stopped.

## 5. Deferred / not built (see `docs/01-requirements/future.md`)

- **F-A9 (empty-trip detection) — item 67.** Entirely unimplemented, not
  even at declaration-only scope. Blocked on this backend having no trip
  concept, no driver identity/auth, and no consumption-curve data.
  Recommended cheapest future starting point: a driver-declared
  empty/loaded flag on an explicit endpoint, deferring automatic
  telemetry-based inference until a real trip model exists.
- No authentication/authorization on any `drivers` endpoint — matches
  every other domain today, not a new gap.
- No fleet/team scoping, no driver-to-multiple-vehicle support (the 1:1
  partial-unique-index assumption), no driver-side app/login — out of
  MVP scope, not part of F-E4's stated requirements.
