# Planner: Backend CRUD Telematics (F-G1, F-F2)

> Feature code: F-G1 (Tri-Ring vehicle telematics integration), F-F2 (Device provisioning)
> Status: 🚧 Source implemented; a minimal smoke test exists, full CRUD/integration
> regression tests are still tracked
> in [`backend-automated-tests.md`](./backend-automated-tests.md)
> Created: 2026-07-28

## Required terminology distinction

- **Telematic**: a physical device (TBOX) installed on a vehicle, with its own
  serial, firmware, operating status, and mapping to a vehicle.
- **Telemetry**: the data record/message sent by a Telematic, e.g. position,
  speed, battery, and the time it was measured. Telemetry is not a device and
  a single Telematic can produce many records.

The CRUD in this planner manages only the **Telematic** (physical device). The
`telemetry` domain only handles data messages/records sent by devices over
MQTT and does not own CRUD for the device record.

## Overview

Build a CRUD API for the Telematic device in the `telematics` domain, **similar
to the `vehicles` CRUD**. The API serves record management and manual
provisioning before the device starts sending telemetry messages over MQTT.

Scope:

- Backend FastAPI only, implemented with the same approach and scope level as
  the `backend-crud-vehicles.md` planner, tested via Swagger UI.
- Create, list, get detail, update, and soft delete telematics.
- When creating or updating, the client provides `vehicle_vin`; the backend
  looks up the vehicle by VIN and stores `vehicles.vehicle_id` into
  `telematics.vehicle_id`.
- If the VIN is not found, the device is still created/updated with
  `vehicle_id = NULL`.
- Not included: frontend, a dedicated attach/detach device API, MQTT commands,
  or bulk import.

## Business decisions

1. `telematic_serial` is the device's business identifier and must be unique.
2. `vehicle_vin` is a convenience input for the API and is not duplicated in
   the `telematics` table. The response returns the current VIN by reading it
   from the corresponding vehicle.
3. A VIN that does not exist is not a validation error; the device is created
   in an unassigned state (`vehicle_id = NULL`). An empty/null VIN produces the
   same result.
4. If the VIN is found but the vehicle has been soft deleted, treat it as not
   found and leave `vehicle_id = NULL`.
5. If a valid VIN is already attached to another telematic, return a conflict
   error; do not automatically detach the old device. The unique constraint on
   `telematics.vehicle_id` remains the last line of protection.
6. Updating `vehicle_vin` re-resolves the mapping. Not sending this field means
   keeping the current mapping; sending `null` means detaching the mapping
   within the scope of this CRUD.
7. Delete is a soft delete so as not to break telemetry history; ingestion and
   the list/detail APIs ignore deleted devices by default.

## Domain boundaries and transactions

- CRUD code lives in `backend/app/domains/telematics/`. The `telemetry` domain
  is responsible only for measurement data and MQTT ingestion.
- `telematics.service` is allowed to call public functions of
  `vehicles.service` to look up a vehicle by VIN. It must not import
  `vehicles.repository` or `vehicles.models`.
- `telemetry.service`/ingestion calls the public service of `telematics` to
  resolve `telematic_serial`; it must not import `telematics.repository` or
  `telematics.models`.
- The router handles only HTTP concerns and translates domain exceptions into
  status codes.
- Service/repository must not call `commit()`/`rollback()`; `Depends(get_db)`
  owns the transaction for the request.
- Creating/updating the VIN-to-telematic mapping must be atomic within the
  same transaction.

## Data design

Use the existing `Telematic` model and review/extend it as needed:

- `telematic_id`: UUID primary key.
- `telematic_serial`: `VARCHAR(50)`, unique, not null, indexed.
- `vehicle_id`: UUID nullable, FK to `vehicles.vehicle_id`, `ON DELETE SET NULL`,
  indexed; unique so each vehicle has at most one telematic, while multiple
  NULL values remain valid.
- `status`: `active | inactive | maintenance`.
- `firmware_version`: nullable.
- `created_at`, `updated_at`: timezone-aware UTC.
- `deleted_at`: timezone-aware nullable, used for soft delete.

Do not create a new migration if the current schema already satisfies these
requirements; if `deleted_at` is missing, create a separate migration with
upgrade/downgrade and review its impact on ingestion lookups.

## API contract

Suggested prefix: `/api/v1/telematics`.

- `POST /telematics`
  - Request: `telematic_serial`, `vehicle_vin?`, `status`, `firmware_version?`.
  - Resolve the VIN; a VIN that does not exist still returns `201` with
    `vehicle_id = null`.
  - Duplicate serial: `409`.
  - VIN already attached to another device: `409`.
- `GET /telematics?page=1&page_size=20&status=...`
  - Returns only non-soft-deleted records, with pagination and total.
- `GET /telematics/{telematic_id}`
  - Returns `telematic_id`, serial, status, firmware, `vehicle_id`,
    `vehicle_vin`, timestamps.
- `PATCH /telematics/{telematic_id}`
  - Partial update using `exclude_unset=True`.
  - `vehicle_vin: null` detaches the mapping; a VIN that is not found sets the
    mapping to NULL.
  - `telematic_id` cannot be edited; serial/VIN conflicts are handled as
    domain errors.
- `DELETE /telematics/{telematic_id}`
  - Sets `deleted_at`, does not delete `vehicle_telemetry` history; returns
    `204`.

## Implementation step list

### Step 1: Review the model and migration

- Check the `Telematic` model, FK, unique constraint, timezone handling, and
  `deleted_at`.
- Add a migration only for what is missing; do not modify already-merged
  migrations.
- Confirm `vehicle_id` is nullable and `ON DELETE SET NULL`.

### Step 2: Expose a public lookup service from vehicles

- Add a public function such as `get_active_vehicle_id_by_vin(...)` in
  `vehicles/service.py` or an equivalent existing contract.
- The function should return only the appropriate vehicle identity or `None`,
  with no dependency on FastAPI.
- Do not let telemetry access the internal repository/model of vehicles
  directly.

### Step 3: Implement telematics schemas, repository, and service

- Create the `backend/app/domains/telematics/` package following the same CRUD
  pattern as the `vehicles` domain: `router.py`, `service.py`,
  `repository.py`, `schemas.py`, `models.py`, and `exceptions.py` as needed.
- Create/update the Create, Update, Response, ListResponse schemas and
  examples.
- The repository supports list/count/detail, create, update, soft delete;
  contains no policy logic.
- The service resolves the VIN, checks for conflicts, filters out deleted
  records, and preserves the transaction boundary.
- Update Vietnamese docstrings/comments per CLAUDE.md.

### Step 4: Implement the router and register the API

- Create endpoints with the `telematics` tag and the correct status codes per
  the contract.
- Register the router in `app/api/main.py`.
- Ensure `__init__.py` contains only a module docstring.

### Step 5: Verification and acceptance

- Run Black, isort, Ruff, and mypy via `uv`.
- Minimal smoke test: create with a valid VIN, a nonexistent VIN, a null VIN,
  conflict, update to change/detach the VIN, list/detail, and soft delete.
- Confirm ingestion does not re-resolve the VIN and still receives
  `vehicle_id = NULL` when the device has not been assigned to a vehicle.
- Check the import boundary between `telemetry` and `vehicles` via review/`rg`.

## Completion criteria

- [x] `/api/v1/telematics` CRUD exists in the source and OpenAPI.
- [x] Create/update accept `vehicle_vin` and correctly resolve it to
      `vehicle_id`.
- [x] A nonexistent VIN does not fail the request; the mapping is NULL.
- [x] No direct import of `vehicles.repository`/`vehicles.models` from
      telemetry.
- [x] Soft delete does not lose historical telemetry data, by FK design.
- [ ] Automated regression tests will be completed per
      `backend-automated-tests.md`.

## Out of scope / needs its own planner

- A dedicated attach/detach API with audit and authorization.
- Automatic provisioning from MQTT or syncing serials from devices.
- Bulk import, firmware management, commands, and mapping cache.
