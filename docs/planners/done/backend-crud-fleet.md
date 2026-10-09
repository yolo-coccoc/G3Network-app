# Planner: Fleet CRUD & Vehicle Membership (F-E1)

> Feature code: F-E1 (Fleet list & map)
> Status: ✅ Done (MVP/POC scope); F-E2/F-E3/F-A8 remain blocked or deferred; refactored 2026-10-09 (§7)
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
  service) that `deferred.md` item 10 (2026-07-26) planned to convert to a
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
- **One hierarchy level, no vehicle-group nesting.** `deferred.md` item 54
  already names "vehicle-group" as a distinct, unresolved concept from
  fleet; adding a speculative `parent_fleet_id` now would be exactly the
  premature placeholder repo-conventions forbids. *(Superseded by FL-02:
  `parent_fleet_id` was built on 2026-10-09, see §7.)*
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
  (`deferred.md` item 35) — F-E1's list endpoint doesn't fabricate one.
- **F-E2 deferred by explicit decision**, not by blocker — it needs two
  changes outside `fleet`: extracting a DTO from `telemetry`'s
  `get_vehicle_operating_report` (which today returns an HTTP response
  schema, forbidden across a domain boundary by coding-conventions §5.1)
  and a new `notifications` count-by-vehicle function. See `deferred.md`
  item 72.
- **F-E3/F-A8 are hard-blocked, not deferred by choice.**
  `charging_sessions` has no `vehicle_id`/`driver_id` column at all — no
  code change in `fleet` can produce "charging sessions per driver" or
  "per fleet/vehicle" data that doesn't exist upstream (`deferred.md` item
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

## 6. Deferred / blocked (see `docs/decisions/deferred.md`)

- **F-E2** (KPI dashboard) — item 72, deferred by explicit scope decision;
  needs a `telemetry` DTO extraction and a new `notifications`
  count-by-vehicle function.
- **F-E3** (charging & warranty report) and **F-A8** (per-driver
  charging-efficiency report) — hard-blocked on `charging_sessions`
  having no vehicle/driver linkage (`deferred.md` item 62); F-E3 also needs
  the `policy` domain.
- **item 10** (the original `vehicles.fleet_id` → FK plan) — superseded,
  recorded with its resolution in `deferred.md`.
- True utilization rate and a live online/offline vehicle signal for
  F-E1's "status" — neither has honest backing data in this backend yet
  (`deferred.md` items 46, 35).
- CSV/PDF export — no export machinery exists anywhere in this backend;
  not needed until F-E2/F-E3/F-A8 (all deferred) actually require it.

## 7. Later change — 2026-10-09: fleet refactor (FL-08, FL-09)

Sections 1-6 describe the build as of 2026-09-18 and are kept as history.
On 2026-10-09 the domain was brought to the reviewed design (FL-02, FL-08,
FL-09 in [`decision-log.md`](../../decisions/decision-log.md); FLT-01/FLT-02
in the feature catalog), edited into the baseline migration:

- `fleets`: `status`, the `FleetStatus` enum (`fleetstatus`) and
  `ix_fleets_status` dropped, with the `status` field of the fleet
  requests/responses and the `status` filter of `GET /fleets`. `name` and
  `fleet_code` are optional, at least one required (request validation 422;
  check constraint `ck_fleets_name_or_code`); `fleet_code` stays unique
  across all fleets for now. New `parent_fleet_id` (self-FK, `RESTRICT`,
  `ix_fleets_parent_fleet_id`), set on create or by `PATCH` (move) and
  returned in `FleetResponse`: the parent must be a live fleet
  (`FleetParentNotFoundError`, 404), a move under the fleet itself or one
  of its sub-fleets is refused (`FleetHierarchyLoopError`, 400; the service
  walks up from the new parent, one query per level), and deleting a fleet
  with live sub-fleets is refused (`FleetHasSubFleetsError`, 409;
  `repository.count_child_fleets`). Nothing rolls up to a parent fleet yet.
- `fleet_vehicle_memberships`: `membership_id` → `fleet_vehicle_membership_id`
  and `joined_at`/`left_at` → `added_at`/`removed_at`, in the API too
  (`FleetMembershipResponse`, `FleetVehicleResponse.added_at`, and the path
  `DELETE /fleets/{fleet_id}/memberships/{fleet_vehicle_membership_id}`);
  the plain `fleet_id` index dropped (covered by
  `ix_fleet_vehicle_memberships_fleet_time`).
- Not built, waiting for the identity tables: `fleets.organization_id`,
  per-organization fleet-code uniqueness, `added_by`/`removed_by`, the
  fleet change history (`deferred.md` item 97). `PATCH` cannot clear a
  name/code or move a fleet to the top level (`deferred.md` item 98).

Verification (2026-10-09): `make check` passed (566 smoke tests passed, 19
integration tests skipped; domain-model and feature-catalog checks OK);
`make backend-test-integration` 19 passed; `make db-check` reported no new
upgrade operations. New tests:
`test_fleet_create_request_needs_a_name_or_a_code`,
`test_create_fleet_rejects_unknown_parent`,
`test_update_fleet_refuses_a_move_under_its_own_sub_fleet`,
`test_soft_delete_fleet_refuses_a_fleet_with_sub_fleets` and the PostgreSQL
`test_fleet_tree_and_name_or_code_rule_on_postgres`.
