# Scenario: the driver mobile app

> Step-by-step flows of the DRIVER app. Each step: what the driver sees or
> does, the API call the client makes, and the main error cases. Facts live
> in their own homes (decisions in
> [decision-log.md](../decisions/decision-log.md), tables in
> [domain-model.dbml](../design/domain-model/domain-model.dbml), progress in the
> [feature catalog](../product/features/README.md)); this file only strings
> them into a story. Written 2026-10-09; update it when a flow changes.

## Conventions

- Base path `/api/v1`. **built** = the route exists in `backend/app/domains/*/router.py`
  today; **planned** = proposed path, nothing written yet. The `identity` part (section 1) is **built** (WP2a); `billing` (wallet, top-up, tariffs, bills) is **built** (WP9, BL-19..BL-24); check-in, the driver's own summary and trips are **built** (WP5, DR-15); the old `drivers` assignment
  routes (`POST /drivers/{driver_id}/assignment`) are gone, replaced by check-in
  (`POST /driving-sessions/` is built with `vehicle_code` (VIN or plate) or `vehicle_vin` in the body and no `driver_id` for the driver's own check-in; DR-07,
  [decision-log.md:339](../decisions/decision-log.md#L339)).
- Every call except sign-up/login/OTP sends `Authorization: Bearer <access token>`
  (all other routers enforce it since WP2b, ID-50). A DRIVER-only caller sees
  their own data: their sessions, scans, notifications and support cases, and
  the live data of the truck they are checked in to (`403` after check-out).
  The token names the person and the organization the app is acting for
  (`user_sessions.organization_id`); every query filters by it.
- Common errors on every authenticated call: `401` token missing or expired
  (refresh once, then back to login), `403` the role or the data reach does
  not allow it, `422` request fails schema validation, `429` rate limit.
  Domain errors follow `app/libs/common/errors.py`: `400` breaks a business
  rule, `404` not found, `409` conflict or disallowed state.
- Times are UTC in the API; the app shows Vietnam time.

## 1. Sign-up and login

Tables: `users`, `user_credentials`, `user_sessions`, `one_time_codes`,
`memberships`, `user_state`. Decisions: ID-15 (guest sign-up), ID-16 (codes),
ID-23 (lockout), ID-36 (sessions), all in [decision-log.md](../decisions/decision-log.md).

| # | Screen / step | API call (all **built**, WP2a) | Main error cases |
|---|---|---|---|
| 1 | New guest enters phone number | `POST /auth/otp/send` `{phone_number, purpose: "SIGN_UP"}` | `409` phone already has an account (go to login); `429` resend cooldown or daily per-phone limit (SMS-pumping guard); a reset or invitation for an unknown phone answers `202` like a known one and sends nothing |
| 2 | Enters the 6-digit code, full name, password | `POST /auth/sign-up` `{phone_number, code, full_name, email?, password, platform, app_version, device_label, push_token}` returns tokens; creates user, credential, a personal INDIVIDUAL organization (the guest is its ORG_ADMIN and DRIVER); there is no `legal_form` field, a guest is always an individual | `400` wrong or expired code (5 wrong attempts kill the code); `400` weak or recently used password; `409` phone or e-mail taken; `422` malformed body |
| 3 | Invited driver (a manager registered them by phone) taps "claim account" | `POST /auth/otp/send` `{purpose: "INVITE"}` then `POST /auth/invitations/accept` `{phone_number, code, password}` | `400` code wrong, expired (72 h) or used; an unknown phone gets the same `400` as a wrong code (no probing) |
| 4 | Login: phone + password | `POST /auth/login` `{phone_number, password, platform, app_version, device_label, push_token}` returns `access_token`, `refresh_token`, `memberships[]` | `401` wrong phone or password; `423` short lockout after repeated failures (ID-23); `403` user LOCKED or organization SUSPENDED/CLOSED |
| 5 | Person has several organizations: pick one (the last one is remembered, `user_state.last_organization_id`, and opens directly) | `GET /auth/me` lists memberships, roles and features; `POST /auth/organization` `{organization_id}` switches the session without a new login; `POST /memberships/{membership_id}/accept` accepts an invitation to a further organization | `403` membership not ACTIVE or organization suspended/closed; `404` not a member. A request made before an organization is picked answers `403` |
| 6 | Forgot password | `POST /auth/otp/send` `{purpose: "PASSWORD_RESET"}`, then `POST /auth/password/reset` `{phone_number, code, new_password}` (also clears the lockout, ID-30) | `400` bad code; `429` limits |
| 7 | Silent session refresh (app start, or after `401`) | `POST /auth/refresh` `{refresh_token}` returns a new pair (the old refresh token is replaced each time; the access token lives 15 minutes) | `401` token unknown, expired (90 days on mobile) or already used: session deleted, go to login |
| 8 | Push token changed | `PUT /auth/session/push-token` `{push_token}`, `DELETE` the same path to stop pushes; `GET /auth/sessions` lists the "my devices" screen, `DELETE /auth/sessions/{session_id}` logs one out | `404` session gone |
| 9 | Logout (this device or all) | `POST /auth/logout` `{all_devices: bool}` returns `204` | none expected; the row is deleted, idempotent |
| 10 | Change password, change phone number | `POST /auth/password/change` `{current_password, new_password}` (other devices are logged out); `POST /auth/phone/change` `{new_phone_number}` then `POST /auth/phone/change/confirm` `{code}` | `400` current password wrong or password reused; `409` phone taken |
| 11 | Accept the legal texts | `GET /legal-documents/current` (public), `GET /legal-documents/pending`, `POST /consents/` `{legal_document_id, on_behalf_of_organization}` | `409` a newer version is in force |

## 2. Check-in to a truck and check-out

Tables: `driving_sessions`, `drivers`. Rules: DR-07
([decision-log.md:339](../decisions/decision-log.md#L339)): one open session
per truck and per driver, location check against the truck's last T-Box
position; DR-10 (profile, licence, membership must be active). **The QR
content is not decided** (deferred item 94,
[deferred.md:1846](../decisions/deferred.md#L1846)); the proposal is a random
`check_in_code` inside a web link. Until then the API takes a `vehicle_code`
the app extracts from the QR, so the contract does not change when the content
is fixed.

| # | Screen / step | API call | Main error cases |
|---|---|---|---|
| 1 | "Nearby trucks" list (alternative to scanning) | `GET /driving-sessions/nearby-vehicles?lat=&lon=` (**planned**, not part of WP5) returns trucks whose last T-Box position is close to the phone | `404` no truck near; empty list is `200` |
| 2 | Scan the QR on the truck (DRV-02, DRV-04: this also identifies who drives the shift) | `POST /driving-sessions/` `{check_in_method: "QR", vehicle_code, latitude, longitude}` (**built**; `vehicle_code` is a VIN or a plate, `vehicle_vin` also works; a QR or APP check-in needs both coordinates, `400` otherwise) | `404` unknown truck; not an error: a truck with another driver at the wheel is taken over (old session ends TAKEN_OVER), and the driver's open session on another truck ends OTHER_TRUCK (DR-07); the response lists `ended_sessions`; `400` phone farther than `DRIVERS_CHECKIN_MAX_DISTANCE_M` from the truck's last position, or driver profile not ACTIVE / licence expired / membership not active (DR-10) |
| 3 | Pick a truck from the nearby list | same call with `check_in_method: "APP"` | same as step 2 |
| 4 | Warnings | in the response (`warnings`): `OTHER_ORGANIZATION` (the driver belongs to another organization than the truck's owner) and `NO_RECENT_TRUCK_POSITION` (the truck has no T-Box position newer than `DRIVERS_CHECKIN_POSITION_MAX_AGE_MINUTES`, so the phone could not be compared); the app shows a notice, check-in still succeeds | none |
| 5 | Home shows the truck, live battery and location while checked in (NT-07) | `GET /driving-sessions/current` (**built**), then `GET /telemetry/vehicles/{vehicle_id}/latest` (**built**) | `404` not checked in; `403` after check-out (the driver no longer sees the truck) |
| 6 | Check out | `POST /driving-sessions/check-out` (**built**; the caller's own open session, no ID needed) | `404` no open session (for example auto-ended after the truck stayed still: `AUTO_ENDED`). A trip still in progress is closed automatically as COMPLETED with a reason (DR-12) |

## 3. Trips (planned jobs and their execution)

Tables: `trips`. Rule: DR-12
([decision-log.md:344](../decisions/decision-log.md#L344)). A driver without a
fleet has no plan: Start creates a personal trip. Driver confirmation of an
assigned trip is deferred (deferred item 95): an assigned trip simply waits in
the list until Start.

| # | Screen / step | API call | Main error cases |
|---|---|---|---|
| 1 | "My trips" list: assigned, in progress, done | `GET /trips/?status=PLANNED&status=IN_PROGRESS&mine=true&page=&page_size=` (**built**; a DRIVER-only caller always gets their own trips) | none |
| 2 | Open a trip: origin, destination, planned times, truck | `GET /trips/{trip_id}` (**built**; also distance, energy, kWh/km and cost once finished) | `404` not the caller's trip |
| 3 | **Start trip** (must be checked in); optionally declare LOADED or EMPTY (MON-13) | `POST /trips/{trip_id}/start` `{declared_load_status}` (**built**); for a personal trip `POST /trips/start-personal` `{origin_name?, destination_name?, declared_load_status}` creates and starts it. The server records time, T-Box position, odometer and battery % (empty when the truck has no recent sample) | `409` not checked in, or a trip already in progress in this session, or trip not PLANNED; a truck or driver different from the plan is allowed and flagged (`vehicle_differs_from_plan`, `driver_differs_from_plan`); `404` a trip that names another driver; offline start (the app's press time) is not built |
| 4 | **Finish trip** | `POST /trips/{trip_id}/finish` (**built**) | `409` not IN_PROGRESS; `404` not the caller's trip |
| 5 | Reminder: truck moving with no trip started | push notification `NO_TRIP_STARTED` (inbox, section 8) (**planned**; the type and its routing rule exist, nothing raises it yet). A trip a manager assigns arrives as `TRIP_ASSIGNED` (**built**, NT-15) | none |

## 4. Wallet

Tables: `wallets`, `payments`, `wallet_transactions`. Decisions: BL-13, BL-14,
BL-15, BL-22, BL-23 ([decision-log.md:248](../decisions/decision-log.md#L248)).
The wallet is the person's, across organizations, and is created the first time
something needs it. Top-up at launch is a **VietQR bank transfer**: the app
draws a QR from the `vietqr_payload` string; it carries a unique `transfer_code`;
a bank-notification service tells us when the money arrives (no card gateway).
All amounts are whole dong (`int`).

| # | Screen / step | API call (**built**, WP9) | Main error cases |
|---|---|---|---|
| 1 | Wallet tab: balance (may be negative after a session overrun, BL-14) | `GET /wallets/me` returns `wallet_id`, `balance`, `currency`, `status`, `status_reason`, `minimum_balance_to_charge` (created on first call) | none |
| 2 | Top up: enter amount | `POST /wallets/me/top-ups` `{amount}` returns `payment_id`, `transfer_code`, `vietqr_payload` (draw it as a QR), `bank_bin`, `account_number`, `account_name`, `expires_at`, status `PENDING` (201) | `400` amount outside `BILLING_TOPUP_MIN_VND`..`MAX_VND`; `403` wallet BLOCKED |
| 3 | Waiting screen after the transfer | poll `GET /payments/{payment_id}` until `SUCCEEDED` (push `TOP_UP_RECEIVED` is WP10) | `FAILED` = the code expired unpaid: start again (a transfer that still arrives is credited with the amount received); a transfer with a wrong code is not matched, it is only logged for staff (BL-23); a different amount is credited as received |
| 4 | Transaction list (TOP_UP, SESSION_BILL, REFUND, ADJUSTMENT) with balance after each | `GET /wallets/me/transactions?page=&page_size=` (newest first; REFUND is not built) | none |
| 5 | Low balance notice | push `LOW_WALLET_BALANCE` (BL-14, WP10) | none |

## 5. QR charging

Tables: `charging_sessions`, `charging_station_commands`,
`charging_session_bills`, `tariffs`. Decisions: CO-13, CO-14
([decision-log.md:416](../decisions/decision-log.md#L416)), CE-10 .. CE-15
([decision-log.md:433](../decisions/decision-log.md#L433)), BL-10. The **QR
is shown on the charger's own screen**; the app only authorizes the driver and
shows progress. The driver picks the gun and presses start/stop on the charger.
Only a token we issued starts a session (CE-11).

| # | Screen / step | API call | Main error cases |
|---|---|---|---|
| 1 | Map of charging locations near me, with free connectors | `GET /charging-stations/nearby?lat=&lon=` (**built**; public locations plus the caller's own organization's and those granted to it, CS-28) | `422` bad coordinates |
| 2 | Open a station: price, connectors | `GET /charging-stations/{station_id}` and `GET /charging-stations/{station_id}/connectors` (**built**); the price: `GET /tariffs/in-force?station_id=&at=` (**built**, PAY-09: price for that hour before and with VAT, normal price, VAT, time-of-use periods) | `404` unknown or deleted station |
| 3 | Tap "Scan to charge", scan the QR on the charger screen | `POST /charging-sessions/scan` `{charger_code, connector_number?}` (**built**, CE-20, CE-21; `charger_code` is what the QR carries: the charger's OCPP identity, its serial number also matches; `connector_number` is the gun when the QR names one). The backend: finds the charger, checks the location's visibility, that charger and gun are in service and connected, that no charge runs on the gun and the caller has none open, then the wallet (blocked, minimum balance `BILLING_MIN_BALANCE_VND`, BL-14); takes the truck from the caller's open driving session (CE-13); creates the session PENDING with a single-use token (`id_token`) and writes a `REMOTE_START` row in `charging_station_commands`; the gateway sends `RemoteStartTransaction` (1.6J) or `RequestStartTransaction` (2.0.1). Then the price: the tariff in force for the charger and the hour is resolved (`billing.resolve_tariff_for_station`) and frozen on a QUOTED bill (BL-10, BL-20). Returns `session_id`, `id_token`, `command_id`, `expires_at`, and the frozen price `tariff_version_id`, `currency`, `price_per_kwh` (before VAT, whole dong), `vat_rate_percent` (201) | `404` unknown charger or gun; `403` private location not allowed for this organization, or wallet BLOCKED; `409` charger or gun out of service, charger offline, a charge already running on the gun, or the caller already has a charge open; `409` with the message `INSUFFICIENT_BALANCE: top up at least N VND`; `409` `NO_TARIFF: no price is set for this charger` |
| 4 | Progress screen: kWh, power, battery % (cost so far = energy x the frozen price, computed by the app from the scan response) | poll `GET /charging-sessions/{session_id}` (**built**: `energy_delivered_wh`, `duration_seconds`, `soc_start_percent`, `soc_end_percent`, `current_power_kw`, `max_power_kw`) and `GET /charging-sessions/{session_id}/measurements` (**built**), every few seconds; the session is PENDING until the charger confirms | `404` not the caller's session; PENDING longer than `CHARGING_PENDING_SESSION_TIMEOUT_SECONDS` (300 s), or a refused / timed-out remote start, becomes ABANDONED at once (the app says "charger did not start"; its bill becomes VOID) |
| 5 | Stop | by default on the charger screen or the truck stops itself. The app may offer a stop button: `POST /charging-sessions/{session_id}/stop` (**built**, 202; queues `REMOTE_STOP`; only the person who started the charge, or staff; body `{reason?}`) returns `command_id`; the session turns COMPLETED when the charger sends its own stop message | `403` not the starter; `409` session not ACTIVE; a command outcome REJECTED / TIMEOUT / NOT_SENT (read `GET /charging-stations/{id}/commands/{command_id}`) is shown as "could not stop, use the charger screen" |
| 6 | Summary after the stop: energy, amount before VAT, VAT, total, wallet balance after | `GET /charging-sessions/{session_id}/receipt` (**built**, COMPLETED sessions: place, gun, times, energy, stop reason, `bill_status`, `price_per_kwh`, `vat_rate_percent`, `amount_before_vat`, `vat_amount`, `total_amount` in whole dong; the amounts are `null` while the bill is ON_HOLD); the wallet balance after is `GET /wallets/me`; push `CHARGING_RECEIPT` (WP10) | `409` session not finished; bill ON_HOLD (meter mismatch): "being checked", amount appears later |

## 6. Session history and bills

| # | Screen / step | API call | Main error cases |
|---|---|---|---|
| 1 | History list, newest first | `GET /charging-sessions/mine?page=&page_size=&vehicle_id=&started_from=&started_to=&status=` (**built**, CE-24: every session the caller started, across organizations); a manager lists the organization's with `GET /charging-sessions` (filters `vehicle_id`, `started_by`, `station_id`, dates, `status`) | none |
| 2 | Open one session and its bill (tariff version, energy, amount, VAT) | `GET /charging-sessions/{session_id}` (**built**); the bill: `GET /charging-sessions/{session_id}/bill` (**built**: status, frozen price and tariff version, energy and its source, amounts, `is_paid`) and the receipt `GET /charging-sessions/{session_id}/receipt` (**built**) | `404` not the caller's; no receipt for ABANDONED |
| 3 | Download / share a receipt | a PDF `GET /charging-sessions/{session_id}/bill?format=pdf` (planned, not built; e-invoices belong to organizations, not to the driver wallet at launch) | `404` bill not BILLED yet |

## 7. Own driving summary (DR-11)

A driver sees only a **summary** of their own sessions: date, truck plate,
check-in and check-out time, duration, distance, plus totals per day and week
for the driving-time warning. Never the route, GPS trail, stops or places.

| # | Screen / step | API call | Main error cases |
|---|---|---|---|
| 1 | "My driving" list and totals per day / week | `GET /driving-sessions/mine/summary?from=&to=` (**built**, DR-15): `sessions` (date, plate, check-in/out, duration minutes, odometer distance, end cause), `totals_per_day`, `totals_per_week` (Monday weeks, report time zone; a session counts in the day it started) | `400` range empty, over 366 days or without a time zone; without `from` / `to` the last 30 days |

## 8. Notifications inbox

Tables: `notifications`, `notification_recipients` (NT-09 .. NT-12,
[decision-log.md:266](../decisions/decision-log.md#L266)). The inbox is per
person across all their organizations. Opening the list sets `seen_at` (clears
the badge); tapping an item sets `read_at` and opens the screen named by its
`subject_type` + `subject_id`, where that screen's own access check applies.
Push and e-mail are the only extra channels at launch (SMS deferred, item 96).

| # | Screen / step | API call | Main error cases |
|---|---|---|---|
| 1 | Badge on the app icon | `GET /notifications/unread-count` (**built**, WP10): `unread_count` (no `read_at`) and `unseen_count` (no `seen_at`, the badge) | none |
| 2 | Open the list | `GET /notifications?mine_only=true&order=desc&limit=&before_id=&notification_type=&unread_only=` (**built**; a driver always gets their own inbox; each entry carries the caller's `seen_at` / `read_at`; opening the list marks the page it returns seen; `before_id` is the next page); `POST /notifications/mark-seen` (**built**) clears the badge without opening anything | none |
| 3 | Tap one item | `PATCH /notifications/{notification_id}/read` (**built**; sets `read_at` and, if empty, `seen_at`), then the app opens the screen named by `subject_type` + `subject_id` | `404` not the caller's notification |
| 4 | "Mark all as read" | `POST /notifications/mark-all-read` (**built**; idempotent, sets seen and read) | none |
| 5 | Open an alert's detail | `GET /notifications/{notification_id}` (**built**) | `404` |
| 6 | Register the phone for push | `POST /auth/session/push-token` (**built**, ACC-16); the push the phone gets is the one in `notifications.delivery` (WP10, NT-17): the title and text of the alert plus `notification_id`, `notification_type`, `subject_type`, `subject_id`, `vehicle_id` as data. The provider is a logging fake until a real Firebase one is chosen | none |

Who receives what (NT-15): a driver gets the battery alerts of the truck they are
checked in to (even from another organization, NT-07), a trip a manager plans or
reassigns to them (`TRIP_ASSIGNED`), and, once built, `NO_TRIP_STARTED`. The
organization's push / e-mail switches (web portal flow 11) apply to every alert of
that organization; the inbox is always on.

## 9. Other driver-facing calls already built

- SOS and support: `POST /support/sos` (raises an `SOS_ALERT` notification),
  `POST /support/cases`, `GET /support/cases`, `GET /support/cases/{case_id}`
  (all **built**). Errors: `404` unknown vehicle or driver reference, `422`
  invalid category.
- Truck data the driver may see while checked in: `GET /telemetry/vehicles/{vehicle_id}/latest`
  (**built**).

## Open points this scenario depends on

- QR content for the truck (deferred item 94) and for the charger screen (CO-14: the format the Willdigits HMI produces is unknown, open question 4).
- Whether the app gets a stop button (CO-14 says start and stop happen on the charger).
- `identity` is not built: every `/auth/*` path above is a proposal.
