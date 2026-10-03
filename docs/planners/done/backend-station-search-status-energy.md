# Planner: Nearby Station Search, Connector Status, Station Energy (F-D1, F-C2, F-C5)

> Feature code: F-D1 (Charging station map), F-C2 (Real-time connector
> status), F-C5 (Station-level energy output)
> Status: ✅ Done (MVP/POC scope)
> Created: 2026-09-17

## 1. Goal

Three small, independent additions to already-working domains, delivered
together because each is a handful of new functions inside a domain whose
models/service/router (and, for F-C2, OCPP gateway) already exist:

- **F-D1**: generalize F-A2's single-nearest PostGIS lookup into a
  radius-bounded, filtered, paginated station search (`charging_stations`).
- **F-C2**: handle OCPP `StatusNotification` so a connector's live status
  is recorded (`charging_stations`).
- **F-C5**: aggregate a station's delivered energy over a time window
  (`charging_sessions`).

No new domain. Explicitly out of scope this round (see `deferred.md`): F-J1/
F-J3's device-silence alert (needs this repo's first periodic worker),
`drivers`/`policy`/`fleet` (each needs a brand-new domain).

## 2. Scope decisions

- **F-D1's "availability" stays `maintenance_status`-only** — the same
  admin-set approximation F-A2 already uses, not a live occupancy signal.
  F-C2 now gives this backend real per-connector status, but wiring it into
  F-A2's/F-D1's "available" filter is a separate change (`deferred.md` item
  49) — bundling it into this round would have coupled two independently
  useful features together.
- **F-C2 stores OCPP 2.0.1's native status vocabulary** (`Available`/
  `Occupied`/`Reserved`/`Unavailable`/`Faulted`) rather than inventing a
  "Charging" status — F-C2's own PRD wording ("Available / Charging /
  Faulted") doesn't match OCPP 2.0.1, which folds `Preparing`/`Charging`/
  `SuspendedEV`/`Finishing` into `Occupied` (that was OCPP 1.6J's
  vocabulary). A live "currently charging" view would need to join
  `charging_sessions`, not read this column alone.
- **No out-of-order guard on connector status** — in-order OCPP message
  arrival is this MVP's existing assumption everywhere else (`deferred.md`
  item 27); comparing the incoming timestamp against the stored one would
  be a new reliability mechanism, out of scope here.
- **No try/except in the new OCPP handler.** `python-ocpp` itself already
  wraps every handler invocation, logs the traceback, and replies with
  `CALLERROR(InternalError)` without closing the connection — confirmed by
  reading the library, not assumed. The two existing handlers
  (`on_transaction_event`, `on_meter_values`) already rely on this
  de-facto contract; the new `on_status_notification` matches them.
  Standardizing this instead of relying on the framework default is
  `deferred.md` item 31.
- **F-C5 has no max-time-range cap**, unlike F-A5's telemetry history
  query. `charging_sessions` is an ordinary table bounded by session
  volume per station, not time-density like a hypertable — nothing here
  scales the same way, so a cap would guard against a risk that doesn't
  exist yet.
- **F-C5 returns a zero summary for an unknown `station_id`, not a 404.**
  `charging_sessions` doesn't own station existence, and "no sessions in
  this window" is a legitimate report answer regardless of whether the
  station ID happens to be wrong.

## 3. What was built

### 3.1 F-D1 (`charging_stations`)

- `types.py`: none new (reuses `ChargingStationMaintenanceStatus`).
- `repository.py`: `list_nearby_stations`/`count_nearby_stations`, sharing
  a `_nearby_station_conditions()` helper so the two queries can't drift
  apart. `ST_DWithin` (geography column, native meters) for the radius
  filter, `location.distance_centroid()` (`<->` KNN) for nearest-first
  ordering — same GIST index (`ix_charging_stations_location`) F-A2's
  lookup already uses.
- `schemas.py`: `NearbyChargingStationResponse` (driver-facing — drops
  `ocpp_identity`/timestamps, adds `distance_km`),
  `NearbyChargingStationListResponse`.
- `service.py`: `to_nearby_charging_station_response` (pure mapper) and
  `find_nearby_charging_stations` — clamps `radius_km` to
  `(0, CHARGING_STATIONS_NEARBY_MAX_RADIUS_KM]` (new setting, default 200),
  same page/page_size clamping as `list_charging_stations`, same
  documented per-station connector-count N+1 (`deferred.md` item 34).
- `router.py`: `GET /charging-stations/nearby` — registered **before**
  `GET /charging-stations/{station_id}` (right after the existing list
  endpoint) so `/nearby` isn't captured as a `station_id` path parameter.

### 3.2 F-C2 (`charging_stations`)

- `types.py`: `ChargingConnectorStatus` — OCPP 2.0.1's 5 status values.
- `models.py`: local `enum_values()` helper (duplicated — can't import
  `charging_sessions/models.py` across the domain boundary); `status`
  (nullable) and `status_updated_at` (nullable) on `ChargingConnectorModel`.
- `repository.py`/`service.py`: `update_connector_status` at each layer —
  the service raises `ChargingConnectorNotFoundError` if the connector
  isn't active.
- `ocpp/ocpp_server.py`: new `on_status_notification` handler — parses the
  timestamp via the existing `parse_ocpp_timestamp`, resolves topology via
  the existing flat-int `charging_stations_service.resolve_ocpp_topology`
  (StatusNotification's payload is flat `evse_id`/`connector_id` ints,
  unlike TransactionEvent's nested `EVSEType`), converts
  `ChargingConnectorStatus(connector_status)` (the payload arrives as a
  plain string at runtime despite the type annotation — confirmed via the
  `ocpp` package's dispatch), calls the service, ACKs with
  `call_result.StatusNotification()`.
- Migration `0010_charging_connector_status`: creates the
  `chargingconnectorstatus` enum (stores OCPP's string *values*, not
  Python member names — commented in the migration, since it deliberately
  diverges from `maintenance_status`'s existing name-storing convention),
  adds both columns nullable with no default (a default would falsely
  assert every pre-provisioned connector is `Available`).
- `schemas.py`: `status`/`status_updated_at` added to
  `ChargingConnectorResponse` (read-only — not on
  `ChargingConnectorUpdateRequest`, since status is OCPP-owned).
- Corrected 3 module docstrings (`types.py`, `models.py`, `schemas.py`)
  that previously claimed all live OCPP-derived status was out of scope.

### 3.3 F-C5 (`charging_sessions`)

- `repository.py`: `get_station_energy_summary` — one
  `SUM(energy_delivered_wh)`/`COUNT(*)` aggregate query,
  `status = 'completed' AND ended_at BETWEEN start AND end`.
- `service.py`: `get_station_energy_summary` — reuses this domain's
  existing `_utc()` tz-check helper for both bounds, adds an
  `end_time > start_time` check, converts Wh → kWh.
- `schemas.py`: `StationEnergySummaryResponse`.
- `router.py`: `GET /charging-sessions/stations/{station_id}/energy?start_time=&end_time=`.

## 4. Verification

- `black`, `isort`, `ruff check`, `mypy .` — all clean across the backend.
- `alembic upgrade head` / `downgrade -1` / `upgrade head` — clean;
  confirmed `chargingconnectorstatus` enum and both new columns via
  `\d charging_connectors`.
- Unit tests (`test_service_smoke.py`): F-D1 — radius/page/page_size
  clamping, the pure mapper decoding location/distance; F-C2 —
  `ChargingConnectorNotFoundError` when the repository finds nothing to
  update; F-C5 — Wh→kWh conversion, naive-timestamp rejection,
  non-positive-range rejection. `test_api_smoke.py` asserts both new paths
  are registered (and that `/nearby` is reachable, not swallowed by
  `{station_id}`).
- **Live end-to-end**:
  - F-D1: seeded 3 stations (0 km / 1.56 km / ~1,100 km from a query
    point, mixed power/connector-standard/maintenance-status). Confirmed
    nearest-first ordering with correct `distance_km`; `min_power_kw`
    correctly excluded the lower-power station; the `radius_km` cap
    correctly rejected >200 and accepted exactly 200; `connector_standard`
    and `is_operational_only` filters both worked in combination.
  - F-C2: ran the OCPP gateway, published a `StatusNotification` for a
    real connector over a WebSocket client using the `ocpp` package —
    confirmed `status: "Occupied"` and a fresh `status_updated_at` via
    `GET /charging-connectors/{id}`. Published a second `StatusNotification`
    for a *nonexistent* connector — confirmed a `CALLERROR(InternalError)`
    came back, the transaction rolled back (no row created for the bad
    connector), and a subsequent valid call on the *same* WebSocket
    connection still succeeded (matching the documented "no try/except,
    connection stays open" design).
  - F-C5: inserted two completed sessions (500 Wh, 600 Wh) for a test
    station and queried the energy endpoint over a window covering both —
    got exactly `total_energy_kwh: 1.1`, `session_count: 2`. Confirmed
    `400` for a naive timestamp, `400` for `end_time <= start_time`, and a
    zero summary (not `404`) for an unknown `station_id`.
  - Noted in passing, **not fixed** (pre-existing, unrelated to this
    round): the live simulator run for F-C5 hit an existing bug in
    `on_transaction_event`/`on_meter_values`/`extract_meter_samples` —
    `AttributeError: 'dict' object has no attribute ...` — the installed
    `ocpp` package doesn't auto-deserialize `TransactionEvent`/`MeterValues`'
    nested payload into the dataclasses those handlers expect. F-C2's own
    handler is unaffected (its payload is flat, no nested dataclasses), so
    F-C5 was instead verified against directly-inserted session rows. This
    bug blocks the existing charging-session simulator entirely and should
    be triaged separately from this planner's scope.

## 5. Deferred (see `docs/decisions/deferred.md`)

- Item 49 (new): wiring F-C2's per-connector status into F-A2's/F-D1's
  "available" filters.
- Item 31 (existing): standardized OCPP error handling instead of relying
  on the framework's default ACK-and-log behavior.
- Item 27 (existing): out-of-order/duplicate protection for OCPP messages,
  including `StatusNotification`.
- The `ocpp` package's dataclass-deserialization bug found while verifying
  F-C5 (see §4) — pre-existing, affects `TransactionEvent`/`MeterValues`
  only, not recorded as a `deferred.md` item since it's a bug fix, not a
  deferred feature; worth its own triage.
