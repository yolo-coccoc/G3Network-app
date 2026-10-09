# Backend refactor and implementation plan

> The one progress file for the refactor-and-build run (PR-15 in
> [decision-log.md](../decisions/decision-log.md)). The design review
> ([DBML](../design/domain-model/domain-model.dbml) + decision log) is the
> owner's confirmation of scope: no per-feature planners, no stops for
> approval. Work on `master`, `make check` + commit + push after every step.
> Client flows: [mobile app](../scenario/mobile-app.md),
> [web portal](../scenario/web-portal.md). Features:
> [features.yaml](../product/features/features.yaml).

## Rules in short

- Schema: one baseline migration (`0001_baseline_schema.py`), `make db-reset`,
  `make db-check`; small DBML deviations are recorded in the decision log + DBML.
- Skipped for the whole run: parked groups (support beyond `support_cases`,
  policy, subscription plans/invoices, `driver_scores`, geofence redesign,
  `promotion_campaigns`), open questions 4, 5, 7-11, every `deferred.md` item,
  features not in release P1.0, anything needing a large redesign.
- External providers (VietQR notifications, Firebase push, e-mail, OTP SMS)
  sit behind an interface with a logging fake. Command channel:
  `charging_station_commands` (PR-16).
- Tests lean: 1-2 smoke tests per feature; `make backend-test-integration`
  once per package that changes the schema or a repository.

## Work packages

| WP | Scope | Features | Tables | Depends on | Status |
|---|---|---|---|---|---|
| WP1 | Schema refactor: every built table to its target design and every planned table a non-skipped feature needs; baseline migration; tags dropped from the DBML; code adapted to renamed/dropped columns. Fleets + memberships were done first (FL-08, FL-09). | all below | all non-skipped | – | IN PROGRESS: fleets, memberships and the identity tables + change-history mechanism DONE (chunk 1); vehicles, vehicle models, battery models, batteries, warranties DONE (chunk 2); telematics, telematic_status_reports and telemetry DONE (chunk 3); drivers, driving_sessions, trips and fleet_user_assignments DONE, driver_vehicle_assignments dropped (chunk 4, DR-13); charging onward TODO |
| WP2 | Identity: organizations, users, memberships, roles, credentials, OTP, login/sessions, consent, audit log, access rule by role only (BL-16) | ACC-01..10, 12..18, 20 | organizations, users, user_state, memberships, user_credentials, user_sessions, one_time_codes, user_consents, legal_documents, user_role_assignments, access_audit_logs, organization_settings | WP1 | TODO |
| WP3 | Vehicles, vehicle models, battery models, batteries, warranties | VEH-01..03, 05, 06, BAT-01, WAR-01 | vehicles, vehicle_models, battery_models, batteries, warranties | WP2 | TODO |
| WP4 | Telematics and telemetry adjustments, T-Box status reports | DEV-01..06, 08, MON-01, 02 | telematics, telematic_status_reports, telemetry | WP3 | TODO |
| WP5 | Drivers, driving sessions (check-in by app or portal; QR content deferred), trips | DRV-01, 02, 04, MON-11 | drivers, driving_sessions, trips (driver_vehicle_assignments dropped) | WP2, WP3 | TODO |
| WP6 | Fleet: `fleet_user_assignments`, fleet access limits, fleets/memberships take `organization_id`, `added_by`/`removed_by`, fleet change history | FLT-01..03 | fleets, fleet_vehicle_memberships, fleet_user_assignments | WP2 | TODO |
| WP7 | Charging stations: locations, access, state tables, configuration snapshots, commands and the channel | STN-01..04, 10, 12 | charging_locations, charging_location_access, charging_stations, charging_station_state, charging_evses, charging_connectors, charging_connector_state, charging_station_configuration_*, charging_station_commands | WP2 | TODO |
| WP8 | QR charging start: PENDING sessions, token matching, remote start, measurements | CHG-01..04, 07 | charging_sessions, charging_session_measurements (events dropped) | WP5, WP7 | TODO |
| WP9 | Billing: tariffs + versions, session bills, wallets + ledger, VietQR top-up with fake notifier, minimum balance | PAY-06, 07, 09, 10 | tariffs, tariff_versions, charging_session_bills, payments, wallets, wallet_transactions | WP8 | TODO |
| WP10 | Notifications: recipients (seen/read), organization channel settings, fake push and e-mail | NTF-01, 02, 04..06 | notifications, notification_recipients, organization_notification_settings | WP2 | TODO |

Order of work inside WP1 follows the dependencies: identity tables first (other
tables take `organization_id`), then vehicles, telematics/telemetry, drivers,
fleet rest, charging, billing, notifications.

## Skipped (with reason)

| Item | Reason |
|---|---|
| support tables beyond `support_cases` (`repair_partners`, `maintenance_bookings`) | parked group (PR-15) |
| policy (`charging_policies` ..., `policy_violations`), POL-* | parked group; open question 10 |
| `subscription_plans`, `subscriptions`, `plan_features`, `invoices`, `invoice_lines`, PAY-01..05, 08, 11 | parked (plans/invoices) |
| `driver_scores`, SAF-*, DRV-05..07 | parked group |
| geofence redesign (FL-11) | parked, `deferred.md` 86 |
| `promotion_campaigns`, PAY-12 | parked group |
| features with release P1.1 / P1.5 / P2 (ACC-11, 19, 21, 22, DEV-07, STN-09, STN-15, CHG-08, NTF-03, 07, 08, ...) | not P1.0 |
| MNT-*, RTE-*, CRB-*, PLT-*, SAF-*, TMS-*, most MON-* | no table in the reviewed design / not part of this run's packages |

## Open points

(Question + the assumption taken. Added as they come up.)

- **WP1 chunk 1, history without a reason**: DM-21 says a tracked change without a reason fails; services have no acting user yet. Assumption: the trigger records `Unspecified change` and a NULL actor (DM-29 in the decision log, rule in `database.md`); the fleet repository sets fixed reasons for edit/delete.
- **Chunk 1, history tables are not models**: `<singular>_history` tables are built from the live source table by `app/libs/db/history_ddl.py` and excluded from autogenerate by name; the DBML marks a built one with `@history built` (new tag, domain-model tool adapted: `check` skips generated history tables).
- **Chunk 1, enums as varchar**: the DBML types every identity status/role/purpose as `varchar(n)` with a `Values:` list and no check constraint, so the columns are plain `String` and the allowed values live in `identity/types.py` enums (no DB-level value check).
- **Chunk 1, `user_role_assignments` keys**: both the two-column FK `(membership_id, organization_id)` and the single `membership_id` / `organization_id` FKs of the DBML Refs are created; the domain-model `check` now accepts a Ref that matches any of a column's FKs.
- **Chunk 1, fleet creation takes `organization_id` in the body** (required) until WP2 adds authentication; an unknown organization is detected from the FK violation (404 `FleetOrganizationNotFoundError`) because the identity domain has no service yet.
- **Chunk 1, identity import-linter contract** lists only `identity.models` as forbidden (the other modules do not exist yet); WP2 adds `repository`/`router`/`schemas` when it creates them. Ruff's `banned-from` already lists the future `identity.service` / `identity.repository`.
- **Chunk 1, `added_by` / `removed_by`** are parameters of the fleet repository only (default `None`); the service does not pass them until the caller is known (WP2).
- **Chunk 2, batteries and warranties live in their own domains** (`backend/app/domains/batteries`, `warranties`; VH-13, VH-14) with models and enums only, not in `vehicles/models.py`; each has an import-linter contract (`models` forbidden) and a ruff `banned-from` entry. WP3 adds services, repositories and endpoints there.
- **Chunk 2, no activation**: `vehicles.activation_status`, `mark_device_assigned`, `mark_vehicle_activated` and `GET /vehicles/activation-summary` are removed (VH-06); the computed activation and success rate are for WP4 (VH-20).
- **Chunk 2, vehicle pack capacity** is the vehicle model's nominal capacity only; the installed battery's design capacity (VH-16) needs `batteries` to be readable and comes with WP3/WP4 (VH-20).
- **Chunk 2, vehicle create body**: `organization_id` and `vehicle_model_id` are required body fields, `acquired_at` is optional (default now); `/vehicle-models` create/list/get exist so a vehicle can be created. Ownership transfer (VH-12) is WP3.
- **Chunk 2, status of period views**: both `vehicle_ownership_periods` and `battery_installation_periods` are created by the baseline migration (the DBML defines both); no code reads them yet.
- **Chunk 3, status reports not ingested**: `telematic_status_reports` exists (model, migration, newest report read for the device response) but nothing writes it: the status-topic fields are provisional (mqtt-spec.md 2.2). WP4 adds the consumer once the vendor confirms the format.
- **Chunk 3, API names kept**: HTTP responses and the MQTT payload keep `soc`, `speed`, `message_uuid`, `signal_strength`...; only table columns carry the unit names (TM-16, TM-18). `GET /telemetry/.../latest` still returns `telematic_serial`, resolved from `telematic_id` through the telematics service.
- **Chunk 3, device create**: `organization_id` is a required body field (like vehicles), `acquired_at` defaults to now, `installed_at` is set to now when a device is mounted (create or update) and cleared when unmounted; a soft delete sets INACTIVE, unmounts and stores the reason (DM-25). The organization is not validated beyond the foreign key (404 from SQLSTATE 23503, like vehicles).
- **Chunk 3, config push**: the single and fleet pushes still refuse an INACTIVE / not-ACTIVE device but do not require the device to be mounted (TX-08 says only a mounted, ACTIVE device receives configuration); the organization-wide interval push (TX-09) is WP4.
- **Chunk 4, identity gets a first service**: `identity/service.py` + `repository.py` hold only `resolve_membership_person_reference` (membership + user); `identity.repository` is now in the identity import-linter contract. WP2 extends them.
- **Chunk 4, driver API shape**: `POST /drivers/` takes `membership_id` (the driver's name/phone moved to users, DR-09); the list searches the licence number only. A duplicate licence number across persons is not warned about (DR-09's warning is for the app/portal).
- **Chunk 4, check-in without authentication**: `/driving-sessions` endpoints take `driver_id` in the body; ineligible drivers get `400` (no "forbidden" error base). A driver whose open session is on the same truck gets that session back (idempotent check-in).
- **Chunk 4, session auto-end and ownership change**: `AUTO_ENDED` (needs telemetry + `organization_settings`) and `OWNER_CHANGED` (VH-12 transfer) are never written yet; the end-cause values exist.

## Known issues

(File, what is wrong, why it was left. For the debugging phase.)

| File | Issue | Why left |
|---|---|---|
| `backend/app/libs/db/history_ddl.py` (trigger) | A tracked update with no `set_change_context` is recorded as `Unspecified change` with a NULL actor instead of failing (DM-29). | No acting user before authentication (WP2); switch the trigger back to raising once every tracked update goes through a service that knows its user. |
| `backend/app/domains/fleet/repository.py` | `update_fields` / `soft_delete` set fixed reasons (`Fleet details edited`, `Fleet deleted`) with `changed_by=None`; a typed reason for an administrative decision is not possible yet. | Needs the caller's user and the request's reason (WP2). |
| `backend/app/domains/fleet/service.py` | Adding a vehicle does not check the vehicle belongs to the fleet's organization (FL-09), and `added_by` / `removed_by` are never filled. | `vehicles.organization_id` is not built (later WP1 chunk) and there is no caller (WP2). |
| `backend/app/domains/fleet/service.py` | `FleetOrganizationNotFoundError` is derived from a foreign-key violation (SQLSTATE 23503), so any other FK failure of the insert (e.g. a parent deleted concurrently) would also report "organization not found". | No identity service yet to resolve an organization; replace with a service call in WP2. |
| `backend/app/libs/db/history_ddl.py` | The baseline clear step does not drop the trigger functions (`fn_*_history`); they are re-created with `CREATE OR REPLACE` and stay behind if a table stops being tracked. | The clear step is documented to drop only tables, sequences and enum types; a stale function is harmless. |
| `identity` tables | No service, repository, router or seed data (not even the default `organization_settings` / `user_state` rows "created together with" their parent). | Service layer is WP2. |
| `backend/tests/test_postgres_integration.py` | The history trigger is tested on `organizations` only; `user_history`, `membership_history`, `organization_setting_history` and `fleet_history` are created by the same helper but not asserted row by row. | Lean tests (plan rule); same code path. |
| `backend/app/domains/vehicles/service.py` | `create_vehicle` derives "organization not found" from any foreign-key violation (SQLSTATE 23503) after the vehicle model was pre-checked; the same limitation as the fleet service. | No identity service to ask yet; replace with a service call in WP2. |
| `backend/app/domains/vehicles/repository.py` | Vehicle updates record the fixed reason `Vehicle details edited` / `Vehicle deleted` with a NULL actor; a typed reason (e.g. for a status change to INACTIVE) is not possible. | Needs the caller's user and reason (WP2). |
| `backend/app/domains/fleet/service.py` | The FL-09 organization check reads the vehicle's owner in a second query (`resolve_vehicle_organization_id`) instead of carrying `organization_id` on `VehicleReference`. | Avoided touching ~50 test constructors; fold it into the DTO when `VehicleReference` is next changed. |
| `backend/app/domains/batteries`, `warranties` | Models and enums only: no service, repository, router or seed data; the `limits` keys of a warranty are not validated. | Services are WP3. |
| `backend/tests/test_postgres_integration.py` | The history trigger of `vehicle_models`, `battery_models`, `batteries` and `warranties` is not asserted (only `vehicles` through the ownership view and `batteries` through the installation view). | Lean tests; same helper. |
| `backend/app/domains/vehicles/router.py` | The F-F2 activation success rate endpoint no longer exists anywhere. | Replaced by computed activation in WP4 (VH-06). |
| `backend/app/domains/telematics/service.py` | `update_telematic` does not let a caller clear `imei` or `status_reason` (null means "unchanged"), and a status change does not require a reason. | Convention for PATCH nulls; reason handling needs the caller (WP2). |
| `backend/app/domains/telematics`, `telemetry` | No tests of `telematic_history` beyond the soft-delete row; `telematic_status_reports` has no insert path or test. | Lean tests; ingestion is WP4. |
| `backend/app/domains/telemetry/service.py` | `get_latest_vehicle_telemetry_response` falls back to an empty `telematic_serial` if the device row cannot be found (cannot happen under the foreign key). | Keeps the response type non-null without a new error. |
| `simulator/seed_simulator_devices.py` | Posts a device without `organization_id` (required now) and with the dropped `firmware_version`, so the seed fails against the new API. | `simulator/` was out of scope for this chunk; fix with the e2e run. |
| `backend/app/domains/drivers/service.py` | Check-in does not compare the phone position with the truck's last T-Box position (DR-07), does not warn about a driver from another organization, and ending a membership does not close the profile and session (DR-10). | Needs telemetry access / identity membership-end service (WP5, WP2). |
| `backend/app/domains/drivers` | `trips` has a model and table only: no repository, service or endpoint (plan / start / finish, DR-12), and no test of the `uq_trips_session_in_progress` index. | WP5. |
| `backend/app/domains/fleet` | `fleet_user_assignments` has a model and table only (no repository or service; the FL-10 same-organization check is the service's job). | WP6. |
| `backend/tests/test_postgres_integration.py` | `trip_history` is not asserted (only `driver_history`). | Lean tests; same helper. |
