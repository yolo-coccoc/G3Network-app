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
| `telemetry` | Ingests via MQTT; API only returns the **latest** record per vehicle — no history, map, or alert API yet. |
| `charging_stations` | Station → EVSE → Connector topology CRUD, OCPP 2.0.1 gateway, and station directory metadata (location, power rating, connector standard, operating hours, maintenance status, computed connector count). Assumes pre-provisioned, always-online topology; no live status/connection tracking yet. |
| `charging_sessions` | Happy-path lifecycle only: `Started → Updated/MeterValues → Ended`. No retry, out-of-order handling, or DLQ. |

## Database

Current Alembic head: `0005_station_directory_fields`.

## Not built yet

Web portal, vehicle app, identity/RBAC, driver, notification, policy,
billing/payment, and extended monitoring (full telemetry history, map,
alerts, threshold pushes) have no active source in the repo. See
[`docs/01-requirements/future.md`](../01-requirements/future.md) for the
full list of deferred components and why.
