# Planner: Fleet CRUD & Vehicle Membership (F-E1)

> Feature code: F-E1 (Fleet list & map)
> Status: ✅ Done (MVP/POC scope); F-E2/F-E3/F-A8 remain blocked or deferred
> Created: 2026-09-18

## 1. Goal

Build this backend's second brand-new domain, `fleet`, covering F-E1
(fleet list & map — the list/filter half; map rendering is frontend).
F-E2 (KPI dashboard), F-E3 (charging & warranty report), and F-A8
(per-driver charging-efficiency report) were considered alongside it but
are not built this round — F-E2 by explicit scope decision, F-E3/F-A8
because they are hard-blocked on data this backend genuinely doesn't have.

## 2. Scope decisions

- **A fleet-owned membership table, not a converted `vehicles.fleet_id`
  column.** `vehicles.fleet_id` was a dead `String(36)` column (added in
  `0002`, never given a FK or index, never queried by any repository or
  service) that `future.md` item 10 (2026-07-26) planned to convert to a
  UUID foreign key once `fleet` existed. That plan predates the
  assignment-history-table pattern this backend later established via
  `drivers`/`driver_vehicle_assignments`. Converting `fleet_id` in place
  would have forced `vehicles` to validate it on every write — either a
  new `vehicles → fleet` edge (a real cycle against the unavoidable
  `fleet → vehicles` edge fleet needs just to list vehicles) or leaning on
  the DB foreign key as the primary check, which
  `backend-runtime-conventions.md` explicitly calls a last line of
  defense, not the real validation. It also could never express
  membership history. Per CLAUDE.md's "most recent decision wins" rule,
  item 10 is superseded: `fleet` instead owns
  `fleet_vehicle_memberships`, mirroring `driver_vehicle_assignments`'s
  open/close shape, and the dead column is dropped entirely in the same
  migration — repo-conventions requires removing an old placeholder, not
  converting it, once the real component exists.
- **One structural difference from `driver_vehicle_assignments`**: only
  `vehicle_id` gets a partial unique index (`WHERE left_at IS NULL`) — a
  fleet legitimately holds many vehicles at once, so there is no
  equivalent index on `fleet_id`.
- **One hierarchy level, no vehicle-group nesting.** `future.md` item 54
  already names "vehicle-group" as a distinct, unresolved concept from
  fleet; adding a speculative `parent_fleet_id` now would be exactly the
  premature placeholder repo-conventions forbids.
- **A new `VehicleSummary` DTO, not a widened `VehicleReference`.**
  F-E1 needs `license_plate`/`status` alongside `vehicle_id`/`vin`, which
  `VehicleReference` doesn't carry. Rather than widen that existing DTO
  (which would touch its 17 existing test-construction sites and hand
  `telemetry` — `VehicleReference`'s other consumer — fields it never
  reads), `vehicles/types.py` gained a sibling `VehicleSummary` plus
  `resolve_vehicle_summary_by_id`. Coding-conventions §5.1 names `Summary`
  ("summarized data") as its own DTO role distinct from `Reference`
  ("minimal reference to an object") — this is the intended pattern, not
  an invention.
- **F-E1's "status" reports only the vehicle lifecycle enum.** No
  queryable online/offline flag exists anywhere in this backend
  (`future.md` item 35) — F-E1's list endpoint doesn't fabricate one.
- **F-E2 deferred by explicit decision**, not by blocker — it needs two
  changes outside `fleet`: extracting a DTO from `telemetry`'s
  `get_vehicle_operating_report` (which today returns an HTTP response
  schema, forbidden across a domain boundary by coding-conventions §5.1)
  and a new `notifications` count-by-vehicle function. See `future.md`
  item 72.
- **F-E3/F-A8 are hard-blocked, not deferred by choice.**
  `charging_sessions` has no `vehicle_id`/`driver_id` column at all — no
  code change in `fleet` can produce "charging sessions per driver" or
  "per fleet/vehicle" data that doesn't exist upstream (`future.md` item
  62 records the RFID/token-identity question as an unresolved business
  decision). F-E3 additionally needs the `policy` domain, which has no
  source at all.

## 3. What was built

### 3.1 New domain `backend/app/domains/fleet/`

Standard layout mirroring `drivers/` file-for-file.

- `types.py`: `FleetStatus` (ACTIVE, INACTIVE), `FleetReference`.
- `models.py`: `FleetModel` (`fleets` — `fleet_code` unique natural key,
  same role as a driver's `license_number`) and
  `FleetVehicleMembershipModel` (`fleet_vehicle_memberships` —
  `joined_at`/`left_at`, the one partial-unique-index difference from
  `driver_vehicle_assignments` described above, plus
  `ix_fleet_vehicle_memberships_fleet_time`).
- `exceptions.py`: `FleetError`, `FleetNotFoundError`,
  `FleetConflictError`, `FleetVehicleNotFoundError`,
  `FleetMembershipConflictError`, `FleetMembershipNotFoundError`.
- `schemas.py`: `FleetCreateRequest`/`FleetUpdateRequest`/`FleetResponse`
  (+ enriched `vehicle_count`), `FleetListResponse`,
  `FleetVehicleAddRequest` (`vehicle_vin`), `FleetVehicleResponse` (F-E1's
  shape: `vehicle_id`/`vin`/`license_plate`/`status`/`joined_at`),
  `FleetVehicleListResponse`, `FleetMembershipResponse`/
  `FleetMembershipHistoryResponse`.
- `repository.py`: fleet CRUD mirroring `drivers/repository.py`, plus
  membership functions (`get_active_membership_by_vehicle`,
  `list_active_memberships_by_fleet`,
  `list_all_active_memberships_by_fleet` — an unpaginated variant used
  only internally by soft-delete, so a large fleet's membership closure
  can never be silently truncated by a page-size cap,
  `count_active_memberships_by_fleet`, `list_memberships_by_fleet`,
  `count_memberships_by_fleet`, `insert_membership`, `close_membership`).
- `service.py`: standard CRUD, `add_vehicle_to_fleet` (resolve VIN → 404;
  vehicle already in a *different* fleet → 409; same fleet → idempotent
  no-op), `remove_vehicle_from_fleet`, `list_fleet_vehicles` (F-E1,
  per-vehicle `resolve_vehicle_summary_by_id` call, no batching — a
  membership whose vehicle no longer resolves is silently skipped rather
  than raised, since this is a listing, not a single-vehicle lookup),
  `list_fleet_membership_history`, `soft_delete_fleet` (closes every open
  membership first, via the unpaginated repository function),
  `resolve_fleet_reference_by_id`.
- `router.py`: `POST /`, `GET /`, `GET/{fleet_id}`, `PATCH /{fleet_id}`,
  `DELETE /{fleet_id}`, `POST /{fleet_id}/vehicles`,
  `DELETE /{fleet_id}/vehicles/{vehicle_id}`,
  `GET /{fleet_id}/vehicles` (F-E1), `GET /{fleet_id}/memberships`.

### 3.2 Changes to `vehicles`

- `types.py`: new `VehicleSummary` DTO (sibling to `VehicleReference`).
- `service.py`: new `to_vehicle_summary`/`resolve_vehicle_summary_by_id`.
- `models.py`/`schemas.py`: **removed** `fleet_id` (column, docstring
  line, and all three schema fields) — a deliberate API contract break,
  justified by repo-conventions' rule to remove a dead placeholder
  outright rather than convert it.
- Fallout fixed: `simulator/seed_simulator_devices.py` (dropped the
  `fleet_id: None` payload field) and 5 test-fixture sites across
  `test_schema_smoke.py`, `test_service_smoke.py`, and
  `test_postgres_integration.py`.

### 3.3 Wiring

- `backend/app/api/main.py`: registers `fleet_router` at `/api/v1/fleets`.
- `backend/app/libs/db/migrations/env.py`: imports `FleetModel`,
  `FleetVehicleMembershipModel`.

### 3.4 Migration `0020_fleet`

`down_revision = "0019_support_cases"`. Creates `fleets` and
`fleet_vehicle_memberships` (including the partial unique index), then
`op.drop_column("vehicles", "fleet_id")`. **No backfill** — existing
values were opaque non-UUID strings with no `fleets` row to point at.
`downgrade()` re-adds the nullable `String(36)` column but does not
restore data.

## 4. Boundary edges

`fleet → vehicles` only (`resolve_vehicle_reference_by_vin` on
membership-add, `resolve_vehicle_summary_by_id` on F-E1's list).
One-directional; `vehicles` never calls back into `fleet`. No cycle.

## 5. Verification

- `black`, `isort`, `ruff check`, `mypy .` — all clean.
- Unit tests (`test_service_smoke.py`): fleet creation and vehicle_count
  enrichment; duplicate-fleet-code conflict; add-vehicle success with VIN
  enrichment; unknown-VIN rejection; cross-fleet-conflict rejection
  (vehicle already active in another fleet); idempotent re-add to the
  same fleet; remove-vehicle closing the membership; remove rejection
  when no active membership exists; F-E1 listing enriching each row via
  `VehicleSummary`; soft-delete closing every active membership first;
  `get_fleet` 404ing for an unknown ID.
- Migration `0020` verified via `upgrade head` → `downgrade -1` →
  `downgrade -1` → `upgrade head` against the live local Postgres
  (alongside `0019`'s round trip), confirming both tables, the partial
  unique index, and that `vehicles.fleet_id` is actually gone.
- **Live end-to-end** against the running API: created two vehicles and
  two fleets; added both vehicles to fleet A (confirmed `vehicle_count`
  reached 2); confirmed adding a vehicle already in fleet A to fleet B
  409s; confirmed re-adding the same vehicle to fleet A is idempotent
  (same `membership_id`, no duplicate row); confirmed `GET
  /fleets/{id}/vehicles` returns the F-E1 shape (`vin`/`license_plate`/
  `status`/`joined_at`); removed one vehicle (204), confirmed a second
  removal 404s; confirmed membership history showed one closed + one
  active row; soft-deleted fleet A while it still held an active
  membership and confirmed (via direct `psql`) that membership was
  auto-closed; confirmed the freed vehicle could then join fleet B. All
  test data cleaned up afterward; API server stopped.

## 6. Deferred / blocked (see `docs/01-requirements/future.md`)

- **F-E2** (KPI dashboard) — item 72, deferred by explicit scope decision;
  needs a `telemetry` DTO extraction and a new `notifications`
  count-by-vehicle function.
- **F-E3** (charging & warranty report) and **F-A8** (per-driver
  charging-efficiency report) — hard-blocked on `charging_sessions`
  having no vehicle/driver linkage (`future.md` item 62); F-E3 also needs
  the `policy` domain.
- **item 10** (the original `vehicles.fleet_id` → FK plan) — superseded,
  recorded with its resolution in `future.md`.
- True utilization rate and a live online/offline vehicle signal for
  F-E1's "status" — neither has honest backing data in this backend yet
  (`future.md` items 46, 35).
- CSV/PDF export — no export machinery exists anywhere in this backend;
  not needed until F-E2/F-E3/F-A8 (all deferred) actually require it.
