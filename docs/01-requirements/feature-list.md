# Feature List — G3 Network Platform (Phase 1)

Source: `G3 Network_Định hướng và Yêu cầu sản phẩm app__PRD__v3.xlsx`, sheets
**"4. P1 - Chức năng"** (functional) and **"5. P1 - Phi chức năng"** (non-functional), v3.0.

This document describes company-wide product intent across every actor
(driver app, fleet & admin portal, CSKH & ops console, background system) —
it is not scoped to any single repository. This repository (`G3Network-app`)
implements **only the backend**, so each feature below also carries this
repo's **backend implementation status** and, where applicable, the
**backend domain** that owns it.

## Status legend

**✅ Done** | **🚧 In progress** | **📋 Planned (not started)** | **⚪ N/A
(no backend component)** — a small number of features are pure
frontend/third-party-handoff with no distinct G3 backend logic to build (see
each entry for why); these are marked N/A rather than tracked as ✅/🚧/📋.

**This status reflects backend work only.** A ✅ here means the backend
logic is implemented — it does not mean the end-user experience (mobile app,
web portal, CSKH console) is complete; those live outside this repository.
For a quick, domain-level summary instead of this per-feature detail, see
[`docs/00-status/overview.md`](../00-status/overview.md). The **Priority ·
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
  operational station and its distance, queryable via `GET /api/v1/notifications`. Since there is
  no mobile app in this backend's scope, delivery is backend-storage-plus-portal-polling, not a
  push to a device — the app-level constraints ("must work while backgrounded", a navigation
  button) don't apply here. "1 alert per threshold per trip" is approximated as "1 alert per
  threshold crossing" (previous SOC above, current at-or-below), since no trip concept exists yet
  (F-A9 is still Planned); "nearest available station" only reflects `deleted_at`/
  `maintenance_status`, since no live occupancy/online signal exists. NF-01/NF-03 are not
  measured — this backend's current scope is MVP/POC, not a performance/uptime target (see
  `future.md`).

### F-A9 Empty-trip (deadhead) detection
- **Actor:** Driver (declares load status); System (infers automatically)
- **Trigger:** Driver marks trip status in-app, or the system compares per-km battery
  consumption against the vehicle's load-based consumption curve
- **Input:** Driver-entered load/empty status; per-km energy consumption vs. reference curve
- **Output:** Trip tagged loaded/empty; a flag is raised when the two sources disagree
- **Constraints:** Manual declaration ≤2 taps; automatic inference accuracy target ≥90% vs.
  declared status; both sources are cross-checked
- **Priority · Release:** Should · P1.5
- **Backend domain:** `telemetry` (automatic inference); `drivers` (manual declaration — future domain)
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
- **Status:** 📋 Planned — out of the current happy-path OCPP MVP scope (see `tech-decisions.md`)

### F-D1 Charging station map
- **Actor:** Driver
- **Trigger:** Driver opens the map
- **Input:** Driver's current location
- **Output:** Nearby stations filtered by availability / power / connector standard
- **Priority · Release:** Must · P1.0
- **Backend domain:** `charging_stations` (nearby-station search/filter query; map rendering itself is frontend)
- **Status:** 📋 Planned

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
- **Backend domain:** `support` (future domain)
- **Status:** 📋 Planned

### F-I3 Maintenance scheduling
- **Actor:** Driver
- **Trigger:** Driver books a slot at a G3-network workshop/partner, usually from a maintenance
  reminder
- **Input:** Selected workshop, time slot
- **Output:** Confirmed booking; maintenance history stored per vehicle
- **Priority · Release:** Could · P1.5
- **Backend domain:** `support` (future domain — booking data portion; workshop discovery/booking UI is client-side)
- **Status:** 📋 Planned

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
- **Backend domain:** `vehicles` (geofence-boundary config); `telemetry` (GPS/speed history, trip
  segmentation, geofence events)
- **Status:** 📋 Planned — `telemetry` currently returns only the latest record per vehicle; no
  history, trip replay, or geofencing yet
- Note: the source also mentions this feature supports the internal vehicle-repossession
  process — that's usage context, not an additional software requirement.

### F-A6 Operating performance report
- **Actor:** Fleet manager
- **Trigger:** Fleet manager opens the performance report
- **Output:** Km/day, kWh consumed, kWh/km, cost/km per vehicle; daily/weekly/monthly report;
  CSV export
- **Constraints:** Cost formula must be configurable (electricity price varies)
- **Priority · Release:** Must · P1.1
- **Backend domain:** `fleet` (future domain)
- **Status:** 📋 Planned

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
- **Backend domain:** `fleet` (future domain)
- **Status:** 📋 Planned

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
  (migration `0005_station_directory_fields`). "Map view" is satisfied by exposing lat/lon in the
  API response; actual map rendering is a frontend concern (F-D1, out of this repo)

### F-C5 Station-level energy output
- **Actor:** G3 Energy operations
- **Output:** kWh sold per station per time window, for time-of-day pricing optimization
- **Priority · Release:** Should · P1.1
- **Backend domain:** `charging_sessions` (aggregation over stored session data)
- **Status:** 📋 Planned

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
- **Backend domain:** `fleet` (future domain — list/filter query; map rendering is frontend)
- **Status:** 📋 Planned

### F-E2 Fleet KPI dashboard
- **Actor:** Fleet manager
- **Output:** Km, kWh, cost/km, SOH, utilization rate, alerts — aggregated and per-vehicle; time
  filter; export
- **Priority · Release:** Must · P1.1
- **Backend domain:** `fleet` (future domain — data portion; dashboard UI is client-side)
- **Status:** 📋 Planned

### F-E3 Charging & warranty report
- **Actor:** Fleet manager
- **Output:** Charging sessions, policy compliance, warranty status per fleet/vehicle; CSV/PDF
  export, filterable
- **Priority · Release:** Must · P1.1
- **Backend domain:** `fleet` (future domain — aggregates `charging_sessions` and `policy` data)
- **Status:** 📋 Planned

### F-E4 Driver management & assignment
- **Actor:** Fleet manager
- **Output:** Add/edit drivers, assign/reassign vehicles, per-driver activity history
- **Priority · Release:** Should · P1.1
- **Backend domain:** `drivers` (future domain — data portion; assignment UI is client-side)
- **Status:** 📋 Planned

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
- **Status:** 📋 Planned

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
- **Backend domain:** `support` (future domain)
- **Status:** 📋 Planned

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
- **Backend domain:** `support` (future domain)
- **Status:** 📋 Planned

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
  pursued yet, so the following are deferred rather than blocking completion — the
  online/offline flag (future.md item 35), NF-01 latency measurement, NF-04 300→1,200+
  scale-testing, and NF-06 device mTLS/certificate identity (MQTT currently supports only
  optional username/password) (future.md item 36). History/retention query API tracked
  separately in future.md item 33.

### F-A3 Battery health (SOH) & cycle tracking
- **Actor:** System
- **Output:** SOH %, charge/discharge cycle count, estimated capacity fade over time; alert when
  SOH drops below a configured threshold
- **Constraints:** Updated ≥1×/day
- **Priority · Release:** Should · P1.1
- **Backend domain:** `telemetry`
- **Status:** 📋 Planned

### F-A4 Anomaly detection
- **Actor:** System
- **Trigger:** High battery temperature, sudden voltage drop, cell/module fault, motor fault
- **Output:** Real-time alert + event log with a data snapshot
- **Constraints:** fire-safety-related anomalies are Must (upgraded from Should in v1.0, given
  battery fire risk)
- **Priority · Release:** Must · P1.0
- **Backend domain:** `telemetry`
- **Status:** 📋 Planned

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
- **Status:** 🚧 In progress — happy-path lifecycle only (`Started → Updated/MeterValues →
  Ended`); no retry, out-of-order handling, DLQ, or reconciliation check yet

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
- **Status:** 📋 Planned — no `status` column exists on any topology model, and the OCPP
  gateway only handles `TransactionEvent`/`MeterValues`; `StatusNotification` is not handled at
  all, so nothing for this feature is built yet

### F-C6 Per-customer energy usage
- **Actor:** System
- **Output:** kWh consumed per customer/session, for billing and reconciliation
- **Constraints:** must match a 3-way reconciliation (connector–vehicle–payment)
- **Non-functional requirements:** NF-10
- **Priority · Release:** Must · P1.0
- **Backend domain:** `charging_sessions`
- **Status:** 📋 Planned

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
- **Status:** 🚧 In progress — vehicle CRUD + soft delete exist; the activation state machine and
  ≥98% activation-success tracking are not built
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
- **Backend domain:** `notifications` (future domain) — the delivery leg for F-A2, F-B5, F-J3
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
  end-to-end; real Tri-Ring spec compliance is unconfirmed (blocked on #10)

### F-G2 Charging station integration (OCPP)
- **Actor:** System
- **Output:** Real-time connector status and session data over OCPP 1.6J minimum, 2.0.1-ready
- **Non-functional requirements:** NF-02, NF-05 (transport encryption)
- **Business decisions to finalize:** requiring OCPP conformance testing at the point of station
  procurement is a procurement-process step, not a software acceptance criterion. Also see the
  OCPP version decision (see "Items needing confirmation" #11).
- **Priority · Release:** Must · P1.0
- **Backend domain:** `charging_stations`
- **Status:** 🚧 In progress — session data over OCPP 2.0.1 (`TransactionEvent`/`MeterValues`)
  works; OCPP 1.6J is not implemented at all (only `ocpp.v201` is used), and connector status
  (`StatusNotification`) is not handled — see F-C2. NF-05 production security profile also not
  finalized (dev mode intentionally allows no TLS/no auth, per `tech-decisions.md`)

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
- **Status:** 📋 Planned

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
- **Status:** 📋 Planned

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
| NF-02 | Performance | Connector status latency (OCPP) | ≤30s | F-C2, F-G2 | 📋 no connector status implemented yet (see F-C2) |
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
| NF-18 | Maintainability | Modular code, tests, API docs | Clear module boundaries; tests for critical flows (battery alert, charging session, reconciliation, payment); OpenAPI | — | 🚧 smoke tests + 2 skipped-by-default Postgres integration tests exist |
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
12. **Driving-score coverage under NF-20 (F-K1):** should the "model must be retrainable" quality
    bar that explicitly covers F-A7/F-C7/F-D6 also apply to the driver safety-scoring model?
