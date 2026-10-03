# Planner: Vehicle Activation, Battery Health, Device-Silence Monitor (F-F2, F-A3, F-J1, F-J3)

> Feature codes: F-F2 (device activation), F-A3 (battery health/SOH), F-J1
> (device monitoring, partial), F-J3 (tamper/loss detection, partial).
> Also closes out F-G2's status (docs-only, no code — see §3.0).
> Status: ✅ Done (MVP/POC scope); F-A6 suspended, not part of this round.
> Created: 2026-09-17

## 1. Goal

Four small, mostly-independent additions layered onto already-working
domains (`vehicles`, `telemetry`, `telematics`, `notifications`), continuing
the feature-by-feature rollout against `feature-list.md`. F-J1/F-J3 is the
notable one: this repo's **first periodic, non-event-driven background
worker** (everything before it — MQTT ingestion, OCPP — reacts to an
inbound message; this one wakes up on a timer with nothing to react to).

**F-A6 (cost-per-charge estimate) was suspended before any code was
written.** Design surfaced that `ChargingSessionModel` has no vehicle
linkage anywhere, which would block its kWh/cost outputs; the user
confirmed the fix (add `vehicle_id` to `charging_sessions`) but then asked
to suspend the whole feature for this round. It stays 📋 Planned in
`feature-list.md` — nothing described below touches `charging_sessions`.

## 2. Scope decisions

- **F-G2 needed no code.** Its last documented gap ("connector status not
  handled — see F-C2") was already closed when F-C2 shipped
  (`backend-station-search-status-energy.md`). The only remaining gaps are
  OCPP 1.6J support (blocked on an unresolved business decision — see
  `open-questions.md`) and the NF-05 production security profile (already
  a deliberate, separate dev-mode allowance per `tech-decisions.md`, not a
  gap to close here). This round only corrected the status line.
- **Activation is a 3-state one-way ladder, not a richer state machine.**
  `PENDING → DEVICE_ASSIGNED → ACTIVATED`, both transitions monotonic
  (no-op if already at or past the target state). "Device assigned" is
  triggered by a telematic device's `vehicle_id` being set (create or
  update); "activated" is triggered by that vehicle's first-ever telemetry
  message — the same `previous_telemetry is None` signal F-A2/F-A4 already
  compute, reused rather than duplicated. No deactivation/reset path was
  requested or built.
- **`activation_status` defaults to `PENDING` with no backfill.** Existing
  vehicles never went through this new provisioning flow, so `PENDING` is
  the honest starting state, not a guess at their real status.
- **SOH alert reuses F-A2's crossing-rule shape exactly**, not F-A4's
  fire-safety immediate-repeat exception — gradual capacity fade isn't a
  safety condition, so "alert once per crossing" is the right cadence.
  `SOH_ALERT_THRESHOLD_PERCENT = 70.0` is an engineering placeholder (not
  vendor-confirmed), same caveat as F-A4's anomaly thresholds.
- **`soh_percent`/`cycle_count` ride the existing telemetry message/table**
  rather than a separate battery-health table — same shape as every other
  telemetry field, and it makes F-A5's history endpoint serve "capacity
  fade over time" for free, with no new aggregation logic.
- **F-J1/F-J3 is explicitly partial.** Built: last-seen tracking via
  existing `received_at` timestamps, a periodic sweep, and a one-shot
  `DEVICE_OFFLINE_ALERT` per silence episode (deduplicated so a device
  stuck offline across many ticks doesn't spam). **Not built** (see
  `deferred.md` items 50/51): a per-device dashboard (SIM/power status —
  no such field exists in the MQTT contract), and distinguishing sudden
  power loss from ordinary signal loss (no signal exists anywhere in this
  backend to tell the two apart — a per-device MQTT Last Will would need
  each telematic device to be its own MQTT client, which isn't the current
  ingestion topology).
- **Dedup uses `last_notified_at > last_seen_at`, not in-process state.**
  Comparing the last alert's timestamp against the triggering condition's
  own timestamp (not a variable held in the worker process) means the rule
  survives a process restart with no special-cased recovery logic.
- **New domain-boundary edge: `telematics → telemetry`.** The monitor
  needs a vehicle's last-seen time, which only `telemetry` owns. Combined
  with the pre-existing `telemetry → telematics` edge (serial resolution
  during ingestion), `telematics ↔ telemetry` is now bidirectional — both
  directions go through the other domain's public `service.py`. Recorded
  explicitly in `domain-boundaries.md` since every other domain pair in
  this repo is one-directional; the not-yet-installed `import-linter` will
  need an allowed-cycle exception for this pair specifically.

## 3. What was built

### 3.0 F-G2 (docs only)

No source change. `feature-list.md`'s status line corrected to reflect
that F-C2 already closed the connector-status gap.

### 3.1 F-F2 — device activation state machine (`vehicles`)

- `types.py`: `VehicleActivationStatus` (`PENDING`/`DEVICE_ASSIGNED`/`ACTIVATED`).
- `models.py`: `activation_status` column, `nullable=False`, default `PENDING`.
- `repository.py`: `count_by_activation_status(db, activation_status)`,
  mirroring the existing `count()`'s shape/conditions.
- `service.py`: `mark_device_assigned`/`mark_vehicle_activated` (both
  no-op past their target state, both public cross-domain entry points),
  `get_vehicle_activation_summary(db)` — two `count_by_activation_status`
  calls (`DEVICE_ASSIGNED`, `ACTIVATED`), no grouped/batched query (per
  the repo's no-premature-batching convention); `activation_rate_percent`
  is `None` when `attempted_count == 0` rather than a division by zero.
- `schemas.py`: `activation_status` added to `VehicleResponse` (required);
  new `VehicleActivationSummaryResponse`.
- `router.py`: `GET /vehicles/activation-summary`, registered **before**
  `GET /vehicles/{vehicle_id}` (route-ordering rule — same class of bug
  already learned from F-D1's `/nearby`; a path-parameter route would
  otherwise swallow this as an invalid UUID).
- Wiring: `telematics/service.py`'s `create_telematic`/`update_telematic`
  call `mark_device_assigned` after a non-`None` `vehicle_id` resolves;
  `telemetry/service.py`'s `process_message` calls `mark_vehicle_activated`
  when `previous_telemetry is None`.
- Migration `0011_vehicle_activation_status`.

### 3.2 F-A3 — battery health (SOH) & cycle tracking (`telemetry`)

- `schemas.py`: `TelemetryBatteryPayload` gets `soh_percent` (0-100,
  nullable) and `cycle_count` (`ge=0`, nullable); both flow through
  `to_vehicle_telemetry_values` and into `VehicleTelemetryLatestResponse`/
  `VehicleTelemetryHistoryPoint`.
- `models.py`: `soh_percent` (Double, nullable), `cycle_count` (Integer,
  nullable) on `VehicleTelemetryModel`.
- `types.py`: `SOH_ALERT_THRESHOLD_PERCENT = 70.0`.
- `service.py::detect_soh_alert(previous_soh, current_soh)` — pure
  function, `False` if either side is `None`; crossing rule
  `previous > threshold >= current` (see §2). Wired into `process_message`
  alongside the existing F-A2/F-A4 blocks; raises a `SOH_ALERT`
  notification (`NotificationSeverity.WARNING`) with payload
  `{threshold_percent, soh_percent, cycle_count}`.
- Mapper updates: `to_vehicle_telemetry_latest_response`,
  `to_vehicle_telemetry_history_point`.
- Migrations: `0012_telemetry_battery_health` (the two columns),
  `0013_soh_alert_notification_type` (`ALTER TYPE notificationtype ADD
  VALUE 'SOH_ALERT'`, same pattern as F-A4's `0009`).

### 3.3 F-J1/F-J3 (partial) — periodic device-silence monitor (`telematics`)

This repo's first non-event-driven background worker. Structured as a
thin transaction-opening wrapper delegating to a testable core — the same
split already used by `process_message(db, ...)` and the OCPP handlers —
specifically so the sweep logic could be unit-tested with a fake session
and monkeypatched dependencies, without needing a real event loop timer.

- New `telematics/monitoring/` subpackage:
  - `device_health_monitor.py`:
    - `run_monitor(stop_event)` — loops
      `asyncio.wait_for(stop_event.wait(), timeout=TELEMATICS_HEALTH_CHECK_INTERVAL_SECONDS)`,
      `except TimeoutError: pass` as the cancellable-sleep idiom (modeled
      on the dormant `batch_worker.py`'s closest analog); on timeout, runs
      one tick.
    - `run_health_check_tick()` — opens `async_session_factory.begin()`,
      delegates to the core function (the testable wrapper/core split).
    - `check_devices_for_silence(db)` — `telematics_repository
      .list_active_with_vehicle(db)` (new; active, non-deleted, `vehicle_id
      IS NOT NULL`), one device at a time (no bulk query, per convention).
      For each: `telemetry_service.resolve_last_telemetry_at(db,
      vehicle_id)` (new public entry point, built on `received_at` rather
      than `recorded_at` — deliberately, to resist device clock skew);
      skip if `None` (never reported yet). If silent past
      `TELEMATICS_SILENT_THRESHOLD_MINUTES`: check
      `notifications_service.resolve_last_notified_at(db, vehicle_id,
      DEVICE_OFFLINE_ALERT)` (new); alert only if no prior alert or the
      prior one predates `last_seen_at` (the dedup rule from §2).
    - `_raise_device_offline_alert` — payload `{telematic_id,
      telematic_serial, last_seen_at, silent_minutes, threshold_minutes}`,
      severity `WARNING`.
  - `entrypoint.py` — same shape as `charging_stations/ocpp/entrypoint.py`
    (SIGINT/SIGTERM → `stop_event`, `finally: close_db()`, `SystemExit(1)`
    on failure).
- `telematics/repository.py`: `list_active_with_vehicle`.
- `telemetry/service.py`: `resolve_last_telemetry_at(db, vehicle_id)`.
- `notifications/types.py`: `DEVICE_OFFLINE_ALERT` member.
- `notifications/repository.py`: `find_latest_by_vehicle_and_type`.
- `notifications/service.py`: `resolve_last_notified_at`.
- `config.py`: `TELEMATICS_HEALTH_CHECK_INTERVAL_SECONDS` (default 300.0),
  `TELEMATICS_SILENT_THRESHOLD_MINUTES` (default 180) — the interval must
  stay materially smaller than the threshold or a device could sit past
  threshold for a whole interval before anyone notices.
- Migration `0014_device_offline_alert` (`ALTER TYPE notificationtype ADD
  VALUE 'DEVICE_OFFLINE_ALERT'`). Renamed mid-implementation from
  `0014_device_offline_notification_type` — the original revision string
  was 37 characters, over the 32-character `alembic_version.version_num`
  limit; caught by a real `StringDataRightTruncationError` on `alembic
  upgrade head`, fixed by shortening the revision id.
- New Makefile target `telematics-monitor-dev`.

## 4. Verification

- `black`, `isort`, `ruff check`, `mypy .` — all clean.
- All four migrations verified via full `upgrade head` → `downgrade -1`
  (×4) → `upgrade head` round trips against the local Postgres container.
- `pytest tests/` — 94 passed, 2 skipped (the two PostgreSQL integration
  tests, skipped by default per repo convention). New coverage added:
  `detect_soh_alert` (table-driven, including the threshold-boundary
  case), the F-F2 `mark_device_assigned`/`mark_vehicle_activated` no-op
  and transition paths, `get_vehicle_activation_summary`'s zero-attempt
  `None` rate, `check_devices_for_silence`'s skip/alert/dedup paths, and
  `resolve_last_notified_at`/`resolve_last_telemetry_at`. Two pre-existing
  tests needed updates for new `process_message` side effects (missing
  `soh_percent` on `SimpleNamespace` stand-ins; new cross-domain calls
  needing monkeypatches against fake sessions).
- **Live end-to-end**: created a vehicle via the running API, assigned a
  telematic device, confirmed `activation_status` moved
  `PENDING → DEVICE_ASSIGNED`; published one crafted MQTT telemetry
  message, confirmed `ACTIVATED` and `GET /vehicles/activation-summary`
  reflecting it. Published telemetry with `soh_percent` crossing 70%,
  confirmed exactly one `SOH_ALERT`. Ran `telematics-monitor-dev` with
  `TELEMATICS_SILENT_THRESHOLD_MINUTES=1`/`TELEMATICS_HEALTH_CHECK_INTERVAL_SECONDS=5`
  against a device that stopped publishing; confirmed exactly one
  `DEVICE_OFFLINE_ALERT` fired, and that it did **not** re-fire on
  subsequent ticks while still silent (dedup rule holds across real
  timer ticks, not just in a unit test). All test data cleaned up via
  direct SQL afterward.

## 5. Deferred (see `docs/decisions/deferred.md`)

- F-J1's per-device dashboard (SIM/data status, power status) — item 50.
- F-J3's power-loss-vs-signal-loss discrimination — item 51.
- F-A6 (cost-per-charge estimate) — suspended before implementation;
  `ChargingSessionModel`'s missing `vehicle_id` linkage is the known
  blocker for whenever it resumes, still 📋 Planned.
- A queryable online/offline flag/field (item 35's original ask) — the
  monitor computes silence transiently per tick; it doesn't persist a
  flag anywhere, only a one-shot alert.
