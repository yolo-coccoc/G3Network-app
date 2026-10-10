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
  today; **planned** = proposed path, nothing written yet. The `identity`,
  `billing` and trips parts are all planned; the old `drivers` assignment
  routes (`POST /drivers/{driver_id}/assignment`) are gone, replaced by check-in
  (`POST /driving-sessions/` is built with `driver_id` and `vehicle_vin` in the body; DR-07,
  [decision-log.md:339](../decisions/decision-log.md#L339)).
- Every call except sign-up/login/OTP sends `Authorization: Bearer <access token>`.
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

| # | Screen / step | API call (all planned) | Main error cases |
|---|---|---|---|
| 1 | New guest enters phone number | `POST /auth/otp/send` `{phone_number, purpose: "SIGN_UP"}` | `409` phone already has an account (go to login); `429` resend cooldown or per-phone limit (SMS-pumping guard) |
| 2 | Enters the 6-digit code, full name, password | `POST /auth/sign-up` `{phone_number, code, full_name, password, legal_form, platform, device_label}` returns tokens; creates user, credential, a personal INDIVIDUAL organization (the guest is its ORG_ADMIN and DRIVER) | `400` wrong or expired code (5 wrong attempts kill the code); `422` weak password |
| 3 | Invited driver (a manager registered them by phone) taps "claim account" | `POST /auth/otp/send` `{purpose: "INVITE"}` then `POST /auth/invitations/accept` `{phone_number, code, password}` | `400` code expired (72 h) or used; `404` no invitation for this phone |
| 4 | Login: phone + password | `POST /auth/login` `{phone_number, password, platform, app_version, device_label, push_token}` returns `access_token`, `refresh_token`, `memberships[]` | `401` wrong phone or password; `423` short lockout after repeated failures (ID-23); `403` user LOCKED or organization SUSPENDED/CLOSED |
| 5 | Person has several organizations: pick one (the last one is remembered, `user_state.last_organization_id`, and opens directly) | `GET /auth/me` lists memberships; `POST /auth/organization` `{organization_id}` switches the session without a new login | `403` membership not ACTIVE; `404` not a member |
| 6 | Forgot password | `POST /auth/otp/send` `{purpose: "PASSWORD_RESET"}`, then `POST /auth/password/reset` `{phone_number, code, new_password}` (also clears the lockout, ID-30) | `400` bad code; `429` limits |
| 7 | Silent session refresh (app start, or after `401`) | `POST /auth/refresh` `{refresh_token}` returns a new pair (the old refresh token is replaced each time) | `401` token unknown, expired (90 days on mobile) or already used: session deleted, go to login |
| 8 | Push token changed | `PUT /auth/session/push-token` `{push_token}` | `404` session gone |
| 9 | Logout (this device or all) | `POST /auth/logout` `{all_devices: bool}` | none expected; the row is deleted, idempotent |

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

| # | Screen / step | API call (all planned) | Main error cases |
|---|---|---|---|
| 1 | "Nearby trucks" list (alternative to scanning) | `GET /driving-sessions/nearby-vehicles?lat=&lon=` returns trucks whose last T-Box position is close to the phone | `404` no truck near; empty list is `200` |
| 2 | Scan the QR on the truck | `POST /driving-sessions` `{check_in_method: "QR", vehicle_code, location: {lat, lon}}` | `404` unknown or revoked code; not an error: a truck with another driver at the wheel is taken over (old session ends TAKEN_OVER), and the driver's open session on another truck ends OTHER_TRUCK (DR-07); the response says which session ended; `400` phone too far from the truck; `403` driver profile not ACTIVE or licence expired |
| 3 | Pick a truck from the nearby list | same call with `check_in_method: "APP"` and `vehicle_id` | same as step 2 |
| 4 | Warning when the truck belongs to another organization | returned in the response (`warnings: ["OTHER_ORGANIZATION"]`); the app shows a notice, check-in still succeeds | none |
| 5 | Home shows the truck, live battery and location while checked in (NT-07) | `GET /driving-sessions/current`, then `GET /telemetry/vehicles/{vehicle_id}/latest` (**built**) | `404` not checked in; `403` after check-out (the driver no longer sees the truck) |
| 6 | Check out | `POST /driving-sessions/{driving_session_id}/check-out` | `409` already ended (for example auto-ended after the truck stayed still); `404` not the caller's session. A trip still in progress is closed automatically as COMPLETED with a reason (DR-12) |

## 3. Trips (planned jobs and their execution)

Tables: `trips`. Rule: DR-12
([decision-log.md:344](../decisions/decision-log.md#L344)). A driver without a
fleet has no plan: Start creates a personal trip. Driver confirmation of an
assigned trip is deferred (deferred item 95): an assigned trip simply waits in
the list until Start.

| # | Screen / step | API call (all planned) | Main error cases |
|---|---|---|---|
| 1 | "My trips" list: assigned, in progress, done | `GET /trips?status=PLANNED,IN_PROGRESS&scope=mine&offset=&limit=` | none |
| 2 | Open a trip: origin, destination, planned times, truck | `GET /trips/{trip_id}` | `404` not the caller's trip |
| 3 | **Start trip** (must be checked in); optionally declare LOADED or EMPTY (MON-13) | `POST /trips/{trip_id}/start` `{declared_load_status}`; for a personal trip `POST /trips` `{origin_name, destination_name, declared_load_status}` creates and starts it. The server records time, T-Box position, odometer and battery % | `409` not checked in, or a trip already in progress in this session, or trip not PLANNED; `409` truck differs from the planned truck (allowed, flagged to the manager in the portal, not an error); offline start: the app sends its press time and the server checks it |
| 4 | **Finish trip** | `POST /trips/{trip_id}/finish` | `409` not IN_PROGRESS; `404` not the caller's trip |
| 5 | Reminder: truck moving with no trip started | push notification `NO_TRIP_STARTED` (inbox, section 8) | none |

## 4. Wallet

Tables: `wallets`, `payments`, `wallet_transactions`. Decisions: BL-13, BL-14,
BL-15 ([decision-log.md:248](../decisions/decision-log.md#L248)). The wallet is
the person's, across organizations. Top-up at launch is a **VietQR bank
transfer**: the app shows a QR carrying a unique `transfer_code`; a
bank-notification service matches the incoming transfer (no card gateway).

| # | Screen / step | API call (all planned) | Main error cases |
|---|---|---|---|
| 1 | Wallet tab: balance (may be negative after a session overrun, BL-14) | `GET /wallet` returns `balance`, `currency`, `status`, `minimum_balance_to_charge` | `404` wallet is created at first login, so unexpected |
| 2 | Top up: enter amount | `POST /wallet/top-ups` `{amount}` returns `payment_id`, `transfer_code`, VietQR payload, `expires_at` (payment PENDING) | `400` amount below the minimum; `403` wallet BLOCKED |
| 3 | Waiting screen after the transfer | `GET /wallet/top-ups/{payment_id}` polled, or push `TOP_UP_RECEIVED` | status FAILED (expired unpaid or amount mismatch): start again; a transfer with a wrong code is not matched and goes to staff (portal, wallets) |
| 4 | Transaction list (TOP_UP, SESSION_BILL, REFUND, ADJUSTMENT) with balance after each | `GET /wallet/transactions?offset=&limit=` | none |
| 5 | Low balance notice | push `LOW_WALLET_BALANCE` (BL-14) | none |

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
| 1 | Map of charging locations near me, with free connectors | `GET /charging-stations/nearby?lat=&lon=` (**built**; public locations only until login exists, then private ones for the owner's members and allowed organizations) | `422` bad coordinates |
| 2 | Open a station: price, connectors | `GET /charging-stations/{station_id}` and `GET /charging-stations/{station_id}/connectors` (**built**) | `404` unknown or deleted station |
| 3 | Tap "Scan to charge", scan the QR on the charger screen | `POST /charging-sessions/scan` `{qr_payload}` (planned). The backend: finds the charger, checks the wallet minimum balance (BL-14), checks access to a private location, freezes tariff price and VAT in a bill (QUOTED), creates the session PENDING with a single-use token (`id_token`), writes a `REMOTE_START` row in `charging_station_commands`; the gateway sends `RemoteStartTransaction` (1.6J) or `RequestStartTransaction` (2.0.1). Returns `session_id`, price per kWh | `404` QR unknown; `403` private location not allowed for this organization; `409` with code `INSUFFICIENT_BALANCE`: wallet below the minimum, with the amount to top up; `409` charger offline, or a session already active on the connector; `403` wallet BLOCKED |
| 4 | Progress screen: kWh, power, cost so far, battery % | poll `GET /charging-sessions/{session_id}` (**built**) and `GET /charging-sessions/{session_id}/measurements` (**built**), every few seconds; session is PENDING until the charger confirms | `404` not the caller's session; PENDING too long becomes ABANDONED (the app says "charger did not start") and the bill is VOID |
| 5 | Stop | by default on the charger screen or the truck stops itself. If the app offers a stop button later: `POST /charging-sessions/{session_id}/stop` (planned; command `REMOTE_STOP`) | `409` session not ACTIVE; charger answer REJECTED/TIMED_OUT is shown as "could not stop, use the charger screen" |
| 6 | Summary after the stop: energy, amount before VAT, VAT, total, wallet balance after | `GET /charging-sessions/{session_id}` plus bill fields; push `CHARGING_RECEIPT` | bill ON_HOLD (meter mismatch): "being checked", amount appears later |

## 6. Session history and bills

| # | Screen / step | API call | Main error cases |
|---|---|---|---|
| 1 | History list, newest first | `GET /charging-sessions?offset=&limit=` (**built**; planned filter `scope=mine` for the driver's own sessions) | none |
| 2 | Open one session and its bill (tariff version, energy, amount, VAT) | `GET /charging-sessions/{session_id}` (**built**); `GET /charging-sessions/{session_id}/bill` (planned) | `404` not the caller's; bill absent for ABANDONED |
| 3 | Download / share a receipt | `GET /charging-sessions/{session_id}/bill?format=pdf` (planned; e-invoices belong to organizations, not to the driver wallet at launch) | `404` bill not BILLED yet |

## 7. Own driving summary (DR-11)

A driver sees only a **summary** of their own sessions: date, truck plate,
check-in and check-out time, duration, distance, plus totals per day and week
for the driving-time warning. Never the route, GPS trail, stops or places.

| # | Screen / step | API call (planned) | Main error cases |
|---|---|---|---|
| 1 | "My driving" list | `GET /driving-sessions?scope=mine&from=&to=` | `422` range too large |
| 2 | Totals per day / week | `GET /driving-sessions/summary?scope=mine&group_by=day\|week` | none |

## 8. Notifications inbox

Tables: `notifications`, `notification_recipients` (NT-09 .. NT-12,
[decision-log.md:266](../decisions/decision-log.md#L266)). The inbox is per
person across all their organizations. Opening the list sets `seen_at` (clears
the badge); tapping an item sets `read_at` and opens the screen named by its
`subject_type` + `subject_id`, where that screen's own access check applies.
Push and e-mail are the only extra channels at launch (SMS deferred, item 96).

| # | Screen / step | API call | Main error cases |
|---|---|---|---|
| 1 | Badge on the app icon | `GET /notifications/unread-count` (**built**; today counts `read_at`, to be moved to the per-person recipients table; the "seen" count is planned) | none |
| 2 | Open the list | `GET /notifications?offset=&limit=` (**built**); planned `POST /notifications/mark-seen` sets `seen_at` | none |
| 3 | Tap one item | `PATCH /notifications/{notification_id}/read` (**built**), then the app opens the linked screen | `404` not the caller's notification |
| 4 | "Mark all as read" | `POST /notifications/mark-all-read` (**built**; idempotent, sets seen and read) | none |
| 5 | Open an alert's detail | `GET /notifications/{notification_id}` (**built**) | `404` |

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
