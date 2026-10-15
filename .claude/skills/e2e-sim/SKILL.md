---
name: e2e-sim
description: Run this backend end to end on the local stack (PostgreSQL, EMQX, API, OCPP gateway, ingestion workers), provision data through the API and verify with SQL what landed. Charger and driver-app traffic can come from the standalone simulators in simulators/ (OCPP 1.6J charger, phone page, charger monitor); vehicle telemetry needs a real T-Box or an MQTT client (e.g. mosquitto_pub per mqtt-spec.md). Use when asked to run/start the app, smoke-test a change against real services, reproduce an ingestion/OCPP bug with external traffic, or prove a feature works beyond unit tests.
---

# End-to-end run on the local stack

Rewritten on 2026-10-14 when the old simulators were removed; not yet run
in this form (steps 1, 2 and 4 are unchanged from the run verified on
2026-10-12). Run from the repo root unless a step says otherwise. **Step 1
wipes the local database.**

This skill starts the stack, provisions data through the API and checks the
database. **Charger and driver-app traffic** can come from the standalone
simulators in `simulators/` (TT-24 to TT-27 in
`docs/decisions/decision-log.md`; how to run them: `simulators/README.md`):
an OCPP 1.6J charger with two guns and a sample car, a phone page that plays
the driver app through the API, and a charger monitor. Each reads only its
own `.env` and imports no backend code. **Vehicle telemetry** has no
simulator: it needs a real T-Box or an MQTT publisher such as
`mosquitto_pub`. A real charger or another OCPP 1.6J / 2.0.1 client works
too. Gateway and adapter behaviour without a device is covered in
process by `make backend-test` and `make backend-test-integration`.

## 1. Infrastructure and a clean database

```bash
make infra-up          # PostgreSQL+TimescaleDB+PostGIS :5432, EMQX :1883
make db-reset          # clear + rebuild from 0001_baseline_schema
make identity-bootstrap   # internal organization + first HEAD_ADMIN
```

Every API route needs a login; the first account is the bootstrap
HEAD_ADMIN, so set `IDENTITY_BOOTSTRAP_ADMIN_PHONE` and
`IDENTITY_BOOTSTRAP_ADMIN_PASSWORD` in `backend/.env` (or export them in the
shell that runs the bootstrap) before this step.

## 2. Start the services (background, logs in a scratch dir)

Start them **after** `make db-reset`. A service that was running across a
reset keeps asyncpg statements cached against the dropped schema and fails
with `InvalidCachedStatementError` — restart it.

```bash
LOG=/tmp/g3-e2e && mkdir -p $LOG && cd backend
nohup uv run uvicorn app.api.main:app --port 8000          > $LOG/api.log 2>&1 &
nohup uv run python -m app.domains.telemetry.ingestion.entrypoint > $LOG/ingest.log 2>&1 &
nohup uv run python -m app.domains.charging_stations.ocpp.entrypoint > $LOG/ocpp.log 2>&1 &
for i in $(seq 1 30); do curl -sf localhost:8000/health >/dev/null && ss -ltn | grep -q ':9000 ' && break; sleep 1; done
curl -s localhost:8000/health        # {"status":"healthy",...}
```

(`make backend-dev`, `make telemetry-dev`, `make charging-ocpp-dev` run the
same three in the foreground, one per terminal.) Optional fourth service,
the device-silence monitor (F-J1/F-J3):
`nohup uv run python -m app.domains.telematics.monitoring.entrypoint > $LOG/monitor.log 2>&1 &`
(foreground: `make telematics-monitor-dev`). Another optional service is the
T-Box status-report ingestion (DEV-03, `make telematics-status-dev`,
`nohup uv run python -m app.domains.telematics.ingestion.entrypoint ...`); feed
it a status message by hand
(`mosquitto_pub -t g3network/telematics/<serial>/status -m '{"firmware_version":"1.0","signal_dbm":-70}'`)
and read it from `GET /telematics/<id>/health`. The API logs as JSON to
`$LOG/api.log`, including the development SMS (`fake_sms_sent`, the code is
in `sms_text`).

## 3. Flows (from `backend/`)

Helpers:
```bash
Q(){ docker exec g3network-db psql -U g3network -d g3network -Atc "$1"; }
J='content-type: application/json'
TOKEN(){ curl -s -XPOST localhost:8000/api/v1/auth/login -H "$J" \
  -d "{\"phone_number\":\"$1\",\"password\":\"$2\",\"platform\":\"$3\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])'; }
ADMIN="Authorization: Bearer $(TOKEN <bootstrap phone> '<bootstrap password>' WEB)"
```

**Provisioning** — as the admin, through the API (request bodies in
`GET /docs`):
- a truck and its T-Box: `POST /api/v1/vehicles/`, then
  `POST /api/v1/telematics/` with the device's serial and the vehicle;
- a charger: `POST /api/v1/charging-locations`, `POST /api/v1/charging-stations`
  (its `ocpp_identity` is the device's charger ID),
  `POST /api/v1/charging-stations/{station_id}/evses` and
  `POST /api/v1/charging-evses/{evse_id}/connectors`. A 1.6J charger needs one
  EVSE per gun (EVSE `n` / connector `1` for gun `n`). An unprovisioned
  identity gets HTTP 404 at the WebSocket handshake.

**Telemetry (F-A1)** — needs an external publisher. By hand, publish one
message in the shape of `docs/design/specifications/mqtt-spec.md` §8.2 with
the provisioned serial (a new `message_uuid` each time), then:
```bash
Q "select count(*), count(distinct vehicle_id), max(recorded_at) from telemetry"
VID=$(Q "select vehicle_id from telemetry limit 1")
curl -s localhost:8000/api/v1/telemetry/vehicles/$VID/latest -H "$ADMIN"  # lat/lon, soc, ...
```
Expected: one row per accepted message; `latest` returns the newest point.

**Charger traffic (F-B2, CHG-01)** — needs the charger simulator
(`simulators/charger_simulator/`), a real charger or an OCPP client pointed
at `ws://localhost:9000/ocpp/<ocpp_identity>` (`ocpp1.6` or
`ocpp2.0.1`). After it connects:
```bash
Q "select s.ocpp_identity, t.ocpp_protocol_version, t.vendor, t.last_boot_at, t.last_seen_at from charging_station_state t join charging_stations s using (station_id)"
Q "select direction, count(*) from charging_ocpp_messages group by 1"
Q "select ocpp_transaction_id, status, id_token, meter_start_wh, meter_stop_wh, stop_reason from charging_sessions"
```
Only a token issued by a QR scan starts a charge (CE-11), and a scan needs
the charger connected and a DRIVER account: create a customer organization
(`POST /api/v1/organizations/`), invite the driver
(`POST /api/v1/organizations/{id}/members` with `"roles":["DRIVER"]`; the
invitation code is in `$LOG/api.log` as `fake_sms_sent`), accept it
(`POST /api/v1/auth/invitations/accept`), log in as the driver, then
`POST /api/v1/charging-sessions/scan` with the charger code and connector
number. The gateway then sends the charger a remote start with the token.

API spot checks (with `-H "$ADMIN"`): `GET /api/v1/charging-stations?page=1&page_size=10`
(`is_online: true` while a charger is connected), `GET /docs` for the rest.

## 4. Stop everything

```bash
pkill -f "[u]vicorn app.api.main:app"
pkill -f "[a]pp.domains.telemetry.ingestion.entrypoint"
pkill -f "[a]pp.domains.charging_stations.ocpp.entrypoint"
pkill -f "[a]pp.domains.telematics.monitoring.entrypoint"   # if started
ss -ltn | grep -E ':(8000|9000) ' || echo stopped
```
Run these in a **separate shell command** from the one that started the
services: `pkill -f` matches full command lines, so a shell whose own script
also contains the start commands (`uvicorn app.api.main:app ...`) kills
itself, bracket or not. The `[x]` bracket only stops the pattern matching the
`pkill` text itself. SIGTERM (the `pkill` default) is a clean stop: ingestion
logs "Telemetry ingestion stopped" and exits 0. `make infra-down` stops the
containers and keeps the data.

## When something fails

Read `$LOG/*.log` first (structured JSON logs). Common causes: the API isn't
up yet (`Connection refused`), the bootstrap admin is missing (login fails:
set `IDENTITY_BOOTSTRAP_ADMIN_*`, run `make identity-bootstrap`), a telemetry
message names an unprovisioned serial (skipped by ingestion), the charger is
not connected when scanning (`409`), the station wasn't provisioned
(gateway answers HTTP 404 to the WebSocket handshake), or the database is
still on an old schema (`make db-reset`). For OCPP behaviour, load the
`ocpp-reference` skill.
