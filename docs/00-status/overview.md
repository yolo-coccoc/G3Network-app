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
| `vehicles` | CRUD and soft delete, plus an F-F2 device-activation state machine (`PENDING → DEVICE_ASSIGNED → ACTIVATED`) and `GET /vehicles/activation-summary` for the fleet-wide success rate. |
| `telematics` | Device CRUD, mapping devices to vehicles. Also runs a periodic device-health monitor (`telematics/monitoring/`, this backend's first non-event-driven background process) that alerts once per silence episode when a device stops reporting telemetry (F-J1/F-J3, partial — see `future.md`). |
| `telemetry` | Ingests via MQTT with a versioned message schema (`schema_version`); API returns the **latest** record per vehicle plus a bounded time-range **history** query (F-A5) — no map, aggregate dashboard, or true trip segmentation yet. F-A1 done at MVP/POC scope; online/offline flag and NF-01/NF-04/NF-06 hardening deferred (`future.md` items 35, 36). F-A2 tiered battery alerts also done: SOC-threshold-crossing detection raises a notification via the `notifications` domain. F-A3 battery health also done: `soh_percent`/`cycle_count` are ingested and a below-threshold `SOH_ALERT` reuses the same crossing-rule pattern. F-A4 anomaly detection also done: high battery temperature, sudden voltage drop, and new device error codes are detected per-message and raise `ANOMALY_ALERT` notifications; cell/module vs. motor fault classification and motor-temperature detection are deferred (`future.md` items 42, 45). F-A5 also done at the same scope: `GET /telemetry/vehicles/{id}/history` returns telemetry points ordered chronologically within a required, capped time range; geofencing and true trip segmentation are deferred (`future.md` items 46, 47). |
| `charging_stations` | Station → EVSE → Connector topology CRUD, OCPP 2.0.1 gateway, station directory metadata (location, power rating, connector standard, operating hours, maintenance status, computed connector count), a nearest-operational-station lookup (F-A2), and a nearby-station radius search filtered by connector standard/power/maintenance status (F-D1), all using PostGIS. The OCPP gateway also handles `StatusNotification` (F-C2), writing a live per-connector status. Assumes pre-provisioned, always-online topology; no heartbeat/connection tracking yet. |
| `charging_sessions` | Happy-path lifecycle only: `Started → Updated/MeterValues → Ended`. No retry, out-of-order handling, or DLQ. Also serves a station-level energy aggregation query over a time window (F-C5). |
| `notifications` | Generic backend-storage notification table (F-A2's delivery leg, also reused by F-A3/F-A4/F-J1/F-J3), polled via `GET /api/v1/notifications?after_id=`; a JSONB payload carries type-specific fields so alert types (`BATTERY_ALERT`, `ANOMALY_ALERT`, `SOH_ALERT`, `DEVICE_OFFLINE_ALERT`, and future F-B5 types) share one table without a new migration or endpoint per type. No push, no recipient scoping (no `identity` domain yet) — poll-only for now. |

## Database

Current Alembic head: `0014_device_offline_alert`.

## Not built yet

Web portal, vehicle app, identity/RBAC, driver, policy, billing/payment,
and extended monitoring (full telemetry history, map, connector-status
aggregation, push/multi-channel delivery) have no active source in the
repo. `notifications` has a minimal backend-storage/polling slice (F-A2)
but not the multi-channel delivery F-F3 describes. See
[`docs/01-requirements/future.md`](../01-requirements/future.md) for the
full list of deferred components and why.
