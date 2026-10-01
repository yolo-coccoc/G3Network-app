---
name: e2e-sim
description: Run this backend end to end on the local stack with the simulators and verify the data landed — vehicle telemetry over MQTT, an OCPP 1.6J charging session and an OCPP 2.0.1 charging session. Use when asked to run/start the app, smoke-test a change against real services, reproduce an ingestion/OCPP bug, or prove a feature works beyond unit tests.
---

# End-to-end simulator run

Verified on 2026-10-01 against the baseline schema, before and after the
source refinement (identical results). Run from the repo root
unless a step says otherwise. **Step 1 wipes the local database.**

## 1. Infrastructure and a clean database

```bash
make infra-up          # PostgreSQL+TimescaleDB+PostGIS :5432, EMQX :1883
make db-reset          # clear + rebuild from 0001_baseline_schema
```

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
(foreground: `make telematics-monitor-dev`).

## 3. Flows (from `backend/`)

Helper: `Q(){ docker exec g3network-db psql -U g3network -d g3network -Atc "$1"; }`

**Telemetry (F-A1)** — seeding needs the API; the simulator publishes every 5 s
forever, so bound it:
```bash
uv run python ../simulator/seed_simulator_devices.py         # vehicle + TBOX-SIM-00001
timeout 14 uv run python ../simulator/telematic_simulator.py  # ~3 messages
Q "select count(*), count(distinct vehicle_id), max(recorded_at) from vehicle_telemetry"
VID=$(Q "select vehicle_id from vehicle_telemetry limit 1")
curl -s localhost:8000/api/v1/telemetry/vehicles/$VID/latest  # lat/lon, soc, ...
```
Expected: ≥1 row per seeded vehicle; `latest` returns the newest point.

**OCPP 1.6J session (F-B2)** — scenarios `boot` (default of the make target),
`status`, `session`:
```bash
make -C .. charging-ocpp16-seed     # station SIM-OCPP16-001, one EVSE per gun
uv run python ../simulator/ocpp16_charge_point_simulator.py --scenario session
Q "select ocpp_transaction_id, status, id_tag, meter_start_wh, meter_stop_wh, energy_delivered_wh, stop_reason from charging_sessions"
Q "select measurand, count(*) from charging_session_measurements group by 1 order by 1"
Q "select direction, count(*) from charging_ocpp_messages group by 1"
Q "select count(*) from charging_station_configuration_entries"
```
Expected: `1|completed|SIMTAG001|1000.000|1500.000|500.000|EVDisconnected`;
8 measurands (energy register, SoC, power, voltage, `Voltage.Demand`, ...);
equal CP_TO_CSMS/CSMS_TO_CP frame counts; >0 configuration entries (the
post-boot `GetConfiguration`).

**OCPP 2.0.1 session**:
```bash
make -C .. charging-ocpp-seed && make -C .. charging-ocpp-sim
Q "select x.ocpp_transaction_id, x.status, x.energy_delivered_wh from charging_sessions x join charging_stations s using (station_id) where s.ocpp_identity='SIM-OCPP-001'"
```
Expected: `SIM-TRANSACTION-001|completed|500.000`.

API spot checks: `GET /api/v1/charging-stations?page=1&page_size=10` (both
stations, `is_online: true` right after a run), `GET /docs` for the rest.

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
up yet (seed scripts get `Connection refused`), the station wasn't seeded
(gateway answers HTTP 404 to the WebSocket handshake), or the database is
still on an old schema (`make db-reset`). For OCPP behaviour, load the
`ocpp16-reference` skill.
