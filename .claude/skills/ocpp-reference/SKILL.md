---
name: ocpp-reference
description: Reference for charging-network work in this repo - OCPP 1.6J (our gateway and the Willdigits DC 240-480 kW charger), OCPP 2.0.1/2.1, OCPI (roaming vocabulary), and how widely used open-source and commercial CSMSs (CitrineOS, SteVe, AMPECO, Monta, Kempower, ChargePoint...) model places, chargers, EVSEs, access, tariffs, credentials and load management. Use when touching backend/app/domains/charging_stations/ or charging_sessions/ (OCPP gateway, adapters, measurements, raw log), the OCPP simulators or seed scripts, the charging tables in the DBML (stations, chargers, EVSEs, connectors, sessions, OCPP messages, configuration, tariffs, credentials, reservations), CHARGING_OCPP_* settings, when designing a charging feature (STN-*, CHG-*, PAY-* tariffs), or when answering how the charger, the gateway, OCPP or other CSMS products behave. Load it instead of reading the 60 KB spec summary and the 103 KB planner.
---

# Charging reference: OCPP, OCPI, CSMS practice

This is an index. Read only the reference file the task needs.

| File | Read when |
|---|---|
| [references/ocpp16-willdigits.md](references/ocpp16-willdigits.md) | Anything about **what is built**: the 1.6J gateway's message handling, measurements, the raw log, settings, simulators, the Willdigits charger's facts, deferred items 73–81, open vendor questions |
| [references/ocpp201.md](references/ocpp201.md) | Designing or coding **OCPP 2.0.1** support (deferred.md 78), smart charging, security profiles (STN-11), remote start/stop (STN-10), reservations, Plug & Charge / Autocharge, or sizing columns for **2.1** |
| [references/ocpi.md](references/ocpi.md) | Designing places, EVSEs, connectors, **tariffs**, billing records or credentials so they stay mappable to the industry's roaming vocabulary; connector standards (CCS2 = `IEC_62196_T2_COMBO`, `GBT_DC`, `MCS`); truck parking attributes |
| [references/csms-landscape.md](references/csms-landscape.md) | Choosing **how to model** something other CSMSs already solved: hierarchy and naming, private stations and access groups, tariff attachment and selection, credentials, site power limits, depot/truck features, the QR target |

## Levels at a glance
Decided in CS-09 (`docs/decisions/decision-log.md`): **the database and code use the industry names; the app, portal and docs show the Vietnamese words.**

| Level (table) | Vietnamese (UI) | OCPP 1.6J | OCPP 2.0.1 | OCPI |
|---|---|---|---|---|
| site: a large area of one organization (no table yet, deferred.md 91) | khu sạc | – | – | – (operator/owner details) |
| location (`charging_locations`): one place drivers go, chargers in a row | trạm sạc | – | – | **Location** |
| charging station (`charging_stations`): one charger = one OCPP connection | trụ sạc | Charge Point | **Charging Station** | – |
| EVSE (`charging_evses`): one outlet, one session at a time | (súng, for 1.6J) | (a 1.6 connector) | EVSE | EVSE |
| connector (`charging_connectors`): one plug | súng sạc | Connector | Connector | Connector |

- **1.6J has no EVSE.** Gun *n* is stored as EVSE *n* with connector 1, and connector 0 (the whole charger) lives on the charger row (CS-03). This is also how OCPI and 2.0.1 map a 1.6 charger, so 2.0.1 chargers fit the same tables.
- `charging_locations` is designed in the 2026-10-05 review; until the refactor, today's `charging_stations` rows still carry the place columns too.
- **Launch start flow (CO-14):** the QR code is shown on the charger's screen; the app authorizes the driver and checks the wallet, the backend sends one `RemoteStartTransaction`, and the driver picks the gun and starts and stops on the charger's screen.

## Version-only columns (CO-15)
The charging tables are protocol-neutral; each OCPP version's adapter in `charging_stations/ocpp/` turns its messages into the same rows. A column only one version can fill is allowed only when that data exists in that version alone. **Keep this list current and short**, and review it whenever a column is added:

| Table | Column | Only in | Why |
|---|---|---|---|
| `charging_station_configuration_captures` | `ocpp_request_id` | 2.0.1 | joins the NotifyReport parts of one GetBaseReport |
| `charging_station_configuration_entries` | `component_name`, `component_instance`, `ocpp_evse_id`, `ocpp_connector_id`, `variable_instance` | 2.0.1 | the device model names a setting by component + variable; 1.6J has a key only |
| `charging_session_events` | `seq_no` | 2.0.1 | TransactionEvent's sequence number; 1.6J has none (reviewed with the sessions tables) |

Columns filled by both versions in different ways stay off this list (e.g. `charging_station_state.charger_status`: 1.6J connector 0, 2.0.1 from its own messages, CS-15).

## Facts that catch people out
- **"Charging station" is the charger** in OCPP 2.0.1, AFIR, CitrineOS, Kempower and our database (CS-09). In Vietnamese usage ("trạm") and the feature catalog's English, "station" is the place: our `charging_locations`.
- **OCPI has no charger level.** A Location groups EVSEs, and the charger stays internal.
- **2.0.1 `transactionId` is a station-chosen string ≤36 chars** (1.6: an integer from our sequence).
  - Station-chosen means unique per charger only.
  - A transaction may exist before authorization (`TxStartPoint = EVConnected`).
  - Events carry `seqNo` and can arrive offline, late or partial.
- **2.0.1 status has 5 values**, and charging detail lives in `TransactionEvent.chargingState`. `Occupied` ≠ charging.
- **Security profile 1 = Basic auth with no TLS.** The OCA does not accept 2.x without profile 2 (`wss://`).
- **Remote start:** 2.0.1 `RequestStartTransaction` needs `idToken` + `remoteStartId`; the station echoes `remoteStartId` to tie the start to the transaction. In 1.6 it's `RemoteStartTransaction` with an `idTag`. This is the launch QR flow (CO-13).
- **Autocharge** = the vehicle's EVCCID (MAC address) as `IdToken type MacAddress`, or `VID:<mac>` in 1.6. The VIN is not in ISO 15118-2.
- **Size token columns for 2.1:** `id_token` up to 255 chars, and the type a free string.
- **Never log** raw frames (idTags), the Basic auth password or the Authorization header (IS-07).

## Sources and confidence
Every reference file marks claims ✅ (schema, official spec or product docs) or 🔎 (secondary), and ends with its sources.
- **Official OCPP specification PDFs are not in the repo.** Message shapes are authoritative in the installed `ocpp` library's JSON schemas: `backend/.venv/lib/python3.12/site-packages/ocpp/{v16,v201,v21}/schemas/`. Read the schema before relying on a field.
- **Product facts change**, so re-check a source before copying a detail from `csms-landscape.md`.
