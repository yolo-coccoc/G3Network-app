# Repo Status

Short, domain-level summary of what's actually implemented right now. This is
the source of truth for **progress at a glance** — unlike most of
`docs/01-requirements/`, which describes intended features and can be revised
independently of what's actually built. The one exception is
[`feature-list.md`](../01-requirements/feature-list.md): each feature there
carries this repo's backend implementation status directly (per `F-XX` item,
not just per domain), and it is also kept current as progress source of
truth — update both together when a change shifts a domain's overall status.
For technical depth (components, diagram, database, infra), see
[architecture.md](./architecture.md).

## Domains

| Domain | Status |
|---|---|
| `vehicles` | CRUD and soft delete, plus an F-F2 device-activation state machine (`PENDING → DEVICE_ASSIGNED → ACTIVATED`) and `GET /vehicles/activation-summary` for the fleet-wide success rate. Also carries a nullable `battery_capacity_kwh` (F-A6/F-C6's kWh-conversion input). |
| `telematics` | Device CRUD, mapping devices to vehicles. Runs a periodic device-health monitor (`telematics/monitoring/`, this backend's first non-event-driven background process) that alerts once per silence episode when a device stops reporting telemetry (F-J1/F-J3, partial — see `future.md`). Also pushes a telemetry publish-interval config to a device over MQTT (`telematics/commands/`, this backend's first-ever MQTT *publish* — F-J2, partial). |
| `telemetry` | Ingests via MQTT with a versioned message schema (`schema_version`); API returns the **latest** record per vehicle, a bounded time-range **history** query (F-A5), and two SOC-based aggregate reports (F-A6, F-C6) — no map, aggregate dashboard, or true trip segmentation yet. F-A1 done at MVP/POC scope; online/offline flag and NF-01/NF-04/NF-06 hardening deferred (`future.md` items 35, 36). F-A2 tiered battery alerts also done: SOC-threshold-crossing detection raises a notification via the `notifications` domain. F-A3 battery health also done: `soh_percent`/`cycle_count` are ingested and a below-threshold `SOH_ALERT` reuses the same crossing-rule pattern. F-A4 anomaly detection also done: high battery temperature, sudden voltage drop, and new device error codes are detected per-message and raise `ANOMALY_ALERT` notifications; cell/module vs. motor fault classification and motor-temperature detection are deferred (`future.md` items 42, 45). F-A5 also done at the same scope: `GET /telemetry/vehicles/{id}/history` returns telemetry points ordered chronologically within a required, capped time range; geofencing and true trip segmentation are deferred (`future.md` items 46, 47). F-A6/F-C6 also done (MVP/POC scope): `GET /telemetry/vehicles/{id}/operating-report` and `.../energy-usage` fold SOC drops/rises from the same telemetry history into per-vehicle consumed/charged energy, distance, and cost — `charging_sessions` has no vehicle linkage, so this is a telemetry-based proxy, not a station-metered figure (see `docs/02-planners/backend-operating-energy-reports.md`). |
| `charging_stations` | Station → EVSE → Connector topology CRUD, OCPP 2.0.1 gateway, station directory metadata (location, power rating, connector standard, operating hours, maintenance status, computed connector count), a nearest-operational-station lookup (F-A2), and a nearby-station radius search filtered by connector standard/power/maintenance status (F-D1), all using PostGIS. The OCPP gateway also handles `StatusNotification` (F-C2), writing a live per-connector status. Assumes pre-provisioned, always-online topology; no heartbeat/connection tracking yet. |
| `charging_sessions` | Happy-path lifecycle only: `Started → Updated/MeterValues → Ended`. Still no retry, out-of-order recovery, DLQ, or dedup (`future.md` item 27), but F-B2 layered four scoped correctness fixes on the happy path: OCPP `seqNo` is persisted per event, an event for an already-`COMPLETED` session is refused rather than applied, a stale `MeterValues` can't overwrite a newer reading, and OCPP `measurand`/unit are read instead of assuming Wh. Also serves a station-level energy aggregation query over a time window (F-C5). |
| `notifications` | Generic backend-storage notification table (F-A2's delivery leg, also reused by F-A3/F-A4/F-J1/F-J3), polled via `GET /api/v1/notifications?after_id=`; a JSONB payload carries type-specific fields so alert types (`BATTERY_ALERT`, `ANOMALY_ALERT`, `SOH_ALERT`, `DEVICE_OFFLINE_ALERT`, and future F-B5 types) share one table without a new migration or endpoint per type. No push, no recipient scoping (no `identity` domain yet) — poll-only for now. |
| `drivers` | This backend's first brand-new domain since the initial baseline. Driver profile CRUD plus a `driver_vehicle_assignments` **assignment-history table** (`assigned_at`/`unassigned_at`, this backend's first use of a partial unique index to enforce "one active vehicle per driver, one active driver per vehicle"), with smooth reassignment (auto-closes the driver's previous active assignment) and full per-driver assignment history (F-E4, done at MVP/POC scope). F-A9 (empty-trip detection) was considered alongside it but is suspended — no trip concept, driver auth, or consumption-curve data exists anywhere in this backend yet (`future.md` item 67). |

## Database

Current Alembic head: `0018_drivers`.

## Not built yet

Web portal, vehicle app, identity/RBAC, policy, billing/payment,
and extended monitoring (full telemetry history, map, connector-status
aggregation, push/multi-channel delivery) have no active source in the
repo. `notifications` has a minimal backend-storage/polling slice (F-A2)
but not the multi-channel delivery F-F3 describes. `drivers` now has a
CRUD/assignment slice (F-E4), but F-A9 (empty-trip detection) remains
unimplemented, blocked on a trip concept this backend doesn't have yet. See
[`docs/01-requirements/future.md`](../01-requirements/future.md) for the
full list of deferred components and why.
