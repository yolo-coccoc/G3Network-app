# Functional comparison between the current workspace and the GitHub repository

> Review date: 2026-09-15
>
> Current workspace: source content matches baseline `55e1342`; the current HEAD is
> the revert commit `fa7f6fc`.
>
> Reference repository: [`quanhlee123/g3-network`](https://github.com/quanhlee123/g3-network)

## Conclusion

The current workspace is a backend MVP focused on two flows:

```text
Vehicle → Telematic → Telemetry
Charging Station → Charging Session
```

The GitHub repository has a broader scope, including a simulator/phase 1 and groups
such as alerting, charging policy, reconciliation, payment, RBAC, notification, portal,
and observability. Those features are for reference only; they must not be considered
already present in the current workspace.

## Backend scope comparison

| Group | Current workspace | GitHub repository |
|---|---|---|
| Vehicle and telematic | CRUD, soft delete, device–vehicle mapping | Extended CRUD/provisioning and fleet business logic |
| Telemetry | MQTT QoS 0, validation, TimescaleDB storage, latest API | Extended query/aggregate, alerting, and device operations |
| Telemetry history | No API to read full history yet | Has extended history/monitoring flows |
| Vehicle/station map | No map API yet | Has map and geographic data |
| Battery/anomaly alerts | Not yet | Has alerting and lifecycle-based anti-repeat |
| Charging station | CRUD for the Station/EVSE/Connector topology | Extended CRUD, status, location, and monitoring |
| OCPP | OCPP 2.0.1, dedicated gateway | Different contract/protocol per the GitHub repository |
| Charging session | `Started → Updated/MeterValues → Ended`, events and meters | Additionally has policy, payment, reconciliation, and operational business logic |
| User/RBAC/driver | Not yet | Has extended identity layers and access scope |
| Frontend | No portal/vehicle app source yet | Has UI components/simulator per the repository's scope |
| Observability | Basic health endpoint and structured logging | Has extended metrics/dashboard/load-test direction |

## Active features in the current workspace

- CRUD for vehicles and telematic devices.
- Receiving telemetry via EMQX and storing it in `vehicle_telemetry`.
- `GET /api/v1/telemetry/vehicles/{vehicle_id}/latest`.
- CRUD for stations, EVSEs, and pre-provisioned connectors.
- OCPP 2.0.1 gateway.
- Storing and reading charging sessions, lifecycle events, and meter values.
- Alembic baseline `0001` through `0004` and a minimal smoke/integration test suite.

## Features not yet active

Do not infer the following features from the fact that the database already stores
telemetry or charging sessions:

- The entire telemetry history, trip data, and aggregate data.
- The full vehicle/station map and aggregated connector status.
- SOC alerts, battery anomalies, anti-repeat, and threshold pushes to devices.
- Geofencing, device health, notification, policy, reconciliation, and payment.
- Identity, RBAC, driver, portal, and vehicle app.

These items are recorded in
[`docs/01-requirements/future.md`](../01-requirements/future.md) once they have been
determined to be a direction for expansion, but they do not yet have an active
API/schema/migration.

## Principles for referencing the GitHub repository

- Only reference use cases, input data, and relevant edge cases.
- Keep the current workspace's stack, domain boundaries, naming, transaction
  ownership, and migration conventions.
- Do not directly copy OCPP, MQTT contracts, or migrations between the two
  repositories without first reconciling protocol and schema differences.
