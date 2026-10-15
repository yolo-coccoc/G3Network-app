# Planner: Bug fixes from the full backend scan (2026-10-15)

> Feature code: none new; fixes in ACC-03/ACC-13 (invitations, roles), DRV-02 /
> DRV-04 (driving sessions, trips), MON-01/MON-02/MON-14 (telemetry reads),
> FLT-02/FLT-03 (fleets), CHG-01/CHG-03 (QR charge, receipt), NTF-01 (inbox),
> WAR-01 (warranties), and the device-input paths of DEV-03, CHG-02, STN-03
> Status: 🚧 In progress — Batch A done (2026-10-15); Batch B waits for the
> first real device (charger bring-up, 1.6J planner Step 10, or the first
> T-Box); Batch C waits for frontend work or deployment (`deferred.md` 106)
> Created: 2026-10-15
>
> Inputs: a read-only scan of every backend domain by six agents on
> 2026-10-15 (identity + `app/api` + `libs`; telemetry + telematics; vehicles,
> batteries, warranties, drivers, fleet, support; charging sessions, billing,
> notifications; charging stations + OCPP gateway). Already-known items (the
> refactor planner's Known issues, Review findings and Next steps,
> `deferred.md`, `done/backend-hardening.md`, open questions) were excluded.
> Each finding was checked end to end in the code; H1, H2, H4 and H5 were
> re-checked by hand, and H3, H4 and H5 were reproduced by the agents.

## 1. Goal

Fix the defects the scan found, in the order they can actually happen. Today
the backend runs alone (no frontend, no real device or external API; only the
simulators in `simulators/`), so:

- **Batch A (now):** wrong business logic or access rules that Swagger or the
  simulators can already reach.
- **Batch B (before the first real device connects):** device input that
  stops a worker, leaves a session open or produces wrong data or alerts.
- **Batch C (before a frontend or deployment):** odd client input that gives a
  500 instead of a 4xx, and gaps only a real client or real traffic exposes.

No schema change is expected.

## 2. Scope decisions

| # | Decision | Recommended | Alternative (rejected) | Status |
|---|---|---|---|---|
| D1 | Telemetry after a truck sale (H6) | Apply VH-11 as written: a customer reads only the telemetry rows of its own ownership periods (`telemetry.organization_id`), the previous owner keeps read access to its period's history and reports, the live view (latest position) is the current owner's only, and the truck's condition (battery-health trend) and lifetime totals follow the truck | Leave the reads keyed on the current owner (the previous owner's GPS trail goes to the buyer) | ✅ 2026-10-15 (owner) |
| D2 | A trip planned by one organization started on another organization's truck | Refuse the start (409): a trip runs only on a truck of its own organization | Re-own the trip to the truck's organization (the planner loses its plan) | ✅ 2026-10-15 (owner) |
| D3 | DISPATCHER receives geofence and trip alerts but may not open the inbox (NTF-01) | Add DISPATCHER to NTF-01 | Stop routing those alerts to DISPATCHER | ✅ 2026-10-15 (owner) |
| D4 | A fleet-limited manager moving a truck between their own fleets | Allowed: adding a truck that sits in one of the caller's visible fleets moves it | Keep refusing (the move is impossible for a limited manager) | ✅ 2026-10-15 (owner) |

## 3. Findings

Severity: H high, M medium, L low. "Repro" = reproduced by a test or a
direct call during the scan.

### Batch A — now

| # | Sev | Where | Defect | Fix |
|---|---|---|---|---|
| A1 (H4) | H | `identity/account_service.py` `send_one_time_code` | A code requested through the public `otp/send` with purpose `INVITE` has no organization, so `invitations/accept` refuses it (ID-52), and being newer it kills the inviter's real code. Repro. | Give the public INVITE code the organization of the person's pending invitation (newest INVITE code / single INVITED membership); stay silent when there is none |
| A2 (H5) | H | `drivers/service.py` check-in | A manager with a driver profile checks themselves in from the portal (`PORTAL`, own profile) to another organization's truck with no location check and takes over its driver. Repro. | Every PORTAL check-in requires the truck to be in the caller's reach (`can_access_organization`) |
| A3 (H6) | H | `telemetry/repository.py`, `telemetry/service.py` | History and reports read every owner's rows and check access against the current owner only (D1) | Filter a customer's reads by `telemetry.organization_id`; let a former owner read its period; battery-health trend unchanged |
| A4 | M | `drivers/router.py` check-in / check-out | The fleet limit (FL-14) does not apply: a limited manager checks drivers in to, or out of, trucks outside their fleets | Pass `get_visible_vehicle_ids`; 404 for a truck outside the set |
| A5 | M | `drivers/service.py` check-in, `identity/member_service.py` `revoke_role` | Check-in does not require the DRIVER role, and revoking DRIVER leaves the open driving session running | Check the role at check-in; a role-revoked hook ends the open session (registered in every process, CV-21) |
| A6 | M | `drivers/trip_service.py` `start_trip` | A trip of organization A started on organization B's truck stores B's positions on A's trip (DR-11, D2) | Refuse with 409 |
| A7 | L | `app/api/vehicle_transfer.py` → `drivers/service.py` | The trip closed by a truck transfer records no actor (hardening D4 says it does) | Pass the staff member as `changed_by` |
| A8 | L | `charging_stations/command_service.py` cancel | Cancelling a queued REMOTE_START leaves its PENDING session (driver blocked, token still accepted) | Abandon the session in the same transaction |
| A9 | L | `app/api/charging_session_flow.py` receipt | The receipt shows amounts of an ON_HOLD bill | Amounts only when the bill is BILLED |
| A10 | L | `identity/types.py` NTF-01, `notifications/routing.py` | DISPATCHER gets alerts it cannot open (D3) | Add DISPATCHER to NTF-01 |
| A11 | L | `fleet/service.py` add vehicle | A limited manager cannot move a truck between their own fleets (D4) | Allow the add as a move when the truck's current fleet is visible to the caller |
| A12 | L | `warranties/service.py` create / edit | Two overlapping warranties of one type on one object pass under concurrent writes | Advisory lock on the object and type before the overlap check |

### Batch B — before the first real device connects

| # | Sev | Where | Defect | Fix |
|---|---|---|---|---|
| B1 (H1) | H | `telemetry/alerting.py` anomaly body | About 23+ fault codes (or one very long code) make the body exceed `notifications.body` varchar(500): the worker stops and crash-loops while the fault lasts | Cap code count and length in the schema; short body (count + first codes), truncated to the column |
| B2 (H2) | H | `telematics/schemas.py` `_coerce_number`, status consumer | A 400-digit integer raises `OverflowError`, uncaught: status ingestion stops for every device. Repro | Catch `OverflowError`; widen the consumer's per-message catch |
| B3 (H3) | H | `charging_stations/ocpp/measurement_units.py` | A float32 "no value" (-3.4E+38) or huge reading on a known measurand raises `InvalidOperation` in StopTransaction: the session never ends or is billed (RV-CS5 incomplete). Repro | Check the range before quantizing; skip the sample |
| B4 (H7) | H | `ocpp/raw_log.py`, adapters' text fields | A NUL character in a frame or text field fails the insert: reconnect loop, Boot never Accepted, stop retried forever | Strip `\x00` before storing |
| B5 (H8) | M | `charging_sessions/repository.py` energy-register reads | Per-phase energy registers are read as the outlet total: every bill ON_HOLD, energy series ~30× | Add `phase IS NULL` to the three energy-register queries |
| B6 | M | `ocpp/ocpp16_charge_point.py` start retry | A retried start on another gun gets the first gun's transactionId: wrong bill | Match the connector before treating it as a retry |
| B7 | M | `ocpp/command_loop.py` | A command swept to TIMEOUT while waiting for the call lock is still sent (RV-CS6 incomplete) | Re-check the outcome inside the lock, or time from the send |
| B8 | M | `telemetry/service.py`, `detection.py`, `geofencing.py` | A late or replayed reading re-raises an alert its successor already raised | Run crossing detectors only for the newest reading |
| B9 | M | `telemetry/detection.py` | Comparing with a previous row that lacks the field: temperature alert storm, SOH alert never | Compare with the newest earlier row that has the field |
| B10 | M | `telematics/schemas.py` timestamp | A status report dated far ahead or back freezes the device's health view | Bound the timestamp like `TELEMETRY_MAX_FUTURE_SKEW_SECONDS` |
| B11 | L | `telematics/repository.py` mapping by serial | Buffered readings from before a remount or sale go to the new truck / owner | Skip or flag readings older than the mount |
| B12 | L | `ocpp/ocpp201_charge_point.py` | 2.0.1 `Started` without meter values refused; a repeated `Ended` gets CALLERROR | Record in `deferred.md` 78 (2.0.1 parity) |

### Batch C — before a frontend or deployment

| # | Sev | Where | Defect | Fix |
|---|---|---|---|---|
| C1 | L | every HTTP request schema with free text | A NUL character gives 500 (RV-OP3 covered MQTT only) | One shared validator for text and `Reason` |
| C2 | L | `libs/common/pagination.py`, `charging_sessions/schemas.py`, `notifications/router.py` | Huge `page`, `connector_number`, `after_id` / notification IDs give 500 | Upper bounds |
| C3 | L | `identity/compliance_router.py` audit search | A time without a zone gives 500 | `AwareDatetime` or 400 |
| C4 | M | `identity/security.py` phone normalization | Non-ASCII digits give one SIM several login IDs and OTP quotas | ASCII-only digits |
| C5 | M | `notifications/repository.py` poll cursor | An alert committed after a higher ID is skipped by `after_id` polling | Commit-lag margin or overlap re-read |
| C6 | L | `libs/db/session.py`, `ocpp/library_logging.py` | DB-error tracebacks carry SQL parameters (QR tokens) into the log | `hide_parameters=True` outside debug |
| C7 | L | `identity/account_service.py` `accept_invitation` | Accepting into a suspended organization activates and commits, then answers 403 | Check the organization first |
| C8 | L | `batteries/service.py` install | A backdated fitting overlaps the truck's previous pack in the history | Bound by the truck's last removal |
| C9 | L | `fleet/service.py` tree, `support/service.py` SOS without VIN | A suspended customer: staff cannot read its fleet tree or log a hotline SOS (409) | Plain existence check on those paths |

## 4. Steps

- [x] **Step 1 — Batch A** (2026-10-15). Commits `08eb91f` (A2, A4-A7, A10
  role table: DR-17, DR-18), `49c93b1` (A1), `703c1f2` (A3: TM-21) and
  `400cc1a` (A8, A9, A11 with the explicit `move` flag, A12, A10 inbox-only:
  FL-17, NT-18). Decisions D1-D4 are TM-21, DR-17, NT-18 and FL-17 in the
  decision log; new edges in `domain-boundaries.md` (`telemetry` →
  `vehicles.has_organization_owned_vehicle`, the role-revoked hook in
  `membership_end_hooks.py`). Review notes kept as they are: a fleet move into a
  fleet outside a limited manager's reach is already refused by
  `_get_fleet_record`; telematics device health still reads `last_seen_at`
  from every owner's rows (no position, system lookup); a truck sold and bought
  back inside one report window folds the other owner's distance into the
  odometer check (rare, not handled).
- [ ] **Step 2 — Batch B**, right before the first real device connects.
- [ ] **Step 3 — Batch C**, right before frontend work or deployment.
- [ ] **Step 4 — Verify and document** per batch: convention review, docs
  sync, feature catalog.

## 5. Verification

Batch A: `make check` 1,139 passed / 60 skipped; `make backend-test-integration`
60/60 passed (the shared `_integration_driver` helper now grants DRIVER).
New smoke tests: `tests/drivers/test_drivers_bugfix_scan_smoke.py` (A2, A4,
A5, A6, A7), `tests/telemetry/test_telemetry_ownership_smoke.py` (A3, 13
tests), A1 in `tests/identity/test_identity_review_smoke.py`, A8 in
`tests/charging_stations/test_charging_stations_wp7_smoke.py`, A9 in
`tests/charging_sessions/test_qr_start_flow_smoke.py`, A10 in
`tests/notifications/test_notifications_service_smoke.py`, A11 in
`tests/fleet/test_fleet_service_smoke.py`, A12 in
`tests/warranties/test_warranties_service_smoke.py`. Convention review: no
violation; the boundary rows and three over-long docstring lines were fixed.

## 6. Deferred / follow-ups

- B12 goes to `deferred.md` 78 (2.0.1 parity).
