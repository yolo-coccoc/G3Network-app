# Scenario: the web portal

> Step-by-step flows for the three portal users: the **fleet manager /
> dispatcher** (a customer organization), the **ORG_ADMIN** (one per
> organization), and **our operations staff** (users of an internal
> organization, `is_internal`, who see every organization). Same conventions
> as [mobile-app.md](./mobile-app.md): **built** = the route exists in
> `backend/app/domains/*/router.py` today, **planned** = proposed path under
> `/api/v1`. Facts live in their own homes ([decision-log.md](../decisions/decision-log.md),
> [domain-model.dbml](../design/domain-model/domain-model.dbml),
> [feature catalog](../product/features/README.md)). Written 2026-10-09; identity flows built 2026-10-10 (WP2a).

## Conventions

- **Access since WP2b (ID-50):** every endpoint below needs `Authorization: Bearer`
  (`401` otherwise) and a role from the `users` list of its feature (`403`
  otherwise; HEAD_ADMIN, CO_ADMIN and ORG_ADMIN always pass). The roles per
  endpoint family are in `FEATURE_ROLES` (`identity/types.py`) and in the
  router constants of each domain; query and body fields that named the actor
  (`granted_by`, `requested_by`, `user_id`, ...) are gone, the caller is the
  actor.
- Data reach is decided by the organization, not the role (ID-44,
  [decision-log.md:229](../decisions/decision-log.md#L229)): internal users see
  every organization, everyone else only their own; a fleet manager or
  dispatcher is further limited to the fleets given to them (FL-10, flow 5).
  An out-of-reach record answers `404` (not `403`) so IDs are not guessable.
- Roles used here: `HEAD_ADMIN` / `CO_ADMIN` (internal only), `ORG_ADMIN`,
  `FLEET_MANAGER`, `DISPATCHER`, `SALES`, support and billing staff. Which role
  gets which feature is set in the permission-granting step (ID-44).
- A change to a tracked table (DM-21) carries a `reason` (varchar 200) in the
  body; without it the call fails with `422`. Deletes are soft (DM-25).
- Lists use `offset` and `limit`. Common errors on every call: `401`, `403`
  role lacks the feature, `404`, `409` conflict or disallowed state, `422`
  validation, `400` breaks a business rule.
- Screens that show personal or location data write one `VIEW` row to
  `access_audit_logs` when opened; exports need a `reason` (flow 14).

## 1. Organizations (our staff)

Tables: `organizations`, `organization_settings`. Onboarding: our sales
creates a company's organization, subscription and first ORG_ADMIN (ID-15).

| Step | API call | Main error cases |
|---|---|---|
| List / search customers | `GET /organizations?q=&status=&page=&page_size=` (**built**; internal callers see all, others their own) | none |
| Create customer: legal form, tax code, names | `POST /organizations` `{legal_form, display_name, legal_name, tax_code, address, first_admin: {phone_number, full_name}}` (**built**; HEAD_ADMIN / CO_ADMIN / SALES; `is_internal: true` HEAD_ADMIN only); sends the invite code to the first ORG_ADMIN | `409` tax code already used; `400` tax code length does not fit `legal_form`; `403` |
| Edit profile / assign account manager | `PATCH /organizations/{organization_id}` `{..., reason}`; `PUT /organizations/{organization_id}/account-manager` `{account_manager_id, reason}` (**built**; the manager must be an active SALES user of an internal organization) | `422` missing reason; `400` not a SALES user; `403` |
| Suspend or close (unpaid debt, contract end) | `POST /organizations/{organization_id}/status` `{status, reason}` (**built**); logins are blocked, memberships stay (ID-34), a closed organization is soft-deleted and `ACTIVE` reopens it | `409` same status already; `403` only HEAD_ADMIN / CO_ADMIN |
| Own settings (ORG_ADMIN): driving-session auto-end time, telemetry interval | `GET` / `PATCH /organizations/{organization_id}/settings` `{driving_session_auto_end_minutes, telemetry_interval_seconds, reason}` (**built**, ID-45) | `422` value out of range or missing reason |

## 2. Users, members and roles

Tables: `users`, `memberships`, `user_role_assignments`, `one_time_codes`.
Rules: ID-26, ID-33 ([decision-log.md:218](../decisions/decision-log.md#L218)).

| Step | API call (all **built**, WP2a) | Main error cases |
|---|---|---|
| Members list with roles and status | `GET /organizations/{organization_id}/members` | none |
| **Invite** by phone and name; roles chosen | `POST /organizations/{organization_id}/members` `{phone_number, full_name, roles[]}`; membership INVITED, SMS invite link (72 h) | `409` already an active member; `400` role not allowed here (HEAD_ADMIN / CO_ADMIN only in internal organizations); the DRIVER role is granted without a driver-profile check (the drivers domain depends on identity, not the reverse) |
| Resend or cancel an invitation | `POST /organizations/{organization_id}/members/{membership_id}/resend` | `409` already accepted |
| Grant / revoke a role | `POST /memberships/{membership_id}/roles` `{role}`, `DELETE /memberships/{membership_id}/roles/{role}` | `409` role already held; `409` revoking the ORG_ADMIN (use handover); the revoke takes no reason (role assignments keep none) |
| **Lock** a member in this organization (account-wide lock is for our HEAD_ADMIN / CO_ADMIN only) | `POST /memberships/{membership_id}/lock` `{reason}`; `.../unlock` | `409` the ORG_ADMIN cannot be locked before a handover |
| Remove a member (ends membership, revokes roles; closing the driver profile and open driving session, DR-10, is still the drivers domain's job) | `DELETE /memberships/{membership_id}?reason=` (also cancels a pending invitation); `POST /memberships/{membership_id}/leave` for the person themself | `409` ORG_ADMIN; `404` |
| **Hand over ORG_ADMIN** to another active member (one transaction: grant new, revoke old) | `POST /organizations/{organization_id}/admin-handover` `{to_membership_id, reason}` | `409` target not ACTIVE; `403` caller is not the current ORG_ADMIN. When the admin is gone, our CO_ADMIN calls the same path with `force: true` |
| Lock / unlock a whole account (internal only); list and read accounts | `POST /users/{user_id}/lock` `{reason}`, `POST /users/{user_id}/unlock`, `GET /users`, `GET /users/{user_id}` | `403` not HEAD_ADMIN / CO_ADMIN |

## 3. Vehicles, batteries, warranties

Tables: `vehicles`, `vehicle_models`, `batteries`, `battery_models`,
`warranties`. Owner changes are recorded as a handover (DM-22, VH-10), not as
a second table.

| Step | API call | Main error cases |
|---|---|---|
| Vehicle list with activation summary | `GET /vehicles` and `GET /vehicles/activation-summary` (**built**) | none |
| Register a truck: VIN, plate, model, owner, `acquired_at` | `POST /vehicles/` (**built**) | `409` duplicate VIN or plate among live trucks; `422` |
| Open / edit a truck | `GET /vehicles/{vehicle_id}`, `PATCH /vehicles/{vehicle_id}` (**built**; `reason` planned with change history) | `404`; `409` |
| Hand over to a new owner (sale) | `POST /vehicles/{vehicle_id}/handover` `{organization_id, acquired_at, reason}` (planned); closes the fleet membership, VH-12 | `409` open driving session; `404` target organization |
| Retire (soft delete, status INACTIVE) | `DELETE /vehicles/{vehicle_id}` (**built**) | `409` still has a live device or open session |
| Battery pack: register, fit to a truck (`installed_at`), history | `POST/GET/PATCH /batteries`, `POST /batteries/{battery_id}/installation` (planned) | `409` pack already fitted elsewhere |
| Warranty: dates, terms, void | `POST/GET/PATCH /warranties`, `POST /warranties/{warranty_id}/void` `{reason}` (planned) | `409` overlapping term; `400` end before start |
| Check-in code (QR sticker) regeneration | `POST /vehicles/{vehicle_id}/check-in-code` `{reason}` (planned, deferred item 94) | `403` |

## 4. Devices (telematics) and device health

Tables: `telematics`, `telematic_status_reports`. One live device per truck
(`uq_telematics_active_vehicle`).

| Step | API call | Main error cases |
|---|---|---|
| Register a T-Box (serial) and link to a truck | `POST /telematics/` (**built**) | `409` serial exists, or the truck already has a live device; `404` truck |
| Device list with online / silent state | `GET /telematics/` (**built**; health computed from newest telemetry) | none |
| Open, edit, replace device | `GET/PATCH /telematics/{telematic_id}` (**built**) | `409` |
| Unlink / retire | `DELETE /telematics/{telematic_id}` (**built**, soft delete: the truck keeps it as history) | `404` |
| Push configuration to one device or a whole fleet (F-J2) | `POST /telematics/{telematic_id}/config`, `POST /telematics/fleets/{fleet_id}/config` (**built**; MQTT publish) | `404`; `502` broker unreachable |
| Device-offline alert | system raises it into `notifications`; shown in the inbox (flow 12) | not a call |

## 5. Fleets (tree, trucks, user limits)

Tables: `fleets`, `fleet_vehicle_memberships`, `fleet_user_assignments`,
`geofences`. Rules: FL-09, FL-10
([decision-log.md:359](../decisions/decision-log.md#L359)).

| Step | API call | Main error cases |
|---|---|---|
| Create a fleet, optionally under a parent (tree) | `POST /fleets/` (**built**) | `409` duplicate name under the same parent; `400` cycle in the tree |
| Fleet tree / list / open | `GET /fleets/`, `GET /fleets/{fleet_id}` (**built**) | `404` |
| Rename, move, delete | `PATCH`, `DELETE /fleets/{fleet_id}` (**built**; delete closes memberships, FL-06) | `409` fleet has children |
| **Add a truck** | `POST /fleets/{fleet_id}/vehicles` `{vehicle_vin}` (**built**) | `404` unknown VIN; `409` truck already in a fleet |
| **Remove a truck** | `DELETE /fleets/{fleet_id}/vehicles/{vehicle_vin}` (**built**) | `404` not a member |
| Trucks of a fleet and membership history | `GET /fleets/{fleet_id}/vehicles`, `GET /fleets/{fleet_id}/memberships` (**built**), `DELETE /fleets/{fleet_id}/memberships/{fleet_vehicle_membership_id}` (**built**) | `404` |
| **Give a user a fleet limit** (FL-10): a FLEET_MANAGER or DISPATCHER sees the chosen fleets and everything below them | `POST /memberships/{membership_id}/fleets` `{fleet_id}`, `DELETE /memberships/{membership_id}/fleets/{fleet_id}`, `GET /memberships/{membership_id}/fleets` (planned) | `400` fleet is not in the membership's organization; the portal warns when the fleet is already covered by an assigned parent (allowed) |
| Geofences of a fleet | `POST/GET/PATCH/DELETE /fleets/{fleet_id}/geofences[/{geofence_id}]` (**built**) | `400` invalid polygon; `404` |

## 6. Drivers and trip planning

Tables: `drivers`, `driving_sessions`, `trips`. Rules: DR-07, DR-10, DR-12.
The built `drivers` routes use the new profile shape (WP1 chunk 4): `POST /drivers/`
takes an existing `membership_id`, not a phone and name (the invite flow is WP2),
and the old `/drivers/{driver_id}/assignment(s)` routes are gone. Check-in is built
as `POST /driving-sessions/` (and `/check-out`, `GET /driving-sessions/`) with
`vehicle_vin` in the body; `driver_id` is optional (the caller's own profile when omitted, a manager may name another driver of the organization, WP2b).

| Step | API call | Main error cases |
|---|---|---|
| Register a driver: membership, licence number/class/expiry | `POST /drivers/` `{membership_id, license_number, license_class, license_expires_on}` (**built**); the person and invite come from flow 2 | `409` person already has a driver profile in this organization; `400` licence expired |
| Driver list and profile | `GET /drivers/`, `GET /drivers/{driver_id}`, `PATCH`, `DELETE` (**built**) | `404`; `409` |
| Check a driver in to a truck for them | `POST /driving-sessions/` `{check_in_method: "PORTAL", driver_id, vehicle_vin}` (**built** without the caller check) | `403` driver inactive; a takeover is not an error (old session ends TAKEN_OVER) |
| Who is driving now / session history | `GET /driving-sessions?vehicle_id=&driver_id=&from=&to=` (planned; managers see full detail, DR-08) | none |
| **Plan a trip**: origin, destination, planned times, driver, truck | `POST /trips` `{origin_name, destination_name, planned_start_at, planned_end_at, planned_driver_id, planned_vehicle_id}` (planned) | `404` driver or truck; `409` truck not in reach; end before start `422` |
| Dispatch board (by day, status) | `GET /trips?from=&to=&status=&fleet_id=` (planned) | none |
| Reassign / cancel a trip | `PATCH /trips/{trip_id}` `{..., reason}`, `POST /trips/{trip_id}/cancel` `{reason}` | `409` trip already IN_PROGRESS or COMPLETED |
| Trip result: actual times, distance, energy, kWh/km | `GET /trips/{trip_id}` (planned; differences of stored readings); a driver or truck other than planned is flagged | `404` |

## 7. Live map and reports

All **built** under `/telemetry`; fleet endpoints need the fleet in reach.

| Step | API call | Main error cases |
|---|---|---|
| Live map of a fleet | `GET /telemetry/fleets/{fleet_id}/vehicles/latest` | `404` fleet |
| One truck now / history trail | `GET /telemetry/vehicles/{vehicle_id}/latest`, `.../history` (both log a `VIEW`) | `404`; `422` range |
| Operating report (truck, fleet) | `GET /telemetry/vehicles/{vehicle_id}/operating-report`, `GET /telemetry/fleets/{fleet_id}/operating-report` | `422` bad period |
| Battery health, energy use | `GET /telemetry/vehicles/{vehicle_id}/battery-health`, `.../energy-usage` | `404` |
| Export a report | `POST /reports/exports` `{report, params, reason}` (planned) | `422` missing reason |

## 8. Charging network (our staff and station owners)

Tables: `charging_locations`, `charging_location_access`, `charging_stations`,
`charging_evses`, `charging_connectors`, `charging_station_configuration_*`,
`charging_station_commands`, `charging_ocpp_messages`. Decisions: CS-10 ..
CS-20. A location holds stations; a private location is open only to its
owner and to organizations in `charging_location_access`.

| Step | API call | Main error cases |
|---|---|---|
| Locations: create with pin, address, public or private | `POST/GET/PATCH/DELETE /charging-locations[/{location_id}]` (**built**; the owner is the caller's organization, internal staff may name another in `organization_id`) | `400` coordinates required; `409` |
| Stations (chargers): register at a location by OCPP identity | `POST /charging-stations` `{location_id, ocpp_identity, registered_serial_number, physical_reference, max_power_kw}` (**built**) | `409` identity or serial exists; `404` location |
| List, open, edit, retire | `GET /charging-stations`, `GET/PATCH/DELETE /charging-stations/{station_id}` (**built**) | `404` |
| EVSEs and connectors | `POST/GET /charging-stations/{station_id}/evses`, `GET/PATCH/DELETE /charging-evses/{evse_id}`, `POST/GET /charging-evses/{evse_id}/connectors`, `GET/PATCH/DELETE /charging-connectors/{connector_id}`, `GET /charging-stations/{station_id}/connectors` (**built**) | `409` topology conflict (duplicate OCPP id, active session on the connector) |
| **Access** for a private location: allow an organization, optional `valid_until`, revoke | `POST /charging-locations/{location_id}/access` `{allowed_organization_id, valid_until}`, `GET` the live grants, `POST .../access/{access_id}/revoke` `{revoke_reason}` (**built**; `granted_by` / `revoked_by` are the caller; the nearby search shows public locations, the caller's own and granted ones) | `409` already granted; `404` |
| **Configuration**: read what the charger reports | `GET /charging-stations/{station_id}/configuration` (**built**); request a fresh capture and change a key: `POST /charging-stations/{station_id}/commands` `{command_type: "GET_CONFIGURATION" \| "CHANGE_CONFIGURATION", parameters, reason}` answers `202`; poll `GET /charging-stations/{station_id}/commands/{command_id}` (**built**; every command type of the DBML is accepted) | `409` charger offline; command outcome `REJECTED`, `ERROR`, `TIMEOUT` or `NOT_SENT` is shown with the charger's own status |
| **Commands**: reset, unlock connector, availability, trigger message | same `POST .../commands` and `GET /charging-stations/{station_id}/commands?outcome=` for the log (planned; rows of `charging_station_commands`) | `400` operator reason required; `409` unlock during a live session |
| Raw OCPP message viewer (STN-15) | `GET /charging-stations/{station_id}/ocpp-messages` (planned; raw frames hold RFID tokens, so a `VIEW` is audited) | `403` |
| Sessions and energy | `GET /charging-sessions`, `.../{session_id}`, `.../measurements`, `GET /charging-sessions/stations/{station_id}/energy[/series]`, `GET /charging-sessions/stations/energy` (**built**) | `404` |

## 9. Tariffs and bills

Tables: `tariffs`, `tariff_versions`, `charging_session_bills`. Rules: BL-08,
BL-10. Only our HEAD_ADMIN / CO_ADMIN publish versions for the public
network; a customer owning private chargers prices its own locations.

| Step | API call (all planned) | Main error cases |
|---|---|---|
| Tariff list per owner and location | `GET /tariffs?organization_id=&location_id=` | none |
| Create a tariff | `POST /tariffs` `{location_id?, name}` | `409` a second ACTIVE default for the owner |
| **Publish a version**: price per kWh, time-of-use periods (Vietnam time), VAT rate, reason | `POST /tariffs/{tariff_id}/versions` | `400` `effective_from` in the past; `422` overlapping periods; `422` missing reason |
| Retire a tariff | `POST /tariffs/{tariff_id}/retire` `{reason}` | `409` |
| Bills list (by organization, status, month) | `GET /charging-session-bills?status=&from=&to=` | none |
| Review an ON_HOLD bill (meter mismatch) and release or void | `POST /charging-session-bills/{bill_id}/release` or `/void` `{reason}` | `409` bill already BILLED (never edited, corrections are refunds or adjustments) |

## 10. Wallets and top-ups (our billing staff)

Tables: `wallets`, `payments`, `wallet_transactions`. BL-13 .. BL-15.

| Step | API call (all planned) | Main error cases |
|---|---|---|
| Find a wallet by phone | `GET /wallets?phone_number=` | `404` |
| Transactions and payments of a wallet | `GET /wallets/{wallet_id}/transactions`, `GET /wallets/{wallet_id}/payments` | none |
| Unmatched bank transfers (wrong `transfer_code`) and manual match | `GET /payments/unmatched`, `POST /payments/{payment_id}/match` `{user_id, reason}` | `409` payment already SUCCEEDED |
| **Adjust** a balance | `POST /wallets/{wallet_id}/adjustments` `{amount, reason}` (ADJUSTMENT row) | `422` reason required; `403` role |
| Block / unblock a wallet (fraud check) | `POST /wallets/{wallet_id}/status` `{status, reason}` | `409` same status |
| Refund unused balance through the original top-up | `POST /payments/{payment_id}/refund` `{amount, reason}` | `409` more than was topped up |

## 11. Notification settings

Table: `organization_notification_settings` (NT-12). Only exceptions are
stored; a missing row uses the default from code. The inbox always shows
every alert; the switch controls push and e-mail only. No per-person choice.

| Step | API call (planned) | Main error cases |
|---|---|---|
| List kinds with their effective push / e-mail state | `GET /organizations/{organization_id}/notification-settings` | none |
| Switch a kind | `PUT /organizations/{organization_id}/notification-settings/{notification_type}` `{push_enabled, email_enabled, reason}` | `404` unknown kind; `403` ORG_ADMIN only |

## 12. Notifications inbox in the portal

Same routes as the app: `GET /notifications`, `GET /notifications/unread-count`,
`PATCH /notifications/{notification_id}/read`, `POST /notifications/mark-all-read`
(all **built**). Alerts for a truck go to the portal as well as to the driver
checked in (NT-07); a truck moving with nobody checked in alerts only the portal.

## 13. Support and rescue

Built: `GET /support/cases`, `GET /support/cases/{case_id}`,
`PATCH /support/cases/{case_id}`, `DELETE /support/cases/{case_id}`. Errors:
`404`, `409` closing a closed case.

## 14. Access audit log

Table: `access_audit_logs` (hypertable, append-only, kept forever; ID-41).
Our staff and each ORG_ADMIN (own organization) read it.

| Step | API call | Main error cases |
|---|---|---|
| Search: who viewed or exported what, logins, failed logins, lockouts | `GET /access-audit-logs/?user_id=&organization_id=&action=&resource_type=&from=&to=&page=&page_size=` (**built**; an ORG_ADMIN is limited to their own organization) | `400` range over the limit (366 days); `403` |
| Write a `VIEW` / `EXPORT` row from another domain | in code: `identity.service.record_data_access` (**built**, no endpoint) | `400` export without a reason |
| Export the log itself | `POST /access-audit-logs/exports` `{filters, reason}` (planned, logged as `EXPORT`) | `422` missing reason |
| Legal texts and consent | `POST /legal-documents/` (HEAD_ADMIN / CO_ADMIN), `GET /legal-documents/current`, `GET /legal-documents/pending`, `POST /consents/`, `GET /consents/me` (**built**) | `409` duplicate version or superseded text |

## Open points

- The identity layer (`/auth`, organizations, members, roles, consent, audit) is built (WP2a) and every other router requires the login since WP2b (ID-50). Still planned: flow 11's notification settings, the audit-log export, and the per-manager fleet limits (FL-10, WP6).
- Billing (`/tariffs`, `/wallets`, bills) and trips are unbuilt; the charging-location split of `charging_stations` is part of the pending refactor (database.md, "Profile vs state").
- Per-role feature lists are set in the permission-granting step (ID-44).
