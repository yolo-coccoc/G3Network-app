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
| WP1 | Schema refactor: every built table to its target design and every planned table a non-skipped feature needs; baseline migration; tags dropped from the DBML; code adapted to renamed/dropped columns. Fleets + memberships were done first (FL-08, FL-09). | all below | all non-skipped | – | TODO (fleets, memberships DONE) |
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

## Known issues

(File, what is wrong, why it was left. For the debugging phase.)

| File | Issue | Why left |
|---|---|---|
