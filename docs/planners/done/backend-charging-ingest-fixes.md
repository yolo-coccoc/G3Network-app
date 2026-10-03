# Planner: Charging-Session Ingestion Correctness Fixes (F-B2)

> Feature code: F-B2 (Charging-session logging)
> Status: ✅ Done (MVP scope; closed 2026-10-04 when moved to `done/`).
> Everything left over is deferred in `docs/decisions/deferred.md`; unticked
> acceptance items below were never re-verified. Status before closing:
>
> 🚧 In progress (four correctness fixes done; the deferred
> reliability path in `deferred.md` item 27 remains open)
> Created: 2026-09-17

## 1. Goal

Stop four known-wrong behaviors in charging-session ingestion from
silently corrupting data that ultimately feeds F-C5's billing/reporting
totals, without reopening the deferred reliability path (retry, DLQ,
out-of-order recovery, dedup — `deferred.md` item 27, `tech-decisions.md`'s
"Charging MVP ideal"). This is deliberately a scoped correctness pass, not
a resilience overhaul: every fix stops the *happy path itself* from
writing a number nothing can vouch for; none of them make the pipeline
tolerant of genuinely lost or duplicated messages.

## 2. Scope decisions

- **A prerequisite bug fix came first (Step 0), not part of F-B2's four
  fixes but blocking all of them.** `python-ocpp`'s `ChargePoint
  ._handle_call` only snake_cases inbound JSON keys and splats the result
  as handler kwargs — it never constructs the `ocpp.v201.datatypes`
  dataclasses the handlers were annotated with. Nested OCPP objects always
  arrive as plain dicts at runtime, which is exactly why the charging
  session simulator was crashing with `AttributeError`. Fix #4 reads even
  deeper into this same structure (`measurand`, `unit_of_measure`), so it
  couldn't be written or verified without this fixed first. Handlers are
  now typed against `OcppPayload = dict[str, Any]`; two new helpers
  (`parse_ocpp_transaction_id`, `parse_ocpp_evse_reference`) read the dict
  shape explicitly. **No defensive dict-or-dataclass dual-mode accessor**
  — that would hide which contract is real and double the state space for
  no benefit, since dataclasses never actually arrive.
- **Fix #2 raises, doesn't log-and-ignore.** An event for an
  already-`COMPLETED` session raises a new `ChargingSessionStateError`,
  which surfaces as an OCPP `CALLERROR` on an otherwise-healthy
  connection — the same contract `on_status_notification` already
  documents. Log-and-ignore was rejected: it would be idempotency handling
  wearing a disguise (exactly `deferred.md` item 27's job), and it forces an
  awkward choice about whether to still append the event row for an
  action that was never actually applied.
- **A duplicate `Ended` and a late `Updated` are treated identically** —
  both are just "an event for a terminal session." Telling them apart
  needs the `seqNo`-keyed dedup that fix #1 only *enables*, not
  implements; guessing wrong risks silently discarding a genuinely
  different final meter reading.
- **Fix #3 is a *time*-ordering check only, never a value check.** A
  meter reset (register decreases while time still moves forward) still
  applies and still produces a wrong total — deliberately, since
  reconciling that is item 27's job. A dedicated test
  (`test_ingest_meter_values_still_applies_decreasing_register`) pins this
  boundary so it can't quietly widen later.
- **The stale-sample watermark (`meter_end_sampled_at`) had to be a new
  stored column, not derived.** `charging_session_meter_values` is never
  written by the `TransactionEvent` path (only by standalone `MeterValues`
  messages), so a derived `MAX(sampled_at)` from that table would be blind
  to readings that arrived embedded in a `TransactionEvent`.
- **Fix #4 skips non-energy measurands but raises on an unrecognized unit
  on the energy register.** Skipping a power/SoC/temperature sample is
  correct (this MVP doesn't model them). Silently skipping an
  *unrecognized unit on the energy register itself* would leave
  `meter_end_wh` stale while the station believes it succeeded — the exact
  silent-corruption shape this fix removes, so that case raises instead.
- **No new column on `charging_session_meter_values`** for
  measurand/unit. After the measurand filter, such a column would hold
  one constant value on every row. The raw pre-normalization payload for
  audit is `deferred.md` item 27's territory by decision, not oversight.
- **The existing test that asserted the bug was renamed, not edited in
  place.** `test_ocpp_meter_value_is_kept_without_unit_conversion` fed a
  1.25 kWh sample and asserted it stayed `1.25` (i.e. treated as Wh) —
  exactly the bug fix #4 removes. Renamed to
  `test_ocpp_kwh_sample_is_normalized_to_wh`, now asserting `1250`, so the
  behavior change can't be missed in review.

## 3. What was built

### 3.1 Step 0 — `charging_stations/ocpp/ocpp_server.py`

`OcppPayload = dict[str, Any]` alias; `evse`, `transaction_info`,
`meter_value` (and `sampled_value` entries) retyped as plain dicts, not
`ocpp.v201.datatypes` dataclasses. New `parse_ocpp_transaction_id`,
`parse_ocpp_evse_reference` dict-reading helpers. Dropped the now-unused
dataclass imports; the module docstring states the dict contract.

### 3.2 Fix #1 — `seq_no` (persist only, no dedup yet)

- `charging_sessions/models.py`: nullable `ChargingSessionEventModel.seq_no`.
- `charging_sessions/service.py`: new `_seq_no(value, field_name)`
  validator (rejects negative values **and** `bool` — Python's
  `isinstance(True, int)` quirk means a stray `True` could otherwise
  silently collide with a real `seqNo` of 0). `ingest_transaction_event`
  gained a required `seq_no: int | None` keyword parameter.
- `charging_sessions/repository.py::insert_event`: new required `seq_no`
  parameter.
- `ocpp_server.py`: `del trigger_reason, seq_no` → `del trigger_reason`
  only; `seq_no` threaded into the service call.
- `charging_sessions/schemas.py`: `ChargingSessionEventResponse.seq_no`.

### 3.3 Fix #2 — refuse mutation of a `COMPLETED` session

- `charging_sessions/exceptions.py`: new `ChargingSessionStateError`.
- `charging_sessions/service.py`: guard in `ingest_transaction_event`
  (after the not-found/topology checks, before any write) and
  `ingest_meter_values` (right after not-found) — raises before touching
  the database.

### 3.4 Fix #3 — stale `MeterValues` can't overwrite energy backwards

- `charging_sessions/models.py`: nullable
  `ChargingSessionModel.meter_end_sampled_at` (no backfill — an existing
  row's true sample time is genuinely unknown).
- `charging_sessions/service.py::_apply_charging_session_meter_end`: new
  `meter_end_sampled_at` parameter; discards the aggregate update (logs a
  structured WARNING) when the incoming sample is older than the stored
  watermark. Ties (`==`) apply — one OCPP message can carry several
  same-timestamp samples. The sample row itself is still always appended
  to history; only the aggregate update is conditional.
- `ingest_transaction_event` gained `meter_end_sampled_at: datetime |
  None = None`, preferring the embedded meter sample's own timestamp over
  `event_occurred_at` when the adapter supplies one (confirmed via the
  simulator that these are genuinely different fields).
- `ocpp_server.py`: passes `samples[-1].sampled_at if samples else None`.
- `charging_sessions/schemas.py`: `ChargingSessionResponse.meter_end_sampled_at`.

### 3.5 Fix #4 — `measurand`/`unitOfMeasure`-aware normalization

- `ocpp_server.py`: new `normalize_sampled_value_to_wh(sampled_value) ->
  Decimal | None` — only `Energy.Active.Import.Register` (or an absent
  measurand, OCPP 2.0.1's own default) is usable; anything else returns
  `None` (skip). Unit defaults to Wh/multiplier 0 when absent (the OCPP
  JSON Schema declares these defaults but validation doesn't inject
  them); `value × 10^multiplier` in the stated unit, matched
  case-insensitively against `{wh: 1, kwh: 1000}`. An energy-register
  sample in an unrecognized unit raises `ValueError`.
- `extract_meter_samples` calls the normalizer per sample and skips
  `None`s.

### 3.6 Migration `0017_charging_ingest_fields`

`down_revision = "0016_vehicle_battery_capacity"`. Two nullable
`add_column`s (`charging_session_events.seq_no`,
`charging_sessions.meter_end_sampled_at`), no server default, no
index — matching `0012`'s precedent for a nullable column on a
TimescaleDB hypertable (catalog-only change, no rewrite).

## 4. Verification

- `black`, `isort`, `ruff check`, `mypy .` — all clean.
- Migration `0017` verified via `upgrade head` → `downgrade -1` →
  `upgrade head` against the live local Postgres; confirmed both columns
  exist, including on the hypertable's existing chunk.
- Unit tests (`test_service_smoke.py`): `seq_no` persisted / rejected
  (negative, `bool`); the COMPLETED-session guard on both
  `ingest_transaction_event` and `ingest_meter_values` (asserting the
  write function was never called); a stale sample discarded (row still
  inserted, aggregate untouched) vs. a newer sample applied vs. an
  equal-timestamp tie applied; **a decreasing register at a newer
  timestamp still applies** (the test whose entire job is to pin the
  item-27 boundary); the transaction-event watermark preferring the
  embedded sample's timestamp over the event's, with fallback. Unit tests
  (`test_schema_smoke.py`, dict-shaped fixtures matching what
  `python-ocpp` actually delivers): default unit/multiplier, multiplier
  scaling, default measurand, a non-energy measurand skipped, an unknown
  unit rejected, mixed measurands in one message keeping only the energy
  register, plus the renamed kWh-normalization test and a Step-0
  regression test.
- **PostgreSQL integration suite run explicitly** (`RUN_DB_INTEGRATION=1`,
  not just the default-skip smoke pass, because of the hypertable column
  addition): the migration baseline test passed, confirming the upgrade/
  downgrade/upgrade cycle and the new columns on a real database. Also
  fixed a stale, unrelated assertion in the same test file (hardcoded to
  `0004_create_charging_mvp_schema` as the expected post-upgrade head,
  masked for a long time because this suite is skipped by default).
- **Live end-to-end** against the running OCPP gateway + charging
  simulator: the full `Started → MeterValues → Updated → Ended` happy
  path completed with no crash (confirming Step 0 fixed the simulator),
  and the resulting session showed the correct `seq_no` per event and
  `meter_end_sampled_at` populated. Two hand-crafted negative cases were
  then run against a live connection: (a) an older-timestamped
  `MeterValues` arriving after a newer one — confirmed discarded via the
  WARNING log, with the final `meter_end_wh` unaffected by the stale
  value and the sample still present in the append-only history table;
  (b) a duplicate `Ended` sent after the session was already
  `COMPLETED` — confirmed rejected with a `CALLERROR` (`InternalError`),
  with `ended_at` and the energy total unchanged from the first `Ended`.
  All test data cleaned up afterward.

## 5. Known pre-existing issue found, not fixed (out of scope)

While running the PostgreSQL integration suite explicitly,
`test_telemetry_repository_round_trip_rolls_back` failed with a
`CompileError: Unconsumed column names: latitude, longitude`. This is a
pre-existing bug unrelated to F-B2 — the test still builds a telemetry
insert dict using the old `latitude`/`longitude` columns from before the
location-unification migration (`0006_telemetry_location_geo`,
`deferred.md` item 9), untouched since the integration test was first
added. It has been masked because this suite is skipped unless
`RUN_DB_INTEGRATION=1`. Left unfixed here since it's a telemetry-domain
test bug, not a charging-ingestion issue; flagged for a future pass.

## 6. Deferred (unchanged — see `docs/decisions/deferred.md` item 27)

Retry, reconnect/connection registry, offline/heartbeat timeout,
duplicate/idempotency resolution using `seq_no`, out-of-order recovery,
technical status history, and raw OCPP payload auditing all remain
deferred. This round only stopped the happy path itself from writing
values nothing could vouch for; it does not make the pipeline tolerant of
genuinely lost, delayed, or duplicated messages.
