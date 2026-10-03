# Planner: Telematics Config Push over MQTT (F-J2, partial)

> Feature code: F-J2 (Remote device configuration - OTA)
> Status: ✅ Done (MVP/POC scope, partial)
> Created: 2026-09-17

## 1. Goal

Give ops a way to push one configuration change — the telemetry publish
interval — to a specific telematic device over MQTT. This is this
backend's first-ever MQTT *publish* path; every prior MQTT usage in the
repo only subscribes (`telemetry/ingestion/mqtt_consumer.py`). The
broader F-J2 ask (push per vehicle/fleet, confirmation of applied config,
rollback, local alert thresholds) is scoped down to exactly this one
capability for this round — see §5 for what's deliberately not built.

## 2. Scope decisions

- **Backend publish only, no simulator change.** There is no real device
  and no device subscriber anywhere in this repo (the simulator only
  publishes telemetry, never subscribes). Per the user's explicit choice,
  verification is `mosquitto_sub` against the command topic, not an
  end-to-end "device applies the config" proof — that would need a
  receiver this round deliberately doesn't build.
- **A short-lived MQTT client per publish, not a long-lived one or an
  outbox.** The publish lives entirely inside one function
  (`publish_device_command`): connect, publish, disconnect. This is the
  strongest form of "a single component owns the connect/run/stop
  lifecycle of an external client" — the lifecycle isn't observable from
  anywhere else, and the API process doesn't grow a second long-lived
  external client alongside its DB engine. An outbox/dispatcher worker
  (reliable delivery, retry) is deferred (`deferred.md` item 55) — it only
  pays off once something actually consumes/acks the command, which
  doesn't exist yet.
- **Fail-closed, publish-then-record.** The command is published before
  anything is written to the database; the database is only updated when
  the publish succeeds. If the broker is unreachable, nothing is
  persisted and the row still reads "never pushed." There is no MQTT ack
  topic (see below), so this is the only honest meaning available for
  `telemetry_interval_seconds`: "the last interval we successfully handed
  to the broker," never "the interval we wish we'd sent." The one
  accepted residual: a process crash between a successful publish and the
  request's commit leaves the DB under-claiming what was actually sent —
  the safer direction, since the next push reconverges it (`deferred.md`
  item 55 also covers closing this window properly).
- **A dedicated MQTT client id, distinct from the telemetry consumer's.**
  Reusing `MQTT_CLIENT_ID` would let a config push evict the ingestion
  consumer's broker session. `MQTT_COMMAND_CLIENT_ID` is suffixed with a
  random token per call so concurrent pushes don't evict each other either.
- **A new route, not a field on the existing PATCH.** Adding
  `telemetry_interval_seconds` to `TelematicUpdateRequest` would make a
  plain CRUD edit (renaming firmware) silently depend on broker
  reachability. `POST /{telematic_id}/config` keeps the MQTT side effect
  on its own endpoint.
- **`INACTIVE` blocks the push; `MAINTENANCE` doesn't.** A device
  deliberately taken out of service shouldn't silently accept a new
  operating config (409); a device under maintenance is plausibly exactly
  when retuning it makes sense.

## 3. What was built

### 3.1 Settings (`backend/app/libs/common/config.py`)

`MQTT_COMMAND_TOPIC_TEMPLATE` (`g3network/telematics/{telematic_serial}/command`),
`MQTT_COMMAND_CLIENT_ID`, `MQTT_COMMAND_QOS` (1 — a dropped one-shot
command is a silent loss, unlike telemetry's QoS 0), `MQTT_COMMAND_RETAIN`
(`False`), `MQTT_COMMAND_TIMEOUT_SECONDS`, and the accepted interval
bounds `TELEMATICS_MIN_TELEMETRY_INTERVAL_SECONDS`/
`TELEMATICS_MAX_TELEMETRY_INTERVAL_SECONDS`.

### 3.2 `telematics` domain (`backend/app/domains/telematics/`)

- `models.py`: `telemetry_interval_seconds`/`config_pushed_at`, both
  nullable, no default (NULL = "never pushed") — migration
  `0015_telematic_config_push`.
- New subpackage `commands/`: `mqtt_publisher.py` —
  `build_command_topic`, `build_set_telemetry_interval_payload` (pure,
  clock injected via `issued_at` so it's assertion-testable), and
  `async publish_device_command` (the short-lived-client publish; lets
  `MqttError`/`TimeoutError` escape uncaught, the same way the repository
  lets `IntegrityError` escape for the service to convert).
- `exceptions.py`: `TelematicNotConfigurableError` (409, `INACTIVE`),
  `TelematicCommandPublishError` (502, broker unreachable/timeout).
- `service.py::push_telematic_config`: fetch → INACTIVE check → build
  payload → publish → (only on success) `update_fields`. Structured
  logging on both the success and failure paths via `extra=`.
- `schemas.py`: `TelematicConfigPushRequest` (bounded interval),
  `TelematicConfigResponse` (echoes `command_topic` so an operator's
  `mosquitto_sub` target is exact, no template reconstruction needed).
  `TelematicResponse` also gained the two new fields.
- `router.py`: `POST /api/v1/telematics/{telematic_id}/config`.
- `create_telematic`'s insert values now explicitly include
  `telemetry_interval_seconds`/`config_pushed_at` as `None` — otherwise a
  freshly-constructed, never-flushed `TelematicModel`'s `__dict__` simply
  lacks the key (SQLAlchemy doesn't populate an unset attribute until a
  refresh), and `build_telematic_response`'s `{**record.__dict__, ...}`
  spread would raise a Pydantic `ValidationError` on create.

### 3.3 `mqtt-spec.md`

Section 2.3 un-deferred: documents `set_telemetry_interval`'s exact JSON
shape, QoS 1/no-retain, and an explicit "no ack topic exists" limitation
note. `restart` stays listed as defined-but-not-implemented. New §8.3
gives the `mosquitto_sub` verification snippet. Bumped to v1.1.0.

## 4. Verification

- `black`, `isort`, `ruff check`, `mypy .` — all clean.
- Migration `0015` verified via `upgrade head` → `downgrade -1` →
  `upgrade head` against the live local Postgres; `\d telematics` confirms
  both nullable columns.
- Unit tests (`test_service_smoke.py`): the two pure builders tested
  directly (no mocking); `push_telematic_config` tested via
  `monkeypatch` on `telematics_repository.get_by_id`/`update_fields` and
  `mqtt_publisher.publish_device_command`, covering the success path
  (asserting the DB row and the wire payload agree on the push
  timestamp), unknown/soft-deleted device (404, publisher never called),
  `INACTIVE` device (409, publisher never called), and — the single most
  important case — a publish failure (`MqttError`) leaving `update_fields`
  uncalled, the executable proof of the fail-closed invariant.
  Schema test for the interval bounds
  (`test_telematic_config_push_request_rejects_interval_outside_bounds`).
  API-smoke route registration assertion. Migration-smoke head bump.
- **Live end-to-end**: created a vehicle + telematic device via the
  running API, subscribed with `mosquitto_sub -t
  'g3network/telematics/+/command' -v`, then called
  `POST /telematics/{id}/config` with `{"telemetry_interval_seconds": 45}`.
  Confirmed the exact same payload (including timestamp) landed on
  `g3network/telematics/TBOX-.../command`, that `GET /telematics/{id}`
  reflects the new interval and `config_pushed_at`, that an unknown device
  ID 404s, that a device flipped to `INACTIVE` 409s on a subsequent push,
  and that an out-of-bounds interval 422s. Test data cleaned up
  afterward.

## 5. Deferred (see `docs/decisions/deferred.md`, items 52-59)

- **Confirmation of applied config** (item 52) — no ack topic exists; a
  successful publish only proves the broker accepted the message. This is
  the keystone gap everything else below builds on.
- **Rollback** (item 53) — needs item 52 plus previous-value history.
- **Fleet/vehicle-group-scoped push** (item 54) — one device per call
  today; a batched loop is deferred per the no-premature-batching rule.
- **Reliable delivery via an outbox + dispatcher** (item 55) — no device
  consumes commands yet, so retry has nothing to converge toward.
- **Command audit history** (item 56) — needs item 52 (an outcome to
  record) and an `identity` domain (an actor to attribute it to).
- **Per-device MQTT ACL enforcement** (item 57) — `mqtt-spec.md`'s ACL
  rules exist on paper; EMQX runs with no auth/ACL applied in dev.
- **Retained commands** (item 58) — deferred until item 52 exists, so a
  stale retained command redelivered on reconnect can be detected.
- **Local alert thresholds, the second config key F-J2 names** (item 59)
  — no device-side threshold semantics are defined anywhere (same
  hardware-contract gap as the unresolved Tri-Ring spec, items 50/51).
