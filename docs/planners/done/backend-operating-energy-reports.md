# Planner: SOC-Based Operating & Energy-Usage Reports (F-A6, F-C6)

> Feature codes: F-A6 (Operating performance report), F-C6 (Per-customer
> energy usage)
> Status: ✅ Done (MVP/POC scope); F-A6 (MVP/POC scope, partial for F-C6 — see deferrals)
> Created: 2026-09-17

## 1. Goal

Give a fleet manager a per-vehicle operating report (distance, energy
consumed, kWh/100km, cost, km/day — F-A6) and give billing a per-"customer"
energy figure (F-C6). Both were originally scoped to read from
`charging_sessions`, but that table carries no vehicle linkage at all —
confirmed by grepping the domain and the OCPP ingestion path, which never
reads the `idToken` field that would carry a vehicle/driver identity (it's
absorbed by a catch-all kwarg). Per an explicit user decision, both
features instead compute their energy figures from **SOC deltas in the
vehicle's own telemetry**, assuming telemetry is reported frequently:
F-A6 sums SOC *drops* (energy leaving the pack), F-C6 sums SOC *rises*
(energy entering it) — the same underlying window-function query serves
both.

## 2. Scope decisions

- **Both live in `telemetry`, not a new `fleet` domain or `charging_sessions`.**
  The entire computation is one query over `vehicle_telemetry`, which
  `telemetry` already owns. A `fleet` domain would own no table, no model,
  no repository — just a forwarding shim, which `directory-structure.md`'s
  "don't create an empty domain before a concrete task needs it" rule
  argues against. Placing F-C6 in `charging_sessions` would be actively
  wrong now: not one column of that table is read.
- **"Customer" = one vehicle, one vehicle per customer.** Per the user's
  explicit simplification — there is no customer/owner entity anywhere in
  this backend. F-C6's endpoint is keyed by `vehicle_id`.
- **Battery capacity: a nullable `vehicles.battery_capacity_kwh` column,
  with a documented engineering-default fallback.** Adding the column was
  the user's choice over a fleet-wide-constant-only approach, since pack
  size is a genuine static vehicle attribute. When it's `NULL`,
  `DEFAULT_BATTERY_CAPACITY_KWH = 75.0` is substituted and the response
  sets `is_default_battery_capacity=true` so a consumer never mistakes the
  estimate for a recorded spec.
- **Cost: a hardcoded flat VND/kWh constant**, per the user's earlier
  instruction (same treatment as F-A6's stated "cost formula must be
  configurable" constraint — knowingly unmet this round, recorded in
  `deferred.md`).
- **404 on an unknown/soft-deleted vehicle, not F-C5's zero-result
  behavior.** F-C5's station-energy summary deliberately returns a zero
  result for an unknown station because `charging_sessions` doesn't own
  station existence. Here, `telemetry` already depends on `vehicles` and
  already 404s on its other two endpoints (`/latest`, `/history`), and the
  vehicle lookup is a required input anyway (it's where capacity comes
  from) — not an avoidable extra query.
- **Two endpoints, not one combined response**, even though they share
  one query. Different consumers (fleet dashboard vs. billing), different
  future evolution paths (F-C6 is destined to be re-founded on
  station-metered data; F-A6 on fleet rollups/CSV export) — merging them
  would make either endpoint's evolution a breaking change for the other.
- **Raw sums are always numbers; every derived rate is `None` when
  undefined** (fewer than two telemetry samples, or zero distance) —
  applied uniformly across both endpoints so a fabricated `0.0`/`inf` rate
  never leaks out. `distance_per_day_km` divides by the *requested*
  window, not the observed sample span, so a report over a mostly-silent
  window doesn't rescale into an implausible daily figure.

## 3. What was built

### 3.1 `vehicles` domain

- `models.py`: nullable `battery_capacity_kwh` (Double) — migration
  `0016_vehicle_battery_capacity`, no server default (an existing vehicle
  genuinely has no recorded spec; a default would fabricate one).
- `schemas.py`: exposed on `VehicleCreateRequest`/`VehicleUpdateRequest`/
  `VehicleResponse` via `_VehicleInputFields` (`gt=0, le=1000`). Per the
  existing `VehicleUpdateRequest` PATCH semantics, it can be set/changed
  but not cleared back to `NULL` via a single field.
- `types.py`: `VehicleReference` gained `battery_capacity_kwh: float | None`
  — one cross-domain lookup now serves both "does this vehicle exist" and
  "what's its pack size," avoiding a second query.

### 3.2 `telemetry` domain — the shared aggregate

- `types.py`: `VehicleTelemetryWindowSummary` DTO (discharge/charge sums,
  distance, sample counts, first/last timestamps);
  `DEFAULT_BATTERY_CAPACITY_KWH`, `ENERGY_COST_PER_KWH_VND` — both
  "engineering default, not vendor-confirmed" in the same style as
  `SOH_ALERT_THRESHOLD_PERCENT`.
- `repository.py::get_vehicle_window_summary` — the domain's first
  window-function query. An inner `SELECT` computes `lag(soc)`/
  `lag(odometer)` over `(PARTITION BY vehicle_id ORDER BY recorded_at)`
  within the time-bounded window; an outer aggregate sums
  `GREATEST(COALESCE(delta, 0), 0)` for SOC-drop, SOC-rise, and
  odometer-delta respectively (a window function can't nest inside an
  aggregate in one `SELECT`). Reuses the existing
  `ix_vehicle_telemetry_vehicle_time` index for both the filter and the
  window's sort order. Deliberately not built on
  `get_vehicle_telemetry_history` — its hard `limit` would silently
  truncate a month of frequent telemetry.
- `service.py`: renamed `_normalize_history_bound` → `_normalize_time_bound`
  (now shared by F-A5/F-A6/F-C6). New `_resolve_report_context` (shared
  validation + vehicle resolution + aggregate call), pure `calculate_*`
  helpers (`calculate_energy_kwh`, `calculate_energy_cost_vnd`,
  `calculate_energy_per_100km_kwh`, `calculate_cost_per_km_vnd`,
  `calculate_distance_per_day_km`), `_resolve_battery_capacity`, and the
  two public functions `get_vehicle_operating_report`/
  `get_vehicle_energy_usage_report`.
- `schemas.py`: `VehicleOperatingReportResponse` (F-A6),
  `VehicleEnergyUsageResponse` (F-C6, no cost field — money belongs to a
  future `billing` domain). Both echo the normalized (UTC) window and
  document the accuracy limitations in their own docstrings.
- `router.py`: `GET /vehicles/{vehicle_id}/operating-report`,
  `GET /vehicles/{vehicle_id}/energy-usage`.
- `config.py`: `TELEMETRY_REPORT_MAX_RANGE_DAYS` (31 — its own cap,
  deliberately not reusing F-A5's 7-day `TELEMETRY_HISTORY_MAX_RANGE_DAYS`,
  which bounds a JSON-point-per-row response size; this endpoint returns
  O(1) bytes via a SQL aggregate regardless of window length).

## 4. Verification

- `black`, `isort`, `ruff check`, `mypy .` — all clean.
- Migration `0016` verified via `upgrade head` → `downgrade -1` →
  `upgrade head` against the live local Postgres; `\d vehicles` confirms
  the nullable column.
- Unit tests (`test_service_smoke.py`): the five pure `calculate_*`
  helpers tested directly; the two report functions tested via
  monkeypatched `get_vehicle_window_summary`/
  `resolve_vehicle_reference_by_id`, covering naive-timezone/bad-range/
  over-cap rejection, unknown-vehicle 404, default-vs-recorded battery
  capacity, all-`None` derived rates on an empty or single-sample window,
  "energy without distance" (a parked vehicle draining SOC via HVAC —
  `energy_consumed_kwh > 0` while every rate stays `None`), and UTC-window
  echoing.
- **The window-function SQL itself was verified live, not merely by unit
  test** (no SQLite/in-memory substitute exists for a TimescaleDB
  hypertable + PostGIS NOT NULL column, so a monkeypatched unit test can
  only prove the service's arithmetic, not the query). Seeded a vehicle
  with `battery_capacity_kwh=100.0`, published a hand-crafted SOC series
  (100 → 80 → 60 → 90 → 70) and odometer series (1000 → 1020 → 1020 →
  1060 → 1100) over real MQTT through the running ingestion worker, then
  called both endpoints. Results matched the hand-computed expectation
  exactly: `distance_km=100.0`, `energy_consumed_kwh=60.0` (F-A6, from the
  60-point discharge sum), `energy_charged_kwh=30.0` (F-C6, from the
  30-point charge sum), `energy_per_100km_kwh=60.0`, `distance_per_day_km=50.0`
  (100 km / 2-day window), `cost_per_km_vnd=1800.0`. Also verified: 404 on
  an unknown vehicle, 400 on an inverted range and on a range exceeding
  31 days, and the default-capacity fallback (a vehicle with no recorded
  capacity returns `battery_capacity_kwh=75.0`,
  `is_default_battery_capacity=true`, and an all-zero/all-`None` report
  for an empty window). Test data cleaned up afterward.

## 5. Deferred (see `docs/decisions/deferred.md`, items 60-65)

- **Configurable electricity tariff** (item 60) — replacing the hardcoded
  `ENERGY_COST_PER_KWH_VND`, per F-A6's own unmet constraint.
- **Vendor-confirmed battery capacity / vehicle-model catalog** (item 61)
  — replacing `DEFAULT_BATTERY_CAPACITY_KWH` and the free-text `make`/
  `model` columns.
- **Station-metered per-customer energy + NF-10 reconciliation** (item 62)
  — the "real" F-C6, needing the `charging_sessions` vehicle-linkage this
  round deliberately avoided reopening.
- **Fleet-level rollup and CSV export** (item 63) — the remaining half of
  F-A6's stated output, plus the `fleet` domain itself.
- **A SOC dead-band / current-integration energy method** (item 64) — SOC
  quantization means higher sampling frequency *increases* the over-count
  from sensor dither, which is counter-intuitive and worth fixing properly.
- **A TimescaleDB continuous aggregate for the report window** (item 65)
  — gated on a benchmark showing the raw window-function scan is actually
  too slow; not pursued preemptively.
