# Feature List — G3 Network Platform (Phase 1)

Source: `G3 Network_Định hướng và Yêu cầu sản phẩm app__PRD__v3.xlsx`, sheets
**"4. P1 - Chức năng"** (functional) and **"5. P1 - Phi chức năng"** (non-functional), v3.0.

This document describes company-wide product intent across every actor
(driver app, fleet & admin portal, CSKH & ops console, background system) —
it is not scoped to any single repository. This repository (`G3Network-app`)
implements **only the backend**, so each feature below also carries this
repo's **backend implementation status** and, where applicable, the
**backend domain** that owns it.

> **Superseded for backend status (2026-10-10).** The refactor-and-build run
> ([plan](../planners/backend-refactor-implementation.md)) rebuilt the backend
> on the reviewed design and renumbered the features. The per-feature backend
> status now lives in the [feature catalog](./features/README.md) (`surfaces.backend`);
> the statuses below are the older F-xx record and are no longer kept current.

## Status legend

**✅ Done** | **🚧 In progress** | **📋 Planned (not started)** | **⚪ N/A
(no backend component)** — a small number of features are pure
frontend/third-party-handoff with no distinct G3 backend logic to build (see
each entry for why); these are marked N/A rather than tracked as ✅/🚧/📋.

**This status reflects backend work only.** A ✅ here means the backend
logic is implemented — it does not mean the end-user experience (mobile app,
web portal, CSKH console) is complete; those live outside this repository.
For a quick, domain-level summary instead of this per-feature detail, see
[`docs/design/architecture.md`](../design/architecture.md). The **Priority ·
Release** field is separate — the PRD's own prioritization (MoSCoW + release
wave — P1.0 = launch day, P1.1 = 90 days later, P1.5 = 2026–27), independent
of build status.

## How this differs from the raw PRD sheet

The PRD's functional sheet (4) mixed three kinds of content in a few rows: pure software
features, platform-wide non-functional qualities (security/compliance) described as if they
were a feature, and internal business process or unresolved commercial policy. This checklist
carries only the functional content per feature. Where a feature also had:

- a genuinely relevant **non-functional requirement linked to more than one feature**, it's
  cross-referenced under each of those features and the full definition stays in the NFR table
  (§5) so nothing is single-sourced. An NFR linked to **exactly one** feature is moved fully
  into that feature's entry instead (still keeping its original `NF-XX` ID) and removed from
  §5's table — see the note above that table;
- an embedded **business/process item**, it's called out under that feature and also collected
  in **"Items needing confirmation"** at the end, cross-referenced to the open-decision number
  (Q#) in the PRD's own decisions log (sheet 14) where one exists.

One row, **F-G4 "Data governance & security,"** wasn't a discrete feature at all — it duplicated
several platform-wide NFRs (encryption, RBAC, audit, retention, Decree 13/2023 compliance). It's
folded into §5 as **NF-22** instead of appearing as a checklist item.

Features are grouped by the surface a user actually interacts with (driver app / fleet & admin
portal / CSKH & ops console / background system), rather than the PRD's own module lettering
(A–K), since that's closer to how a dev team would actually slice sprints. Each entry still
carries its original PRD code so you can trace it back.

---

## 1. Driver mobile app

### F-A2 Tiered battery alerts
- **Actor:** Driver (receives); fleet manager (also receives alerts from the 20% threshold up)
- **Trigger:** Battery SOC crosses 30% (early) / 20% (main) / 10% (critical)
- **Input:** Real-time SOC, GPS location, nearby station availability
- **Output:** Alert with distance to the nearest available station + navigation button
- **Constraints:** Must fire within ≤30s of crossing a threshold; must work while the app is
  backgrounded; de-duplicated to 1 alert per threshold per trip (no spam)
- **Non-functional requirements:** NF-01 (≤30s p95 data latency)
- **Non-functional requirement — NF-03 (Availability/SLA):** Platform uptime ≥99.5%/month;
  battery & station alerts are the top-priority flow.
- **Priority · Release:** Must · P1.0
- **Backend domain:** `telemetry` (threshold detection); `notifications` (storage/delivery)
- **Status:** ✅ Done (MVP/POC scope) — SOC-threshold crossing detection runs inside telemetry
  ingestion; a crossing raises a notification (`notifications` domain) carrying the nearest
  available station, its distance and its coordinates (`station_latitude`/`station_longitude`,
  for a navigation hand-off), queryable via `GET /api/v1/notifications` (filterable by
  `vehicle_id`, `notification_type`, `severity`, newest-first with `order=desc`; unread count and
  mark-all-read exist). Since there is no mobile app in this backend's scope, delivery is
  backend-storage-plus-portal-polling, not a push to a device — the app-level constraints ("must
  work while backgrounded", a navigation button) don't apply here. "1 alert per threshold per
  trip" is approximated as "1 alert per threshold crossing" (previous SOC above, current
  at-or-below), since no trip concept exists yet (F-A9 is still Planned). "Nearest available
  station" (2026-10-01, decision D3 of `docs/planners/done/backend-happy-path-completion.md`) means
  not deleted, `OPERATIONAL`, and at least one connector whose last reported status is
  `Available`; the charger's `is_online` is not required and a stale status after a charger goes
  offline is not invalidated (`deferred.md` item 76). NF-01/NF-03 are not measured — this backend's
  current scope is MVP/POC, not a performance/uptime target (see `deferred.md`).

### F-A9 Empty-trip (deadhead) detection
- **Actor:** Driver (declares load status); System (infers automatically)
- **Trigger:** Driver marks trip status in-app, or the system compares per-km battery
  consumption against the vehicle's load-based consumption curve
- **Input:** Driver-entered load/empty status; per-km energy consumption vs. reference curve
- **Output:** Trip tagged loaded/empty; a flag is raised when the two sources disagree
- **Constraints:** Manual declaration ≤2 taps; automatic inference accuracy target ≥90% vs.
  declared status; both sources are cross-checked
- **Priority · Release:** Should · P1.5
- **Backend domain:** `telemetry` (automatic inference); `drivers` (manual declaration — the domain exists, F-E4; the declaration itself is not built)
- **Status:** 📋 Planned
- Note: empty-km data collected from P1 onward is meant to feed the Phase 2 backhaul
  optimization feature (out of scope here).

### F-B5 Warranty-risk alert
- **Actor:** Driver / vehicle owner
- **Trigger:** Charging behavior detected that puts warranty coverage at risk
- **Input:** Violation/compliance data from F-B3
- **Output:** Real-time alert + periodic summary naming the specific behavior and how to fix it
- **Priority · Release:** Must · P1.0
- **Backend domain:** `policy` (detection — future domain); `notifications` (delivery — future domain)
- **Status:** 📋 Planned

### F-C3 Queue & wait-time estimate
- **Actor:** Driver (views); System (computes)
- **Trigger:** Driver opens a station's detail screen
- **Input:** Actual queue at the station + number of vehicles currently heading toward it
- **Output:** Number waiting, number expected to arrive, ETA until a connector frees up
- **Constraints:** Alerts the driver when predicted wait exceeds a threshold so they can
  re-plan; the underlying load-balancing algorithm itself lives in F-C7
- **Priority · Release:** Should · P1.5
- **Backend domain:** Unassigned — spans `charging_stations`/`telemetry`, tied to F-C7 (see F-C7)
- **Status:** 📋 Planned

### F-C4 Connector reservation
- **Actor:** Driver
- **Trigger:** Driver selects a station and a time slot
- **Input:** station_id, desired reservation window
- **Output:** Connector held for the selected window
- **Constraints:** Needs a configurable no-show penalty
- **Business decisions to finalize:** the no-show penalty amount is a commercial policy value,
  not something the software decides on its own.
- **Priority · Release:** Could · P1.5
- **Backend domain:** `charging_stations`
- **Status:** 📋 Planned — out of the current happy-path OCPP MVP scope (see the decision log, CE-01)

### F-D1 Charging station map
- **Actor:** Driver
- **Trigger:** Driver opens the map
- **Input:** Driver's current location
- **Output:** Nearby stations filtered by availability / power / connector standard
- **Priority · Release:** Must · P1.0
- **Backend domain:** `charging_stations` (nearby-station search/filter query; map rendering itself is frontend)
- **Status:** ✅ Done (MVP/POC scope) — `GET /charging-stations/nearby` returns stations within a
  capped radius (`CHARGING_STATIONS_NEARBY_MAX_RADIUS_KM`, default 200 km), nearest first, filtered
  by `connector_standard` (exact match) and `min_power_kw`, using the same PostGIS `ST_DWithin`/KNN
  approach as F-A2's nearest-station lookup. Each result carries `available_connector_count`
  (connectors whose last reported status is `Available`, computed at read time) and the derived
  `is_online`; `is_available_only=true` keeps only stations that are `OPERATIONAL` with at least
  one available connector (2026-10-01, the same rule as F-A2; `is_online` is informational, not
  required). Not built: invalidating a stale status when a charger goes offline (`deferred.md`
  item 76)

### F-D2 Navigation to station
- **Actor:** Driver
- **Trigger:** Driver selects a station
- **Output:** Turn-by-turn directions to the chosen (available/nearest) station
- **Constraints:** Opens in-app navigation or hands off to Google Maps/VietMap
- **Priority · Release:** Must · P1.0
- **Status:** ⚪ N/A (no backend component) — directions are handed off entirely to a third-party
  map provider; no G3 backend logic involved.

### F-D3 Range-aware warning
- **Actor:** Driver
- **Trigger:** Driver selects a destination station
- **Input:** Current SOC, distance, load, terrain, ambient temperature
- **Output:** Warning if SOC is insufficient to reach the selected station; suggests stations
  within range
- **Constraints:** v3.0 corrects the old linear SOC estimate for load/terrain/temperature using
  the F-A7 forecasting model
- **Non-functional requirements:** NF-20 (inherits the ≤10%/≤15% forecast-error target from the
  F-A7 model it depends on)
- **Priority · Release:** Should · P1.1
- **Backend domain:** `telemetry` (threshold check + suggestion list, built on F-A7)
- **Status:** 📋 Planned

### F-D4 Driver mobile app (iOS & Android)
- **Actor:** Driver
- **Trigger:** App launch / login
- **Output:** Vehicle & battery status, alerts, history, station search/navigation, charging
  payment, customer support — the app shell hosting the other driver-facing features above
- **Constraints:** Vietnamese-language UI; must be usable outdoors; per-driver login
- **Non-functional requirement — NF-12 (UX):** Outdoor-usable, Vietnamese-language driver UI —
  large text, high contrast, one-handed use; core actions ≤3 taps.
- **Non-functional requirement — NF-13 (Compatibility):** Android 10+ / iOS 15+ (most drivers on
  mid-range Android). NF-13's browser-compatibility clause (Chrome/Edge/Safari) applies to the
  web portal, which has no separate FR entry yet — kept here since NF-13 links to no other
  feature.
- **Priority · Release:** Must · P1.0
- **Status:** ⚪ N/A (no backend component) — this is an app-shell/bundling label, not a
  discrete feature with its own backend deliverable; it hosts the other driver-app features
  above, each of which is tracked (and backed) separately. Worth splitting into smaller
  shell/navigation tickets during grooming rather than estimating as one.

### F-D5 Offline mode
- **Actor:** Driver, in low-signal areas
- **Trigger:** Device loses network connectivity
- **Input:** Locally cached SOC/status, previously downloaded station map
- **Output:** Cached SOC and map stay viewable; local threshold-based alerts keep working from
  on-device data
- **Constraints:** Cached data shown with its timestamp; actions taken offline queue and sync
  once connectivity returns
- **Non-functional requirements:** NF-09 (device-side store-and-forward ≥48h) — related but a
  different layer: NF-09 is the device buffering data (that's F-A1/`telematics`'s job), F-D5 is
  what the app shows the driver
- **Priority · Release:** Should · P1.1
- **Status:** ⚪ N/A (no backend component) — purely client-side caching/local-alert continuation
  over data already fetched by other (separately tracked) features; nothing distinct to build
  in this repository.

### F-D6 Cost-optimal charging time & station suggestion
- **Actor:** Driver
- **Trigger:** System evaluates current SOC, route, and upcoming charging need
- **Input:** Time-of-day/source electricity price (F-C8), station load forecast (F-C7), current
  SOC, route consumption forecast (F-A7)
- **Output:** Suggested time/station to charge at lowest cost while still making the trip on
  time
- **Constraints:** Every suggestion must state estimated savings and delay risk; driver can
  always dismiss it
- **Non-functional requirements:** NF-20 (suggestion quality depends on the A7/C7 forecasts)
- **Priority · Release:** Should · P1.5
- **Backend domain:** Unassigned — a recommendation engine spanning `telemetry` (F-A7),
  `charging_stations`/unassigned (F-C7), and `billing` (F-C8, future)
- **Status:** 📋 Planned

### F-H1 In-app charging payment
- **Actor:** Driver
- **Trigger:** Driver scans the QR code on a connector
- **Input:** session_id, selected payment method (VNPay/Momo/wallet)
- **Output:** Scan → charge → pay in ≤3 steps; kWh receipt issued
- **Constraints:** No card data stored on G3 systems (tokenized via the payment gateway); keeps
  the session open and bills afterward if signal is weak during charging
- **Non-functional requirements:** NF-05 (encryption/tokenization)
- **Priority · Release:** Must · P1.0
- **Backend domain:** `billing` (future domain)
- **Status:** 📋 Planned

### F-H2 Wallet & transaction history
- **Actor:** Driver / fleet (centralized payment)
- **Trigger:** Driver opens the wallet tab, or tops up/withdraws
- **Output:** Prepaid wallet balance for driver or fleet; charging-session and transaction
  history; fleet-level centralized billing option
- **Constraints:** Top-up/withdrawal limits per regulation; wallet balance must reconcile
  against charging sessions
- **Priority · Release:** Should · P1.1
- **Backend domain:** `billing` (future domain)
- **Status:** 📋 Planned

### F-I2 Roadside/incident SOS
- **Actor:** Driver, in a breakdown or out-of-charge situation
- **Trigger:** Driver taps the SOS button
- **Input:** Current location, active error code (if any)
- **Output:** Location + error code sent to customer support; support calls back and forwards
  the case to the repair/rescue network (F-I4)
- **Constraints:** SOS button always visible; callback SLA ≤5 minutes; works while backgrounded;
  hotline fallback if the app can't be used
- **Business decisions to finalize:** the callback is made by a human agent after the software
  hands off the case — actual 24/7 staffing/coverage is an organizational decision (see
  "Items needing confirmation").
- **Priority · Release:** Must · P1.0
- **Backend domain:** `support`
- **Status:** ✅ Done (MVP/POC scope) — `POST /support/sos` records the case (location, error code,
  vehicle/driver context) with the ≤5-minute response-SLA deadline stored on the row. The backend's
  job ends at recording the handoff: the callback itself is a human action taken after this call
  returns, per this feature's own stated business decision. Since 2026-10-01 every new SOS also
  raises a `CRITICAL` `SOS_ALERT` notification in the same transaction (case/vehicle/driver IDs,
  VIN, channel, category, coordinates, error code, `response_due_at`), so the polling console sees
  it without listing cases; and the request takes a `channel` (default `IN_APP`) so a
  hotline-fallback SOS is recorded as an SOS with the SOS SLA — coordinates are required only for
  `IN_APP`. Forwarding to F-I4 is not built — see `docs/decisions/deferred.md` item 68.

### F-I3 Maintenance scheduling
- **Actor:** Driver
- **Trigger:** Driver books a slot at a G3-network workshop/partner, usually from a maintenance
  reminder
- **Input:** Selected workshop, time slot
- **Output:** Confirmed booking; maintenance history stored per vehicle
- **Priority · Release:** Could · P1.5
- **Backend domain:** `support` (booking data portion; workshop discovery/booking UI is client-side)
- **Status:** 📋 Planned — deferred this round in favor of F-I1/F-I2 (see
  `docs/decisions/deferred.md`); needs a bookable-slot/calendar inventory concept that doesn't
  exist anywhere in this backend yet.

---

## 2. Fleet & Admin web portal

### F-A5 Location, trip history & geofencing
- **Actor:** Fleet manager / operations
- **Trigger:** Continuous vehicle tracking; entry/exit of a defined geofence
- **Input:** Real-time GPS/speed; geofence boundaries per vehicle/fleet
- **Output:** Live location, trip replay, in/out-of-zone alerts
- **Constraints:** Location updates ≤30s; trip history retained ≥6 months
- **Non-functional requirements:** NF-08 (consent & audit log for any access to location data —
  this is personal-data tracking under Decree 13/2023)
- **Priority · Release:** Must · P1.0
- **Backend domain:** `fleet` (geofence-boundary config, fleet-scoped for now); `telemetry`
  (GPS/speed history, geofence entry/exit detection; trip segmentation not built)
- **Status:** ✅ Done (MVP/POC scope) — "live location" is served by F-A1's
  `GET /telemetry/vehicles/{id}/latest` (with `is_online`) and, fleet-wide, by
  `GET /telemetry/fleets/{fleet_id}/vehicles/latest` (F-E1). "Trip replay" is served by
  `GET /telemetry/vehicles/{id}/history` returning a vehicle's telemetry points within a required,
  capped time range (default 7-day max span, `TELEMETRY_HISTORY_MAX_RANGE_DAYS`), ordered
  chronologically for the frontend to draw a route polyline — not segmented trips; this backend
  has no trip concept (`deferred.md` items 38, 46). **Geofencing** (2026-10-01): a fleet manager
  defines polygons per **fleet** (`/fleets/{fleet_id}/geofences` CRUD, GeoJSON `Polygon`, stored
  as `geography(POLYGON, 4326)` in the new `geofences` table); telemetry ingestion compares the
  geofences covering a vehicle's previous and current reading (in the vehicle's current fleet,
  `ST_Covers`, a boundary point counts as inside) and raises one `GEOFENCE_ALERT` notification
  per area entered or left. A vehicle's first reading and a vehicle in no fleet raise nothing.
  "Per vehicle" geofences are not built — the fleet scope is an adaptation until customer
  accounts exist (`deferred.md` item 86). Still deferred: the ≥6-month retention constraint as an
  enforced policy (`deferred.md` item 48) and NF-08's consent/audit-log requirement (no `identity`
  domain yet, same gap as every other personal-data-adjacent endpoint)
- Note: the source also mentions this feature supports the internal vehicle-repossession
  process — that's usage context, not an additional software requirement.

### F-A6 Operating performance report
- **Actor:** Fleet manager
- **Trigger:** Fleet manager opens the performance report
- **Output:** Km/day, kWh consumed, kWh/km, cost/km per vehicle; daily/weekly/monthly report;
  CSV export
- **Constraints:** Cost formula must be configurable (electricity price varies)
- **Priority · Release:** Must · P1.1
- **Backend domain:** `telemetry` (corrected from `fleet`, which has no active source — see note)
- **Status:** ✅ Done (MVP/POC scope) — `GET /telemetry/vehicles/{vehicle_id}/operating-report`
  computes distance/energy/cost over a time window (max 366 days,
  `TELEMETRY_REPORT_MAX_RANGE_DAYS`) directly from `vehicle_telemetry`: energy consumed is the sum
  of positive SOC drops between consecutive samples, converted to kWh via the vehicle's recorded
  `battery_capacity_kwh` (nullable column) or a documented engineering-default fallback (75 kWh,
  flagged in the response); distance is the sum of positive odometer deltas. km/day divides by
  the requested window, not the observed sample span. Every derived rate (kWh/100km, cost/km) is
  `null` when undefined (zero distance or fewer than two samples) rather than a fabricated number.
  Since 2026-10-01: an optional `granularity=day|week|month` adds per-period rows (periods cut in
  `APP_REPORT_TIMEZONE`, ISO weeks, first/last period clipped to the window; periods sum to the
  window), `format=csv` exports the same report, and the electricity price is a setting
  (`TELEMETRY_ENERGY_COST_PER_KWH_VND`, one flat VND/kWh — time-of-use or per-tenant pricing is
  still `deferred.md` item 60). The fleet-level view is
  `GET /telemetry/fleets/{fleet_id}/operating-report[?format=csv]`: one row per current member
  vehicle plus fleet totals whose rates are recomputed from the summed distance/energy (the CSV
  ends with a `TOTAL` row); it has no period breakdown. It is served by `telemetry` through a
  `telemetry → fleet` edge (planner D7), not by `fleet` (see
  `docs/planners/done/backend-happy-path-completion.md`).
- Note: `charging_sessions` was the original implied data source for kWh, but that table carries
  no vehicle linkage at all (the same blocker recorded for F-C6), so this round computes energy
  from the vehicle's own telemetry instead, per an explicit user decision. This is a genuine
  accuracy tradeoff (gross discharge, not net; SOC quantization; sparse-telemetry
  under-counting) — see the response schema's docstring for the full list.

### F-A8 Per-driver charging-efficiency report
- **Actor:** Fleet manager (primary); driver (can view their own report in-app)
- **Trigger:** Weekly/monthly report generation
- **Input:** Charging sessions per driver, time-of-day pricing
- **Output:** % of kWh charged during off-peak/renewable-surplus hours, cost/km, estimated VND
  savings vs. peak-hour charging, in-fleet ranking
- **Constraints:** CSV/PDF export
- **Business decisions to finalize:** using this report as the basis for a reward/competition
  policy is an internal HR/operations decision — the software's job is producing accurate
  numbers.
- **Priority · Release:** Should · P1.1
- **Backend domain:** `fleet`
- **Status:** 📋 Planned — hard-blocked: `charging_sessions` has no `vehicle_id`/`driver_id`
  column at all, so there is no session→driver path anywhere in this backend to compute
  "charging sessions per driver" from (`deferred.md` item 62 records the RFID/token-identity
  question as an unresolved business decision, not a coding gap). `fleet` itself now has active
  source, but this feature needs that link resolved first.

### F-B1 Charging-policy configuration
- **Actor:** G3 Mobility (warranty operations)
- **Trigger:** G3 Mobility sets or updates a warranty-linked charging policy
- **Input:** Time-of-day window, SOC min–max (e.g. 20–90%), duration/frequency, allowed power
- **Output:** Policy applied per vehicle/fleet/model; takes effect immediately; every version
  retained for audit
- **Business decisions to finalize:** making policy configurable is the software requirement;
  the actual policy values are a G3 Mobility warranty/contract decision, not the software's to
  make (see "Items needing confirmation" #2).
- **Priority · Release:** Must · P1.0
- **Backend domain:** `policy` (future domain)
- **Status:** 📋 Planned

### F-B4 Warranty status dashboard
- **Actor:** G3 Mobility (warranty operations) / fleet manager
- **Output:** Per-vehicle compliance score, violation count, warranty risk level; filterable by
  risk; exportable report for G3 Mobility
- **Priority · Release:** Must · P1.1
- **Backend domain:** `policy` (future domain — data portion; dashboard UI is client-side)
- **Status:** 📋 Planned

### F-B6 Violation report to Warranty team
- **Actor:** System (generates); G3 Mobility (receives)
- **Trigger:** Scheduled and on-demand reporting
- **Output:** Violation case file sent to G3 Mobility for handling
- **Constraints:** Case-history retained
- **Business decisions to finalize:** generating and sending the report is the software
  requirement; what G3 Mobility then does with it (warning vs. denying a warranty claim, per
  contract) is a human business process outside the software.
- **Priority · Release:** Should · P1.1
- **Backend domain:** `policy` (future domain)
- **Status:** 📋 Planned

### F-C1 Charging station directory
- **Actor:** G3 Energy operations
- **Output:** Station records with GPS, power rating, connector count, CCS2 standard, operating
  hours, maintenance status; CRUD; map view
- **Priority · Release:** Must · P1.0
- **Backend domain:** `charging_stations`
- **Status:** ✅ Done — station records carry GPS (PostGIS geography), power rating, a computed
  connector count, connector standard, operating hours, and maintenance status, plus full CRUD
  (now part of the single baseline migration `0001_baseline_schema`). Since 2026-10-01 the station
  response also carries a read-time `available_connector_count` next to the derived `is_online`.
  "Map view" is satisfied by exposing lat/lon in the API response; actual map rendering is a
  frontend concern (F-D1, out of this repo)

### F-C5 Station-level energy output
- **Actor:** G3 Energy operations
- **Output:** kWh sold per station per time window, for time-of-day pricing optimization
- **Priority · Release:** Should · P1.1
- **Backend domain:** `charging_sessions` (aggregation over stored session data)
- **Status:** ✅ Done (MVP/POC scope) — `GET /charging-sessions/stations/{station_id}/energy?start_time=&end_time=`
  sums `energy_delivered_wh` (converted to kWh) and counts completed sessions ending within a
  required, timezone-checked window; both bounds are otherwise unbounded (an ordinary table, not
  a hypertable, so no max-range cap like F-A5's). An unknown `station_id` returns a zero summary
  rather than a 404 - this domain doesn't own station existence. Since 2026-10-01, for
  time-of-day analysis: `GET /charging-sessions/stations/{station_id}/energy/series?granularity=hour|day`
  returns a dense kWh series over `[start_time, end_time)` (max
  `CHARGING_ENERGY_SERIES_MAX_RANGE_DAYS`, default 31), buckets cut in `APP_REPORT_TIMEZONE`, built
  from energy-register deltas between consecutive readings attributed to the later reading's
  bucket (active sessions included), so energy is split across slot boundaries rather than booked
  whole at `ended_at`; and `GET /charging-sessions/stations/energy` lists the per-station totals
  for every active station, highest first (served by `charging_stations`, which owns the station
  directory). Not built: non-transaction clock-aligned metering (`deferred.md` item 77)

### F-C8 Dynamic pricing by generation source & time-of-day
- **Actor:** G3 Energy (configures tariffs); driver (sees current price)
- **Trigger:** G3 Energy sets a time-of-day / generation-source (grid / solar / wind / biomass /
  BESS discharge) tariff
- **Output:** App shows the current price and the cheapest window of the day at each station, to
  nudge charging toward cheap/surplus hours
- **Constraints:** Tariffs versioned with an effective date range; the price shown in-app at
  session start must match the billed price 100%
- **Non-functional requirement — NF-19 (Pricing transparency):** Displayed price = billed
  price — 100% match between the in-app price shown at session start and the price actually
  billed; tariff log kept per session ≥5 years.
- **Business decisions to finalize:** who owns the tariff (G3 Energy vs. G3 Network) and how
  large the peak/off-peak spread should be is an unresolved commercial decision between the two
  entities (see "Items needing confirmation" #5).
- **Priority · Release:** Should · P1.5
- **Backend domain:** `billing` (future domain)
- **Status:** 📋 Planned

### F-E1 Fleet list & map
- **Actor:** Fleet manager
- **Output:** Full fleet list, status, real-time location on the web portal; filter/search
- **Priority · Release:** Must · P1.0
- **Backend domain:** `fleet` (list/filter query; map rendering is frontend)
- **Status:** ✅ Done (MVP/POC scope) — `fleet` is this backend's second brand-new domain (after
  `drivers`). Fleet CRUD (`POST/GET/PATCH/DELETE /fleets`) mirrors the vehicles/drivers pattern.
  Fleet refactor (2026-10-09, FL-02/FL-08 in `docs/decisions/decision-log.md`): a fleet has no
  status; `name` and `fleet_code` are optional but at least one is required (422 on create,
  `ck_fleets_name_or_code`); fleets nest through `parent_fleet_id`, set on create or by `PATCH`
  (move) — an unknown parent is a 404, a move under the fleet itself or its own sub-fleets a 400,
  and deleting a fleet with live sub-fleets a 409. Nothing rolls up to a parent fleet yet. The
  owning organization, per-organization codes, who added/removed a vehicle and the fleet change
  history wait for the identity tables (`deferred.md` item 97); clearing a name/code or moving a
  fleet back to the top level is item 98.
  Vehicle membership is a genuine history table (`fleet_vehicle_memberships`,
  `added_at`/`removed_at`, FL-09), the same open/close shape as `driver_vehicle_assignments`, so
  `GET /fleets/{id}/memberships` gives real history and `GET /fleets/{id}/vehicles` gives the
  current list (F-E1's "full fleet list" — `vehicle_id`, `vin`, `license_plate`, `status`; a
  member whose vehicle was soft-deleted is still listed, with `vin`/`license_plate`/`status` =
  null). A vehicle joins by VIN (`POST /fleets/{id}/vehicles`) and leaves by VIN
  (`DELETE /fleets/{id}/vehicles/{vin}`). One
  active fleet per vehicle is enforced via a partial unique index (`WHERE removed_at IS NULL`), but
  unlike `driver_vehicle_assignments` there is no equivalent index on `fleet_id` — a fleet
  legitimately holds many vehicles at once. `vehicles.fleet_id` (a dead `String(36)` column with
  no FK, no index, never queried by any code) is dropped and replaced by this membership table —
  see `deferred.md` item 10, now superseded. Filter/search (2026-10-01): `GET /fleets` takes `q`
  (name or fleet code, case-insensitive) and `vehicle_vin` ("which fleet is this vehicle in"), and
  `GET /fleets/{id}/vehicles` takes `status` (vehicle lifecycle) and `q` (VIN or plate substring).
  A membership whose vehicle was soft-deleted is closed by ID
  (`DELETE /fleets/{fleet_id}/memberships/{fleet_vehicle_membership_id}`). "Real-time location" and the online
  "status" are served by `GET /telemetry/fleets/{fleet_id}/vehicles/latest` (paged members, oldest
  first, with VIN/plate/lifecycle status, the newest position, `recorded_at`/`received_at`,
  `is_online` = newest telemetry within `TELEMETRY_ONLINE_THRESHOLD_SECONDS`, and signal strength),
  owned by `telemetry` through the one-directional `telemetry → fleet` edge. See
  `docs/planners/done/backend-crud-fleet.md` and
  `docs/planners/done/backend-happy-path-completion.md`.

### F-E2 Fleet KPI dashboard
- **Actor:** Fleet manager
- **Output:** Km, kWh, cost/km, SOH, utilization rate, alerts — aggregated and per-vehicle; time
  filter; export
- **Priority · Release:** Must · P1.1
- **Backend domain:** `fleet` (data portion; dashboard UI is client-side)
- **Status:** 📋 Planned — deferred in favor of F-E1 (see `docs/decisions/deferred.md` item
  72). Partly unblocked on 2026-10-01: the km/kWh/cost-per-km half exists as the fleet operating
  report (`GET /telemetry/fleets/{fleet_id}/operating-report`, aggregated and per-vehicle, CSV —
  see F-A6) and `telemetry.service.resolve_vehicle_operating_summary` returns a DTO other domains
  may call. Still missing: SOH and alert columns in the same view (`notifications` only has an
  unread count per vehicle, not a count over a window as a DTO), a time-bucketed fleet view, and
  "utilization rate", which has no backing data (no trip/duty concept).

### F-E3 Charging & warranty report
- **Actor:** Fleet manager
- **Output:** Charging sessions, policy compliance, warranty status per fleet/vehicle; CSV/PDF
  export, filterable
- **Priority · Release:** Must · P1.1
- **Backend domain:** `fleet` (aggregates `charging_sessions` and `policy` data)
- **Status:** 📋 Planned — hard-blocked on both halves: the charging half needs a session→vehicle
  link `charging_sessions` doesn't have (`deferred.md` item 62, same blocker as F-A8), and the
  warranty/policy half needs the `policy` domain, which has no active source at all. `fleet`
  itself now has active source, but this feature needs both blockers resolved first.

### F-E4 Driver management & assignment
- **Actor:** Fleet manager
- **Output:** Add/edit drivers, assign/reassign vehicles, per-driver activity history
- **Priority · Release:** Should · P1.1
- **Backend domain:** `drivers` (data portion; assignment UI is client-side)
- **Status:** ✅ Done (MVP/POC scope) — `drivers` is this backend's first brand-new domain since the
  vehicles/telematics baseline. Driver CRUD (`POST/GET/PATCH/DELETE /drivers`) mirrors the
  vehicles domain exactly. Vehicle assignment is a genuine history table
  (`driver_vehicle_assignments`, `assigned_at`/`unassigned_at`), not a single current-vehicle
  column, so `GET /drivers/{id}/assignments` gives real "per-driver activity history" — a
  deliberate improvement over the existing `telematics.vehicle_id` pattern, which cannot express
  history and never auto-frees the old row on reassignment (its other known bug, a unique
  constraint not scoped to non-deleted rows, was fixed on 2026-10-01 — `deferred.md` item 82). `POST /drivers/{id}/assignment` (201) reassigns smoothly in
  one call (auto-closing the driver's previous vehicle) rather than requiring a manual unassign
  first. One active vehicle per driver and one active driver per vehicle are enforced via two
  partial unique indexes (`WHERE unassigned_at IS NULL`) — this backend's first use of a partial
  index. Assumption, stated plainly: driver fields (`full_name`, `phone_number`, `license_number`,
  `status`) aren't specified by the PRD beyond "Add/edit drivers"; the 1:1-at-a-time assignment
  cardinality is also an assumption, not a stated requirement. Since 2026-10-01 `GET /drivers`
  also takes `q` (case-insensitive substring of name, phone or license number; LIKE wildcards are
  matched literally) and `vehicle_vin` (the driver currently assigned to that vehicle). See
  `docs/planners/done/backend-crud-drivers.md`.

### F-F1 Accounts & RBAC
- **Actor:** Admin (G3 Network)
- **Output:** Role-based permissions per the RBAC matrix, account invite/lock, audit log
- **Constraints:** every access to sensitive data (location) must be logged
- **Non-functional requirements:** NF-08 (audit log for access to personal/location data)
- **Priority · Release:** Must · P1.0
- **Backend domain:** `identity` (future domain — becomes the foundational domain once added:
  every other domain may depend on it, while it depends on none of them)
- **Status:** 📋 Planned
- Note: account invite/lock is ordinary admin CRUD; full RBAC enforcement and sensitive-data
  audit logging is really a platform security capability underneath it — see the RBAC matrix
  and NF-06 for the fuller spec.

### F-F4 Maintenance reminders & promotions
- **Actor:** Admin / fleet manager
- **Output:** Reminders by distance/time; promotional campaign management
- **Priority · Release:** Should · P1.1
- **Backend domain:** Unassigned — doesn't map to any domain named in `domain-boundaries.md` today
- **Status:** 📋 Planned

### F-H4 SaaS subscription billing
- **Actor:** Admin
- **Output:** Assign Standard-tier subscription per vehicle/month, billing cycle, expiry
  reminders, feature lock on overdue; subscription revenue report
- **Business decisions to finalize:** the actual Standard-tier price (VND/vehicle/month) and any
  annual-plan discount are unresolved commercial decisions — the software just needs to support
  plan assignment/cycles/locking (see "Items needing confirmation" #7).
- **Priority · Release:** Should · P1.5
- **Backend domain:** `billing` (future domain)
- **Status:** 📋 Planned

### F-J1 Telematics device health
- **Actor:** Operations / admin
- **Output:** Per-device dashboard — last-seen, firmware version, SIM/data status, power status;
  auto-alert when a vehicle has been "silent" for over X hours, distinguishing a device fault
  from the engine simply being off
- **Non-functional requirements:** NF-06 (device identity)
- **Priority · Release:** Must · P1.0
- **Backend domain:** `telematics`
- **Status:** ✅ Done (MVP/POC scope, partial) — the auto-alert half is built: a new periodic
  monitor (`telematics/monitoring/`, this backend's first non-event-driven background process)
  sweeps active devices every `TELEMATICS_HEALTH_CHECK_INTERVAL_SECONDS` (default 300s) and
  raises a `DEVICE_OFFLINE_ALERT` notification once per silence episode once a vehicle exceeds
  `TELEMATICS_SILENT_THRESHOLD_MINUTES` (default 180) without telemetry, judged from the newest
  backend `received_at` so a skewed device clock can't fake silence. Devices on a soft-deleted
  vehicle are skipped (2026-10-01). The dashboard's data half is partly built (2026-10-01): every
  `TelematicResponse` (get/list/create/update) carries `last_seen_at`, `is_online` (newest
  telemetry within `TELEMETRY_ONLINE_THRESHOLD_SECONDS`), `is_silent` (the monitor's own rule,
  shared through `telematics/monitoring/silence_rule.py`) and `last_signal_strength_dbm`, derived
  at read time; firmware version was already a device field. Not built: SIM/data status and power
  status — no such field exists anywhere in this backend's MQTT contract (`deferred.md` item 50) —
  and distinguishing a device fault from the engine being off (see F-J3, same gap)

### F-K1 Driver safety scoring
- **Actor:** System (computes); fleet manager (views weekly report)
- **Trigger:** Computed continuously from CAN-bus/GPS data already collected for F-A1; also
  evaluated after each shift
- **Input:** Hard braking, sudden acceleration, sharp cornering, speeding vs. route limit,
  continuous driving time
- **Output:** 0–100 safety score per driver, normalized per 100km so high-mileage drivers aren't
  unfairly penalized; in-fleet ranking
- **Constraints:** event weights configurable; P1.1 uses fixed thresholds per vehicle model,
  P1.5 recalibrates from real data and may add camera/ADAS input where fitted
- **Non-functional requirements:** none explicitly named in the source for this model — NF-20's
  stated scope only names F-A7/F-C7/F-D6; worth confirming whether "must be retrainable" should
  extend to this scoring model too (see "Items needing confirmation" #12)
- **Priority · Release:** Should · P1.1
- **Backend domain:** `scoring` (future domain — computation); `telemetry` (existing — the raw
  event data this model consumes is already collected, but nothing computes a score from it yet)
- **Status:** 📋 Planned
- Note: the source PRD's acceptance-criteria cell for this feature also held a long
  implementation-design memo (data sources, scoring formula, rollout phases). That's design
  guidance, not a testable acceptance criterion — kept as reference below, not as a checklist
  line: *data sources need no new hardware (CAN-bus/GPS from F-A1, continuous-driving time from
  shift sessions); scored events are hard braking, sudden acceleration, sharp cornering,
  route-limit speeding, and >4h continuous driving; score is 0–100 normalized per 100km with
  configurable event weights; P1.1 ships fixed per-model thresholds, P1.5 recalibrates from real
  data and adds camera/ADAS if available; used for weekly fleet reports, driver competitions,
  and as an input to future insurance pricing (P2).*

---

## 3. CSKH & Ops console

### F-I1 In-app support tickets
- **Actor:** Driver (creates); customer support / CSKH (handles)
- **Trigger:** Customer submits a support request
- **Input:** Vehicle context auto-attached (VIN, location, error code)
- **Output:** Ticket created, categorized, tracked against an SLA; Zalo/hotline contacts also
  logged as tickets
- **Business decisions to finalize:** who staffs 24/7 CSKH (Holding in-house vs. outsourced) and
  the committed SLA is an organizational decision — the software just needs to expose ticket
  creation, categorization and an SLA timer.
- **Priority · Release:** Should · P1.1
- **Backend domain:** `support`
- **Status:** ✅ Done (MVP/POC scope) — `POST /support/cases` creates a ticket with vehicle/driver
  context (VIN/location/error-code resolved and validated, not just echoed), a category, and a
  response-SLA deadline copied onto the row at creation time so a later config change never
  rewrites a past case's SLA. `GET/PATCH/DELETE /support/cases{,/…}` cover lifecycle status
  (OPEN → ACKNOWLEDGED → RESOLVED → CLOSED, plus CANCELLED), filtering, and soft delete; a
  CLOSED/CANCELLED case refuses further updates (409). `first_responded_at`/`resolved_at` are
  stamped only the first time a status implies them (cancelling is not a response), and
  `is_sla_breached` compares the deadline with the first response, or `closed_at` for a case
  cancelled before any response, or else now. Since 2026-10-01 the case list also filters by
  `category`, `channel`, `driver_id`, `awaiting_response` and `sla_breached` (the SQL form of the
  same breach rule, judged at one instant per request). F-I2's SOS reuses the same table
  (`case_type=SOS`) since the spec ties the two into one lifecycle. Zalo/hotline logging exists as
  a `channel` value with no actual integration behind it. No proactive SLA-breach monitor
  (`deferred.md` item 70). See `docs/planners/done/backend-support-cases.md`.

### F-I4 Repair & rescue network dispatch
- **Actor:** Customer support / CSKH (dispatches); driver (tracks status)
- **Trigger:** CSKH accepts an incident case from F-I2 (serious error code, breakdown, accident)
- **Input:** Vehicle location, VIN, error code, on-site photos
- **Output:** Case auto-routed to the nearest available repair/rescue partner in the network;
  driver can track handling status and ETA
- **Constraints:** Partner directory by region (capability, coverage, hours); SLA to accept a
  case ≤15 minutes with a confirmed ETA; synced with the F-I1 ticket; incident history retained
  per vehicle for warranty/predictive-maintenance use
- **Non-functional requirement — NF-21 (Partner integration):** Standardized repair/rescue
  intake API, ≤15-minute acceptance SLA (measurable); swapping regional partners requires no
  source-code change.
- **Business decisions to finalize:** which entity actually operates the repair/rescue network
  (a G3 workshop, a Tri-Ring dealer, or a regional third party) is unresolved — if the
  ≤15-minute SLA isn't achievable across the whole operating area, the real coverage should be
  published rather than promising more than the network can deliver.
- **Priority · Release:** Must · P1.1
- **Backend domain:** `support`
- **Status:** 📋 Planned — deferred this round in favor of F-I1/F-I2 (see
  `docs/decisions/deferred.md`); needs a partner directory (with a nearest-partner PostGIS
  lookup) and dispatch routing that don't exist yet. `support` itself now has active source.

---

## 4. Background system & integrations

> F-G4 "Data governance & security" from the source PRD isn't listed here as a feature — it
> duplicated several platform-wide NFRs and is folded into **NF-22** in §5 instead.
>
> Every feature in this section has **System** as its actor — no dedicated frontend, so its
> status here is the feature's whole status, not just a "backend portion."

### F-A1 Real-time vehicle telemetry ingestion
- **Actor:** System
- **Trigger:** Continuous stream from every online Tri-Ring vehicle
- **Input:** SOC, SOH, battery voltage/temperature, charge/discharge current, motor status,
  speed, odometer, GPS, error codes from the BMS/on-board device
- **Output:** Latest state stored and queryable; ≥12 months of hot history retained
- **Constraints:** Update ≤30s p95 while online; online/offline flag maintained; schema is
  versioned
- **Non-functional requirements:** NF-01 (≤30s p95, target ≤10s), NF-06 (per-device
  mTLS/certificate identity, revocable), NF-09 (≥48h on-device store-and-forward)
- **Non-functional requirement — NF-04 (Scale):** Concurrent vehicles/devices scale from 300
  (2026) to 1,200+ (2029) without an architecture change.
- **Priority · Release:** Must · P1.0
- **Backend domain:** `telemetry`
- **Status:** ✅ Done (MVP/POC scope) — MQTT ingestion, latest-value storage, the
  latest-per-vehicle read API, and schema versioning (`schema_version` on
  `TelemetryMessage`/`vehicle_telemetry`, defaults to 1 for backward compatibility) are
  implemented. This backend's current scope is an MVP/POC: non-functional concerns like
  scale/performance and anything beyond a basic security posture are intentionally not
  pursued yet, so the following are deferred rather than blocking completion — NF-01 latency
  measurement, NF-04 300→1,200+ scale-testing, and NF-06 device mTLS/certificate identity (MQTT
  currently supports only optional username/password) (deferred.md item 36). The online/offline flag
  is built (2026-10-01, `deferred.md` item 35 resolved): `GET /telemetry/vehicles/{id}/latest` adds
  `received_at` and a read-time `is_online` (newest telemetry received within
  `TELEMETRY_ONLINE_THRESHOLD_SECONDS`, default 300 s; nothing is stored), and the same flag is
  exposed per fleet (F-E1) and per device (F-J1). Devices on a soft-deleted vehicle no longer map
  their telemetry to it. History query: F-A5; retention policy: deferred.md item 48.

### F-A3 Battery health (SOH) & cycle tracking
- **Actor:** System
- **Output:** SOH %, charge/discharge cycle count, estimated capacity fade over time; alert when
  SOH drops below a configured threshold
- **Constraints:** Updated ≥1×/day
- **Priority · Release:** Should · P1.1
- **Backend domain:** `telemetry`
- **Status:** ✅ Done (MVP/POC scope) — `soh_percent`/`cycle_count` are now part of the MQTT
  battery payload and `vehicle_telemetry`, exposed via the existing latest/history endpoints.
  Below-threshold detection reuses the same crossing-rule pattern as F-A2/F-A4, raising a
  `SOH_ALERT` notification; the threshold is a setting since 2026-10-01
  (`TELEMETRY_SOH_ALERT_THRESHOLD_PERCENT`, default 70.0 — an engineering default, not
  vendor-confirmed). "Estimated capacity fade over time" (2026-10-01):
  `GET /telemetry/vehicles/{id}/battery-health?start_time=&end_time=` returns one point per
  `APP_REPORT_TIMEZONE` day that has a reading — the day's last SOH, last cycle count and an
  estimated usable capacity (SOH × the vehicle's recorded `battery_capacity_kwh`, when recorded) —
  over up to `TELEMETRY_BATTERY_HEALTH_MAX_RANGE_DAYS` (366); days without a reading are omitted,
  not interpolated, and no regression/forecast is computed. "Updated ≥1×/day" is trivially
  satisfied since telemetry updates far more often whenever the field is present

### F-A4 Anomaly detection
- **Actor:** System
- **Trigger:** High battery temperature, sudden voltage drop, cell/module fault, motor fault
- **Output:** Real-time alert + event log with a data snapshot
- **Constraints:** fire-safety-related anomalies are Must (upgraded from Should in v1.0, given
  battery fire risk)
- **Priority · Release:** Must · P1.0
- **Backend domain:** `telemetry`
- **Status:** ✅ Done (MVP/POC scope) — high battery temperature (≥60°C) and sudden voltage drop
  (≥50V between consecutive readings) are detected per-message (`telemetry/detection.py`,
  raised by `telemetry/alerting.py`),
  reusing the `notifications` domain (`ANOMALY_ALERT` type) for delivery, exactly as F-A2 does
  for battery alerts; each anomaly's payload carries both its evidence and a full telemetry
  snapshot as the "event log" this feature asks for; since 2026-10-01 that log can be read per
  vehicle and per type (`GET /notifications?vehicle_id=&notification_type=ANOMALY_ALERT&severity=`,
  `order=desc` for newest first, `GET /notifications/{id}` for one entry). Approximations:
  "cell/module fault" and "motor fault" cannot be told apart from the MQTT contract's opaque error code strings, so both
  collapse into one generic `DEVICE_FAULT` anomaly pending a vendor error-code catalog
  (`deferred.md` item 42); both thresholds are unvalidated engineering defaults, not
  vendor-confirmed (`deferred.md` item 43); an anomaly alerts once on entry and stays silent while
  it persists, with no re-alert/escalation for an unacknowledged condition (`deferred.md` item 44);
  no motor-temperature detector exists, since motor temperature isn't one of F-A4's four named
  triggers (`deferred.md` item 45)

### F-A7 Route-aware range & consumption forecast
- **Actor:** System
- **Trigger:** Continuously, as trip/route data comes in
- **Input:** Route terrain (elevation/DEM data), ambient temperature, load, driving style —
  replacing the old linear SOC-based estimate
- **Output:** Remaining-range estimate feeding F-D3 (range-aware), F-C7 (station load forecast),
  F-D6 (charging suggestion), and the future Phase-2 EV-aware routing feature
- **Constraints:** model must be periodically retrained on real route data
- **Non-functional requirements:** NF-20 (forecast error ≤10% p50 / ≤15% p90 on the pilot route;
  model must be retrainable from accumulated data)
- **Priority · Release:** Should · P1.1
- **Backend domain:** `telemetry`
- **Status:** 📋 Planned

### F-B2 Charging-session logging
- **Actor:** System
- **Trigger:** Every charging session
- **Output:** Immutable log per session — time, station, connector, power, kWh, start/end SOC,
  duration, cost
- **Constraints:** 100% of sessions on the G3 Energy network must be logged and cross-checked
  against vehicle telemetry
- **Non-functional requirements:** NF-10 (3-way reconciliation, kWh deviation <1%), NF-11
  (append-only/immutable session records)
- **Priority · Release:** Must · P1.0
- **Backend domain:** `charging_sessions`
- **Status:** ✅ Done (MVP/POC scope, partial) — happy-path lifecycle (`Started →
  Updated/MeterValues → Ended`) plus four scoped correctness fixes on top of it: OCPP's `seqNo`
  is now persisted per event (enabling future dedup, not implementing it); an event for an
  already-`COMPLETED` session is refused rather than silently re-mutating a finished record; a
  `MeterValues` sample older than the one already applied is discarded instead of overwriting a
  newer reading backwards; and OCPP's `measurand`/`unitOfMeasure` are read so a kWh-unit or
  non-energy sample can no longer silently corrupt the energy total. Also fixed the prerequisite
  bug blocking all of this and the simulator: OCPP handlers were annotated for dataclasses
  `python-ocpp` never actually delivers (it only snake_cases JSON into plain dicts), causing a
  live `AttributeError` crash - see `docs/planners/done/backend-charging-ingest-fixes.md`.
  **OCPP 1.6J** is now handled too (`docs/planners/backend-ocpp16-charger-integration.md`):
  `StartTransaction`/`StopTransaction`/`MeterValues`, with the backend assigning the integer
  `transactionId` from a database sequence, the `idTag` (every tag accepted for now), the stop
  reason and the charger's authoritative `meterStop` stored on the session, and the session found
  by `(station, transactionId)` so a `MeterValues` after a reconnect is not lost. All measurements
  live in one `charging_session_measurements` table (it replaced `charging_session_meter_values`):
  the energy register (`kWh` converted to Wh) drives the total, while `SoC`, power, voltage,
  current, temperature, `Power.Offered` and vendor-specific measurands are stored as sent and
  readable via `GET /charging-sessions/{id}/measurements`. Every OCPP frame is also kept verbatim
  in an append-only raw message log. Not built: retry, out-of-order recovery, DLQ, and dedup
  (`deferred.md` item 27, a deliberately deferred reliability path, not a gap in this round);
  orphaned-session and offline back-fill handling (waiting for real-charger logs); `idTag`
  validation and vehicle linkage (items 26/62); a duplicate `Started` still surfaces a raw
  `IntegrityError` instead of a domain exception (`deferred.md` item 66). Since 2026-10-01 the
  session detail (`GET /charging-sessions/{id}`) adds `duration_seconds`, `soc_start_percent`,
  `soc_end_percent` and `max_power_kw`, computed at read time from the stored measurements (SoC
  and power exist only where the charger sends them — today only the 1.6J path stores non-energy
  measurands, `deferred.md` item 78), and the session list filters by `station_id`,
  `connector_id`, `status` and a `started_from`/`started_to` window. Cost is not part of the log:
  pricing belongs to the future `billing` domain

### F-B3 Policy-violation matching & flagging
- **Actor:** System
- **Trigger:** Every completed/ongoing charging session
- **Input:** Session data vs. active charging policy (out-of-window charging, consistently >90%
  or <20% SOC, excessive fast-charging)
- **Output:** Violation flagged, categorized, and evidenced with an immutable snapshot
- **Non-functional requirements:** NF-11 (immutable evidence for contract disputes)
- **Business decisions to finalize:** flagging + evidence is the software's job; the actual
  consequence of a violation (warning vs. a contractual penalty) is an unresolved legal/warranty
  policy decision (see "Items needing confirmation" #3).
- **Priority · Release:** Must · P1.0
- **Backend domain:** `charging_sessions` (session-data portion, existing); `policy` (rule
  evaluation, future domain)
- **Status:** 📋 Planned

### F-C2 Real-time connector status
- **Actor:** System
- **Trigger:** OCPP status change at a connector
- **Output:** Available / Charging / Faulted status, updated in real time
- **Constraints:** update ≤30s; status accuracy ≥99%
- **Non-functional requirements:** NF-02 (≤30s OCPP status latency)
- **Priority · Release:** Must · P1.0
- **Backend domain:** `charging_stations`
- **Status:** ✅ Done (MVP/POC scope) — the OCPP gateway handles `StatusNotification` for both
  protocols, writing the status and a timestamp onto `ChargingConnectorModel`, exposed via
  `GET /charging-connectors/{id}`. The status is stored **exactly as the charger reported it**:
  OCPP 2.0.1's five values (`Available`/`Occupied`/`Reserved`/`Unavailable`/`Faulted`) plus OCPP
  1.6J's `Preparing`/`Charging`/`SuspendedEV`/`SuspendedEVSE`/`Finishing`, so F-C2's "Charging"
  is available for a 1.6J charger (a 2.0.1 station still reports `Occupied`). Busy rule: a
  connector is free **only** when `Available`; `Suspended*` are normal pauses, not faults. For
  1.6J the connector also stores `errorCode`/`vendorErrorCode`/`info`, and connector `0` (the whole
  charger) is stored on the station (`charger_status`, `charger_error_code`, …) because it has no
  topology row. Stations expose a derived `is_online` (`last_seen_at` within
  `CHARGING_OFFLINE_TIMEOUT_SECONDS`, stamped by every inbound frame of either protocol), but a
  connector's last status is **not** invalidated when its charger goes offline (`deferred.md` item
  76). Since 2026-10-01 `GET /charging-stations/{station_id}/connectors` returns the whole
  charger's status plus every gun's status and error details in one read, and the per-connector
  status now drives F-A2/F-D1 availability. Not built: fault alerting (item 75). NF-02's
  ≤30s/≥99% targets aren't measured (MVP/POC scope, no monitoring yet); no out-of-order guard
  (in-order arrival is this MVP's assumption, `deferred.md` item 27). Verified against a simulator
  only; see `docs/planners/backend-ocpp16-charger-integration.md`

### F-C6 Per-customer energy usage
- **Actor:** System
- **Output:** kWh consumed per customer/session, for billing and reconciliation
- **Constraints:** must match a 3-way reconciliation (connector–vehicle–payment)
- **Non-functional requirements:** NF-10
- **Priority · Release:** Must · P1.0
- **Backend domain:** `telemetry` (corrected from `charging_sessions` — see note)
- **Status:** ✅ Done (MVP/POC scope, partial) — `GET /telemetry/vehicles/{vehicle_id}/energy-usage`
  reports kWh charged per "customer," where a customer is simplified to one vehicle (one vehicle
  per customer), per an explicit product decision for this MVP — there is no customer/owner entity
  anywhere in this backend. Energy is the sum of positive SOC rises between consecutive telemetry
  samples (the mirror of F-A6's SOC-drop sum, sharing the same underlying query), converted to kWh
  the same way F-A6 does. **NF-10's 3-way reconciliation (connector–vehicle–payment, <1% deviation)
  is not met and cannot be met by this method** — it measures energy that entered the pack, not
  kWh billed at a station meter, so it excludes charger/conversion losses (typically 5-15%) and
  includes any non-station charging or regenerative braking. No cost/payment field is included
  either — that belongs to the future `billing` domain.
- Note: the original design implied keying this off `charging_sessions`, but that table has no
  vehicle, customer, or driver identity column at all — confirmed via the OCPP ingestion path,
  which never reads the `idToken` field OCPP would carry one in (see
  `docs/planners/done/backend-operating-energy-reports.md`). Adding that linkage, and thereby a
  station-metered version of this feature that could actually satisfy NF-10, is deferred
  (`deferred.md`).

### F-C7 Station load forecast & load balancing
- **Actor:** System
- **Trigger:** Continuously, as vehicles approach stations
- **Input:** Vehicles' location, SOC, heading, route and history vs. each station's available
  connectors
- **Output:** Warns a driver when more vehicles are converging on a station than it can serve
  within a 60-minute window, and suggests ≥1 alternative station within current range
- **Non-functional requirements:** NF-20 (forecast quality, measured weekly)
- **Priority · Release:** Must · P1.5
- **Backend domain:** Unassigned — spans `charging_stations` (capacity) and `telemetry` (vehicle
  position/SOC/heading); the forecast computation itself doesn't have an obvious single owner
  among the named domains
- **Status:** 📋 Planned

### F-F2 Device provisioning
- **Actor:** System (activation flow)
- **Trigger:** Vehicle handover
- **Output:** Device activated against a VIN; end-to-end data flow confirmed
- **Constraints:** ≥98% activation success rate
- **Non-functional requirements:** NF-06 (device identity/certificate issued at provisioning)
- **Priority · Release:** Must · P1.0
- **Backend domain:** `vehicles`
- **Status:** ✅ Done (MVP/POC scope) — `VehicleModel.activation_status` tracks
  `PENDING → DEVICE_ASSIGNED → ACTIVATED`: `DEVICE_ASSIGNED` fires when `telematics` assigns a
  device to the vehicle, `ACTIVATED` fires on the vehicle's first-ever telemetry message
  (end-to-end data flow confirmed). `GET /vehicles/activation-summary` reports the fleet-wide
  success rate (`activated_count / attempted_count`, where "attempted" = `DEVICE_ASSIGNED` +
  `ACTIVATED`). NF-06 (device identity/certificate) is not built — no PKI/certificate issuance
  exists anywhere in this backend. Since 2026-10-01 `GET /vehicles` also filters by
  `activation_status` (combinable with `status`), so operations can list the vehicles still
  waiting for a device or for their first telemetry
- Note: the source also describes a physical handover checklist alongside this item — that's an
  operational process, not a software requirement; only the activation flow and data-flow
  confirmation are in scope here.

### F-F3 Multi-channel notifications
- **Actor:** System
- **Trigger:** Low battery, charging-policy violation, maintenance due, anomaly, device offline
- **Output:** Push / in-app / SMS, per configured channel and threshold; history kept
- **Constraints:** SMS reserved as a fallback for critical alerts (e.g. battery ≤10% with no
  data connection) to control cost
- **Priority · Release:** Must · P1.0
- **Backend domain:** `notifications` (exists as a poll-only storage slice for F-A2; push/SMS delivery not built) — the delivery leg for F-A2, F-B5, F-J3
- **Status:** 📋 Planned

### F-G1 Tri-Ring vehicle telematics integration
- **Actor:** System
- **Output:** Core telemetry fields received over a swappable interface (mock ↔ real, no
  business-logic change needed to switch)
- **Constraints:** core fields received in full; test/mock environment available
- **Non-functional requirements:** NF-06 (per-device mTLS), NF-01 (latency)
- **Business decisions to finalize:** finalizing the Tri-Ring telematics spec (fields,
  frequency, protocol, BMS access, test environment) is a contract/procurement milestone, not a
  software acceptance test. If unresolved by the deadline, the fallback is a third-party
  OBD/CAN gateway (see "Items needing confirmation" #10).
- **Priority · Release:** Must · P1.0
- **Backend domain:** `telematics`
- **Status:** 🚧 In progress — device CRUD + vehicle mapping exist and ingestion works
  end-to-end. Mapping fixes (2026-10-01): a VIN that matches no live vehicle on create/update is
  rejected (404) instead of silently leaving the device unassigned (an explicit
  `vehicle_vin: null` still unassigns); a soft-deleted device no longer blocks a replacement
  (partial unique index `uq_telematics_active_vehicle`, `WHERE deleted_at IS NULL`); and a device
  whose vehicle was soft-deleted no longer maps telemetry. Real Tri-Ring spec compliance is
  unconfirmed (blocked on "Items needing confirmation" #10)

### F-G2 Charging station integration (OCPP)
- **Actor:** System
- **Output:** Real-time connector status and session data over OCPP 1.6J minimum, 2.0.1-ready
- **Non-functional requirements:** NF-02, NF-05 (transport encryption)
- **Business decisions to finalize:** requiring OCPP conformance testing at the point of station
  procurement is a procurement-process step, not a software acceptance criterion. Also see the
  OCPP version decision (see "Items needing confirmation" #11).
- **Priority · Release:** Must · P1.0
- **Backend domain:** `charging_stations`
- **Status:** 🚧 In progress — both **OCPP 2.0.1** (`BootNotification`/`Heartbeat` since
  2026-10-01 — device info and `last_boot_at` stored, heartbeat interval
  `CHARGING_OCPP_HEARTBEAT_INTERVAL_SECONDS` returned —, `TransactionEvent`/`MeterValues`/
  `StatusNotification`) and **OCPP 1.6J** are supported by one gateway that negotiates the
  subprotocol and uses one adapter per protocol (decided 2026-09-24; the first real charger,
  Willdigits DC, speaks 1.6J). For 1.6J the gateway answers `BootNotification`/`Heartbeat`,
  handles `StatusNotification`, `Authorize`, `StartTransaction`, `StopTransaction` and
  `MeterValues`, records the charger's device info and liveness, keeps a verbatim raw log of every
  frame, and captures the charger's `GetConfiguration` (including `SupportedFeatureProfiles`)
  after every boot — see `docs/planners/backend-ocpp16-charger-integration.md`. "Items needing
  confirmation" #11 is answered for this first hardware. **All of this was verified against
  simulators only; the real-charger bring-up (planner Step 10) has not happened yet.** Still open:
  the NF-05 production security profile — dev mode intentionally allows no TLS/no auth, per
  decision IS-05 (`deferred.md` item 73) —, remote commands from the API (item 74), fault
  alerting (item 75), the rest of 2.0.1 parity (stop reason, `idToken`, non-energy measurands;
  item 78) and the reliability path (item 27)

### F-G3 Data pipeline (ETL)
- **Actor:** System
- **Output:** Collect → normalize → tag → store (lake/warehouse + time-series); data-quality
  monitoring
- **Constraints:** normalized per vehicle/customer/route; alerts on dirty data
- **Non-functional requirements:** NF-16 (retention & schema versioning)
- **Non-functional requirement — NF-14 (Observability):** Monitoring, centralized logging, ops
  alerting — a system/pipeline health dashboard, with an alert on ingest interruption.
- **Priority · Release:** Should · P1.1
- **Backend domain:** Unassigned — broader than any single domain's ingestion path
- **Status:** 📋 Planned

### F-H3 E-invoicing for kWh sold
- **Actor:** System (integration)
- **Output:** Compliant e-invoice per retail customer and a monthly consolidated invoice per
  fleet
- **Constraints:** integrates with a licensed VN e-invoice provider
- **Non-functional requirements:** NF-10 (must reconcile with F-C6)
- **Priority · Release:** Must · P1.1
- **Backend domain:** `billing` (future domain)
- **Status:** 📋 Planned

### F-J2 Remote device configuration (OTA)
- **Actor:** System (ops-triggered)
- **Output:** Push config changes (send frequency, local alert thresholds) to devices remotely
- **Constraints:** push per vehicle/fleet; confirmation of applied config; rollback supported
- **Non-functional requirements:** NF-06
- **Priority · Release:** Should · P1.1
- **Backend domain:** `telematics`
- **Status:** ✅ Done (MVP/POC scope, partial) — `POST /telematics/{telematic_id}/config` pushes a
  telemetry publish-interval change to a device over MQTT (`g3network/telematics/{serial}/command`,
  QoS 1, this backend's first-ever MQTT publish), fail-closed: the interval is only recorded on
  `telematics` once the broker accepts the message (PUBACK), never before. Verified end-to-end with
  `mosquitto_sub`. Push per fleet (2026-10-01): `POST /telematics/fleets/{fleet_id}/config`
  sends the same body to the device of every current fleet member, one at a time through the same
  publish-then-record path, and answers 200 with one `published`/`skipped`/`failed` result per
  vehicle (a vehicle that is soft-deleted, has no live device or whose device is not `ACTIVE` is
  skipped; a failed publish does not stop the loop). Not built: local alert thresholds (only
  send-frequency is implemented — no device-side threshold semantics are defined anywhere, same
  hardware-contract gap as F-G1, `deferred.md` item 59), confirmation that the device actually
  applied the config (no ack topic exists in the MQTT contract — a successful publish only proves
  the broker accepted it, item 52), and rollback (needs the same missing confirmation signal
  first, item 53). See `docs/planners/done/backend-telematics-config-push.md`.

### F-J3 Device offline / tamper alert
- **Actor:** System
- **Output:** Alert when a device goes offline abnormally or shows signs of tamper/power loss
- **Constraints:** must distinguish sudden power loss from ordinary signal loss
- **Non-functional requirements:** NF-09, NF-06
- **Business decisions to finalize:** the alert itself is a software requirement; the actual
  vehicle-repossession process that may follow is an operational/legal process outside the
  software.
- **Priority · Release:** Must · P1.0
- **Backend domain:** `telematics`
- **Status:** ✅ Done (MVP/POC scope, partial) — the "device offline" half is built, sharing the
  same periodic monitor and `DEVICE_OFFLINE_ALERT` notification as F-J1 (devices on a soft-deleted
  vehicle are ignored since 2026-10-01). Not built: distinguishing
  sudden power loss from ordinary signal loss - no signal exists anywhere in this backend to tell
  the two apart (a per-device Last Will only exists for the backend's own MQTT consumer process,
  not per-telematic-device). NF-09 (≥48h on-device store-and-forward) and NF-06 (device identity)
  are not built either

---

## 5. Non-functional requirements (platform-wide)

Full list from the PRD's sheet 5, plus NF-22 (merged from F-G4). "Linked feature(s)" shows where
a requirement is also cross-referenced above; requirements with no linked feature are genuinely
platform-wide rather than tied to one piece of functionality.

**An NFR linked to exactly one feature has been moved into that feature's own entry** (still
under its original ID) and removed from the table below, so it's single-sourced there instead of
duplicated: **NF-03** → F-A2, **NF-04** → F-A1, **NF-12** → F-D4, **NF-13** → F-D4, **NF-14** →
F-G3, **NF-19** → F-C8, **NF-21** → F-I4. Everything remaining below is either linked to more
than one feature (kept here, cross-referenced under each) or genuinely platform-wide (no linked
feature at all).

| Code | Category | Requirement | Target / Threshold | Linked feature(s) | Backend status |
|---|---|---|---|---|---|
| NF-01 | Performance | Vehicle telemetry → system latency | ≤30s p95 (target ≤10s) while online | F-A1, F-A2, F-G1 | 🚧 not yet measured |
| NF-02 | Performance | Connector status latency (OCPP) | ≤30s | F-C2, F-G2 | 🚧 connector status implemented for both protocols (see F-C2); latency not measured |
| NF-05 | Security | Encryption & key management | TLS 1.2+ in transit, encrypted at rest, secrets in a vault (never hardcoded) | F-H1, F-G2 | 🚧 dev mode intentionally has no TLS/auth |
| NF-06 | Security | Device identity & authentication | mTLS/certificate or a unique per-device token, revocable | F-A1, F-F1 (partial), F-F2, F-G1, F-J1, F-J2, F-J3 | 📋 |
| NF-07 | Security | Penetration testing | Mandatory before Gate 2; zero critical findings to go live | — | 📋 |
| NF-08 | Privacy & Compliance | Personal-data protection | Complies with Decree 13/2023: consent at activation, purpose notice, minimal collection, data-subject rights | F-A5, F-F1 | 📋 |
| NF-09 | Reliability / Offline | Buffering & sync on signal loss | ≥48h on-device store-and-forward; no record loss | F-A1, F-D5, F-J3 | 📋 |
| NF-10 | Data integrity | 3-way reconciliation: connector (OCPP) ↔ vehicle (telemetry) ↔ payment | kWh deviation <1%; auto-alert on mismatch | F-B2, F-C6, F-H3 | 📋 |
| NF-11 | Data integrity | Immutable violation evidence | Session & violation records are append-only/immutable | F-B2, F-B3 | 📋 |
| NF-15 | Backup & DR | Backup & disaster recovery | RPO ≤15 min · RTO ≤4h; recovery drill 2×/year | — | 📋 |
| NF-16 | Data lifecycle | Retention & schema versioning | Hot 12 months (time-series) / cold 5 years (matches warranty period); schema is versioned & backward-compatible | F-A3 (implied), F-B1 (implied), F-G3 | 📋 |
| NF-17 | Localization | Language & formats | Vietnamese by default; VND/km/kWh; multi-language ready (Laos, China for GMS) | — | ⚪ N/A (frontend) |
| NF-18 | Maintainability | Modular code, tests, API docs | Clear module boundaries; tests for critical flows (battery alert, charging session, reconciliation, payment); OpenAPI | — | 🚧 smoke tests + 18 skipped-by-default Postgres integration tests (`RUN_DB_INTEGRATION=1`) exist |
| NF-20 | Forecast model quality | Accuracy & retrainability | Remaining-range forecast error ≤10% (p50) / ≤15% (p90); station-load forecast measured weekly; every model must be retrainable from accumulated data | F-A7, F-C7, F-D6, F-D3 (inherits) | 📋 |
| NF-22 | Security & Compliance *(merged from F-G4)* | Data governance & security | Encryption in transit & at rest; RBAC; audit log; Decree 13/2023 compliance; retention policy (hot 12mo/cold 5yr); driver consent at activation | (platform-wide) | 📋 |

---

## Items needing confirmation before implementation

Before a dev team estimates or builds these, the business decisions below need an answer — no
dev team (or AI coding agent) can guess these correctly on its own. Where the PRD's own
open-decisions log (sheet 14) already tracks the same question, its reference number is
included.

1. **Empty-trip mismatch handling (F-A9):** when the driver's declared load status and the
   system's automatic inference disagree repeatedly, is there a follow-up process (reminder,
   escalation), or does the flag alone suffice for now?
2. **Charging-policy values (F-B1):** the specific time-of-day windows, SOC min–max, and power
   limits per vehicle/fleet/model — these are G3 Mobility's warranty decisions, not defaults the
   software should assume.
3. **Violation consequences (F-B3, F-B6):** is a flagged violation just a warning + evidence
   record, or does it carry a contractual penalty (reduced warranty coverage, a fee)? *(PRD ref:
   Q4)*
4. **No-show penalty for reservations (F-C4):** what is the actual penalty, if the reservation
   feature ships at all?
5. **Dynamic-pricing ownership & spread (F-C8):** does G3 Energy or G3 Network own the tariff,
   and how large should the peak/off-peak price gap be to actually change charging behavior?
   *(PRD ref: Q13)*
6. **Reward/competition policy from charging reports (F-A8):** is the per-driver efficiency
   report tied to an actual incentive program, or informational only for now?
7. **SaaS subscription price (F-H4):** the Standard-tier VND/vehicle/month price and any
   annual-plan terms. *(PRD ref: Q3)*
8. **24/7 CSKH staffing (F-I1, F-I2):** does G3 Holding staff customer support in-house, or
   outsource it — and what SLA can actually be committed to? *(PRD ref: Q6)*
9. **Repair & rescue network operator (F-I4):** who runs the partner network — a G3 workshop, a
   Tri-Ring dealer network, or third-party partners by region? If ≤15-minute acceptance isn't
   achievable everywhere, what coverage should be published instead of promised? *(PRD ref: Q18)*
10. **Tri-Ring telematics spec sign-off (F-G1, and everything in §4 that depends on it):** the
    exact fields, frequency, protocol, and BMS access from Tri-Ring — this blocks nearly all of
    Module A/B/D/K if unresolved. A fallback (third-party OBD/CAN gateway) exists if this slips.
    *(PRD ref: Q1 — flagged in the PRD as the single highest-risk dependency for the whole
    phase)*
11. **OCPP version requirement at procurement (F-G2):** commit to 1.6J only, or require 2.0.1
    support at first purchase? *(PRD ref: Q8)*
    *(Answered for the first hardware, 2026-09-24: the Willdigits charger speaks 1.6J and the
    backend will support 1.6J alongside 2.0.1 — see F-G2. The procurement policy for later
    purchases remains open.)*
12. **Driving-score coverage under NF-20 (F-K1):** should the "model must be retrainable" quality
    bar that explicitly covers F-A7/F-C7/F-D6 also apply to the driver safety-scoring model?
