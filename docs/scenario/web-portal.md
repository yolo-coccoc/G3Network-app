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
| **Invite** by phone and name; roles chosen | `POST /organizations/{organization_id}/members` `{phone_number, full_name, roles[]}`; membership INVITED, SMS invite link (72 h); an invitation never sets an e-mail, and for a number that already has an account the answer shows no name or e-mail until the person accepts (`POST /memberships/{id}/accept`); invitation SMS are limited per number and per inviter (`429`) | `409` already an active member; `400` role not allowed here (HEAD_ADMIN / CO_ADMIN only in internal organizations); the DRIVER role is granted without a driver-profile check (the drivers domain depends on identity, not the reverse) |
| Resend or cancel an invitation | `POST /organizations/{organization_id}/members/{membership_id}/resend` | `409` already accepted |
| Grant / revoke a role | `POST /memberships/{membership_id}/roles` `{role}`, `DELETE /memberships/{membership_id}/roles/{role}` | `409` role already held; `409` revoking the ORG_ADMIN (use handover); the revoke takes no reason (role assignments keep none) |
| **Lock** a member in this organization (account-wide lock is for our HEAD_ADMIN / CO_ADMIN only) | `POST /memberships/{membership_id}/lock` `{reason}`; `.../unlock` | `409` the ORG_ADMIN cannot be locked before a handover |
| Remove a member (ends membership, revokes roles, and closes the driver profile and open driving session, DR-10, through the hook of DR-15) | `DELETE /memberships/{membership_id}?reason=` (also cancels a pending invitation); `POST /memberships/{membership_id}/leave` for the person themself | `409` ORG_ADMIN; `404` |
| **Hand over ORG_ADMIN** to another active member (one transaction: grant new, revoke old) | `POST /organizations/{organization_id}/admin-handover` `{to_membership_id, reason}` | `409` target not ACTIVE; `403` caller is not the current ORG_ADMIN. When the admin is gone, our CO_ADMIN calls the same path with `force: true` |
| Lock / unlock a whole account (internal only); list and read accounts | `POST /users/{user_id}/lock` `{reason}`, `POST /users/{user_id}/unlock`, `GET /users`, `GET /users/{user_id}` | `403` not HEAD_ADMIN / CO_ADMIN |

## 3. Vehicles, batteries, warranties

Tables: `vehicles`, `vehicle_models`, `batteries`, `battery_models`,
`warranties`. Owner changes are recorded as a handover (DM-22, VH-10), not as
a second table.

| Step | API call | Main error cases |
|---|---|---|
| Vehicle list (search, status, model, owner) | `GET /vehicles?q=&status=&vehicle_model_id=&organization_id=` (**built**; the activation is computed in `telemetry`, flow 4: `GET /telemetry/vehicles/activation`) | none |
| Register a truck: VIN, plate, model, owner, `acquired_at` | `POST /vehicles/` (**built**) | `409` duplicate VIN or plate among live trucks; `422` |
| Open / edit a truck | `GET /vehicles/{vehicle_id}`, `PATCH /vehicles/{vehicle_id}` (**built**; a typed `status_reason` becomes the history reason) | `404`; `409` |
| Hand over to a new owner (sale), internal staff | `POST /vehicles/{vehicle_id}/transfer-ownership` `{organization_id, acquired_at, reason}` (**built**, VH-21): one transaction closes the seller's fleet membership, ends the open driving session (`OWNER_CHANGED`) and moves a pack the seller owns; answers the truck plus what ended or moved | `404` truck or buyer; `400` same owner or bad date |
| Ownership periods of a truck | `GET /vehicles/{vehicle_id}/ownership-periods` (**built**, from the view) | `404` |
| Retire (soft delete, status INACTIVE) | `DELETE /vehicles/{vehicle_id}?reason=` (**built**; a live device or open session does not block it yet) | `404` |
| Truck model catalog (internal staff write, all read) | `POST/GET /vehicle-models`, `GET/PATCH/DELETE /vehicle-models/{vehicle_model_id}`, list filters `q`, `make` (**built**) | `409` make + name exist; `404` |
| Battery model catalog | `POST/GET /battery-models`, `GET/PATCH/DELETE /battery-models/{battery_model_id}`, filters `q`, `chemistry` (**built**) | `409` maker + name exist |
| Battery pack: register, edit, status, delete | `POST/GET /batteries`, `GET/PATCH/DELETE /batteries/{battery_id}` (**built**; filters `q`, `status`, `battery_model_id`, `vehicle_id`, `is_installed`, `organization_id`) | `409` serial exists; `404` |
| Fit a pack to a truck (`installed_at`), take it out, history | `POST /batteries/{battery_id}/installation`, `DELETE /batteries/{battery_id}/installation?reason=`, `GET /batteries/{battery_id}/installation-periods` (**built**) | `409` pack fitted elsewhere or inactive, or the truck holds a pack; `404` truck; `400` future time |
| Hand a pack (alone) to another organization | `POST /batteries/{battery_id}/transfer-ownership` `{organization_id, acquired_at, reason}` (**built**) | `409` same owner; `400` bad date |
| Warranty: enter, list, edit, void, delete | `POST/GET /warranties`, `GET/PATCH/DELETE /warranties/{warranty_id}`, `POST /warranties/{warranty_id}/void` `{reason}` (**built**; filters `vehicle_id`, `battery_id`, `telematic_id`, `station_id`, `status`, `warranty_type`, `expiring_within_days`, `organization_id`) | `409` overlapping term of the same type, or voided; `400` end before start, or a limits key the object does not allow; `404` object |
| Check-in code (QR sticker) regeneration | `POST /vehicles/{vehicle_id}/check-in-code` `{reason}` (planned, deferred item 94) | `403` |

## 4. Devices (telematics) and device health

Tables: `telematics`, `telematic_status_reports`. One live device per truck
(`uq_telematics_active_vehicle`).

| Step | API call | Main error cases |
|---|---|---|
| Register a T-Box (serial, IMEI) and link to a truck | `POST /telematics/` (**built**; `installed_at` is set when it is mounted) | `409` serial or IMEI exists, or the truck already has a live device; `404` truck |
| Device list with online / silent state | `GET /telematics/` (**built**; health computed from newest telemetry) | none |
| Open, edit, replace device | `GET/PATCH /telematics/{telematic_id}` (**built**; a new `vehicle_vin` mounts it, `null` unmounts it) | `409` |
| Unlink / retire | `DELETE /telematics/{telematic_id}?reason=` (**built**, soft delete: INACTIVE, unmounted, the truck keeps it as history) | `404` |
| Device health dashboard: one device | `GET /telematics/{telematic_id}/health` (**built**; state `HEALTHY` / `ATTENTION` / `SILENT` / `NO_DATA` / `NOT_MOUNTED` / `INACTIVE`, silence and online flags, newest status report: firmware, SIM, voltage, signal, storage, GNSS) | `404` |
| Device health dashboard: list and share of healthy devices | `GET /telematics/health/devices?fleet_id=&organization_id=&health_state=&page=&page_size=`, `GET /telematics/health/summary?fleet_id=&organization_id=` (**built**; `organization_id` is for internal staff) | `404` fleet |
| Health trend of one device | `GET /telematics/{telematic_id}/status-reports?since=&limit=` (**built**; newest first) | `404` |
| Push configuration to one device or a whole fleet (F-J2) | `POST /telematics/{telematic_id}/config`, `POST /telematics/fleets/{fleet_id}/config` (**built**; MQTT publish; only a mounted, ACTIVE device receives it, TX-08) | `404`; `409` not mounted or INACTIVE; `502` broker unreachable |
| Truck activation (VEH-05) | `GET /telemetry/vehicles/{vehicle_id}/activation`, `GET /telemetry/vehicles/activation?activation_status=&organization_id=&page=` (**built**; computed from the device mounted now and its data since the handover; the list carries the success rate and, with `activation_status=AWAITING_DATA`, the trucks still waiting) | `404` |
| Device-offline alert | system raises it into `notifications` for the truck's organization (once per silence episode); the notifications routing table (NT-15) delivers it to its ORG_ADMIN and FLEET_MANAGER members, a limited manager only for trucks in their fleets; shown in the inbox (flow 12), push / e-mail by the organization's switches (flow 11) | not a call |
| T-Box status reports | the device publishes to `g3network/telematics/{serial}/status`; `make telematics-status-dev` stores them (mqtt-spec.md 2.2) | not a call |

## 5. Fleets (tree, trucks, user limits)

Tables: `fleets`, `fleet_vehicle_memberships`, `fleet_user_assignments`,
`geofences`. Rules: FL-09, FL-10
([decision-log.md:359](../decisions/decision-log.md#L359)).

| Step | API call | Main error cases |
|---|---|---|
| Create a fleet, optionally under a parent (tree) | `POST /fleets/` (**built**) | `409` duplicate name under the same parent; `400` cycle in the tree |
| Fleet tree / list / open | `GET /fleets/tree?organization_id=` (**built**: nested, each node with `vehicle_count` and the roll-up `vehicle_count_with_descendants`; `organization_id` is for internal staff), `GET /fleets/?parent_fleet_id=&include_descendants=` and `GET /fleets/{fleet_id}?include_descendants=` (**built**; `include_descendants` lists every fleet below the parent and makes `vehicle_count` include the sub-fleets) | `404` |
| Rename, move, delete | `PATCH`, `DELETE /fleets/{fleet_id}` (**built**; delete closes memberships, FL-06, and ends the user assignments pointing at the fleet, `unassigned_by` empty = system, FL-10) | `409` fleet has children; `403` a manager limited to some fleets deletes a fleet given to someone |
| **Add a truck** | `POST /fleets/{fleet_id}/vehicles` `{vehicle_vin}` (**built**) | `404` unknown VIN; `409` truck already in a fleet |
| **Remove a truck** | `DELETE /fleets/{fleet_id}/vehicles/{vehicle_vin}` (**built**) | `404` not a member |
| Trucks of a fleet and membership history | `GET /fleets/{fleet_id}/vehicles`, `GET /fleets/{fleet_id}/memberships` (**built**), `DELETE /fleets/{fleet_id}/memberships/{fleet_vehicle_membership_id}` (**built**) | `404` |
| **Give a user a fleet limit** (FL-10): a FLEET_MANAGER or DISPATCHER sees the chosen fleets and everything below them | `POST /memberships/{membership_id}/fleets` `{fleet_id}` (answers `warnings: ["FLEET_ALREADY_COVERED_BY_ASSIGNED_PARENT"]` when a fleet above is already assigned; allowed), `DELETE /memberships/{membership_id}/fleets/{fleet_id}`, `GET /memberships/{membership_id}/fleets?include_closed=`, `GET /fleets/{fleet_id}/user-assignments` (all **built**, FL-13; organization administrator and internal staff only; with no open row the member sees the whole organization) | `400` fleet is not in the membership's organization; `404` membership or fleet; `409` fleet already held; `404` taking away a fleet the member does not hold |
| What a limited manager sees | the same endpoints, narrower: fleets (list, tree, open, trucks, memberships, geofences), vehicles (`GET /vehicles/`, `/vehicles/{id}`), driving sessions and trips lists, `/telemetry` fleet and per-vehicle views, device health and config push of a fleet. Organization administrators and internal staff are never limited | `404` for anything outside the limit |
| Geofences of a fleet | `POST/GET/PATCH/DELETE /fleets/{fleet_id}/geofences[/{geofence_id}]` (**built**) | `400` invalid polygon; `404` |

## 6. Drivers and trip planning

Tables: `drivers`, `driving_sessions`, `trips`. Rules: DR-07, DR-10, DR-12.
All routes below are **built** (WP5, DR-15) unless marked planned. `POST /drivers/`
takes an existing `membership_id`, not a phone and name (the invite flow is flow 2),
and the old `/drivers/{driver_id}/assignment(s)` routes are gone. Check-in is
`POST /driving-sessions/` (and `/check-out`, `GET /driving-sessions/`) with
`vehicle_code` (VIN or plate) or `vehicle_vin` in the body; `driver_id` is optional (the caller's own profile when omitted, a manager may name another driver of the organization, WP2b).

| Step | API call | Main error cases |
|---|---|---|
| Register a driver: membership, licence number/class/expiry | `POST /drivers/` `{membership_id, license_number, license_class, license_expires_on}`; the person and invite come from flow 2; the membership must hold the DRIVER role and not be locked or ended; the response carries `warnings: ["LICENSE_NUMBER_ON_OTHER_PERSON"]` when another person's live profile has the number (DR-09) | `409` person already has a driver profile in this organization; `400` licence expired, role missing, membership locked or ended |
| Driver list, search and profile | `GET /drivers/?q=&status=&license_expires_within_days=` (`q` matches licence number, name or phone; the expiry filter drives the reminder, `is_license_expired` and `days_until_license_expiry` are in every response), `GET /drivers/{driver_id}`, `PATCH` (licence, status; setting INACTIVE ends the open driving session), `DELETE` | `404`; `409`; `400` new expiry in the past |
| Ending or locking a member (DR-10) | `DELETE /memberships/{membership_id}?reason=` also soft-deletes the driver profile and ends the open session `DRIVER_REMOVED`; `POST /memberships/{membership_id}/lock` only ends the open session (the profile stays) | see flow 2 |
| Check a driver in to a truck for them | `POST /driving-sessions/` `{check_in_method: "PORTAL", driver_id, vehicle_vin}` (only a manager may use PORTAL; no phone position needed) | `400` driver inactive or licence expired; a takeover is not an error (old session ends TAKEN_OVER) |
| Who is driving now / session history | `GET /driving-sessions/?vehicle_vin=&driver_id=` (managers see full detail, DR-08; from/to filters are planned) | none |
| **Plan a trip**: origin, destination, planned times, driver, truck | `POST /trips/` `{origin_name, destination_name, planned_start_at, planned_end_at, planned_driver_id, planned_vehicle_id}` | `404` driver or truck out of reach; arrival before departure `422`; `403` a driver-only caller |
| Dispatch board (by day, status) | `GET /trips/?from=&to=&status=&driver_id=` (a fleet filter is planned) | none |
| Reassign / cancel a trip | `PATCH /trips/{trip_id}` `{..., reason}`, `POST /trips/{trip_id}/cancel` `{reason}` (every change goes to `trip_history` with the reason) | `409` trip already IN_PROGRESS, COMPLETED or CANCELLED |
| Trip result: actual times, distance, energy, kWh/km, cost | `GET /trips/{trip_id}`: differences of the stored readings; `driver_differs_from_plan` / `vehicle_differs_from_plan` flag a swap | `404` |

Planning a trip for a driver, or moving a planned trip to another driver, sends
that driver a `TRIP_ASSIGNED` notification (**built**, NT-15).

## 7. Live map and reports

All **built** under `/telemetry`; fleet endpoints need the fleet in reach.

| Step | API call | Main error cases |
|---|---|---|
| Live map of a fleet | `GET /telemetry/fleets/{fleet_id}/vehicles/latest?include_descendants=` (the flag adds the trucks of every sub-fleet) | `404` fleet |
| One truck now / history trail | `GET /telemetry/vehicles/{vehicle_id}/latest`, `.../history` (both log a `VIEW`) | `404`; `422` range |
| Operating report (truck, fleet) | `GET /telemetry/vehicles/{vehicle_id}/operating-report`, `GET /telemetry/fleets/{fleet_id}/operating-report?include_descendants=` (rolls up over the sub-fleets) | `422` bad period |
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
| List, open, edit, retire | `GET /charging-stations?scope=MANAGED\|VISIBLE&organization_id=&location_id=&is_public=&status=&q=` (same filters on `GET /charging-locations`; `MANAGED` = the caller's own, all for staff, `VISIBLE` adds public and granted ones), `GET/PATCH/DELETE /charging-stations/{station_id}` (**built**); a delete is refused (`409`) while a charge runs on one of its guns | `404`, `409` |
| EVSEs and connectors | `POST/GET /charging-stations/{station_id}/evses`, `GET/PATCH/DELETE /charging-evses/{evse_id}`, `POST/GET /charging-evses/{evse_id}/connectors`, `GET/PATCH/DELETE /charging-connectors/{connector_id}`, `GET /charging-stations/{station_id}/connectors` (**built**) | `409` topology conflict (duplicate OCPP id, active session on the connector) |
| **Access** for a private location: allow an organization, optional `valid_until`, revoke | `POST /charging-locations/{location_id}/access` `{allowed_organization_id, valid_until}`, `GET` the live grants, `POST .../access/{access_id}/revoke` `{revoke_reason}` (**built**; `granted_by` / `revoked_by` are the caller; the nearby search shows public locations, the caller's own and granted ones) | `409` already granted; `404` |
| **Configuration**: read what the charger reports | `GET /charging-stations/{station_id}/configuration` (**built**); request a fresh capture and change a key: `POST /charging-stations/{station_id}/commands` `{command_type: "GET_CONFIGURATION" \| "CHANGE_CONFIGURATION", parameters, reason}` answers `202`; poll `GET /charging-stations/{station_id}/commands/{command_id}` (**built**; every command type of the DBML is accepted) | `409` charger offline; command outcome `REJECTED`, `ERROR`, `TIMEOUT` or `NOT_SENT` is shown with the charger's own status |
| **Commands**: reset, unlock connector, availability, trigger message | same `POST .../commands` `{command_type, evse_id, session_id, parameters, reason}` (**built**; `parameters` are checked per type; `400` for an unknown key or value or a missing operator `reason`; `409` when the charger is not connected, or an unlock is asked while a charge runs on that gun); the log `GET /charging-stations/{station_id}/commands?outcome=&command_type=`; `POST .../commands/{command_id}/cancel` for a command no gateway has picked up yet (**built**, ends `NOT_SENT` with `response_status` "Cancelled", `409` once sent) | `400`, `409` |
| Connection facts (STN-03) | `GET /charging-stations/{station_id}/connection` (**built**: `is_online`, `last_seen_at`, protocol version, latest boot data, registered vs reported serial) | `404` |
| Live status (STN-04) | `GET /charging-stations/{station_id}/status` (**built**; also at the older `.../connectors`): whole-charger status, guns with plug and power, derived counts, `is_status_stale` when the charger is offline; `GET /charging-stations/status` is the board with one row per charger | `404` |
| OCPP message log (minimal STN-15) | `GET /charging-stations/{station_id}/ocpp-messages?action=&direction=&occurred_from=&occurred_to=&include_raw_frame=` (**built**, staff only; the frame text is left out unless asked for, and asking writes a `VIEW` audit row, because frames hold RFID tokens; the full viewer is P1.1) | `403` |
| Sessions and energy | `GET /charging-sessions`, `.../{session_id}`, `.../measurements`, `GET /charging-sessions/stations/{station_id}/energy[/series]`, `GET /charging-sessions/stations/energy` (**built**) | `404` |

## 9. Tariffs and bills

Tables: `tariffs`, `tariff_versions`, `charging_session_bills`. Rules: BL-08,
BL-10, BL-20, BL-21. Only our HEAD_ADMIN / CO_ADMIN publish versions for the
public network; a customer's organization administrator prices its own
locations (OPERATIONS read). **Built** (WP9). Amounts are whole dong.

| Step | API call (**built**) | Main error cases |
|---|---|---|
| Tariff list per owner and location | `GET /tariffs?organization_id=&location_id=&status=&page=&page_size=` (each with the version in force now; `organization_id` only for staff) | none |
| Create a tariff | `POST /tariffs` `{name, location_id?, organization_id?}` (staff may name the owner; the location must belong to the owner) | `409` a second ACTIVE tariff for the owner and location; `400` location of another owner; `404` unknown location |
| **Publish a version**: price per kWh, time-of-use periods (Vietnam time), VAT rate, reason | `POST /tariffs/{tariff_id}/versions` `{price_per_kwh, vat_rate_percent, time_periods?, effective_from?, change_reason}`; `GET /tariffs/{tariff_id}/versions` lists them; versions are never edited | `400` `effective_from` in the past or not after the previous version, bad or overlapping periods, a price with a fraction; `409` tariff retired |
| Rename / retire / reactivate | `PATCH /tariffs/{tariff_id}` `{name}`, `POST /tariffs/{tariff_id}/retire`, `POST /tariffs/{tariff_id}/reactivate` `{reason}` | `409` already in that state, or another ACTIVE tariff holds the owner and location |
| Price of a charger now | `GET /tariffs/in-force?station_id=&at=` | `404` charger not visible; `409` `NO_TARIFF` |
| Bills list (by organization, status, month) | `GET /charging-session-bills?status=&from=&to=&started_by=&organization_id=&page=&page_size=` (ACCOUNTANT, FLEET_MANAGER: their own organization; staff: any) | none |
| One session's bill | `GET /charging-sessions/{session_id}/bill` | `404` |
| Review an ON_HOLD bill (meter mismatch) and release or void | `POST /charging-session-bills/{bill_id}/release` or `/void` `{reason}` (our billing staff only) | `409` bill not ON_HOLD (a BILLED bill is never edited, corrections are refunds or adjustments), or release of a hold with no computed figures |

## 10. Wallets and top-ups (our billing staff)

Tables: `wallets`, `payments`, `wallet_transactions`. BL-13 .. BL-15, BL-22,
BL-23. **Built** (WP9) except where marked. A wallet is keyed by the **user**
(the portal finds the user first, ACC-02).

| Step | API call | Main error cases |
|---|---|---|
| Find a wallet by phone | **not built**: find the user (`GET /users`), then `GET /wallets/{user_id}` | `404` no wallet yet |
| Balance, transactions of a wallet | `GET /wallets/{user_id}`, `GET /wallets/{user_id}/transactions?page=&page_size=` (**built**, internal billing staff) | `404` |
| A person's payments | not built (`GET /payments/{payment_id}` reads one; staff may read any) | `404` |
| Unmatched bank transfers (wrong `transfer_code`) and manual match | **not built**: such a transfer is only logged (BL-23, no table to hold it) | – |
| **Adjust** a balance | `POST /wallets/{user_id}/adjustments` `{amount, reason}` (**built**, ADJUSTMENT row with who and why) | `400` zero amount; `422` reason missing; `403` role |
| Block / unblock a wallet (fraud check) | `POST /wallets/{user_id}/status` `{status, reason}` (**built**; a tracked decision) | `409` same status; `404` no wallet |
| Simulate a bank transfer (development, fake provider) | `POST /payments/vietqr/simulate` `{transfer_code, amount?, bank_transaction_id?}` (HEAD_ADMIN / CO_ADMIN; only when `BILLING_SIMULATE_TRANSFERS_ENABLED` is on, BL-26) | `404` unknown code, `409` switched off |
| Refund unused balance through the original top-up | **not built** (`POST /payments/{payment_id}/refund`; REFUND payments wait for a real bank channel) | – |

The bank-notification service calls `POST /payments/vietqr/notifications` with
the header `X-Webhook-Secret` (`BILLING_WEBHOOK_SECRET`); it is the only public
billing endpoint.

## 11. Notification settings

Table: `organization_notification_settings` (NT-12, NT-17). Only exceptions are
stored; a missing row uses the default from code (push on for every kind,
e-mail on for `SOS_ALERT`). The inbox always shows every alert; the switch
controls push and e-mail only. No per-person choice.

| Step | API call | Main error cases |
|---|---|---|
| List kinds with their effective push / e-mail state | `GET /organizations/{organization_id}/notification-settings` (**built**; one entry per kind with `is_default` and `updated_at`) | `404` organization out of reach; `403` not an ORG_ADMIN or our administrator |
| Switch a kind | `PUT /organizations/{organization_id}/notification-settings/{notification_type}` `{push_enabled, email_enabled, reason?}` (**built**; the row is created at the first save, later saves write `organization_notification_setting_history` with the actor and the reason) | `422` unknown kind; `404` organization out of reach; `403` |

## 12. Notifications inbox in the portal

Same routes as the app (all **built**): `GET /notifications` (the portal polls it
with `after_id=` and the returned `latest_notification_id`, about every 10 s;
`mine_only=true&order=desc` is the notification centre with `before_id` paging,
`unread_only` and the type / severity / vehicle filters), `GET
/notifications/unread-count`, `POST /notifications/mark-seen`, `PATCH
/notifications/{notification_id}/read`, `POST /notifications/mark-all-read`.
Staff roles (fleet manager, operations, customer care, administrators) also read
the organization view (`GET /notifications` without `mine_only`; internal staff
see every organization). Who receives each kind is the routing table (NT-15):
alerts for a truck go to the managers who may see it, and the driver checked in
(NT-07); our customer care also receives every `SOS_ALERT`.

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

- The identity layer (`/auth`, organizations, members, roles, consent, audit) is built (WP2a) and every other router requires the login since WP2b (ID-50). Still planned: the audit-log export. Notification settings (flow 11) and the routing / push / e-mail delivery are built (WP10, NT-15..NT-17). The per-manager fleet limits (FL-10) are built (WP6, FL-13).
- Billing (`/tariffs`, `/wallets`, `/payments`, bills) is built (WP9, BL-19..BL-24); what is not: unmatched-transfer review, refunds, payment lists, the wallet lookup by phone. The charging-location split of `charging_stations` is part of the pending refactor (database.md, "Profile vs state").
- Per-role feature lists are set in the permission-granting step (ID-44).
