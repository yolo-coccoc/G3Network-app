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
| `telemetry` → `telematics` | serial → `(telematic_id, vehicle_id)` resolution; `resolve_mounted_device_by_vehicle_id` (the device mounted now, for VEH-05 activation) | F-A1 ingestion, VEH-05 |
| `telemetry` → `vehicles` | `resolve_vehicle_reference_by_id` (existence + battery capacity); `resolve_vehicle_summary_by_id` (VIN/plate/status in the fleet live-position list); `list_vehicle_summaries` + `resolve_first_handover_at` (the computed VEH-05 activation: the trucks of a scope and their handover date) | F-A6/F-C6 reports, F-E1, VEH-05 |
| `telemetry` → `notifications` | raise battery / anomaly / SOH alerts | F-A2, F-A4, F-A3 |
| `telemetry` → `charging_stations` | nearest available station (≥1 `Available` connector) for the alert payload | F-A2 |
| `telemetry` → `fleet` | `list_active_member_vehicle_ids` (fleet live positions, fleet report rollup, optionally over the sub-fleets and under the caller's fleet limit), `resolve_principal_visible_vehicle_ids` (a limited manager reads only the trucks of their fleets), `find_current_fleet_id_by_vehicle` + `list_geofences_containing` (geofence entry/exit alerts) | F-E1, F-A6, F-A5, FLT-01, FL-10 |
| `telematics` → `vehicles` | resolve/validate the vehicle mapping (a soft-deleted vehicle maps nothing) | F-G1, F-J1 |
| `telematics` → `telemetry` | `resolve_last_telemetry_at` (device-health monitor), `resolve_vehicle_live_status` (device health on the API) | F-J1/F-J3 |
| `telematics` → `notifications` | raise device-offline alerts and deliver them (`create_notification`, `add_notification_recipients`) | DEV-05 |
| `telematics` → `identity` | `list_organization_role_holder_user_ids` (ORG_ADMIN + FLEET_MANAGER of the truck's organization receive the silence alert) | DEV-05 |
| `telematics` → `fleet` | `list_active_member_vehicle_ids` (fleet-wide config push, device health of a fleet; takes the principal so the fleet limit applies) | F-J2, FL-10 |
| `charging_stations` → `charging_sessions` | OCPP adapters push normalized session events/measurements, allocate the 1.6J `transactionId`, `resolve_session_by_transaction`, `has_active_session_on_connector`; `resolve_station_energy_total` (all-stations energy endpoint); `resolve_session_command_reference` (the token and transaction ID the gateway sends in a remote start / stop); `is_start_token_valid` (both `Authorize` handlers); the gateway's command loop calls `abandon_pending_session` (a failed remote start) and `abandon_expired_pending_sessions` (the sweep) | F-B2, F-C5, F-H1, CHG-01 |
| `drivers` → `vehicles` | `resolve_vehicle_reference_by_code` (VIN or plate) / `_by_vin` / `_by_id` (check-in: the truck and its owner), `resolve_vehicle_summary_by_id` (plate in the driver's summary) | F-E4, DR-11 |
| `drivers` → `identity` | `resolve_membership_person_reference` (a profile's person: name, phone, statuses), `membership_holds_role` (a profile needs the DRIVER role), `search_membership_ids_by_person` (list search by name/phone), `resolve_organization_settings` (auto-end time) | F-E4, DRV-01 |
| `drivers` → `telemetry` | `resolve_vehicle_live_status` (phone-to-truck check at check-in, trip start/end position, odometer, battery %), `resolve_last_telemetry_at` + `resolve_last_movement_at` (auto-end of an idle session), `resolve_distance_km_in_window` (distance in the driver's summary) | DR-07, DR-11, DR-12 |
| `app/api/membership_end_hooks.py` → `identity`, `drivers` | `register_membership_end_hook`, `handle_membership_end` (ending or locking a membership closes the driver profile and the open driving session, DR-10) | DR-10 (DR-15) |
| `support` → `vehicles` | `resolve_vehicle_reference_by_vin` | F-I1/F-I2 |
| `support` → `drivers` | `resolve_driver_reference_by_id` (validate + `driver_name`), `resolve_own_driver_reference` (the caller's profile), `is_membership_checked_in_to_vehicle` (an SOS on a borrowed truck) | F-I1/F-I2 |
| `support` → `notifications` | `create_notification` (`SOS_ALERT` when an SOS is created) | F-I2 |
| `fleet` → `vehicles` | `resolve_vehicle_reference_by_vin`, `resolve_vehicle_summary_by_id` | F-E1 |
| `drivers` → `notifications` | `create_notification` (`TRIP_ASSIGNED` to the driver a manager plans a trip for, with `recipient_user_ids`) | DRV-04, NTF-06 |
| `notifications` → `identity` | `list_organization_role_holders`, `list_internal_role_holder_user_ids` (who receives an alert by role), `list_push_targets_by_user_ids`, `list_email_targets_by_user_ids`, `remove_push_token` (delivery), plus `find_organization_reference` (channel settings); `identity` is foundational, so every domain may call it | NTF-02, 04, 05, 06 |
| `fleet` → `identity` | `resolve_organization_for_new_record` (owner of a new fleet, the tree's organization), `resolve_membership_person_reference` (a fleet limit names a membership and its organization) | FLT-01, FLT-03 |
| `app/api/fleet_visibility.py` → `identity`, `fleet` | `register_visible_vehicle_resolver`, `resolve_principal_visible_vehicle_ids` (the vehicle, driving-session and trip lists read the caller's fleet limit through the `get_visible_vehicle_ids` request dependency: `vehicles` and `drivers` cannot call `fleet`, FL-01) | FLT-03 (FL-13) |
| `batteries` → `vehicles` | `resolve_vehicle_reference_by_id` (the truck to fit a pack to exists) | BAT-01 |
| `batteries` → `identity` | `resolve_organization_for_new_record` (owner of a new pack or a transfer) | BAT-01 |
| `telemetry` → `batteries` | `resolve_installed_battery_capacity_kwh` (pack capacity precedence, VH-16) | MON-14 reports |
| `warranties` → `vehicles` / `batteries` / `telematics` / `charging_stations` | the owner of the covered object (`resolve_vehicle_reference_by_id`, `resolve_battery_owner_organization_id`, `resolve_telematic_owner_organization_id`, `resolve_station_owner_organization_id`) | WAR-01 |
| `app/api/vehicle_transfer.py` → `vehicles`, `fleet`, `drivers`, `batteries` | `transfer_vehicle_ownership`, `close_membership_of_sold_vehicle`, `end_open_session_on_ownership_change`, `transfer_installed_battery_with_vehicle` | VEH-02 (VH-12) |
| `app/api/charging_session_flow.py` → `charging_stations`, `charging_sessions`, `billing`, `drivers` | scan: `resolve_scan_target`, `has_active_session_on_connector`, `has_open_session_by_user`, `resolve_wallet_standing`, `has_minimum_balance`, `resolve_tariff_for_station`, `find_open_vehicle_id_by_membership`, `create_pending_session`, `create_quoted_bill`, `queue_station_command`; stop: `authorize_session_stop`, `queue_station_command`; receipt and bill: `get_charging_session`, `resolve_session_place_reference`, `find_session_bill_reference`, `get_session_bill_response` | CHG-01, CHG-03, PAY-10 (CE-20) |
| `app/api/billing_hooks.py` → `charging_sessions`, `billing` | `register_session_ended_hook`; the hook calls `settle_session_bill` (a completed session) or `void_session_bill` (an abandoned scan); registered by `app/api/startup.py` in the API **and** in the OCPP gateway process | PAY-10 (BL-19) |
| `billing` → `charging_stations` | `resolve_station_location_reference` (a charger's location and that location's owner: which tariff prices it), `resolve_location_owner_reference` (a tariff's location belongs to its owner), `get_charging_station` (the price endpoint checks the caller may see the charger) | PAY-09 (BL-08) |
| `billing` → `identity` | `resolve_organization_for_new_record` (owner of a new tariff) | PAY-09 |
| `telemetry` → `drivers` | `is_membership_checked_in_to_vehicle` (a driver reads the live data of the truck they are checked in to) | MON-02, DR-11 |

**Exceptions — `telematics ↔ telemetry` and `drivers ↔ telemetry` are
bidirectional** (telematics/telemetry: ingestion one way, device-health
monitoring the other; drivers/telemetry: `telemetry` asks whether a person is
checked in to a truck, `drivers` reads the truck's T-Box data, DR-15). The current `forbidden` contracts
don't check cycles; if an acyclic/layers contract is ever added, this pair needs
an explicit exception rather than being treated as a violation.

**Cross-domain actions that need several one-way edges (VH-21).** An action
that must change data in domains that depend on each other the wrong way round
(the ownership transfer: `vehicles` cannot call `fleet`, `drivers` or
`batteries`) is orchestrated in the HTTP layer, in a module under `app/api/`
that calls each owner's public service inside the request's one transaction.
Such a module is not a domain, holds no business rule of its own and is
listed in the table above. The QR charge is the second case (CE-20):
`charging_stations` already calls `charging_sessions`, so the scan, stop and
receipt, which need both plus `billing` and `drivers`, sit in
`app/api/charging_session_flow.py` instead of adding the reverse edge
`charging_sessions` → `charging_stations`. The same goes for billing (BL-19): `charging_sessions` may not call `billing`, so it exposes a hook list (`register_session_ended_hook`) and `app/api/billing_hooks.py` registers the billing function in every process that ends sessions (`app/api/startup.py`; the OCPP gateway calls it too). When `identity` (which depends on no domain) must
trigger work elsewhere (ending or locking a membership, DR-10), it exposes a
hook (`register_membership_end_hook`) that `app/api/membership_end_hooks.py`
fills with the other domain's public function at start-up (DR-15); the same
pattern gives the vehicle endpoints the caller's fleet limit
(`register_visible_vehicle_resolver`, filled by `app/api/fleet_visibility.py`,
FL-13).

## Domain roles worth knowing

- **`billing`** is a leaf (it calls no domain). WP8 added the wallet minimum and the bill read for the QR charge; the rest comes with WP9.
- **`notifications`** depends only on `identity` (the foundational domain): `create_notification` stores the alert, derives its recipients from the routing table (`routing.py`, NTF-06), puts it in their inboxes and sends the push / e-mail the organization has on (`delivery.py`, fake providers, NT-15). The two truck-related answers it cannot get itself - the driver checked in to a truck (`drivers`) and the trucks a fleet-limited manager may see (`fleet`) - are registered at start-up by `app/api/notification_hooks.py` (`register_vehicle_audience_hooks`), the same pattern as `fleet_visibility.py`; every process that raises alerts (API, telemetry ingestion, device-health monitor) calls `app.api.startup.register_notification_hooks`, and a process that does not still routes by role. Its settings routes are mounted under `/organizations/{id}/notification-settings`.
- **`charging_stations`** owns station/EVSE/connector topology and the OCPP gateway (2.0.1 and 1.6J). **`charging_sessions`** only stores normalized events, measurements and session lifecycle; it owns no WebSocket and never calls back into `charging_stations`. Authorization, RFID/driver policy, remote-control logic, pricing, payment and debt are out of MVP scope — record them in `deferred.md` before reopening.
- **`telematics`'s F-J2 config-push publisher** (`commands/`) publishes over MQTT directly and needs no domain edge for a single device; only the fleet-wide push resolves the fleet's members through `telematics → fleet`.
- **`fleet`** calls only `vehicles`; `telemetry` and `telematics` call `fleet`. Fleet-wide telemetry views (live positions, the operating rollup) and geofence detection therefore live in `telemetry` (`/telemetry/fleets/{fleet_id}/...`, `telemetry/geofencing.py`) — never add a `fleet → telemetry` call, it would close a cycle.
- **Vehicle activation (VEH-05)** is computed in `telemetry` (`telemetry/activation.py`, `/telemetry/vehicles/.../activation`), not in `vehicles`: `telemetry` already calls `vehicles` and `telematics`, so a `vehicles -> telemetry` call would close a cycle. The `vehicles` read model carries no activation field.
- **`support`** is deliberately **not** wired to `telemetry`: vehicle context (VIN, location, error code) is client-supplied at case creation.
- **`identity`** (auth & RBAC, built in WP2) is the foundational domain: every domain may depend on it, it depends on none. A router authenticates with `identity.dependencies` (`Depends(require_roles(*roles_for(<feature codes>)))`, the roles per feature are `FEATURE_ROLES` in `identity/types.py`); the service takes the `Principal` and passes `principal.data_scope` (the organization, `None` for internal staff) to its repository, which filters by it, so another organization's record is a 404 (ID-50); a service that writes personal-data access rows calls `identity.service.record_data_access`. The edge `X → identity` is allowed for every X without a table row.
