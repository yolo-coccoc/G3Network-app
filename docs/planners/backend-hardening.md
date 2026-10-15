# Planner: Backend hardening before real devices (FLT-03, PAY-10, ACC-01, ACC-13)

> Feature code: FLT-03 (Fleet manager scope), PAY-10 (session bills), ACC-01 /
> ACC-13 (accounts, administrator roles); no new feature
> Status: 🚧 In progress — Steps 1-5 done and committed (2026-10-15), Step 6 done except the owner's confirmation of D5 (assumed); the planner moves to `done/` once D5 is confirmed
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
  `identity.dependencies.get_visible_vehicle_ids` (`telematics` included;
  its existing `fleet` edge still serves only the fleet-wide views).

### 3.2 Data model

No schema change. The history trigger function changes (built by
`app/libs/db/history_ddl.py`), so the baseline is re-applied with
`make db-reset`.

### 3.3 API / interfaces

| Method | Path | Request | Response | Errors |
|---|---|---|---|---|
| POST | /api/v1/charging-sessions/{session_id}/bill/settle | – | `SessionBillResponse` | 404 session or bill, 409 session not ended |

Fleet limit added (404 outside the limit) to: notification list and detail,
support case list / detail / create / update / delete and SOS, charging
session list / detail / meter values / measurements / bill / receipt, bill
list, battery list / detail / installation periods, warranty list / detail,
telematics list / detail / health / status reports / device-health list and
summary / config push / create / update / delete, vehicle ownership periods
/ update / delete, trip detail and its actions, trip planning on a truck
outside the limit.

## 4. Steps

- [x] **Step 1 — Token secret required (D1).** 2026-10-15, commit `c4e6701`.
  `identity/security.require_token_secret` (≥32 characters, exposed as
  `identity.service.require_token_secret`) is called in the API's lifespan;
  the random fallback is removed; `make setup` writes a generated key into
  `backend/.env` when the line is missing, empty or too short; README,
  `.env.example`, `dev-environment.md` updated. Decision IS-12. Evidence:
  `tests/identity/test_identity_security_smoke.py` (missing / short key
  refused, long key accepted); `tests/conftest.py` gives the test run a
  random key when the configured one is missing or short.
- [x] **Step 2 — Last HEAD_ADMIN lock (D3).** 2026-10-15, commit `03cdc4f`.
  `identity/repository.lock_role_holder_changes` (`pg_advisory_xact_lock` on
  `hashtext('role-holders:HEAD_ADMIN')`) and
  `account_service.count_other_head_admins_under_lock` (counts the other
  usable holders, excluding the membership or the account being removed);
  used by ending / locking a membership, revoking HEAD_ADMIN and, new, locking
  the account. Decision ID-53. Evidence: smoke tests in
  `tests/identity/test_identity_review_smoke.py` (locking the last usable
  HEAD_ADMIN's account refused; a locked HEAD_ADMIN membership can end while
  a usable one remains) and the PostgreSQL test
  `test_role_holder_lock_holds_a_second_transaction_until_commit`
  (`tests/test_postgres_integration.py`).
- [x] **Step 3 — Bill retry action (D2).** 2026-10-15, commit `0f3c73d`.
  `POST /api/v1/charging-sessions/{session_id}/bill/settle`
  (`SESSION_BILL_SETTLERS`: PAY-10 roles, internal only) in
  `app/api/charging_session_flow.py` calls the new
  `charging_sessions.service.resolve_session_ended_event` (session locked
  `FOR UPDATE`, 409 unless COMPLETED / ABANDONED) and
  `app/api/billing_hooks.bill_ended_session`. `SessionEndedEvent.ended_by`
  carries the staff member to `settle_session_bill` / `void_session_bill`
  (`changed_by` of the bill history), also on a force-close. Decision BL-29;
  `deferred.md` 105 (automatic retry) and a note on 101. Evidence:
  `tests/billing/test_billing_hooks_smoke.py` and the PostgreSQL test
  `test_staff_retry_bills_a_completed_session_whose_hook_failed`
  (`tests/test_postgres_billing_review.py`).
- [x] **Step 4 — History trigger raises (D4).** 2026-10-15, commit `bfd0680`.
  The trigger function built by `app/libs/db/history_ddl.py` raises
  SQLSTATE `HC001` (`MISSING_CHANGE_REASON_SQLSTATE`) when no reason is set;
  `UNSPECIFIED_CHANGE_REASON` removed; the check sits after the trigger's
  `WHEN`, so an untracked-only change (a wallet balance) passes. Needs
  `make db-reset` on an existing database. Decision DM-30 (DM-29's deviation
  superseded, DM-S4); `database.md` rule restored. Evidence:
  `test_missing_change_reason_sqlstate_is_a_valid_custom_code`
  (`tests/identity/test_identity_schema_smoke.py`);
  `test_tracked_update_writes_history_row_with_actor_and_reason` now expects
  the update without a context to fail; PostgreSQL tests that updated rows
  directly set a reason first.
- [x] **Step 5 — Fleet limit on every reachable endpoint (D5, D6).**
  2026-10-15, commit `eefd2ff`. The `get_visible_vehicle_ids` dependency is
  passed by the routers of notifications, support, charging sessions (and the
  session flow's bill and receipt), billing (bill list), batteries,
  warranties, telematics, vehicles and trips (§3.3); outside the limit is a
  404. D5 applied as assumed (truck-less notifications and support cases
  visible, other truck-less rows hidden, a limited manager must plan a trip
  with a truck, a caller's own charging sessions and bills always pass). D6:
  `fleet.service.handle_membership_end` registered in
  `app/api/membership_end_hooks.py` (closes open assignments on END, not on
  LOCK); `assign_fleet_to_membership` refuses an ended membership (404).
  Decisions FL-14, FL-15 (⏳), FL-16; new `domain-boundaries.md` edge
  `membership_end_hooks.py` → `fleet`. Evidence: smoke tests per area (§5)
  and the PostgreSQL test `test_every_fleet_limited_list_runs_on_postgresql`
  (`tests/test_postgres_operations_review.py`).
- [x] **Step 6 — Verify and document**: `make check`,
  `make backend-test-integration`, convention review, docs sync (2026-10-15;
  results in §5). Open: the owner's confirmation of D5.

## 5. Verification

- `make check`: passed, 1112 passed / 60 skipped (ruff, import-linter, mypy,
  smoke tests, domain-model and feature-catalog checks).
- `make backend-test-integration`: 59 / 59 passed on the Step 5 code before
  the review fixes.
- New PostgreSQL tests: the role-holder lock holds a second transaction until
  the first commits (`tests/test_postgres_integration.py`); the staff retry
  bills a completed session whose hook failed
  (`tests/test_postgres_billing_review.py`); every fleet-limited list query
  runs on PostgreSQL (`tests/test_postgres_operations_review.py`).
- Smoke tests per area: identity (token secret; account lock of the last
  usable HEAD_ADMIN; a locked HEAD_ADMIN's membership can end), billing hooks
  (`ended_by` reaches the bill), the history `SQLSTATE` constant, and the
  fleet limit in `tests/batteries/`, `tests/billing/`,
  `tests/charging_sessions/`, `tests/drivers/` (trip detail, plan outside the
  limit, plan without a truck), `tests/fleet/` (END closes assignments, LOCK
  does not; no assignment to an ended membership), `tests/notifications/`
  (organization view narrowed; a delivered alert stays readable),
  `tests/support/`, `tests/telematics/`, `tests/vehicles/`,
  `tests/warranties/`; `tests/test_startup_hooks_smoke.py` checks the new
  membership-end hook is registered.
- Convention review notes not acted on:
  - `backend/app/domains/fleet/service.py` is about 1,830 lines, over the
    ~1,000-line threshold of `backend-coding-conventions.md` §7.4; splitting
    it into area modules is left to a later `refactor:` commit (no behaviour
    change, never mixed into a fix).
  - The expression "the caller's ID only when the caller is limited"
    (`principal.user_id if visible_vehicle_ids is not None else None`) is
    repeated in `billing/bill_service.py` and twice in
    `charging_sessions/service.py`; small enough to leave as is.

## 6. Deferred / follow-ups

- `deferred.md` 105 — automatic retry of a failed session-ended hook (D2).
- `deferred.md` 101 — note added: the staff member of a force-close is now in
  the bill's history; the session row still does not name them.
- Owner to confirm D5 (FL-15 in the decision log, question on FLT-03 in the
  feature catalog).
