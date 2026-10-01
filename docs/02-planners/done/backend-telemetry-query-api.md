# Planner: Backend Telemetry Query API

> Feature code: F-A1 (Real-time vehicle telemetry ingestion), F-A5 (Location, trip history & geofencing)
> Status: ✅ Latest + bounded time-range history done (MVP/POC scope); map,
> aggregate, and alert-push APIs still have no source. Geofencing and true
> trip segmentation are out of scope here — see
> `docs/01-requirements/future.md` items 46, 47.
> Created: 2026-07-29
> Updated: 2026-09-17

## 1. Goal

Build a read-only HTTP API layer over data already stored in the
`vehicle_telemetry` table. This planner is where future telemetry query APIs
will be added; it does not handle MQTT ingestion, does not write telemetry,
and does not manage telematic device records.

The active scope includes the API to get the latest telemetry record for a
vehicle (F-A1), plus a bounded time-range history query for a vehicle (F-A5,
§2.2).

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

### 2.2. Get telemetry history for a vehicle within a time range (F-A5)

```http
GET /api/v1/telemetry/vehicles/{vehicle_id}/history?start_time=...&end_time=...&limit=...
```

Scoped as a time-range location/telemetry history query for trip replay,
not segmented trips - this backend has no trip concept anywhere (see
`docs/01-requirements/future.md` items 38, 46). The frontend draws the
polyline from the returned points; this backend does no trip-boundary
detection.

Rules:

- `vehicle_id` is the vehicle's internal ID, same identifier rule as §2.1.
- `start_time`/`end_time` are both **required** query params and must carry
  a timezone; a naive value is rejected with `400`.
- `end_time` must be strictly after `start_time`, and the span must not
  exceed `settings.TELEMETRY_HISTORY_MAX_RANGE_DAYS` (default 7 days) -
  either violation returns `400`.
- `limit` is optional, defaults to `settings.TELEMETRY_HISTORY_DEFAULT_LIMIT`
  (500) and is clamped to `settings.TELEMETRY_HISTORY_MAX_LIMIT` (2000). No
  offset/page: if a range holds more points than `limit`, the caller
  narrows the time window - this avoids `OFFSET` pagination on a hypertable
  ordered by time.
- Vehicle does not exist or has been soft deleted: return `404`, same
  contract as §2.1.
- Points are ordered by `recorded_at` ascending (oldest first), reusing the
  existing `ix_vehicle_telemetry_vehicle_time` index (a btree index on
  `vehicle_id, recorded_at DESC` can be scanned backwards for an ascending
  range scan - no new index needed).

The response carries `vehicle_id`, `points` (each with the same field set as
§2.1's response minus `vehicle_id`/`telematic_serial`, which are redundant
per point in a single-vehicle history), and `count`. No `total`/`page`
fields, per the no-offset rule above.

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

## 4. Results of the implementation phases

- A repository query for the latest record by `vehicle_id` and `recorded_at`
  exists.
- A service that checks the vehicle is active via the public service of
  `vehicles` exists, for both the latest and history queries.
- A response schema and the `/api/v1/telemetry/vehicles/{vehicle_id}/latest`
  endpoint exist.
- **F-A5 (2026-09-17):** a repository query bounded by `vehicle_id` and a
  `recorded_at` range; `TelemetryInvalidRangeError` for a naive/backwards/
  too-large range; `VehicleTelemetryHistoryPoint`/`VehicleTelemetryHistoryResponse`
  schemas; the `/api/v1/telemetry/vehicles/{vehicle_id}/history` endpoint;
  three new settings (`TELEMETRY_HISTORY_MAX_RANGE_DAYS`,
  `TELEMETRY_HISTORY_DEFAULT_LIMIT`, `TELEMETRY_HISTORY_MAX_LIMIT`). No
  migration - reuses the existing `vehicle_telemetry` table/index.
- The map, aggregate, alert-push, and geofencing APIs are not yet
  implemented (see §6 and `docs/01-requirements/future.md` items 46, 47).
- Automated regression tests have been split out to
  [`backend-automated-tests.md`](../backend-automated-tests.md).

## 5. Extended work breakdown

1. Add a repository query to get the latest record by `vehicle_id`. ✅
2. Add a service function and a domain exception for the no-data case. ✅
3. Add a dedicated response schema for the API. ✅
4. Create `backend/app/domains/telemetry/router.py` if the domain does not
   have a router yet. ✅
5. Register the router in `app.api.main` with the prefix `/api/v1/telemetry`. ✅
6. Add fleet-scope authorization checks once the identity contract is ready.
7. Smoke test: data present, multiple records, no data yet, vehicle does not
   exist, and vehicle has been soft deleted. ✅ (latest); ✅ (history, see
   §2.2 rules) - `vehicles_public_service.resolve_vehicle_reference_by_id`
   and `telemetry_repository.get_vehicle_telemetry_history` are monkeypatched
   in `test_service_smoke.py`.

## 6. Verification (F-A5 history endpoint)

- `black`, `isort`, `ruff check`, `mypy .` - all clean across the backend.
- Unit tests (`test_service_smoke.py`): happy path returns ordered points
  with the right count; vehicle not found; naive `start_time`/`end_time`;
  `end_time <= start_time`; span over `TELEMETRY_HISTORY_MAX_RANGE_DAYS`;
  `limit` above the max is clamped, not rejected. `test_api_smoke.py`
  asserts the new path is registered.
- **Live end-to-end**: ran `telemetry-dev` and the API server against the
  seeded device (`TBOX-SIM-00001`), published 5 telemetry messages 1 minute
  apart over MQTT, then called `GET .../history?start_time=...&end_time=...`
  covering that window. Confirmed: exactly 5 points came back, ordered
  oldest-first, matching what was published; `end_time <= start_time`
  returned `400`; an 8-day span (over the 7-day max) returned `400` with
  "Requested range exceeds the maximum of 7 day(s)"; a naive `start_time`
  returned `400` with "start_time must have a timezone"; an unknown
  `vehicle_id` returned `404`.

## 7. APIs to be added later

The following APIs will be added to this planner once finalized:

- GPS trip history with true trip segmentation (start/end detection,
  idle-gap grouping) - needs F-A9's trip concept first
  (`docs/01-requirements/future.md` item 46);
- aggregated data by day/shift;
- aggregated status for the dashboard;
- realtime push mechanism if WebSocket or SSE is decided on;
- geofencing (boundary config + in/out-of-zone events/alerts) -
  `docs/01-requirements/future.md` item 47.
