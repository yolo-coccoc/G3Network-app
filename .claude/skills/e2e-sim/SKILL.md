---
name: e2e-sim
description: Run this backend end to end on the local stack with the simulators and verify the data landed — vehicle telemetry over MQTT, an OCPP 1.6J charging session and an OCPP 2.0.1 charging session. Use when asked to run/start the app, smoke-test a change against real services, reproduce an ingestion/OCPP bug, or prove a feature works beyond unit tests.
---

# End-to-end simulator run

Verified on 2026-10-12 (login, QR scan, billing). Run from the repo root
unless a step says otherwise. **Step 1 wipes the local database.**

## 1. Infrastructure and a clean database

```bash
make infra-up          # PostgreSQL+TimescaleDB+PostGIS :5432, EMQX :1883
make db-reset          # clear + rebuild from 0001_baseline_schema
make identity-bootstrap   # internal organization + first HEAD_ADMIN
```

Every API route needs a login. The seed scripts and the telemetry simulator
log in as the bootstrap HEAD_ADMIN (`simulator/api_client.py`, which re-exports
the client of the standalone test-day folder,
`simulator/charging_test_day/g3_api.py`), so set
`IDENTITY_BOOTSTRAP_ADMIN_PHONE` and `IDENTITY_BOOTSTRAP_ADMIN_PASSWORD` in
`backend/.env` (or export them in the shell that runs the bootstrap and the
scripts) before this step.

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
`nohup uv run python -m app.domains.telematics.ingestion.entrypoint ...`); the
telemetry simulator does not publish status messages, so publish one by hand
with `mosquitto_pub -t g3network/telematics/<serial>/status -m '{"firmware_version":"1.0","signal_dbm":-70}'`
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

**Telemetry (F-A1)** — seeding needs the API; the simulator publishes every 5 s
forever, so bound it:
```bash
uv run python ../simulator/seed_simulator_devices.py         # vehicle + TBOX-SIM-00001
timeout 14 uv run python ../simulator/telematic_simulator.py  # ~3 messages
Q "select count(*), count(distinct vehicle_id), max(recorded_at) from telemetry"
VID=$(Q "select vehicle_id from telemetry limit 1")
curl -s localhost:8000/api/v1/telemetry/vehicles/$VID/latest -H "$ADMIN"  # lat/lon, soc, ...
```
Expected: ≥1 row per seeded vehicle; `latest` returns the newest point.

**OCPP 1.6J session by QR scan (CHG-01, F-B2)** — scenarios `boot` (default
of the make target), `status`, `session`. Only a DRIVER can scan, and only a
token a scan issued starts a charge (CE-11), so first make a driver: a
customer organization, an invitation (its code is in the API log), the accept:
```bash
make -C .. charging-ocpp16-seed     # location + tariff + SIM-OCPP16-001, one EVSE per gun
ORG=$(curl -s -XPOST localhost:8000/api/v1/organizations/ -H "$ADMIN" -H "$J" -d '{"legal_form":"COMPANY","display_name":"E2E Logistics","legal_name":"E2E Logistics LLC","first_admin":{"phone_number":"+84900000010","full_name":"Org Admin"}}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["organization_id"])')
curl -s -XPOST localhost:8000/api/v1/organizations/$ORG/members -H "$ADMIN" -H "$J" -d '{"phone_number":"+84900000021","full_name":"E2E Driver","roles":["DRIVER"]}'
grep fake_sms_sent $LOG/api.log | tail -1        # 6-digit code in sms_text
curl -s -XPOST localhost:8000/api/v1/auth/invitations/accept -H "$J" -d '{"phone_number":"+84900000021","code":"<code>","password":"Driver-Passw0rd!","platform":"ANDROID"}'
DRIVER="Authorization: Bearer $(TOKEN +84900000021 'Driver-Passw0rd!' ANDROID)"

# The simulator boots, then waits up to 120 s for the scan's RemoteStartTransaction
nohup make -C .. charging-ocpp16-sim SCENARIO=session > $LOG/sim16.log 2>&1 &
sleep 5   # until sim16.log says WAITING: a scan needs a connected charger
curl -s -XPOST localhost:8000/api/v1/charging-sessions/scan -H "$DRIVER" -H "$J" \
  -d '{"charger_code":"SIM-OCPP16-001","connector_number":1}'   # -> session_id, PENDING
wait
curl -s localhost:8000/api/v1/charging-sessions/<session_id>/receipt -H "$DRIVER"
Q "select ocpp_transaction_id, status, id_token, meter_start_wh, meter_stop_wh, stop_reason from charging_sessions"
Q "select measurand, count(*) from charging_session_measurements group by 1 order by 1"
Q "select direction, count(*) from charging_ocpp_messages group by 1"
Q "select count(*) from charging_station_configuration_entries"
```
Expected: `1|COMPLETED|<the token>|1000.000|1500.000|EVDisconnected`; the
receipt shows 500 Wh, `BILLED`, 1,890 VND (3,500 VND/kWh + 8 % VAT) and the
driver's wallet (`GET /wallets/me`) goes to -1,890, so the next scan is refused
until a top-up (BL-25: `POST /wallets/me/top-ups` as the driver, then
`POST /payments/vietqr/simulate` with its `transfer_code` as the admin);
8 measurands (energy register, SoC, power, voltage, `Voltage.Demand`, ...);
equal CP_TO_CSMS/CSMS_TO_CP frame counts; >0 configuration entries (the
post-boot `GetConfiguration`).

The 1.6J simulator models each gun like a real charger (a remote start only
authorizes; the charge starts when the gun is plugged and started; `Authorize`
follows `AuthorizeRemoteTxRequests`; `ConnectionTimeOut` expiry; a refused
start ends with `StopTransaction(0, DeAuthorized)`; readings every
`MeterValueSampleInterval`). Scripted: `ARGS="--end remote --sample-interval 5"`
charges until `POST /charging-sessions/{id}/stop`; `--plug after-scan|never`,
`--start screen`, `--end local|full|power-loss`, `--remote-start-requires-gun`,
`--config KEY=VALUE`, `--sessions 2` (both guns); `--naive-timestamps
--uppercase-id-tag --long-texts --float-meter-start --resend-stop` reproduce
firmware quirks (CO-16, CO-17). `--scenario charger` is driven by typed
commands (`plug 1`, `start 1`, `stop 1`, `unplug 1`, `status`, `help`). Instead of the curl walk-through above,
the charging test-day tools drive the same flow over the API:
`make charging-test-drivers-seed` (drivers with a credited wallet, no
invitation code needed), `make driver-app ARGS="scan --driver 1 --charger
SIM-OCPP16-001"` and `make station-console ARGS="..."` (traffic feed,
configuration, `force-close` of an orphan); see section 6 of
`docs/planners/backend-charging-test-day.md`. The 1.6J simulator, the charger
seed and those two tools live in `simulator/charging_test_day/` (standalone,
no backend import; laptop use in its `CLAUDE.md`); their evidence files and
saved meters go to its git-ignored `logs/` (`test-day/`, `simulator-state/`).
They read their settings from the shell, then `backend/.env`, then the
folder's `.env`; `G3_OCPP_URL` / `TEST_DAY_CHARGER_ID` set the simulator's
default `--url` / `--identity`. `make charging-ocpp16-sim` always passes
`--identity` and `--url ws://localhost:9000` (an `ARGS="--url ..."` still
wins), so the folder's laptop settings never send a local run through the
public IP.

**OCPP 2.0.1 session**:
```bash
make -C .. charging-ocpp-seed && make -C .. charging-ocpp-sim
Q "select x.ocpp_transaction_id, x.status from charging_sessions x join charging_stations s using (station_id) where s.ocpp_identity='SIM-OCPP-001'"
```
Expected: no row: the 2.0.1 simulator sends no `idToken` and does not answer
`RequestStartTransaction`, so its start is answered `Invalid` (CE-11).

API spot checks (with `-H "$ADMIN"`): `GET /api/v1/charging-stations?page=1&page_size=10`
(both stations, `is_online: true` right after a run), `GET /docs` for the rest.

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
up yet (seed scripts get `Connection refused`), the bootstrap admin is missing
(seed scripts fail to log in: set `IDENTITY_BOOTSTRAP_ADMIN_*`, run
`make identity-bootstrap`), the charger is not connected when scanning (`409`,
start the simulator first), the wallet is negative (`INSUFFICIENT_BALANCE`), the station wasn't seeded
(gateway answers HTTP 404 to the WebSocket handshake), or the database is
still on an old schema (`make db-reset`). For OCPP behaviour, load the
`ocpp-reference` skill.
