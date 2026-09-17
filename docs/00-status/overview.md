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
| `vehicles` | CRUD and soft delete. |
| `telematics` | Device CRUD, mapping devices to vehicles. |
| `telemetry` | Ingests via MQTT with a versioned message schema (`schema_version`); API returns the **latest** record per vehicle plus a bounded time-range **history** query (F-A5) — no map, aggregate dashboard, or true trip segmentation yet. F-A1 done at MVP/POC scope; online/offline flag and NF-01/NF-04/NF-06 hardening deferred (`future.md` items 35, 36). F-A2 tiered battery alerts also done: SOC-threshold-crossing detection raises a notification via the `notifications` domain. F-A4 anomaly detection also done: high battery temperature, sudden voltage drop, and new device error codes are detected per-message and raise `ANOMALY_ALERT` notifications; cell/module vs. motor fault classification and motor-temperature detection are deferred (`future.md` items 42, 45). F-A5 also done at the same scope: `GET /telemetry/vehicles/{id}/history` returns telemetry points ordered chronologically within a required, capped time range; geofencing and true trip segmentation are deferred (`future.md` items 46, 47). |
| `charging_stations` | Station → EVSE → Connector topology CRUD, OCPP 2.0.1 gateway, station directory metadata (location, power rating, connector standard, operating hours, maintenance status, computed connector count), and a nearest-operational-station lookup (F-A2) using PostGIS. Assumes pre-provisioned, always-online topology; no live status/connection/occupancy tracking yet. |
| `charging_sessions` | Happy-path lifecycle only: `Started → Updated/MeterValues → Ended`. No retry, out-of-order handling, or DLQ. |
| `notifications` | Generic backend-storage notification table (F-A2's delivery leg, also reused by F-A4), polled via `GET /api/v1/notifications?after_id=`; a JSONB payload carries type-specific fields so alert types (`BATTERY_ALERT`, `ANOMALY_ALERT`, and future F-B5/F-J3 types) share one table without a new migration or endpoint per type. No push, no recipient scoping (no `identity` domain yet) — poll-only for now. |

## Database

Current Alembic head: `0009_anomaly_notification_type`.

## Not built yet

Web portal, vehicle app, identity/RBAC, driver, policy, billing/payment,
and extended monitoring (full telemetry history, map, connector-status
aggregation, push/multi-channel delivery) have no active source in the
repo. `notifications` has a minimal backend-storage/polling slice (F-A2)
but not the multi-channel delivery F-F3 describes. See
[`docs/01-requirements/future.md`](../01-requirements/future.md) for the
full list of deferred components and why.
