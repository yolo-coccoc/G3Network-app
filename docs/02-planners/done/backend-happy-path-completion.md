# Planner: Happy-path completion of the current features

> Feature code: F-A1, F-A2, F-A3, F-A4, F-A5, F-A6, F-B2, F-C1, F-C2, F-C5,
> F-C6, F-D1, F-E1, F-E4, F-F2, F-G1, F-G2, F-I1, F-I2, F-J1, F-J2, F-J3
> Status: ✅ Done (MVP/POC, happy path) 2026-10-01 — phases A (schema +
> settings), B (four work packages) and C (fleet views, geofence alerts,
> device health, fleet push), end-to-end run and convention review done;
> what stays out is listed in §2.1 and §6
> Created: 2026-10-01
>
> Inputs: four read-only gap analyses (2026-10-01) of every built/in-progress
> feature against its `feature-list.md` Output; future.md items cited below.

## 1. Goal

Make every feature that already has backend code **complete for its happy
path**: everything its Output asks of the backend that can be built now,
inside existing domains, without vendor/hardware input, a new domain
(identity, billing, policy, trip) or reliability work (retry, dedup,
reconnect, retention, security hardening — explicitly out of scope).

## 2. Scope decisions

The owner asked to proceed without a decision round ("refine all our current
features first, make them complete, happy path"), so the agent chose the
recommended option for each; every row is cheap to revisit.

| # | Decision | Chosen | Alternative (rejected) | Status |
|---|---|---|---|---|
| D1 | Scope | Every (a)-class gap, incl. geofencing, report breakdowns, F-C5 time series, OCPP 2.0.1 Boot/Heartbeat (F-G2 "2.0.1-ready") | Small items only | ✅ Agent choice 2026-10-01 |
| D2 | "Online" vehicle | Newest telemetry `received_at` within `TELEMETRY_ONLINE_THRESHOLD_SECONDS` (300), computed at read time | Reuse the 180-min silence threshold (too coarse for a map) | ✅ Agent choice |
| D3 | "Available" station | ≥1 connector whose last status is `Available` (D4 busy rule), station not deleted and OPERATIONAL; `is_online` NOT required (2.0.1 chargers never stamp liveness) | Also require online; keep maintenance-only | ✅ Agent choice (premise corrected — see the notes below) |
| D4 | Report calendar | Buckets in `APP_REPORT_TIMEZONE` (`Asia/Ho_Chi_Minh`); timestamps stay UTC | UTC buckets | ✅ Agent choice |
| D5 | F-C5 time-slot attribution | Energy-register deltas between consecutive samples, bucketed by sample time | Whole session kWh at `ended_at` (wrong across slot boundaries) | ✅ Agent choice |
| D6 | Geofence owner | **Fleet** (`geofences.fleet_id`; applies to the fleet's current member vehicles). Designed owner is the customer account (D1 of the domain model), which doesn't exist; `account_id` stays a planned column | Per-vehicle geofences; defer until identity | ✅ Agent choice |
| D7 | Fleet-wide telemetry views | Served by telemetry (`/telemetry/fleets/{fleet_id}/...`) using a new one-directional `telemetry → fleet` edge, so geofence checks (also `telemetry → fleet`) don't create a `fleet ↔ telemetry` cycle | Fleet endpoints calling telemetry (cycle) | ✅ Agent choice |
| D8 | Orphaned fleet membership (#84) | `DELETE /fleets/{fleet_id}/memberships/{membership_id}` | Resolve-by-VIN including deleted vehicles | ✅ Agent choice |
| D9 | Fleet-wide config push (#54) | One result per device in the response (`published`/`failed` + reason); devices not ACTIVE are skipped and reported | All-or-nothing | ✅ Agent choice |
| D10 | Unknown VIN on telematics create/update (#83) | 404 `TelematicVehicleNotFoundError`; explicit `vehicle_vin: null` still unassigns | Keep silent unassign | ✅ Agent choice |
| D11 | Devices of a soft-deleted vehicle | Ignored by ingestion mapping and the silence monitor (telematics checks the vehicle via the existing `telematics → vehicles` edge) | Unassign on vehicle delete (needs a `vehicles → telematics` cycle) | ✅ Agent choice |
| D12 | SOS from a hotline call | `SupportSosCreateRequest.channel` (default `IN_APP`); location required only for `IN_APP` | TICKET workaround (wrong SLA) | ✅ Agent choice |

Notes found while building (2026-10-01):

- **D3's stated premise was wrong, the decision stands.** "2.0.1 chargers
  never stamp liveness" is false: the raw message log
  (`ocpp/raw_log.py` → `ocpp_state_service`) stamps `last_seen_at` for every
  inbound frame of *either* protocol, so 2.0.1 stations already reported a
  meaningful `is_online`. `is_online` is still not required for
  "available" — the connector's last status is the availability signal,
  and stale statuses of an offline charger stay `future.md` item 76.
- **D13 of the OCPP planner is reopened** (as D1 scoped it): the 2.0.1
  adapter now handles `BootNotification` (device info and `last_boot_at`
  via `ocpp_state_service.record_charger_boot`, heartbeat interval
  `CHARGING_OCPP_HEARTBEAT_INTERVAL_SECONDS`) and `Heartbeat`; the 2.0.1
  simulator boots first. The rest of 2.0.1 parity (stop reason, `idToken`,
  non-energy measurands) stays `future.md` item 78, so a 2.0.1 session's
  SoC/power summary fields (F-B2) are null.
- **Edges actually added** (`domain-boundaries.md`, commit `7dd982c`):
  `telemetry → fleet`, `telematics → fleet`, `support → notifications`, plus
  new calls on existing edges — `telematics → telemetry`
  (`resolve_vehicle_live_status`), `charging_stations → charging_sessions`
  (`resolve_station_energy_total`, the all-stations energy endpoint is
  served by `charging_stations` because it owns the station directory);
  the docs sync added the remaining ones to the table: `telemetry →
  vehicles` (`resolve_vehicle_summary_by_id`, fleet live positions) and
  `telematics → vehicles` (D11's soft-deleted-vehicle check).
- **Not built inside this round's scope:** the fleet operating report has no
  day/week/month breakdown (only the per-vehicle report does); F-C6
  (`energy-usage`) is unchanged.

### 2.1 Out of scope (deferred, unchanged)

Remote start/stop (D10 of the OCPP planner, #74, #26), session↔vehicle/driver
attribution (#62), fault alerting (#75), power-loss vs signal-loss (#51),
config ack/rollback/thresholds (#52, #53, #59), consuming the device status
topic before the Tri-Ring spec (#10), recipient scoping and push delivery
(#40, #41), trip segmentation (#46, F-A9), customer/billing entity (F-C6),
activation reset on device swap (planner decision kept).

## 3. Design

### 3.1 Domains and new edges

New one-directional edges (rows added to `domain-boundaries.md` when built):
`telemetry → fleet` (fleet member vehicle ids, a vehicle's current fleet,
geofence containment), `telematics → fleet` (member vehicle ids for the
fleet-wide config push), `support → notifications` (SOS alert).

### 3.2 Data model (phase A — done)

- `telematics`: `uq_telematics_vehicle_id` → partial unique index
  `uq_telematics_active_vehicle` (`WHERE deleted_at IS NULL`) — #82.
- `notificationtype`: `SOS_ALERT`, `GEOFENCE_ALERT`.
- `geofences` (fleet domain): `geofence_id`, `fleet_id` → fleets, `name`,
  `boundary geography(POLYGON,4326)`, timestamps, soft delete;
  `ix_geofences_fleet_id`.
- Settings: `TELEMETRY_ONLINE_THRESHOLD_SECONDS`,
  `TELEMETRY_ENERGY_COST_PER_KWH_VND`, `TELEMETRY_SOH_ALERT_THRESHOLD_PERCENT`,
  `TELEMETRY_BATTERY_HEALTH_MAX_RANGE_DAYS`, `APP_REPORT_TIMEZONE`,
  `CHARGING_ENERGY_SERIES_MAX_RANGE_DAYS`; `TELEMETRY_REPORT_MAX_RANGE_DAYS`
  31 → 366.

### 3.3 API / behaviour by work package

**B-telemetry** (F-A1–A6): `/latest` adds `received_at`, `is_online`;
battery alert payload adds station `latitude`/`longitude`; configurable cost
and SOH threshold (settings replace constants); `GET
/telemetry/vehicles/{id}/battery-health` (daily SOH/cycles/estimated
capacity); operating report `granularity=day|week|month` (+ `format=csv`);
public DTO functions for other domains/packages: latest position (lat/lon,
recorded/received, online, signal strength) and per-vehicle operating
summary.

**B-charging** (F-B2, C2, C5, D1, G2): nearby `is_available_only` + response
`is_online`, `available_connector_count`; `find_nearest_operational_station`
requires ≥1 Available connector (D3); `GET
/charging-stations/{id}/connectors` (all guns + connector-0 status);
session detail adds `duration_seconds`, `soc_start_percent`,
`soc_end_percent`, `max_power_kw`; session list filters (`station_id`,
`connector_id`, `status`, time window); station energy series
(`/charging-sessions/stations/{id}/energy/series?granularity=hour|day`) and
all-stations energy; OCPP 2.0.1 BootNotification/Heartbeat handlers.

**B-devices** (F-F2, G1, J1, J3): #83 (D10), D11, `GET
/vehicles?activation_status=`.

**B-people** (F-E1, E4, I1, I2, notifications): fleet list `q`, fleet
vehicles `status`/`q`, `GET /fleets?vehicle_vin=`, #84 (D8), geofence CRUD
`/fleets/{id}/geofences` + public containment function; `GET
/drivers?vehicle_vin=` and `q`; SOS channel (D12) and `SOS_ALERT`; support
filters (`sla_breached`, `awaiting_response`, `category`, `channel`,
`driver_id`); notifications filters (`vehicle_id`, `notification_type`,
`severity`), `GET /notifications/{id}`, newest-first, unread count,
mark-all-read; PostgreSQL integration tests for drivers/fleet/support (#85);
public fleet functions: member vehicle ids, a vehicle's current fleet.

**C-telemetry** (after B): `/telemetry/fleets/{id}/vehicles/latest` (F-E1
location + online), `/telemetry/fleets/{id}/operating-report` (+ CSV),
geofence entry/exit detection in ingestion → `GEOFENCE_ALERT`.

**C-devices** (after B): device health on the telematics API (`last_seen_at`,
`is_silent`, `is_online`, `last_signal_strength_dbm`); fleet-wide config push
(D9).

## 4. Steps

- [x] **Phase A — schema + settings** (2026-10-01): models, baseline,
  DBML (18 built tables), settings; `make db-reset`, `make db-check` clean,
  `make check`, integration 6/6.
- [x] **Phase B** (2026-10-01) — four parallel worktree agents, reviewed,
  merged and gated:
  - B-devices: `a37592c` (#83/D10, #82 update pre-check, D11), `40a1afc`
    (`GET /vehicles?activation_status=`).
  - B-telemetry: `135ec9b` (`/latest` `received_at`/`is_online`, station
    coordinates in the battery alert, settings for tariff and SOH threshold,
    `/battery-health`, operating-report `granularity` + `format=csv`,
    `resolve_vehicle_live_status`/`resolve_vehicle_operating_summary`).
  - B-charging: `6d11f09` (session summary, list filters, energy series),
    `50886d6` (2.0.1 Boot/Heartbeat), `3d41760` (availability D3,
    `/charging-stations/{id}/connectors`, all-stations energy).
  - B-people: `7b7d8cc` (notifications), `112adbd` (SOS channel,
    `SOS_ALERT`, support filters), `433dfab` (driver filters), `0feb9f3`
    (fleet filters, membership close by ID, geofence CRUD, public lookups),
    `f9e1908` (PostgreSQL integration tests, #85).
- [x] **Phase C** (2026-10-01) — `23e8584` (device health on the
  telematics API via `monitoring/silence_rule.py`, fleet-wide config push
  `POST /telematics/fleets/{fleet_id}/config`), `ac78ffc`
  (`/telemetry/fleets/{fleet_id}/vehicles/latest`,
  `/telemetry/fleets/{fleet_id}/operating-report[?format=csv]`, geofence
  entry/exit → `GEOFENCE_ALERT` in ingestion via `telemetry/geofencing.py`),
  `7dd982c` (import-linter contract for `geofencing`, a smoke test that
  fails when a domain module is missing from its contract, new edges in
  `domain-boundaries.md`).
- [ ] **Verify and document** — `e2e-sim`, `convention-reviewer` (running);
  `docs-sync` done 2026-10-01 (feature list, status docs, `future.md`,
  rules — see §5).

## 5. Verification

| Phase | Command | Result |
|---|---|---|
| A | `make db-reset`, `make db-check` | clean ("No new upgrade operations detected") |
| A | `make check`; `RUN_DB_INTEGRATION=1` integration suite | pass; 6/6 |
| B + C | `make check` (ruff, import-linter, mypy, smoke tests, domain-model check) | pass — 553 passed, 18 skipped (the integration tests) at the start of the docs sync; 562 passed, 18 skipped at `c4764f4` (after the review follow-ups `3cb5f40`, `c4764f4`); "18 built tables match the models; views are up to date" |
| B + C | `make backend-test-integration` (`RUN_DB_INTEGRATION=1`) | 18/18 pass (new: replace-after-delete device, battery-health day buckets, report periods incl. a reading at the inclusive end, drivers/fleet/geofence/support/SOS-notification tests) |
| docs | `make check` after the docs sync | pass |

| e2e | `e2e-sim` against the real stack (API, MQTT ingestion, OCPP gateway, PostgreSQL) | pass: geofence ENTER then EXIT alerts over real MQTT; fleet live positions (online, signal); device health (online, not silent); fleet operating report + CSV TOTAL row; fleet-wide config push (1 published); hotline SOS → `SOS_ALERT`; nearby `is_available_only` with `available_connector_count`/`is_online`; station connectors view; 1.6J session summary (SoC 40→95 %, 120 kW); session filters; hourly energy series and all-stations totals; 2.0.1 Boot + session completed; zero ERROR lines in all service logs |
| review | `convention-reviewer` over `37caa93^..HEAD` | no blocker/major; minor keyword-only findings fixed in `c4764f4`; nested-schema naming and parent/child id pairs left (documented exception) |

**Found by the end-to-end run, fixed in `3cb5f40`:** every write endpoint
answered before its transaction committed — FastAPI runs a `yield`
dependency's teardown (our commit) after the response unless it is declared
`scope="function"`. The 2.0.1 seed got 404 creating an EVSE right after
creating its station. All 81 `Depends(get_db)` now use
`scope="function"`; `tests/test_db_session_scope_smoke.py` enforces it.

Docs synced 2026-10-01: `feature-list.md` (all touched features, NF-02,
NF-18), `docs/00-status/overview.md`, `docs/00-status/architecture.md`,
`future.md` (items 18, 35, 47, 49, 54, 63, 82-85 moved to
`future-resolved.md`; dated updates on 33, 37, 50, 60, 72, 76, 78; new item
86), `.claude/rules/` (`directory-structure.md`, `database.md`,
`domain-boundaries.md`, `dev-environment.md`), the `ocpp16-reference`
skill, `README.md`'s scope summary, and a dated note on D13 in
`backend-ocpp16-charger-integration.md`.

## 6. Deferred / follow-ups

Section 2.1; geofence `account_id` once customer accounts exist
(`future.md` item 86, decision D6). Partly addressed items that stay open
with a dated update: `future.md` 33, 37, 50, 60, 72, 76, 78.
