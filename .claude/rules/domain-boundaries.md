# Domain boundary rules (backend)

> Read this file before adding a new domain, calling across two domains, or
> when unsure whether an import violates a bounded context. Directory
> structure in [directory-structure.md](./directory-structure.md). Which
> product features a domain serves is mapped in
> [`docs/01-requirements/feature-list.md`](../../docs/01-requirements/feature-list.md)
> (each feature carries a **Backend domain:** field); per-feature dependencies
> are in that file's "Dependencies" column, not repeated here.

## Rules

- Each directory under `backend/app/domains/` is **1 bounded context**. A domain may only call another domain through that domain's **public `service.py`** (importing the DTOs/enums of its `types.py` that the service's signatures use is fine) — **never** import/query another domain's `repository.py`/`models.py`.
  - This applies only **between** domains. Inside one domain, files call each other directly (e.g. `telemetry/ingestion/mqtt_consumer.py` → `telemetry/repository.py`, or the OCPP adapters → `charging_stations/repository.py`).
- Cross-domain calls take and return primitives or frozen dataclass DTOs (`VehicleReference`, `TransactionSessionReference`...), never an ORM model or an HTTP schema.
- Every edge is **one-directional** unless listed as an exception below. Adding a new edge (or a new domain) means adding a row to the table below in the same change.
- A new domain must reference its feature code in `feature-list.md` (e.g. `F-C1`, `F-B2`).
- `import-linter` isn't installed yet (deferred to CI, `future.md` item 11), so review imports manually: no `from app.domains.<other>.repository`/`.models` outside `<other>`.

## Current dependency edges

| From → To | Calls (public service) | For |
|---|---|---|
| `telemetry` → `telematics` | serial → `(telematic_id, vehicle_id)` resolution | F-A1 ingestion |
| `telemetry` → `vehicles` | `resolve_vehicle_reference_by_id` (existence + battery capacity) | F-A6/F-C6 reports |
| `telemetry` → `notifications` | raise battery / anomaly / SOH alerts | F-A2, F-A4, F-A3 |
| `telemetry` → `charging_stations` | nearest operational station for the alert payload | F-A2 |
| `telematics` → `vehicles` | resolve/validate the vehicle mapping | F-G1 |
| `telematics` → `telemetry` | `resolve_last_telemetry_at` (device-health monitor) | F-J1/F-J3 |
| `telematics` → `notifications` | raise device-offline alerts | F-J1/F-J3 |
| `charging_stations` → `charging_sessions` | OCPP adapters push normalized session events/measurements, allocate the 1.6J `transactionId` | F-B2 |
| `drivers` → `vehicles` | `resolve_vehicle_reference_by_vin` / `_by_id` | F-E4 |
| `support` → `vehicles` | `resolve_vehicle_reference_by_vin` | F-I1/F-I2 |
| `support` → `drivers` | `resolve_driver_reference_by_id` (validate + `driver_name`) | F-I1/F-I2 |
| `fleet` → `vehicles` | `resolve_vehicle_reference_by_vin`, `resolve_vehicle_summary_by_id` | F-E1 |

**Only exception — `telematics ↔ telemetry` is bidirectional** (ingestion one
way, device-health monitoring the other). When `import-linter` is added, this
pair needs an explicit allowed-cycle exception rather than being treated as a
violation.

## Domain roles worth knowing

- **`notifications`** is a leaf: it stores/reads notifications and depends on no domain.
- **`charging_stations`** owns station/EVSE/connector topology and the OCPP gateway (2.0.1 and 1.6J). **`charging_sessions`** only stores normalized events, measurements and session lifecycle; it owns no WebSocket and never calls back into `charging_stations`. Authorization, RFID/driver policy, remote-control logic, pricing, payment and debt are out of MVP scope — record them in `future.md` before reopening.
- **`telematics`'s F-J2 config-push publisher** reads only its own repository and publishes over MQTT directly: no domain edge.
- **`support`** is deliberately **not** wired to `telemetry`: vehicle context (VIN, location, error code) is client-supplied at case creation.
- **`identity`** (auth & RBAC), once it exists, becomes the foundational domain: every domain may depend on it, it depends on none. Don't create a placeholder before a concrete task.
