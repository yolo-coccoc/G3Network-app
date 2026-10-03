# Planner: Backend Anomaly Detection (F-A4 Anomaly detection)

> Feature code: F-A4 (Anomaly detection)
> Status: ✅ Done (MVP/POC scope)
> Created: 2026-09-17

## 1. Goal

Give the backend a way to detect vehicle-level anomalies from telemetry
readings and raise an alert for them, reusing the `notifications` domain
built for F-A2 — its module docstring already anticipated this: "each only
needs to add a `NotificationType` member and shape its own payload — not a
new table, migration, or endpoint." F-A4 is therefore a detection-only
change inside `telemetry`, plus one enum member and one enum-altering
migration.

## 2. Scope decisions

- **Event log = the notification's JSONB `payload`.** No `vehicle_anomalies`
  table. F-A4's output is "real-time alert + event log with a data
  snapshot" — the payload carries both the detector's `evidence` and a full
  telemetry `snapshot`, satisfying the event-log requirement through the
  existing `GET /api/v1/notifications` poll API. See
  `telemetry/service.py::to_telemetry_snapshot`.
- **Fault codes → one generic `DEVICE_FAULT`.** F-A4 names "cell/module
  fault" and "motor fault" as separate triggers, but `mqtt-spec.md` defines
  `errors` only as opaque strings (e.g. `["E001", "E005"]`) with no vendor
  catalog mapping a code to either category. Any code newly appearing
  versus the previous reading raises one `DEVICE_FAULT` anomaly listing the
  new codes (`telemetry/service.py::detect_new_error_codes`). The missing
  catalog is `deferred.md` item 42.
- **High battery temperature uses a crossing rule, same shape as F-A2.**
  It's a level condition (a truck sitting at 67°C sends a message every
  5-10s), so it must not alert on every message while above the threshold.
  It fires once on *entry*, stays silent while it persists, and re-arms on
  recovery — see `detect_high_battery_temperature`.
- **Divergence from F-A2 on the first reading.**
  `detect_battery_alert_level` returns `None` when there's no previous SOC
  reading, so a vehicle's very first message never alerts. Applying that
  verbatim to F-A4 would silently ignore a *first* reading of 80°C — exactly
  the fire-safety case F-A4 was upgraded to Must for in v1.0. So level
  anomalies (`HIGH_BATTERY_TEMPERATURE`, `DEVICE_FAULT`) treat a missing
  previous reading as "below threshold / no codes" and **do** alert on a
  first message; the crossing rule exists to suppress *repeats*, not the
  first observation. `SUDDEN_VOLTAGE_DROP` is a delta between two readings
  and is undefined without a previous one, so it stays silent on the first
  message, matching F-A2.
- **Voltage drop is an absolute delta, not relative or rate-based.**
  `previous_voltage - current_voltage >= VOLTAGE_DROP_THRESHOLD_VOLTS`
  (50.0V) — no percentage scaling, no time normalization. Simplest rule that
  satisfies "sudden voltage drop" without adding time math or a
  divide-by-zero/clock-skew edge case to the ingest path.
- **Both thresholds (60°C, 50V) are engineering defaults, not
  vendor-confirmed.** Recorded as `deferred.md` item 43 rather than presented
  as validated values.
- **No motor-temperature detector.** F-A4's four named triggers are battery
  temperature, voltage drop, cell/module fault, motor fault — motor
  *temperature* isn't one of them, and using it as a proxy for "motor
  fault" would be inventing a trigger the spec doesn't ask for. Recorded as
  `deferred.md` item 45.
- **No re-alert/escalation for a persisting anomaly.** Same "not needed
  until real device data shows a need" reasoning as F-A2's item 39
  (hysteresis/re-arm margin). Recorded as `deferred.md` item 44.

## 3. What was built

### 3.1 `notifications` domain

- `types.py`: added `NotificationType.ANOMALY_ALERT`.
- Migration `0009_anomaly_notification_type`: adds the enum member via
  `ALTER TYPE ... ADD VALUE` (transaction-safe here since the value is never
  read/written in the same migration). Downgrade recreates the
  `notificationtype` enum without it (PostgreSQL has no `DROP VALUE`),
  deleting any `ANOMALY_ALERT` rows first — documented as destructive and
  only intended for rolling back a not-yet-released migration.

### 3.2 `telemetry` domain

- `types.py`: `VehicleAnomalyType` (`HIGH_BATTERY_TEMPERATURE`,
  `SUDDEN_VOLTAGE_DROP`, `DEVICE_FAULT`), the two threshold constants,
  `VEHICLE_ANOMALY_SEVERITIES` (fixed severity per type), and the
  `VehicleAnomaly` DTO (`anomaly_type`, `severity`, `evidence`).
- `service.py`:
  - Three pure detectors — `detect_high_battery_temperature`,
    `detect_sudden_voltage_drop`, `detect_new_error_codes` — each returning
    `VehicleAnomaly | None`. No I/O.
  - `detect_vehicle_anomalies(previous_telemetry, message)` — pure
    orchestrator; runs all three and returns every anomaly that fires (a
    single reading can trip more than one).
  - `to_telemetry_snapshot(message)` — pure mapper building the JSONB-safe
    data snapshot (UUID/datetime converted to strings).
  - `_raise_vehicle_anomaly_alert` — builds the notification payload
    (`anomaly_type`, `evidence`, `snapshot`) and calls
    `notifications_service.create_notification` with
    `NotificationType.ANOMALY_ALERT`.
  - `process_message` — the previous telemetry row it already loaded for
    F-A2 (previously read only via `.soc`) is now also passed to
    `detect_vehicle_anomalies`; each anomaly detected raises one
    notification. This never affects `processed`/`skipped`/`errors` — each
    is reported via its own structured log line
    (`"vehicle anomaly alert raised"`).
  - Only wired into `process_message` (the live per-message flow), same as
    F-A2. `process_batch`/`batch_worker.py` remain dormant and untouched.

## 4. Verification

- `black`, `isort`, `ruff check`, `mypy .` — all clean across the whole
  backend.
- `alembic upgrade head` / `downgrade -1` / `upgrade head` — clean; confirmed
  `SELECT enum_range(NULL::notificationtype)` returns
  `{BATTERY_ALERT,ANOMALY_ALERT}` after upgrade.
- Unit tests (`test_service_smoke.py`): table-driven coverage per detector
  (below/at/above threshold, missing previous reading, persists-above stays
  silent, recovery-then-re-entry re-alerts; voltage drop's undefined-without-both-readings
  case; error codes unchanged/cleared/newly-added); `detect_vehicle_anomalies`
  returning multiple anomalies from one reading and reading the stored
  `{"codes": [...]}` JSONB shape correctly; `to_telemetry_snapshot`
  round-tripping through `json.dumps`; a `process_message` test asserting
  the right notification type/severity/payload on a multi-anomaly reading
  and that ingestion counters are unaffected. `test_migrations_smoke.py`
  updated to the new head.
- **Live end-to-end**: ran `telemetry-dev` and the API server against the
  existing seeded device (`TBOX-SIM-00001`), then published a crafted
  battery-temperature/voltage/error-code sequence directly over MQTT (the
  simulator's own fixed `voltage: 650.0`/`errors: []` and 25-40°C random
  walk can never trip any detector):

  | step | temp °C | volts | errors | expected |
  |---|---|---|---|---|
  | 0 | 35 | 650 | [] | none (baseline) |
  | 1 | 67 | 648 | [] | HIGH_BATTERY_TEMPERATURE (entry) |
  | 2 | 70 | 650 | [] | none (still above, no repeat) |
  | 3 | 45 | 650 | [] | none (recovered) |
  | 4 | 62 | 591 | [E042] | HIGH_BATTERY_TEMPERATURE (re-entry) + SUDDEN_VOLTAGE_DROP (-59V) + DEVICE_FAULT (E042) |
  | 5 | 62 | 591 | [E042] | none (unchanged state) |
  | 6 | 62 | 591 | [E042, E007] | DEVICE_FAULT (E007 only) |

  Confirmed via the worker log and `GET /api/v1/notifications?after_id=`:
  exactly 5 `ANOMALY_ALERT` notifications in the predicted order, each with
  `notification_type: "ANOMALY_ALERT"`, correct severity
  (`HIGH_BATTERY_TEMPERATURE`/`DEVICE_FAULT` → `CRITICAL`/`WARNING` per
  `VEHICLE_ANOMALY_SEVERITIES`, `SUDDEN_VOLTAGE_DROP` → `WARNING`), and a
  `payload` carrying both `evidence` and a complete `snapshot`; steps 0, 2,
  3, and 5 raised nothing.

## 5. Deferred (see `docs/decisions/deferred.md`)

- Item 42 — device error-code catalog, to split `DEVICE_FAULT` into distinct
  cell/module vs. motor fault types.
- Item 43 — vendor-validated, configurable anomaly thresholds.
- Item 44 — re-alert/escalation for an anomaly that persists unacknowledged.
- Item 45 — a high-motor-temperature detector.
