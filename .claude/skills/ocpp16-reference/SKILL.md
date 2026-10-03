---
name: ocpp16-reference
description: Condensed reference for the OCPP 1.6J charger integration (Willdigits DC 240–480 kW charger). Use when touching backend/app/domains/charging_stations/ocpp/ (gateway, OCPP16ChargePoint, ocpp16_measurements, raw_log), the 1.6J simulator or seed script, the OCPP tables (charging_ocpp_messages, charging_session_measurements, charging_station_configuration_entries, the charger/connector status columns, the transactionId sequence), CHARGING_OCPP_* settings, or when answering questions about how the charger or the gateway behaves, what is implemented, deferred, or still unknown from the vendor. Load it instead of reading the 60 KB spec summary and the 103 KB planner.
---

# OCPP 1.6J reference (Willdigits charger)

spec = `docs/design/specifications/charging-station-specification-summary.md`; planner =
`docs/planners/backend-ocpp16-charger-integration.md` (D1–D14). ✅ fact · ⚠️ unverified · 🔎 inference.

## Charger facts (spec Part 1, §3, §4.3)
- Unit under evaluation: **240 kW, dual gun, CCS2+CCS2** ✅; own DC meter ✅.
- **OCPP 1.6J** ✅ verbal only; optional profiles ⚠️ (Core near-certain).
- Charger opens the WebSocket outward; URL = `ws://<host>:9000/ocpp/<identity>`; identity = HMI "Charger ID" (🔎 charger likely appends it to the URL — confirm in Step 10, mismatch A4).
- `connectorId 0` = whole charger, `1` = gun A, `2` = gun B. Connector 0 `Faulted` = whole charger down.
- Dual-gun power: equal or dynamic split; `Power.Offered` shows it.
- Default HMI password `77777777` — change on site first.
- 🔎 Quirks: SoC 0 before BMS handshake (= unknown), 10 Wh energy steps, vendor measurands `Voltage.Demand`/`Current.Demand`, stale status when offline, mixed-shape transaction IDs.

## Message handling (`ocpp/ocpp16_charge_point.py`, `OCPP16ChargePoint`)
| Message | Backend behaviour |
|---|---|
| BootNotification | Always `Accepted`; `currentTime` (UTC ms `Z`), `interval` = `CHARGING_OCPP_HEARTBEAT_INTERVAL_SECONDS`. Stores vendor/model/serial/firmware/`last_boot_at`; firmware change → WARNING only (#75). |
| Heartbeat | Returns `currentTime` only. |
| StatusNotification | Connector 0 → `charging_stations.charger_*`; gun n → EVSE n / connector 1 (D3). Status stored as exact 1.6 label (D4); `errorCode`, `vendorErrorCode`, `info` stored as sent; a report without them **clears** old values. Missing `timestamp` → receive time. Schema-validated. |
| Authorize | Accepts every idTag (D7); real validation #26/#62. |
| StartTransaction | Integer `transactionId` from sequence `charging_ocpp16_transaction_id_seq` (D6; rollback gaps harmless), stored as text in `ocpp_transaction_id`; stores `idTag` (≤20), `meterStart` (Wh). Active session already on connector → WARNING only (D14). Connector 0 refused. |
| StopTransaction | `skip_schema_validation=True`. Session found by `(station, transactionId)`, no per-connection memory. `transactionData` stored first; `meter_stop_wh` always stored; `meter_end_wh` follows forward-in-time watermark; `reason` truncated to 30. Unknown txn / completed session → CALLERROR. |
| MeterValues | `skip_schema_validation=True`. Session by `transactionId` (survives reconnect). No `transactionId` → nothing stored, DEBUG log (#77). |
| GetConfiguration (CSMS→CP) | Only request the backend sends (D10). `@after(boot_notification)` schedules a task (never await `call()` in a handler: deadlock); no key; `suppress=False`; timeout `CHARGING_OCPP_REQUEST_TIMEOUT_SECONDS`; task cancelled on disconnect. Appends one capture per boot. |
| Anything else | `CALLERROR NotImplemented`; frame kept in raw log (#81). |

Measurement rules (`ocpp/ocpp16_measurements.py`, never shares code with the 2.0.1 normaliser): no measurand ⇒ `Energy.Active.Import.Register`; energy unit `Wh`/`kWh` only (kWh ×1000 → canonical Wh, unit `Wh`), other unit / unreadable / signed data **raises**. Other measurands (incl. vendor names) stored as sent with default units; signed/non-numeric/non-finite/too-long samples **skipped and counted** (WARNING). Energy → `ingest_meter_values` (aggregate + row), rest → `ingest_measurements`.

Cross-cutting: timestamps must carry a timezone (D11). One transaction per message, owned by the gateway. Raw log (`ocpp/raw_log.py`, `RecordingConnection`) writes every frame verbatim in its **own** transaction — inbound before parsing, outbound after send; persistence failure ends the connection; the same write updates `last_seen_at`/`ocpp_protocol_version` (leaves `updated_at` alone). `is_online` = `last_seen_at` within `CHARGING_OFFLINE_TIMEOUT_SECONDS`, derived at read time, never stored (D5). Frames contain idTags: **never copy them into application logs**.

## Gateway and boundaries
- Layout (since 2026-10-01): `ocpp/ocpp_server.py` handshake + connection only; `ocpp/ocpp201_charge_point.py` the 2.0.1 adapter; `ocpp/ocpp16_charge_point.py` the 1.6J adapter; device state the adapters write goes through the internal `ocpp_state_service.py` / `ocpp_state_repository.py` (not `service.py`, which other domains use). The stop-reason width is `charging_sessions.types.STOP_REASON_MAX_LENGTH`.
- `ocpp/ocpp_server.py`: `SUPPORTED_SUBPROTOCOLS = ("ocpp2.0.1", "ocpp1.6")` (2.0.1 preferred when both offered); 426 if neither; 404 bad path/unknown identity; `create_charge_point` picks `OCPP201ChargePoint` or `OCPP16ChargePoint` (D1, D2). 2.0.1 adds only `BootNotification` (device info + `last_boot_at`, same heartbeat interval) and `Heartbeat` (2026-10-01, D13 reopened by `docs/planners/done/backend-happy-path-completion.md`); otherwise shared plumbing only — stop reason, `idToken`, non-energy measurands still missing (#78).
- `charging_stations → charging_sessions` via public `service.py` only (no new edge): `allocate_ocpp16_transaction_id`, `has_active_session_on_connector`, `resolve_session_by_transaction`, `ingest_measurements`.

## Status enum (D4)
`ChargingConnectorStatus` has 10 values: 2.0.1's `Available, Occupied, Reserved, Unavailable, Faulted` + 1.6's `Preparing, Charging, SuspendedEV, SuspendedEVSE, Finishing`. Also used for `charger_status`. Free only when `Available`; `Suspended*` are normal, not faults; `Finishing` is busy. This rule drives F-A2/F-D1 availability since 2026-10-01 (≥1 `Available` connector, `is_online` not required; `available_connector_count`, `GET /charging-stations/{id}/connectors`).

## Data (`.claude/rules/database.md`)
`charging_ocpp_messages` (hypertable, `raw_frame TEXT`, `direction` CP_TO_CSMS/CSMS_TO_CP, no API/retention #79) · `charging_stations` device + connector-0 columns · `charging_connectors.error_code/vendor_error_code/status_info` · `charging_sessions.id_tag/stop_reason/meter_stop_wh` · `charging_session_measurements` (hypertable, replaced `charging_session_meter_values`, D9) · `charging_station_configuration_entries` (append-only, grouped by `capture_id`). The planner cites the historical revisions `0021`–`0026`; the chain is now collapsed into `backend/app/libs/db/migrations/versions/0001_baseline_schema.py` (sequence + hypertables hand-written at its end).
APIs: `GET /api/v1/charging-stations/{id}` (device fields, `is_online`), `…/charging-stations/{id}/configuration` (latest capture), `…/charging-sessions/{id}/measurements` (`?measurand=`), `…/meter-values` (energy only).

## Settings (`backend/.env.example`)
`CHARGING_OCPP_HOST`/`PORT` (0.0.0.0/9000), `CHARGING_OCPP_MAX_MESSAGE_BYTES` (1 MiB; larger → close 1009), `CHARGING_OCPP_HEARTBEAT_INTERVAL_SECONDS` (60), `CHARGING_OFFLINE_TIMEOUT_SECONDS` (180), `CHARGING_OCPP_REQUEST_TIMEOUT_SECONDS` (30).

## Run
`make charging-ocpp-dev` (gateway, both protocols) · `make charging-ocpp16-seed` (station `SIM-OCPP16-001`, EVSE per gun) · `make charging-ocpp16-sim` (default scenario `boot`). Other scenarios: `cd backend && uv run python ../simulator/ocpp16_charge_point_simulator.py --scenario status|session`. 2.0.1: `make charging-ocpp-seed` / `charging-ocpp-sim`. Tests: `backend/tests/charging_stations/test_ocpp*_smoke.py`; clean-DB E2E `test_ocpp16_charging_session_end_to_end_on_a_clean_database` in `backend/tests/test_postgres_integration.py` (`RUN_DB_INTEGRATION=1`).

## Status and deferred work
Steps 0–9, 11 done (M1–M3, simulator only). **Step 10, real-charger bring-up, waits for hardware** (runbook + 9 SQL queries in planner). Deferred (`docs/decisions/deferred.md`): #73 TLS/auth (D12, dev is plain `ws://`) · #74 remote commands + cross-process command channel (RemoteStart/Stop, ChangeConfiguration, TriggerMessage, Reset…) · #75 fault alerting + 80-code catalog · #76 stale status / online-aware availability (connector-status availability done, #49) · #77 non-transaction / clock-aligned metering · #78 rest of 2.0.1 parity · #79 raw-log API + retention · #80 per-gun power/connector standard · #81 ⚠️ unverified: field lists come from the library schema, card-tap flow (Authorize→Start?) undocumented, unhandled DataTransfer/FirmwareStatus/DiagnosticsStatus · #27 retry, dedup, orphan sessions, back-fill, out-of-order.
Open vendor questions (open questions 4–5 in `docs/decisions/decision-log.md`): OCPP implementation guide; 80 error codes → `vendorErrorCode` mapping; DC meter brand/class/seal (⚠️ whether `meterStart/Stop` come from the meter — invoice legality); `wss://` + auth; profiles in writing; HMI permission levels; VIN Autocharge over OCPP?; offline buffer size; `DataTransfer` use. Non-software: truck inlet CCS2 vs GB/T, VN meter verification, local warranty. Interim: build to the standard; meter readings stored as reported, not legally verified.

## Read the full source when…
- Exact decision wording/rejected alternatives, per-step evidence, Step 10 runbook and SQL → planner §2, §4 (Step 10), §6.
- Config key targets, reference session, error/stop codes, acceptance checklist → spec §4.2, §4.4, §4.7 + Appendix A, §5.3.
- Hardware, installation, HMI screens, vendor requests → spec Parts 1–3, §6.
- Full deferred-item text → `docs/decisions/deferred.md` items 27, 73–81.
- Mismatch IDs A1…E3 → `docs/reports/charging-station-spec-vs-current-system.md`.
