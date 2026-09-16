# Planner: Backend Telemetry Query API

> Feature code: F-A1 (Real-time vehicle telemetry ingestion), F-A5 (Location, trip history & geofencing)
> Status: 🚧 The latest API has been implemented within MVP scope; the
> extended history/map/alert APIs have no source yet
> Created: 2026-07-29

## 1. Goal

Build a read-only HTTP API layer over data already stored in the
`vehicle_telemetry` table. This planner is where future telemetry query APIs
will be added; it does not handle MQTT ingestion, does not write telemetry,
and does not manage telematic device records.

The current active scope only includes the API to get the latest telemetry
record for a vehicle.

## 2. APIs in current scope

### 2.1. Get the latest telemetry for a vehicle

```http
GET /api/v1/telemetry/vehicles/{vehicle_id}/latest
```

Rules:

- Fetch exactly one record with the largest `recorded_at` for the given
  `vehicle_id`.
- `vehicle_id` is the vehicle's internal ID; the license plate or
  `telematic_serial` must not be used as the endpoint identifier.
- Vehicle does not exist or has been soft deleted: return `404` per the
  `vehicles` domain.
- Vehicle exists but has no telemetry yet: return `404` with a clear business
  error.
- Do not infer online/offline status and do not create new data.
- The query must make use of the index on `vehicle_id` and
  `recorded_at DESC`.

The minimal response includes `vehicle_id`, `telematic_serial`,
`recorded_at`, position, speed, heading, SOC, odometer, battery fields, signal,
and any error codes present in the model.

## 3. Implementation boundaries

- The router handles only HTTP concerns and translates domain exceptions into
  status codes.
- The service handles the use case, does not import FastAPI, and does not
  commit/rollback.
- The repository only queries the `VehicleTelemetry` model, with no business
  logic.
- The response schema does not import the SQLAlchemy model.
- Vehicle existence and access checks go through the public service of the
  `vehicles` domain; do not import `vehicles.repository` or `vehicles.models`
  directly.
- Use `Depends(get_db)` as the entry boundary that owns the session and
  transaction.

## 4. Results of the first implementation phase

- A repository query for the latest record by `vehicle_id` and `recorded_at`
  exists.
- A service that checks the vehicle is active via the public service of
  `vehicles` exists.
- A response schema and the `/api/v1/telemetry/vehicles/{vehicle_id}/latest`
  endpoint exist.
- The full history, trip, map, aggregate, alert, and realtime push APIs are
  not yet implemented.
- Automated regression tests have been split out to
  [`backend-automated-tests.md`](./backend-automated-tests.md).

## 5. Extended work breakdown

1. Add a repository query to get the latest record by `vehicle_id`.
2. Add a service function and a domain exception for the no-data case.
3. Add a dedicated response schema for the API.
4. Create `backend/app/domains/telemetry/router.py` if the domain does not
   have a router yet.
5. Register the router in `app.api.main` with the prefix `/api/v1/telemetry`.
6. Add fleet-scope authorization checks once the identity contract is ready.
7. Smoke test: data present, multiple records, no data yet, vehicle does not
   exist, and vehicle has been soft deleted.

## 6. APIs to be added later

The following APIs will be added to this planner once finalized:

- telemetry history over a time range;
- GPS trip history;
- aggregated data by day/shift;
- aggregated status for the dashboard;
- realtime push mechanism if WebSocket or SSE is decided on.
