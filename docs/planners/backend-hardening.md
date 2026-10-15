# Planner: Backend hardening before real devices (FLT-03, PAY-10, ACC-01, ACC-13)

> Feature code: FLT-03 (Fleet manager scope), PAY-10 (session bills), ACC-01 /
> ACC-13 (accounts, administrator roles); no new feature
> Status: 🚧 In progress
> Created: 2026-10-15
>
> Inputs: "Next steps (debugging phase)" of
> [backend-refactor-implementation.md](./backend-refactor-implementation.md#next-steps-debugging-phase)
> (items 2, 4, 9, 10 and RV-ID7), decisions DM-29, FL-10, FL-13, BL-19, ID-51 in
> [decision-log.md](../decisions/decision-log.md). The owner approved this
> batch on 2026-10-15 ("go ahead" to the recommendation).

## 1. Goal

Close the gaps of the refactor run that need no product decision, before the
first real chargers and T-Boxes connect: a stable token secret, no lost bill,
no way to remove the last usable HEAD_ADMIN, a change history that always
names its reason, and a fleet limit that holds on every endpoint a limited
manager can reach. Backend only; no schema change.

Not part of it: real SMS / e-mail / bank providers (item 1 and 3 of the next
steps), HTTP-level route tests (needs the `httpx` approval), RV-OP9 and
RV-ID10 (owner decisions).

## 2. Scope decisions

| # | Decision | Recommended | Alternative (rejected) | Status |
|---|---|---|---|---|
| D1 | How to stop the random per-process token key | `IDENTITY_TOKEN_SECRET_KEY` is required everywhere (≥32 characters): the API refuses to start without it, `security.py` drops the random fallback, `make setup` writes a generated key into `backend/.env` when the line is empty | A new `APP_ENVIRONMENT` switch that requires the key only in production: a new setting, and development would keep the broken behaviour (logout on every restart) | ✅ 2026-10-15 |
| D2 | A completed session whose bill stayed QUOTED (a failed session-ended hook) | A staff action `POST /charging-sessions/{session_id}/bill/settle` (internal bill reviewers) in `app/api/charging_session_flow.py` re-runs the idempotent settle (COMPLETED) or void (ABANDONED); staff find such bills with the bill list's status filter | An automatic sweep: the hook failed on a bug or a database fault, so retrying blindly repeats it; recorded in `deferred.md` | ✅ 2026-10-15 |
| D3 | Two simultaneous removals of the last two usable HEAD_ADMINs (RV-ID7) | A transaction-level advisory lock (`pg_advisory_xact_lock`) taken before the usable-holder count, on every path that can remove a usable HEAD_ADMIN: end or lock a membership, revoke the role, lock the account (which had no guard) | `SELECT ... FOR UPDATE` on the assignment rows: does not serialize a change of the membership's or the account's status | ✅ 2026-10-15 |
| D4 | History trigger without a reason (DM-29) | Every UPDATE path already sets a reason (inventory 2026-10-15), so the trigger raises again when none is set (ends the DM-29 deviation); a staff force-close and a vehicle transfer pass the acting user on to the bill and trip they close | Keep the `Unspecified change` fallback | ✅ 2026-10-15 |
| D5 | Rows with no truck under the fleet limit | Notifications and support cases with no truck stay visible to a limited manager (alert routing already delivers them to every manager); every other truck-less row (charging sessions and their bills, warranties on chargers, packs and devices in stock) is hidden, like truck-less trips | Hide every truck-less row: a limited manager would lose an SOS sent without a VIN | ❓ Assumed, owner to confirm |
| D6 | Fleet assignments of a membership that ends | Closed by a new membership-end hook when the membership ENDS; a LOCK leaves them open, because no open row means "whole organization" and an unlock would widen the person's reach | Close on lock too | ✅ 2026-10-15 |

### 2.1 Out of scope (deferred)

- Automatic retry of a failed session-ended hook (D2) — new `deferred.md` item.

## 3. Design

### 3.1 Domain and boundaries

- `app/api/membership_end_hooks.py` → `fleet`: `handle_membership_end` (new
  row in `domain-boundaries.md`).
- `app/api/charging_session_flow.py` → `charging_sessions`: a new
  `resolve_session_ended_event` (the readings the billing hook needs).
- Every other fleet-limit filter comes from the router dependency
  `identity.dependencies.get_visible_vehicle_ids`; `telematics` uses its
  existing `fleet` edge.

### 3.2 Data model

No schema change. The history trigger function changes (built by
`app/libs/db/history_ddl.py`), so the baseline is re-applied with
`make db-reset`.

### 3.3 API / interfaces

| Method | Path | Request | Response | Errors |
|---|---|---|---|---|
| POST | /api/v1/charging-sessions/{session_id}/bill/settle | – | `ChargingSessionBillResponse` | 404 session or bill, 409 session not ended |

Fleet limit added (404 outside the limit) to: notification list and detail,
support case list / detail / create, charging session list / detail /
meter values / measurements / bill, bill list, battery list / detail /
installation periods, warranty list / detail, telematics list / detail /
health / status reports / device-health list and summary / config push,
vehicle ownership periods / update / delete, trip detail and its actions,
trip planning on a truck outside the limit.

## 4. Steps

- [ ] **Step 1 — Token secret required (D1).**
- [ ] **Step 2 — Last HEAD_ADMIN lock (D3).**
- [ ] **Step 3 — Bill retry action (D2).**
- [ ] **Step 4 — History trigger raises (D4).**
- [ ] **Step 5 — Fleet limit on every reachable endpoint (D5, D6).**
- [ ] **Step 6 — Verify and document**: `make check`,
  `make backend-test-integration`, convention review, docs sync.

## 5. Verification

<Filled in as steps complete.>

## 6. Deferred / follow-ups

<deferred.md item numbers created by this planner.>
