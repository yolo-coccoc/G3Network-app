# Domain boundary rules (backend)

> Read this file before adding a new domain, calling across two domains, or
> when unsure whether an import violates a bounded context. Directory
> structure in [architecture.md](../../docs/design/architecture.md#directory-structure). Which
> product features a domain serves is mapped in
> [`docs/product/feature-list.md`](../../docs/product/feature-list.md)
> (each feature carries a **Backend domain:** field); per-feature dependencies
> are in that file's "Dependencies" column, not repeated here.

## Rules

- Each directory under `backend/app/domains/` is **1 bounded context**. A domain's **public surface** is exactly three modules: `service.py` (the only thing another domain *calls*), plus the DTOs/enums of `types.py` and the exceptions of `exceptions.py` that the service's signatures use or raise. The one extra: `identity/dependencies.py`, the FastAPI authentication dependencies (`get_current_principal`, `require_roles`, `get_client_context`) every router may depend on. Another domain **never** imports anything else from it — not `repository.py`, `models.py`, `router.py`, `schemas.py`, nor any internal module or subpackage (e.g. `telemetry.detection`, `charging_stations.ocpp_state_service`, the `ocpp/` and `ingestion/` packages).
  - This applies only **between** domains. Inside one domain, files call each other directly (e.g. `telemetry/service.py` → `telemetry/detection.py`/`telemetry/repository.py`, or the OCPP adapters → `charging_stations/ocpp_state_service.py`). A domain may keep such internal modules beside its public `service.py`; other domains never import them.
- Cross-domain calls take and return primitives or frozen dataclass DTOs (`VehicleReference`, `TransactionSessionReference`...), never an ORM model or an HTTP schema.
- Every edge is **one-directional** unless listed as an exception below. Adding a new edge (or a new domain) means adding a row to the table below in the same change.
- A new domain must reference its feature code in `feature-list.md` (e.g. `F-C1`, `F-B2`).
- **Enforced by `import-linter`**: one `forbidden` contract per domain in `backend/pyproject.toml` (`[tool.importlinter]`), run by `make lint` / `make check` and the pre-commit hook. Each contract forbids every module of the domain except `service`, `types` and `exceptions` (the public surface), so a new internal module or subpackage must be added to its domain's `forbidden_modules`. A new domain needs its own contract (copy an existing one) and must appear in the other contracts' `source_modules`. Only direct imports are checked (`allow_indirect_imports = true`).

## Current dependency edges

| From → To | Calls (public service) | For |
|---|---|---|
| `telemetry` → `telematics` | serial → `(telematic_id, vehicle_id)` resolution | F-A1 ingestion |
| `telemetry` → `vehicles` | `resolve_vehicle_reference_by_id` (existence + battery capacity); `resolve_vehicle_summary_by_id` (VIN/plate/status in the fleet live-position list); `mark_vehicle_activated` on a vehicle's first telemetry | F-A6/F-C6 reports, F-E1, F-F2 |
| `telemetry` → `notifications` | raise battery / anomaly / SOH alerts | F-A2, F-A4, F-A3 |
| `telemetry` → `charging_stations` | nearest available station (≥1 `Available` connector) for the alert payload | F-A2 |
| `telemetry` → `fleet` | `list_active_member_vehicle_ids` (fleet live positions, fleet report rollup), `find_current_fleet_id_by_vehicle` + `list_geofences_containing` (geofence entry/exit alerts) | F-E1, F-A6, F-A5 |
| `telematics` → `vehicles` | resolve/validate the vehicle mapping (a soft-deleted vehicle maps nothing); `mark_device_assigned` when a device is linked | F-G1, F-F2, F-J1 |
| `telematics` → `telemetry` | `resolve_last_telemetry_at` (device-health monitor), `resolve_vehicle_live_status` (device health on the API) | F-J1/F-J3 |
| `telematics` → `notifications` | raise device-offline alerts | F-J1/F-J3 |
| `telematics` → `fleet` | `list_active_member_vehicle_ids` (fleet-wide config push) | F-J2 |
| `charging_stations` → `charging_sessions` | OCPP adapters push normalized session events/measurements, allocate the 1.6J `transactionId`, `resolve_session_by_transaction`, `has_active_session_on_connector`; `resolve_station_energy_total` (all-stations energy endpoint); `resolve_session_command_reference` (the token and transaction ID the gateway sends in a remote start / stop) | F-B2, F-C5, F-H1 |
| `drivers` → `vehicles` | `resolve_vehicle_reference_by_vin` / `_by_id` (check-in: the truck and its owner) | F-E4 |
| `drivers` → `identity` | `resolve_membership_person_reference` (a profile's person: name, phone, statuses) | F-E4 |
| `support` → `vehicles` | `resolve_vehicle_reference_by_vin` | F-I1/F-I2 |
| `support` → `drivers` | `resolve_driver_reference_by_id` (validate + `driver_name`), `resolve_own_driver_reference` (the caller's profile), `is_membership_checked_in_to_vehicle` (an SOS on a borrowed truck) | F-I1/F-I2 |
| `support` → `notifications` | `create_notification` (`SOS_ALERT` when an SOS is created) | F-I2 |
| `fleet` → `vehicles` | `resolve_vehicle_reference_by_vin`, `resolve_vehicle_summary_by_id` | F-E1 |
| `batteries` → `vehicles` | `resolve_vehicle_reference_by_id` (the truck to fit a pack to exists) | BAT-01 |
| `batteries` → `identity` | `resolve_organization_for_new_record` (owner of a new pack or a transfer) | BAT-01 |
| `telemetry` → `batteries` | `resolve_installed_battery_capacity_kwh` (pack capacity precedence, VH-16) | MON-14 reports |
| `warranties` → `vehicles` / `batteries` / `telematics` / `charging_stations` | the owner of the covered object (`resolve_vehicle_reference_by_id`, `resolve_battery_owner_organization_id`, `resolve_telematic_owner_organization_id`, `resolve_station_owner_organization_id`) | WAR-01 |
| `app/api/vehicle_transfer.py` → `vehicles`, `fleet`, `drivers`, `batteries` | `transfer_vehicle_ownership`, `close_membership_of_sold_vehicle`, `end_open_session_on_ownership_change`, `transfer_installed_battery_with_vehicle` | VEH-02 (VH-12) |
| `telemetry` → `drivers` | `is_membership_checked_in_to_vehicle` (a driver reads the live data of the truck they are checked in to) | MON-02, DR-11 |

**Only exception — `telematics ↔ telemetry` is bidirectional** (ingestion one
way, device-health monitoring the other). The current `forbidden` contracts
don't check cycles; if an acyclic/layers contract is ever added, this pair needs
an explicit exception rather than being treated as a violation.

**Cross-domain actions that need several one-way edges (VH-21).** An action
that must change data in domains that depend on each other the wrong way round
(the ownership transfer: `vehicles` cannot call `fleet`, `drivers` or
`batteries`) is orchestrated in the HTTP layer, in a module under `app/api/`
that calls each owner's public service inside the request's one transaction.
Such a module is not a domain, holds no business rule of its own and is
listed in the table above.

## Domain roles worth knowing

- **`notifications`** is a leaf: it stores/reads notifications and depends on no domain.
- **`charging_stations`** owns station/EVSE/connector topology and the OCPP gateway (2.0.1 and 1.6J). **`charging_sessions`** only stores normalized events, measurements and session lifecycle; it owns no WebSocket and never calls back into `charging_stations`. Authorization, RFID/driver policy, remote-control logic, pricing, payment and debt are out of MVP scope — record them in `deferred.md` before reopening.
- **`telematics`'s F-J2 config-push publisher** (`commands/`) publishes over MQTT directly and needs no domain edge for a single device; only the fleet-wide push resolves the fleet's members through `telematics → fleet`.
- **`fleet`** calls only `vehicles`; `telemetry` and `telematics` call `fleet`. Fleet-wide telemetry views (live positions, the operating rollup) and geofence detection therefore live in `telemetry` (`/telemetry/fleets/{fleet_id}/...`, `telemetry/geofencing.py`) — never add a `fleet → telemetry` call, it would close a cycle.
- **`support`** is deliberately **not** wired to `telemetry`: vehicle context (VIN, location, error code) is client-supplied at case creation.
- **`identity`** (auth & RBAC, built in WP2) is the foundational domain: every domain may depend on it, it depends on none. A router authenticates with `identity.dependencies` (`Depends(require_roles(*roles_for(<feature codes>)))`, the roles per feature are `FEATURE_ROLES` in `identity/types.py`); the service takes the `Principal` and passes `principal.data_scope` (the organization, `None` for internal staff) to its repository, which filters by it, so another organization's record is a 404 (ID-50); a service that writes personal-data access rows calls `identity.service.record_data_access`. The edge `X → identity` is allowed for every X without a table row.
