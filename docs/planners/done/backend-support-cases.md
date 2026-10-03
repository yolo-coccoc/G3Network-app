# Planner: Support Case Tickets & SOS Intake (F-I1, F-I2)

> Feature code: F-I1 (In-app support tickets), F-I2 (Roadside/incident SOS)
> Status: ✅ Done (MVP/POC scope); F-I3/F-I4 deferred this round
> Created: 2026-09-18

## 1. Goal

Build the `support` domain covering F-I1 (in-app support tickets) and
F-I2 (roadside/incident SOS) — the buildable core of a four-feature spec
(F-I1/F-I2/F-I3/F-I4) that also includes a repair/rescue dispatch network
(F-I4) and maintenance-slot booking (F-I3), both deferred this round by
explicit decision.

## 2. Scope decisions

- **One `support_cases` table discriminated by `case_type`** (`TICKET`,
  `SOS`), not two tables. F-I2's own spec says the SOS case is "forwarded
  to F-I4" and F-I4 says the case is "synced with the F-I1 ticket" —
  three features share one lifecycle. Splitting F-I1/F-I2 into separate
  tables now would have forced inventing a sync mechanism whose only job
  is to undo the split later when F-I4 is built.
- **Vehicle/driver context is client-supplied, then validated — not
  fetched from `telemetry`.** F-I1 says context is "auto-attached (VIN,
  location, error code)"; that auto-attach is the driver app's job — it
  knows its own live GPS fix and the error code on screen, which is more
  trustworthy for an SOS than the last MQTT telemetry point (which may be
  minutes stale). The backend's job is to validate the VIN/driver ID
  resolve to real records and snapshot the rest. This deliberately keeps
  `support` off the `telemetry` domain-boundary edge entirely.
- **SLA is a stored deadline plus a computed flag — no background
  monitor.** `SUPPORT_TICKET_RESPONSE_SLA_MINUTES` (60, engineering
  placeholder) and `SUPPORT_SOS_RESPONSE_SLA_MINUTES` (5, matching the
  spec's stated ≤5-minute callback) are config, not hardcoded constants.
  The service copies the value onto the row at creation time and stores
  `response_due_at`, so a later config change never rewrites a past
  case's SLA history. `is_sla_breached` is computed at response time
  (`(first_responded_at or now) > response_due_at`), never stored. No
  monitor was built: nothing currently consumes a breach (no CSKH
  identity, no escalation target), so a monitor would write signals
  nobody routes — deferred as `deferred.md` item 70.
- **`location` has no GIST index.** Unlike `charging_stations.location`,
  this column is only ever an input snapshot at case-creation time, never
  a search target — matching `vehicle_telemetry.location`'s
  `spatial_index=False` rationale. F-I4's nearest-partner routing is the
  feature that would justify an index, and it's deferred.
- **A CLOSED/CANCELLED case is terminal.** `update_support_case` raises
  `SupportCaseStateError` (409) for any further update once a case
  reaches either state — mirrors F-B2's guard against mutating a
  `COMPLETED` charging session.
- **Status transitions auto-stamp their timestamps.** Moving to
  `ACKNOWLEDGED` sets `first_responded_at` (once); moving to `RESOLVED`
  or `CLOSED` also backfills any earlier timestamp not yet set, since
  reaching a later state implies the earlier one happened even if the
  caller skipped stages.
- **An unknown VIN/driver ID raises, not silently ignores.** Both
  `SupportVehicleNotFoundError` and `SupportDriverNotFoundError` surface
  as 404 — an explicit "attach this vehicle/driver" request that
  silently drops the reference would hide a client bug.

## 3. What was built

### 3.1 New domain `backend/app/domains/support/`

Standard layout mirroring `drivers/` file-for-file.

- `types.py`: `SupportCaseType` (TICKET, SOS), `SupportCaseCategory`
  (TECHNICAL, BATTERY, CHARGING, BREAKDOWN, ACCIDENT, BILLING, OTHER),
  `SupportCaseChannel` (IN_APP, ZALO, HOTLINE — F-I1 logs Zalo/hotline
  contacts as tickets, though no actual integration exists behind those
  members), `SupportCaseStatus` (OPEN, ACKNOWLEDGED, RESOLVED, CLOSED,
  CANCELLED) plus `is_terminal_status`, and `SupportCaseReference`.
- `models.py`: `SupportCaseModel` (`support_cases`) — nullable
  `vehicle_id`/`driver_id` FKs (`ondelete="RESTRICT"`), a `vin` snapshot
  column (kept even if the vehicle's real VIN changes or the vehicle is
  later soft-deleted), `location` (`Geography`, no index), `sla_response_
  minutes`/`response_due_at`/`first_responded_at`/`resolved_at`/
  `closed_at`. Indexes: `(status, created_at)`, `(vehicle_id,
  created_at)`, and the partial `response_due_at WHERE first_responded_at
  IS NULL`.
- `exceptions.py`: `SupportError`, `SupportCaseNotFoundError`,
  `SupportVehicleNotFoundError`, `SupportDriverNotFoundError`,
  `SupportCaseStateError`.
- `schemas.py`: `SupportTicketCreateRequest` (subject required),
  `SupportSosCreateRequest` (no subject — auto-filled; location
  required, unlike the optional pair on a ticket), `SupportCaseUpdateRequest`
  (PATCH), `SupportCaseResponse` (+ computed `is_sla_breached`, enriched
  `driver_name`; `vehicle_vin` read directly from the stored snapshot, no
  cross-domain call needed for it), `SupportCaseListResponse`.
- `repository.py`: `insert`, `get_by_id`, `list_all` (filters: `status`,
  `case_type`, `vehicle_id`), `count`, `update_fields`, `soft_delete`.
- `service.py`: `create_support_ticket`, `create_support_sos` (auto-fills
  `subject = "SOS - <category>"`), `get_support_case`,
  `list_support_cases`, `update_support_case` (terminal-state guard +
  auto-stamped timestamps via `_apply_status_timestamps`),
  `soft_delete_support_case`, `build_support_case_response` (async
  enrichment via `driver_service.resolve_driver_reference_by_id`),
  `calculate_is_sla_breached` (pure), `resolve_support_case_reference_by_id`,
  `_resolve_case_context` (shared VIN/driver validation helper used by
  both create functions).
- `router.py`: `POST /cases`, `POST /sos`, `GET /cases`,
  `GET /cases/{case_id}`, `PATCH /cases/{case_id}`,
  `DELETE /cases/{case_id}`.

### 3.2 Wiring

- `backend/app/api/main.py`: registers `support_router` at
  `/api/v1/support`.
- `backend/app/libs/db/migrations/env.py`: imports `SupportCaseModel`.
- `backend/app/libs/common/config.py`: `SUPPORT_TICKET_RESPONSE_SLA_MINUTES`,
  `SUPPORT_SOS_RESPONSE_SLA_MINUTES`.

### 3.3 Migration `0019_support_cases`

`down_revision = "0018_drivers"`. Creates `support_cases` with all
indexes described above, including the partial index on `response_due_at`.

## 4. Boundary edges

`support → vehicles` (`resolve_vehicle_reference_by_vin`) and
`support → drivers` (`resolve_driver_reference_by_id` — the first
consumer of that DTO outside `drivers` itself). Both one-directional; no
cycle. No `support → telemetry` edge exists by design (see scope
decisions above).

## 5. Verification

- `black`, `isort`, `ruff check`, `mypy .` — all clean.
- Unit tests (`test_service_smoke.py`): ticket creation with the ticket
  SLA; unknown-VIN and unknown-driver-ID rejection; SOS creation using
  the SOS SLA and auto-filled subject/channel; a status update to
  `ACKNOWLEDGED` stamping `first_responded_at` exactly once; a further
  update on a `CLOSED` case raising `SupportCaseStateError`;
  `calculate_is_sla_breached` for a past-due unanswered case and for a
  case answered before its deadline; `get_support_case` 404ing for an
  unknown ID. Schema tests (`test_schema_smoke.py`): the ticket's
  optional-coordinates-together validator, the SOS's required-coordinates
  contract.
- Migration `0019` verified via `upgrade head` → `downgrade -1` →
  `downgrade -1` → `upgrade head` against the live local Postgres
  (alongside `0020_fleet`'s round trip), confirming the table and its
  partial index.
- **Live end-to-end** against the running API: created a ticket with full
  vehicle/driver context (confirmed VIN/driver enrichment, 60-minute SLA
  deadline); confirmed an unknown-VIN ticket 404s; created an SOS
  (confirmed the 5-minute SLA, auto-filled subject `"SOS - BREAKDOWN"`,
  `channel=IN_APP`); walked a ticket through
  OPEN → ACKNOWLEDGED → RESOLVED → CLOSED (confirmed each timestamp
  stamped exactly once); confirmed a further PATCH on the CLOSED case
  returns 409; confirmed `GET /support/cases?status=CLOSED` filtering
  works. All test data cleaned up afterward; API server stopped.

## 6. Deferred (see `docs/decisions/deferred.md`)

- **F-I4** (partner directory, nearest-partner routing, dispatch/
  acceptance-SLA tracking) — item 68.
- **F-I3** (maintenance-scheduling booking) — item 69; also blocked on
  F-F4 (maintenance reminders) having no owning domain.
- **SLA-breach monitor/escalation** — item 70; no consumer of a breach
  exists yet (no CSKH identity).
- **Case ownership without authentication** — item 71; `driver_id` is
  client-supplied and unverified, matching every other domain's current
  unauthenticated state.
- Zalo/hotline ingestion (the `channel` enum exists; no integration
  behind it) and on-site photos/object storage (F-I4's input, not built
  since F-I4 itself is deferred).
