# Planner: Backend Notifications (F-A2 Tiered battery alerts)

> Feature code: F-A2 (Tiered battery alerts)
> Status: ✅ Done (MVP/POC scope)
> Created: 2026-09-17

## 1. Goal

Give the backend a way to raise an operator-facing notification and let a
consumer (the admin web portal) read it, starting with F-A2's battery
threshold alerts. The `notifications` domain is deliberately generic — a
single table with a JSONB payload — so later alert-producing features
(F-A4 anomaly detection, F-B5 warranty-risk alert, F-J3 device offline
alert) can reuse it without a new table, migration, or endpoint; each only
needs to add a `NotificationType` member and shape its own payload.

## 2. Scope decisions

- **Delivery is backend-storage-plus-polling, not push.** There is no
  mobile app in this repo's scope, so F-A2's "push to a backgrounded app"
  constraint doesn't apply. The admin web portal polls
  `GET /api/v1/notifications?after_id=` on an interval; this also settles
  the transport question left open in
  [`backend-telemetry-query-api.md`](./backend-telemetry-query-api.md)'s
  "APIs to be added later" (realtime push via WebSocket/SSE) — polling, for
  now, revisit if/when the portal needs push.
- **"1 alert per threshold per trip" → "1 alert per threshold crossing."**
  No trip concept exists anywhere in the backend (F-A9, which would
  introduce one, is itself still Planned). A crossing is detected as
  `previous_soc > threshold >= current_soc` — strict on the previous side,
  inclusive on the current side, so a reading resting exactly on the
  threshold alerts once, not on every subsequent message. See
  `telemetry/service.py::detect_battery_alert_level` for the exact rule
  and its boundary-value rationale.
- **"Nearest available station" → "nearest operational station."** No live
  occupancy or online/offline signal exists for a station/EVSE/connector
  (see `charging_stations/types.py`'s `ChargingStationMaintenanceStatus`
  docstring and `deferred.md` items 27/28). "Available" is approximated as
  `deleted_at IS NULL AND maintenance_status = OPERATIONAL AND location IS
  NOT NULL` — the honest best available given current data.
- **The station snapshot is frozen at alert time, not recomputed on read.**
  `distance_km` describes where the vehicle was when it crossed the
  threshold; `station_id` is kept in the payload so a live recalculation
  is possible later if the portal ever wants one.
- **NF-01 (latency) and NF-03 (uptime/SLA) are not measured.** This
  backend's current scope is MVP/POC; non-functional targets are
  explicitly deferred (see `docs/decisions/deferred.md`).

## 3. What was built

### 3.1 `notifications` domain (new)

- `models.py`: `NotificationModel` — ordinary relational table (not a
  hypertable), `notification_id` (BIGINT, monotonic — doubles as the poll
  cursor), `notification_type`, `severity`, nullable `vehicle_id` (FK,
  `ondelete=CASCADE`), `title`, `body`, `payload` (JSONB), `created_at`,
  nullable `read_at` (unread = `NULL`).
- `types.py`: `NotificationType` (`BATTERY_ALERT` so far), `NotificationSeverity`
  (`INFO`/`WARNING`/`CRITICAL`), `NotificationReference` DTO (cross-domain
  return type — never the ORM model).
- `repository.py`: `create_notification`, `get_notification_by_id`,
  `list_notifications(db, *, after_id, limit, unread_only)` (cursor-based,
  ascending by ID), `mark_notification_read` (idempotent — keeps the
  original `read_at` on a repeat call).
- `service.py`: `create_notification` is the public cross-domain entry
  point producers call (`telemetry` today); `list_notifications` and
  `mark_notification_read` back the HTTP endpoints.
- `router.py`: `GET /api/v1/notifications` (query params `after_id`,
  `limit`, `unread_only`), `PATCH /api/v1/notifications/{id}/read`.
- Migration `0008_notifications`: creates the table with its two inline
  enums (`notificationtype`, `notificationseverity`) and an index on
  `vehicle_id`.

### 3.2 Nearest-operational-station lookup (`charging_stations`)

The first PostGIS spatial query in the codebase — the GIST index
`ix_charging_stations_location` existed since F-C1 but nothing had queried
it until now.

- `types.py`: `NearestChargingStation` DTO.
- `repository.py`: `find_nearest_station_by_location` — orders by the
  `<->` KNN operator (`GeoAlchemy2`'s `distance_centroid`) so the GIST
  index is used, and separately selects `ST_Distance(...)` (meters) for
  the actual distance value; filtered to
  `deleted_at IS NULL AND location IS NOT NULL AND maintenance_status =
  OPERATIONAL`.
- `service.py`: `find_nearest_operational_station(db, *, latitude,
  longitude)` — the public cross-domain entry point, converts meters to km.

### 3.3 Detection hook (`telemetry`)

- `types.py` (new file — `telemetry` was the only domain without one):
  `BatteryAlertLevel` enum (`EARLY`/`MAIN`/`CRITICAL`) and
  `BATTERY_ALERT_THRESHOLDS` mapping each level to its SOC threshold and
  `NotificationSeverity`.
- `service.py::detect_battery_alert_level` — pure function, no I/O; see
  §2 above for the crossing rule. Iterates least-to-most-severe and keeps
  the last match, so a single message that skips multiple thresholds
  (e.g. 35% → 8%) still raises exactly one, most-severe, alert.
- `service.py::process_message` — reads the vehicle's previous telemetry
  (`get_latest_vehicle_telemetry`) **before** inserting the new row (order
  matters: after insert, that query would return the just-inserted row
  instead of the actual previous reading), then after a successful insert,
  calls `detect_battery_alert_level` and, on a crossing, `_raise_battery_alert`
  (resolves the nearest station, then calls `notifications_service.create_notification`).
  This never affects the `processed`/`skipped`/`errors` counters — it's
  reported via a separate structured log line.
- Only wired into `process_message` (the live per-message flow).
  `process_batch`/`batch_worker.py` are dormant and not part of the
  current process lifecycle, so they were left untouched.

## 4. Verification

- `black`, `isort`, `ruff check`, `mypy .` — all clean.
- `alembic upgrade head` / `downgrade -1` / `upgrade head` — clean.
- Unit tests (`test_service_smoke.py`): table-driven coverage of
  `detect_battery_alert_level` (first-message no-alert, above-all-thresholds,
  each threshold's boundary touch, resting-below no-repeat, multi-threshold
  drop resolves to most severe, rising never alerts); a `process_message`
  test asserting exactly one notification is created with the right
  type/severity/payload on a crossing; a `NotificationResponse` mapper test.
- **Live end-to-end**: seeded a station with a known location ~55m from the
  simulator's test vehicle, ran `telemetry-dev`, published a crafted SOC
  descent (50 → 28 → 26 → 18 → 17 → 9 → 9) directly over MQTT to hit each
  threshold deterministically (the simulator's own random-walk SOC never
  reaches below 20%, so it can't exercise the MAIN/CRITICAL tiers).
  Confirmed via the worker log and `GET /api/v1/notifications`: exactly 3
  notifications (EARLY at 28%, MAIN at 18%, CRITICAL at 9%), each with the
  correct nearest-station payload and distance; the resting/repeat
  messages (26%, 17%, second 9%) raised nothing. Also verified
  `after_id` cursoring, `unread_only` filtering, `PATCH .../read`
  (idempotent), and a 404 on an unknown notification ID.

## 5. Deferred (see `docs/decisions/deferred.md`)

- Live station occupancy/online signal, for a true "nearest *available*"
  rather than "nearest operational."
- True per-trip de-duplication, once F-A9 lands a trip concept.
- A hysteresis/re-arm margin, to guard against sensor noise oscillating
  across a threshold (not needed yet — telemetry's SOC is a single stable
  reading per message, not a noisy raw sensor stream).
- Recipient scoping — notifications are vehicle-scoped, not user/role-scoped,
  since no `identity` domain exists yet.
- Push/multi-channel delivery (F-F3) — today's polling-only API is the
  storage layer that a future push mechanism would sit on top of.
- NF-01 latency measurement and NF-03 uptime/SLA tracking.
