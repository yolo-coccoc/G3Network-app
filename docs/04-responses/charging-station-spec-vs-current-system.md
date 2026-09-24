# Charging-station specification vs. the current system — mismatch report

> **Compared.** The Willdigits charger as described in
> [`docs/03-specifications/charging-station-specification-summary.md`](../03-specifications/charging-station-specification-summary.md)
> (distilled from the vendor manual and G3's OCPP 1.6J handover doc) against the backend as it exists on
> `master` today (`charging_stations`, `charging_sessions`, the OCPP gateway, the simulator, the docs).
>
> **Date:** 2026-09-24. **Nothing was changed** in the code, the database, or other docs; this is a report only.

## Status update — 2026-09-24 (after the OCPP 1.6J planner, Steps 0–9)

This report describes the system **as it was before** the
[OCPP 1.6J planner](../02-planners/backend-ocpp16-charger-integration.md);
the register below is kept unchanged as the record of that comparison. Where each
mismatch stands now (✅ closed · ◐ partly · ⏳ waits for the real charger, Step 10 ·
⛔ deliberately deferred with a `future.md` item). Everything marked ✅ was verified
against **simulators only**.

| ID | Now | How / where |
|---|---|---|
| A1 subprotocol/version | ✅ | Step 2 — `ocpp1.6` and `ocpp2.0.1` negotiated, `426` only when neither is offered |
| A2 Boot/Heartbeat | ✅ | Step 4 |
| A3 message shapes | ✅ | Steps 4–7 (a separate 1.6J adapter) |
| A4 URL/identity path | ⏳ | Step 10: confirm the URL the charger builds |
| A5 TLS/authentication | ⛔ | `future.md` #73 (waits for the vendor) |
| A6 CSMS commands | ◐ | only the post-boot `GetConfiguration` (Step 8); the rest is #74 |
| B1 raw message log | ✅ | Step 1 (read API/retention: #79) |
| B2 connector 0 · B3 EVSE mapping | ✅ | Step 5 (connector 0 on the station; gun `n` = EVSE `n` / connector `1`) |
| B4 status granularity | ✅ | Step 5 — ten-value status enum |
| B5 error codes | ◐ | stored (Step 5); alerting and the vendor code catalog: #75 |
| B6 charger registry | ✅ | Step 4 (device info, `last_seen_at`, derived `is_online`) |
| B7 measurands | ✅ | Steps 7a–7b for 1.6J (2.0.1 parity: #78) |
| B8 transaction ID | ✅ | Step 6 — database sequence |
| B9 idTag / stop reason | ◐ | stored (Step 6); tag validation and VIN linkage: #26/#62 |
| B10 `meterStop` | ✅ | Step 6 — stored apart from the latest sample |
| B11 tariff · B13 immutability · B14 per-gun power | ⛔ | unchanged (#26, #80) |
| B12 configuration history | ◐ | Step 8 stores a snapshot per boot; no diffing |
| C1 reconnect mapping | ✅ | for 1.6J (Step 7b: lookup by `transactionId`); the 2.0.1 adapter keeps its map |
| C2 back-fill · C3 orphans · C4 clock | ⏳ | Step 10 findings, then decide (`future.md` #27) |
| C5 stale status | ◐ | `is_online` is exposed; invalidation: #76 |
| C6/C7 error policy/duplicates | ⛔ | unchanged (`future.md` #31/#66) |
| C8 silent skips | ✅ | Step 7b — skipped samples are counted and logged |
| E1 simulator · E2 tests | ✅ | Steps 3 and 9 |
| E3 public endpoint/TLS infra | ⛔ | with A5 |

The §2 checks (handshake, `kWh` parsing, unhandled actions) now have permanent regression
tests, and the "unit hazard" in §2.2 cannot recur: the 1.6J path has its own normaliser.

**How to read severity**

| Tag | Meaning |
|---|---|
| **BLOCKER** | The real charger cannot connect, or the core data flow cannot work at all |
| **MAJOR** | The spec calls it mandatory/important and the system lacks it, or handles it wrongly |
| **MINOR** | A design difference or a limitation that is tolerable for now |
| **OK** | Already aligned; reusable |

**"Known?" column** — whether the gap was already recorded in this repo (`future.md` item / feature-list note) or is **new**, surfaced only by this comparison.

**Evidence conventions.** File references are `path:line` on the current tree. Statements marked
✔ *verified* were reproduced by running the current code (§2). Statements about the vendor's charger
inherit the confidence tags of the spec summary (✅ confirmed by a source doc, ⚠️ unconfirmed, 🔎 read from a screenshot).

---

## 1. Bottom line

The current system is a working **OCPP 2.0.1** happy-path pipeline (`TransactionEvent` → session/meter storage, `StatusNotification` → connector status).
The vendor's charger speaks **OCPP 1.6J**, which is a **different wire protocol**, not a newer/older dialect the current gateway can tolerate.
Consequently:

1. **The charger cannot connect today.** The gateway rejects any client that doesn't offer the `ocpp2.0.1` subprotocol with **HTTP 426** (✔ verified, §2.1). Nothing else in this report matters until that is resolved.
2. **Even with the handshake fixed, none of the three existing handlers fits 1.6J.** 1.6J has no `TransactionEvent`, no EVSE level, a different `StatusNotification` and `MeterValues` shape, and it needs `BootNotification`/`Heartbeat` answered — none of which exist (§3 A2–A3).
3. **A silent data-corruption hazard exists if the existing meter parser is reused as-is:** 1.6 puts the unit in a flat `unit` field, but the parser reads the 2.0.1 nested `unit_of_measure`, so a `kWh` reading would be stored as Wh — **1000× too small** (✔ verified, §2.2).
4. Beyond the protocol, the spec's **mandatory practices are missing**: no raw OCPP message log, no `connectorId 0` (whole-charger) concept, no `SoC`/power/voltage/current/temperature storage, no error-code storage, no charger registry (boot info, online state, heartbeat), no CSMS-initiated commands, no TLS/authentication.
5. **Real-world behaviour** the spec tells us to test (mid-session network loss, reboot mid-session, offline back-fill) would currently produce rejected messages or orphaned `ACTIVE` sessions (§3 C).
6. Most of the missing pieces are **already deferred deliberately** (`future.md` items 26, 27, 28, 31, 32, 37, 49, 62, 66) under the "ideal MVP" assumption that the station is always online, in-order and duplicate-free. What is **new** here is that a real device has now been named, it is 1.6J, and the "ideal" assumptions no longer hold for it.

| Severity | Count | Of which new (not previously recorded) |
|---|---|---|
| BLOCKER | 3 | 1 |
| MAJOR | 18 | 7 |
| MINOR | 10 | 6 |
| OK (aligned / reusable) | 10 | — |

---

## 2. Checks that were actually run

### 2.1 What happens when a 1.6J charger connects  ✔ verified
Calling the real `OCPPServer._process_request` with the headers a 1.6J charger sends:

| Request path | `Sec-WebSocket-Protocol` offered | Gateway response |
|---|---|---|
| `/ocpp/LSC` | `ocpp1.6` | **`426 Upgrade Required` — "Required WebSocket subprotocol: ocpp2.0.1"** |
| `/ocpp/LSC` | *(none)* | `426 Upgrade Required` |
| `/ocpp/LSC` | `ocpp1.6, ocpp2.0.1` | passes protocol check → `404 Unknown OCPP station identity` (station not provisioned in the run) |
| `/steve/websocket/CentralSystemService/LSC` | `ocpp1.6` | `404 Invalid OCPP station path` (path prefix must be `/ocpp/`) |

`select_ocpp_subprotocol` also returns `None` for a client offering only `ocpp1.6`. The `serve(...)` call registers only `ocpp2.0.1` (`ocpp_server.py:589-597`).
The test used a station identity that didn't exist, so only the handshake logic was exercised; no data was written.

### 2.2 What the current meter parser does with a 1.6-shaped sample  ✔ verified
The installed `ocpp` 2.1.0 library exposes both `v16` and `v201` datatypes:

| | 1.6 `SampledValue` fields | 2.0.1 `SampledValue` fields |
|---|---|---|
| unit | **`unit`** (flat string, e.g. `"kWh"`) | **`unit_of_measure`** (nested `{unit, multiplier}`) |
| others | value, context, format, measurand, phase, location | value, context, measurand, phase, location, signed_meter_value |

`normalize_sampled_value_to_wh` reads `sampled_value.get("unit_of_measure")` (`ocpp_server.py:188`). Feeding it
`{"value": "12.5", "unit": "kWh", "measurand": "Energy.Active.Import.Register"}` returned **`12.5` (Wh)** instead of `12500` — no error, no warning.
Any 1.6 adapter must have its own unit handling; the existing function cannot simply be reused.

### 2.3 What the library does with an action that has no handler  ✔ verified (source read)
`python-ocpp`'s `_handle_call` replies with a `CALLERROR (NotImplemented)` when the action has no `@on(...)` handler. The gateway registers handlers for only `TransactionEvent`, `MeterValues`, `StatusNotification` (`ocpp_server.py:351, 438, 476`), so `BootNotification`, `Heartbeat`, `Authorize`, etc. would be answered with an error.

---

## 3. Mismatch register

### A. Connection and protocol

| ID | Severity | Known? | Spec says | Current system | Consequence |
|---|---|---|---|---|---|
| **A1** | **BLOCKER** | Partly (feature-list "Items needing confirmation" #11 — *OCPP version at procurement — open*; F-G2 "OCPP 1.6J not implemented at all") | The charger speaks **OCPP 1.6J** (✅ verbally; 🔎 HMI protocol selector = `OCPP1.6`) | Gateway is **2.0.1 only**: `OCPP_SUBPROTOCOL = "ocpp2.0.1"` (`ocpp_server.py:50`), handshake returns 426 otherwise (`:638-643`), handlers use `ocpp.v201` | Charger never connects (§2.1). The spec **resolves** the open procurement question: the first real hardware is 1.6J, so a 1.6J path is required |
| **A2** | **BLOCKER** | Partly (item 28 mentions no heartbeat; nothing about Boot) | `BootNotification` (vendor/model/firmware/serial + CSMS returns `currentTime`, `interval`) and `Heartbeat` are Core messages; the charger sends them first and periodically | No handler for either; unhandled actions get `CALLERROR NotImplemented` (§2.3) | Per general OCPP behaviour (not from the docs), a charger whose `BootNotification` isn't answered `Accepted` typically stays in a boot-retry loop and may send nothing else — ⚠️ confirm on the real unit. Also no clock sync (`currentTime`) for the charger |
| **A3** | **BLOCKER** | **New** | 1.6J message set: `StartTransaction`, `StopTransaction`, `MeterValues` (with `connectorId`, optional `transactionId`), `StatusNotification` (with `connectorId`, `errorCode`, `status`, `vendorErrorCode`), `Authorize` | Only 2.0.1 shapes: `TransactionEvent` (Started/Updated/Ended + `evse`), `MeterValues(evse_id, …)`, `StatusNotification(evse_id, connector_id, connector_status)`. 1.6 has **no EVSE field** and **no TransactionEvent** | All three handlers are structurally incompatible with 1.6 (missing `evse_id` → `TypeError` → `CALLERROR`). Need new 1.6 handlers, not adjustments |
| **A4** | MINOR | **New** | Charger URL is an editable field and the charger very likely appends the Charger ID (🔎 sample shows a SteVe-style path) | Path contract is exactly `/ocpp/{identity}`, identity ≤255 chars (`ocpp_server.py:51, 72-91`) | Workable: set the HMI URL to `ws://<host>:9000/ocpp/` and the ID as Charger ID. Exact URL the charger builds (trailing slash, encoding) is ⚠️ unconfirmed |
| **A5** | **MAJOR** | Yes (`tech-decisions.md`: dev mode allows no TLS/no auth; production profile open, NF-05) | ⚠️ `wss://` and authentication (Basic Auth or client cert) **unconfirmed — HIGH RISK**; charging data and commands cross public 4G; VPN/private APN is the fallback. Manual's default HMI password `77777777` lets anyone repoint the charger | `serve(...)` has no `ssl` argument; there is no config for certificates; identity in the URL is the **only** credential — no header/credential check anywhere in `_process_request` | Anyone who knows/guesses a provisioned identity can impersonate that charger. Acceptable in isolated dev, not for a real site. Acceptance items 11–12 (password changed, `wss://`) have no system-side support |
| **A6** | **MAJOR** | Yes (`future.md` #26 — remote start/stop deferred) | CSMS must **send** commands: `GetConfiguration` (first check), `ChangeConfiguration`, `RemoteStart/StopTransaction`, `ChangeAvailability`, `Reset`, `UnlockConnector`, `ClearCache`, `TriggerMessage`, `DataTransfer`, and optionally `SetChargingProfile`, `UpdateFirmware`, `GetDiagnostics`, `ReserveNow` | The gateway is **receive-only**: no `self.call(...)` anywhere, no command API, no registry of live connections (`ocpp_server.py:675-679` comment: "ConnectionRegistry … deferred") | Phase 1 of the spec's test plan (read `SupportedFeatureProfiles`, tune `MeterValueSampleInterval`, `TriggerMessage`) and the QR-scan/RemoteStart flow cannot run on this gateway. *Design implication:* the gateway is a separate OS process (`make charging-ocpp-dev`) from the API, so an HTTP-triggered command needs a cross-process channel to reach the socket |

### B. Data model and storage

| ID | Severity | Known? | Spec says | Current system | Consequence |
|---|---|---|---|---|---|
| **B1** | **MAJOR** | Yes (`future.md` #27: "raw OCPP payload auditing"; `config.py:188-192` has `CHARGING_MAX_RAW_PAYLOAD_BYTES` commented out) | **Mandatory rule:** store every OCPP message, both directions, verbatim, with received timestamp, **before parsing** — append-only; it's the only arbiter in billing/warranty disputes and the only way to see vendor deviations | Nothing stores raw messages. `charging_session_meter_values` docstring: "never the raw pre-normalization payload" (`charging_sessions/models.py:188-202`). Failed messages leave no trace except a log line | Cannot investigate a dispute, cannot see what the Willdigits charger really sent, cannot build the vendor-deviation notes the spec wants for the adapter layer |
| **B2** | **MAJOR** | **New** | `connectorId` **0 = whole charger**; `Faulted` on 0 means the entire charger is down, on 1/2 only one gun. Availability/alerting must distinguish them | Topology is Station → EVSE → Connector with `CHECK (ocpp_connector_id > 0)` and `CHECK (ocpp_evse_id > 0)` (`charging_stations/models.py:177, 241`). There is no row and no column for a charger-level state | Connector 0 status cannot be stored (and would raise `ChargingConnectorNotFoundError`). No way to represent "whole charger faulted/unavailable" |
| **B3** | **MAJOR** | **New** | 1.6 has **no EVSE level**: a dual-gun charger is `chargePoint → connector 1, 2 (+0)` | Every connector must hang under an EVSE with its own `ocpp_evse_id`; provisioning creates station → EVSE → connector (`simulator/seed_charging_topology.py:121-149`) | A mapping convention is needed (e.g. one EVSE per gun, or one shared EVSE) and a resolver from `connectorId` to `(evse, connector)`. Not decided anywhere |
| **B4** | **MAJOR** | Partly (F-C2 note: 2.0.1 has no "Charging" status) | Nine statuses; only `Available` = free; `Preparing`, `Charging`, `SuspendedEV`, `SuspendedEVSE`, `Finishing`, `Reserved`, `Unavailable` = not free; `Faulted` = not free **and alert**. App must show "charging" only on `Charging`, and must not alert on `SuspendedEV/EVSE` | Enum has 5 values (`Available, Occupied, Reserved, Unavailable, Faulted`); `Preparing/Charging/SuspendedEV/SuspendedEVSE/Finishing` all fold into `Occupied` (`charging_stations/types.py:29-43`) | Free-vs-busy semantics survive (Occupied ≠ free ✔). **Lost:** distinguishing `Preparing` from `Charging`, and `SuspendedEV` from `SuspendedEVSE`; spec §"Accepted ≠ charging" rule needs "Charging" precisely. A live "currently charging" view must join sessions (already noted in F-C2) |
| **B5** | **MAJOR** | **New** (item 28 defers metadata but not the error path) | `StatusNotification` carries `errorCode` (13 standard values listed in the manual, 16 in the standard enum) plus `vendorErrorCode` (80 vendor codes); spec has per-code backend actions (block gun on `GroundFailure`, stop billing on `PowerMeterFailure`, ticket on `InternalError`, …). Spec's `connector` table stores `errorCode`, `vendorErrorCode` | `charging_connectors` has only `status` + `status_updated_at`; no error columns; no alert on `Faulted`; `notification_type` has no charging types (`notifications/types.py:15-21`) | Fault details from the charger are discarded. No fault alerting, no auto-ticket, no suspect-session flag. (2.0.1 carries faults via `NotifyEvent`, not handled either.) The 80-code vendor mapping table is also still missing from the vendor (spec §6.2 #2) |
| **B6** | **MAJOR** | Partly (item 28 defers "manufacturer/model/serial/firmware" and connection status) | A charger registry: vendor, model, firmware, serial (baseline to detect a firmware swap), last boot, **online state**, last heartbeat | `charging_stations` has directory fields only (name, location, power, connector standard, hours, maintenance). No boot info, no firmware, no online flag, no heartbeat time | Cannot detect a swapped/updated firmware, cannot tell if a charger is online, cannot implement the spec's "derive availability from online state" |
| **B7** | **MAJOR** | Partly (`backend-charging-ingest-fixes.md` chose "energy register only") | `MeterValues` measurands: **mandatory** `Energy.Active.Import.Register`, `Power.Active.Import`, **`SoC`**; should-have `Voltage`, `Current.Import`, `Temperature`, `Power.Offered`; keep `context` (Transaction.Begin / Sample.Periodic / Transaction.End / Sample.Clock) — *"don't drop it"*; unit, location, phase | Only the cumulative energy register is kept, normalised to Wh, into `charging_session_meter_values(value_wh, sampled_at)`. All other measurands are skipped **silently** (`normalize_sampled_value_to_wh` returns `None`, `extract_meter_samples` `continue`s — `ocpp_server.py:184-186, 222-223`). No measurand/unit/context/phase/location columns | SoC (needed for the three-way reconciliation and driver display), power (ETA, load sharing check via `Power.Offered`), and temperature (over-temperature alerts) are discarded on arrival. Spec acceptance items 7 and 19 cannot be observed in the database. 🔎 Vendor-platform sample also carries non-standard `Voltage.Demand`/`Current.Demand` — currently harmless (skipped) but lost |
| **B8** | **MAJOR** | **New** | 1.6 `transactionId` is an **integer issued by the CSMS** in `StartTransaction.conf`; 🔎 the vendor platform also shows long ~20-digit and empty IDs (offline/vendor-mode records) | `ocpp_transaction_id` is a `String(255)` **supplied by the station** (2.0.1 semantics), unique per `(station_id, ocpp_transaction_id)` (`charging_sessions/models.py:89, 123-127`). Nothing allocates IDs | A 1.6 adapter must allocate an integer ID (durably, surviving CSMS restarts) and answer `StartTransaction.conf` with it. The string column can store the value, but the allocation/idempotency policy doesn't exist. Duplicate `Started` still raises a raw `IntegrityError` (`future.md` #66) |
| **B9** | **MAJOR** | Yes (`future.md` #26 & #62 — idToken/vehicle identity deferred) | Spec's `charge_session` records `idTag`, **VIN**, stop **reason**, `meterStart`, **`meterStop`**; `Authorize` is a Core message; VIN/Autocharge may shorten the payment flow | Session row: no `idTag`, no VIN/vehicle/driver, no stop reason (2.0.1 `stoppedReason` ignored), no authoritative `meterStop`. `Authorize` unhandled | Sessions can't be attributed to a vehicle/driver, so per-customer billing and NF-10 reconciliation stay impossible (`future.md` #62). Stop reason (e.g. `EmergencyStop`, `EVDisconnected`, `PowerLoss`, `Reboot`) is lost |
| **B10** | **MAJOR** | **New** | `StopTransaction.meterStop` is the authoritative closing register; billing validity hinges on `meterStart/meterStop` coming from a real meter; acceptance #10 compares `(meterStop − meterStart)` with the screen kWh and the `MeterValues` integral (< 1 %) | `meter_end_wh` = the **latest sample seen**, and `energy_delivered_wh = meter_end_wh − meter_start_wh` (`charging_sessions/service.py:168-171`); there is no separate `meterStop` and no place for the three-figure comparison. `StopTransaction.transactionData` not read | The stored "energy delivered" is a derived last-sample value, not the charger's own closing reading. The comparison the spec makes decisive (and 🔎 `meter_protocol = none` on the sample unit) can't be evaluated from stored data |
| **B11** | MINOR | Yes (`future.md` #26) | Spec: `tariff_version` per session, keep ≥ 5 years, never hard-code; 🔎 the charger has its own 30-min local price table | No tariff data at all (billing domain deferred) | Nothing to reconcile yet. Decision needed later on how the charger's local price relates to the platform tariff (displayed price must equal billed price) |
| **B12** | MINOR | Yes (`future.md` #27) | Spec: keep history of every `GetConfiguration` (`charge_point_config`) to detect tampering; record read-only keys | No configuration store; no capability schema (`future.md` #28) | `SupportedFeatureProfiles` result (the charger's real spec) has nowhere to live except a file in the repo, as the spec itself suggests |
| **B13** | MINOR | **New** | Sessions and raw log must be **append-only/immutable** (evidence for disputes) | Events and samples are append-only, FK `RESTRICT`. The session aggregate is **mutated in place** (`meter_end_wh`, `updated_at`, `status`, `ended_at`); nothing enforces immutability at DB level | Acceptable while there is no dispute process; note for when evidence handling is designed |
| **B14** | MINOR | **New** | Series specs: 240 kW total, **120 kW per gun** when both are in use; connector standard per gun (`CCS2` here; 🔎 sample units show `GBT`) | `power_rating_kw` and `connector_standard` are **station-level** single values (`charging_stations/models.py:112-115`); `GET /charging-stations/nearby` filters on them | Directory can't express per-gun power or a mixed-standard station; the nearby filter would show 240 kW where a second vehicle only gets ~120 kW. Fine for the MVP, visible in real data |

### C. Behaviour under real-world conditions (the spec's Phase 3 scenarios)

| ID | Severity | Known? | Spec scenario / expectation | What the current system does | Consequence |
|---|---|---|---|---|---|
| **C1** | **MAJOR** | Yes (`ocpp_server.py:462-466`: "surviving a reconnect is the reliability path — future.md 27") | *Network loss mid-session*: after reconnect the charger back-fills the records and `MeterValues` of the running transaction | The `EVSE → session` map (`_session_by_evse`) lives **in memory on one connection** (`ocpp_server.py:349, 467`); after reconnect it's empty → `KeyError` → `CALLERROR` for every `MeterValues` | Back-filled samples are rejected; the charger retries per `TransactionMessageAttempts` (spec target ≥ 3, ≥ 60 s) and may then drop them. The mapping could be rebuilt from the DB (1.6 `MeterValues` carries `transactionId`), but isn't |
| **C2** | **MAJOR** | Partly (`future.md` #27, #32) | *Long outage / offline back-fill*: charger pushes stored records when the network returns; acceptance #17 | Late samples older than the session's watermark are **discarded** from the aggregate (`service.py:154-167`); any event or sample for a `COMPLETED` session is **refused** (`:490-500, 563-564`) → `CALLERROR` | A back-filled record for an already-closed session is rejected and can be lost; a `Started` replay hits the raw `IntegrityError`. Offline capacity is unknown (⚠️), and NF-09 asks ≥ 48 h |
| **C3** | **MAJOR** | Yes (`future.md` #27) | *Reboot / `Reset` mid-session; unplug; emergency stop*: is the session closed, or orphaned? 🔎 The vendor platform itself shows sessions with no end time and no stop value | No timeout, sweeper, or heartbeat-based closure; a session stays `ACTIVE` until an `Ended` arrives | Orphaned `ACTIVE` sessions accumulate and stay in monitoring lists; F-C5 only counts `COMPLETED` so energy is under-reported for those |
| **C4** | **MAJOR** | **New** | *Wrong charger clock*: does an old record get a shifted/timezone-skewed timestamp? 🔎 HMI has a timezone selector (`UTC` on the sample); OCPP timestamps from chargers are often UTC without a timezone marker in the wild | `parse_ocpp_timestamp` **raises** if a timestamp has no timezone (`ocpp_server.py:133-151`) → the whole message becomes a `CALLERROR` | If the Willdigits firmware omits an offset the entire `MeterValues`/`StatusNotification` is rejected. ⚠️ Unknown until real logs; clock sync via `BootNotification.currentTime` (A2) is also missing |
| **C5** | **MAJOR** | Yes (`future.md` #37, #49) | An **offline** charger must not show stale status; 🔎 the vendor platform demonstrates the trap (offline charger still shows `Faulted` for 0/1/2) | Connector status is written on `StatusNotification` and **never invalidated**; no disconnect handling, no heartbeat timeout, no online flag | Status and any availability derived from it stays stale after a disconnect. `nearby` search doesn't use connector status at all (`future.md` #49) |
| **C6** | MINOR | Yes (`future.md` #31) | Handler failures should be handled deliberately; keep the connection | Any handler exception → library-default `CALLERROR(InternalError)`, connection kept; DB failure at handshake isn't mapped to `503`; only a warning log | Consistent with the current MVP; for a real charger, repeated `CALLERROR`s (retries) with no metrics make it hard to see |
| **C7** | MINOR | Yes (`future.md` #32, #66) | Charger may re-send the same transaction / event | Duplicate `Started` → raw `IntegrityError`; any post-`Ended` event refused; `seq_no` stored but not used | See C2. In 1.6 the CSMS chooses the ID, which removes most reuse risk if the ID is allocated durably (B8) |
| **C8** | MINOR | **New** | Spec: silent drops are dangerous ("an uninterpretable register reading must fail loudly" is also this repo's own rule) | Unknown *unit* on the energy register raises (good); unknown *measurands* are skipped without any log/metric | Vendor-specific measurands (🔎 `Voltage.Demand`, `Current.Demand`) will vanish without a trace; there is no counter of "skipped samples" to show the extent |

### D. Product features that lean on the spec

| Feature | Spec dependency | Repo status today | Effect of the mismatch |
|---|---|---|---|
| **F-G2** OCPP integration | 1.6J minimum, 2.0.1-ready; NF-05 transport encryption | 🚧 2.0.1 only; 1.6J "not implemented at all"; security profile open | Direct: A1–A6 |
| **F-C2** real-time connector status | connectorId 0/1/2; nine statuses; ≤ 30 s | ✅ Done (2.0.1) | Handler unusable for 1.6; loses connector 0 and status granularity (B2, B4); no online invalidation (C5); 30 s latency unmeasured |
| **F-B2** session logging | Start/Stop/MeterValues, `transactionId`, `meterStart/meterStop` | ✅ Done (2.0.1 happy path) | Needs a 1.6 mapping (B3, B8, B10) and the reliability path (C1–C3) |
| **F-C6** per-customer energy / NF-10 3-way reconciliation | `SoC` measurand, `meterStop`, meter authenticity, < 1 % | ✅ Partial (telemetry-based; NF-10 not met — item 62) | Still blocked: no vehicle identity on sessions (B9), no SoC stored (B7), no `meterStop` (B10) |
| **F-C3** queue / wait-time | Free-gun counts from statuses (Finishing busy, Suspended not free) | 📋 Planned | Needs B2/B4 resolved to count correctly |
| **F-C4** reservation | Reservation profile ⚠️ unconfirmed | 📋 Planned | No command channel (A6); "soft" fallback if the profile is absent |
| **F-C7** load forecast / balancing | Smart Charging profile ⚠️ **highest priority**, unconfirmed; `Power.Offered` | 📋 Planned, domain unassigned | No command channel (A6), no power storage (B7). Spec fallback is soft steering |
| **F-C8 / NF-19** dynamic pricing | Tariff versioning per session; 🔎 local price table on the charger | 📋 Planned (billing) | B11; note the spec doc attributes "time-slot power limiting" to F-C8 while the repo defines F-C8 as pricing |
| **F-H1** in-app payment (QR → RemoteStart) | `RemoteStartTransaction`; "Accepted ≠ charging" | 📋 Planned | A6; needs statuses to distinguish `Charging` (B4) |
| **F-H3** e-invoicing | Verified DC meter; `meterStop` | 📋 Planned | Legal basis is a spec-level unknown (meter verification), plus B10 |
| **F-I2** SOS | `UnlockConnector` reduces tickets | ✅ Done (case intake only) | No link to gun unlock (A6) |
| **F-J1 / F-J2** device health / remote config | Charger heartbeat / firmware / `ChangeConfiguration` | ✅ Done for **vehicle telematics only** | Nothing for chargers (B6, A6); F-J2's MQTT command path isn't reusable for OCPP |
| **F-D5** offline mode | Charger offline buffer capacity ⚠️ unknown | ⚪ N/A backend | Related NF-09 buffer capacity is a hardware unknown (see C2) |
| **F-D1 / F-A2** nearby / nearest station | "Available" = connector `Available` | ✅ Done, using `maintenance_status` only | Known approximation (`future.md` #37, #49); connector 0/online state make it worse for real chargers (C5) |

### E. Tooling, tests, infrastructure

| ID | Severity | Known? | Spec expectation | Current system | Consequence |
|---|---|---|---|---|---|
| **E1** | **MAJOR** | **New** | Phase 0: a test **CSMS** (SteVe or self-written) + an OCPP **1.6J** charge-point **simulator**, raw-log writer and schema, public endpoint with `ws://` and `wss://`, ready **before** the charger arrives | Simulator speaks **2.0.1 only** (`simulator/charging_session_simulator.py:23`, `TransactionEvent`, single Wh sample); no 1.6 tooling; no TLS endpoint; docker-compose has only `db` and `broker` | The spec's "start now, no hardware needed" phase can't be executed with existing tools |
| **E2** | MINOR | **New** | 20-item acceptance checklist to re-run for every charger, with scripts pre-written | No acceptance scripts; OCPP tests only cover pure parsers (`tests/test_schema_smoke.py`) plus service smoke tests; no handshake or handler-level test | Nothing to prove A1–A3 behaviour automatically |
| **E3** | MINOR | **New** | Public, stable CSMS address; TLS cert; (fallback) VPN/private APN | Gateway binds `0.0.0.0:9000`, no reverse proxy by decision (`tech-decisions.md`) | Infra decisions for a real site aren't started (already noted as "reconsider when building prod compose") |

### F. Already aligned (reusable)

| ID | Spec point | Current system |
|---|---|---|
| OK1 | `chargePointId` is the identity key; fix naming before install; unique | `ocpp_identity` unique, not reusable after soft-delete; station must be **pre-provisioned** and unknown identities are rejected at handshake |
| OK2 | Charger opens the WebSocket to the CSMS; CSMS needs a public stable address | Gateway is a WebSocket **server** the charger dials into |
| OK3 | Default energy measurand = `Energy.Active.Import.Register`, default unit **Wh**, `kWh` also seen | `normalize_sampled_value_to_wh` uses the same defaults (for 2.0.1 shape) and rejects unknown energy units loudly |
| OK4 | Unknown/vendor measurands must not break ingestion | Non-energy measurands are skipped, not errors (tolerant, though silent — see C8) |
| OK5 | `Finishing` is busy; `SuspendedEV/EVSE` are normal, not free; `Faulted` not free | All map to `Occupied`/`Faulted` — busy-vs-free is preserved (granularity lost, B4) |
| OK6 | Adapter layer per vendor; core stays protocol-neutral | `charging_stations/ocpp` is a thin adapter calling the transport-agnostic public services (`ingest_transaction_event`, `ingest_meter_values`, `update_connector_status`) — a 1.6 adapter can reuse them |
| OK7 | No new dependency needed | `ocpp` 2.1.0 already installed and ships `v16` (enums incl. `SoC`, `Power.Offered`, all 9 statuses, 16 error codes) |
| OK8 | UTC everywhere; append-only history | Timestamps normalised to UTC; events and samples are append-only |
| OK9 | Session must not be silently rewritten after it ends | `COMPLETED` is terminal; stale samples never overwrite a newer reading (F-B2) |
| OK10 | Station power up to 480 kW | `power_rating_kw` up to 9999.99, `Numeric(6,2)` |

---

## 4. Decisions this comparison surfaces (need an owner)

1. **Support OCPP 1.6J.** Add it as a second protocol adapter alongside 2.0.1 (recommended by the spec's "adapter per vendor" principle), or replace 2.0.1? The open feature-list item #11 is now effectively answered by the hardware. This also touches `tech-decisions.md`, `CLAUDE.md`'s domain notes and F-G2's status wording.
2. **EVSE mapping for 1.6J.** One EVSE per gun vs. one EVSE holding both guns; and how to represent connector `0` (whole charger) — a charger-level status on `charging_stations`, or an allowed `0` connector.
3. **Transaction ID allocation.** Who issues the integer `transactionId`, how to keep it durable and idempotent, and what to do with vendor-mode/offline long or empty IDs.
4. **What to store from `MeterValues`.** At minimum `SoC` and power; whether to keep every measurand with unit/context (spec: keep `context`), and where the raw payload goes (B1).
5. **Charger-level model.** Boot info, online state, last heartbeat, and how a disconnect invalidates connector status (B6, C5).
6. **Error/fault handling.** Store `errorCode`/`vendorErrorCode`; which faults raise notifications, which block a gun, and what "suspect session" means for billing (B5). Needs the vendor's 80-code mapping first.
7. **Security profile for real devices.** `wss://` + Basic Auth/client cert vs. VPN/private APN, and per-charger credentials (A5). Depends on the vendor's answer (spec §6.2 #4).
8. **Command channel.** How an HTTP request reaches a socket held by a separate process (A6); prerequisite for RemoteStart/Stop, `GetConfiguration`, `ChangeConfiguration`, `Reset`, `UnlockConnector`.
9. **Reliability path scope.** Which pieces of `future.md` #27 (reconnect-safe mapping, offline back-fill, orphan cleanup) become mandatory now that a real charger exists (C1–C3).
10. **Business-side unknowns from the spec** that affect scope but not code: Smart Charging absent → F-C7 becomes soft steering; unverified DC meter → no kWh invoicing / service-fee model; truck inlet standard (CCS2 vs GB/T).

---

## 5. Suggested order of work (for discussion, not started)

Repo conventions require confirmation before adding components or changing decisions, so this is an ordering proposal only.

1. **Foundation for a first real connection** — decisions 1–3; a 1.6J handshake + `BootNotification` (Accepted, `currentTime`) + `Heartbeat` + `StatusNotification` + `Start/StopTransaction` + `MeterValues` path; **raw message log** (B1). This unlocks the spec's Phase 1 and gives the real logs the spec insists on.
2. **A 1.6J simulator and handler tests** (E1, E2), so Phase 0 can run before the hardware arrives, including a `kWh` case that guards the §2.2 hazard.
3. **Data that the reconciliation needs** — store `SoC`/power/voltage/current/temperature/`Power.Offered` with context; store `meterStop` and stop reason; store `errorCode`/`vendorErrorCode` (B5, B7, B9, B10).
4. **Charger registry and status hygiene** — boot info, online/heartbeat, invalidate status on disconnect (B2, B6, C5).
5. **Reliability for the Phase-3 scenarios** — rebuild the transaction mapping from the DB, tolerate back-filled records, close orphaned sessions (C1–C3).
6. **Command channel and remote operations** (A6), then security hardening (A5) as vendor answers arrive.

---

## Appendix A — Message coverage matrix (spec Core + optional messages)

"Handled" means an `@on(...)` handler exists in the current gateway; every 1.6 message is unhandled because the gateway is 2.0.1 only.

| Spec (OCPP 1.6J) | Direction | Current gateway | Nearest 2.0.1 equivalent in the gateway |
|---|---|---|---|
| BootNotification | CP→CSMS | ❌ | none (2.0.1 `BootNotification` also unhandled) |
| Heartbeat | CP→CSMS | ❌ | none |
| StatusNotification | CP→CSMS | ❌ for 1.6 shape | `StatusNotification` (2.0.1 shape, 5 statuses, no error code) — handled |
| Authorize | CP→CSMS | ❌ | none (`idToken` absorbed by `**_`) |
| StartTransaction | CP→CSMS | ❌ | `TransactionEvent(Started)` — handled |
| StopTransaction | CP→CSMS | ❌ | `TransactionEvent(Ended)` — handled |
| MeterValues | CP→CSMS | ❌ for 1.6 shape | `MeterValues(evse_id, …)` — handled, energy register only |
| RemoteStartTransaction / RemoteStopTransaction | CSMS→CP | ❌ (no outbound calls) | none |
| GetConfiguration / ChangeConfiguration | CSMS→CP | ❌ | none |
| ChangeAvailability / Reset / UnlockConnector / ClearCache | CSMS→CP | ❌ | none |
| DataTransfer | both | ❌ | none |
| Optional: SetChargingProfile, ClearChargingProfile, GetCompositeSchedule | CSMS→CP | ❌ | none |
| Optional: TriggerMessage | CSMS→CP | ❌ | none |
| Optional: UpdateFirmware, GetDiagnostics, FirmwareStatusNotification | mixed | ❌ | none |
| Optional: SendLocalList, GetLocalListVersion | CSMS→CP | ❌ | none |
| Optional: ReserveNow, CancelReservation | CSMS→CP | ❌ | none |

## Appendix B — Connector status mapping

| OCPP 1.6J status | Spec: free? | Current enum (`ChargingConnectorStatus`) | Free preserved? | Information lost |
|---|---|---|---|---|
| Available | Yes | `Available` | ✔ | — |
| Preparing | No | `Occupied` | ✔ | Plugged-in vs delivering |
| Charging | No | `Occupied` | ✔ | "Actually delivering power" (needed for "charging" indicator) |
| SuspendedEV | No (normal) | `Occupied` | ✔ | Vehicle-side pause vs charger-side |
| SuspendedEVSE | No (normal) | `Occupied` | ✔ | Charger load-sharing pause |
| Finishing | No | `Occupied` | ✔ | Session ended, gun still plugged |
| Reserved | No | `Reserved` | ✔ | — |
| Unavailable | No | `Unavailable` | ✔ | — |
| Faulted | No + alert | `Faulted` | ✔ | `errorCode`, `vendorErrorCode`, `info`; scope (connector 0 vs 1/2) |

## Appendix C — Measurand coverage

| Measurand | Spec level | Stored today? | Note |
|---|---|---|---|
| `Energy.Active.Import.Register` | Mandatory | ✅ as Wh only (2.0.1 shape) | Parser reads `unit_of_measure`; 1.6 uses `unit` (§2.2) |
| `Power.Active.Import` | Mandatory | ❌ skipped | |
| `SoC` | Mandatory | ❌ skipped | Needed for 3-way reconciliation. 🔎 treat SoC 0 at start as "unknown" |
| `Voltage` | Should | ❌ skipped | |
| `Current.Import` | Should | ❌ skipped | |
| `Temperature` | Should | ❌ skipped | |
| `Power.Offered` | Should | ❌ skipped | Dual-gun load-sharing check |
| 🔎 `Voltage.Demand`, `Current.Demand`, `Voltage.Import` (vendor-platform labels) | — | ❌ skipped | Not standard 1.6 names; tolerate |
| `context` (Transaction.Begin/Sample.Periodic/Transaction.End/Sample.Clock), `phase`, `location`, `unit` | Keep `context` | ❌ not stored | |

## Appendix D — Acceptance checklist vs. what the current system can even observe

Mapping of the spec's 20 acceptance items to whether the *current system* can execute or record the check.

| # | Item | Blocker in spec | Current system can… |
|---|---|---|---|
| 1 | WebSocket stable ≥ 24 h | ✔ | ❌ charger can't connect (A1); no connection-duration record |
| 2 | BootNotification (vendor/model/firmware/serial) | ✔ | ❌ not handled/stored (A2, B6) |
| 3 | GetConfiguration incl. `SupportedFeatureProfiles` | ✔ | ❌ no outbound commands (A6) |
| 4 | StatusNotification for 0/1/2, ≤ 30 s | ✔ | ❌ 1.6 shape and connector 0 unsupported (A3, B2); latency not measured |
| 5 | MeterValues ≤ 30 s, stable | ✔ | ❌ 1.6 shape; cannot change the interval (A6) |
| 6 | Energy measurand present with unit | ✔ | ⚠️ partly — energy stored, unit dropped, 1.6 unit mis-parsed (§2.2) |
| 7 | `SoC` present | ✔ | ❌ discarded (B7) |
| 8 | RemoteStart | ✔ | ❌ (A6) |
| 9 | RemoteStop | ✔ | ❌ (A6) |
| 10 | kWh agreement < 1 % (3 sources) | ✔ | ❌ no `meterStop`, no power (B7, B10) |
| 11 | HMI password changed | ✔ | n/a (site procedure) — nothing in the system records it |
| 12 | `wss://` | | ❌ (A5) |
| 13 | Smart Charging limits power | | ❌ (A6) |
| 14 | Remote Trigger works | | ❌ (A6) |
| 15 | GetDiagnostics from remote | | ❌ (A6) |
| 16 | ReserveNow locks the gun | | ❌ (A6) |
| 17 | Offline back-fill after 30 min | | ❌ back-filled data rejected (C1, C2) |
| 18 | Unplug mid-session → session closed with clear reason | | ⚠️ closes on `Ended`; reason not stored (B9); orphan risk (C3) |
| 19 | Dual-gun load sharing via `Power.Offered` | | ❌ (B7) |
| 20 | Every `vendorErrorCode` mapped | | ❌ not stored (B5); vendor table not delivered |

## Appendix E — Evidence index

| Topic | Location |
|---|---|
| Subprotocol constant, handshake rejection | `backend/app/domains/charging_stations/ocpp/ocpp_server.py:50, 293-309, 616-654` |
| `serve(...)` — no TLS, no auth | `ocpp_server.py:589-597` |
| Registered handlers | `ocpp_server.py:351` (`TransactionEvent`), `:438` (`MeterValues`), `:476` (`StatusNotification`) |
| In-memory EVSE→session map | `ocpp_server.py:349, 467` |
| Meter normalisation / unit read | `ocpp_server.py:154-225` (unit read at `:188`) |
| Timestamp parsing | `ocpp_server.py:133-151` |
| Topology models and `> 0` checks | `backend/app/domains/charging_stations/models.py:139-250` (`:177`, `:241`) |
| Connector status enum (5 values) | `backend/app/domains/charging_stations/types.py:29-43` |
| Session model / transaction ID / unique key | `backend/app/domains/charging_sessions/models.py:42-134` |
| Meter sample table | `backend/app/domains/charging_sessions/models.py:188-228` |
| Watermark, COMPLETED guards | `backend/app/domains/charging_sessions/service.py:124-172, 490-500, 563-564` |
| Notification types (no charging alert types) | `backend/app/domains/notifications/types.py:15-21` |
| Commented-out OCPP settings | `backend/app/libs/common/config.py:188-192` |
| 2.0.1 simulator | `simulator/charging_session_simulator.py:23` |
| Known deferrals | `docs/01-requirements/future.md` items 26, 27, 28, 31, 32, 37, 49, 62, 66; feature-list F-G2 + "Items needing confirmation" #11 |

**Not verified / open.** Everything about how the Willdigits firmware actually behaves (which profiles, whether `BootNotification` is mandatory to proceed, timestamp format, transaction-ID form, `wss://`) is ⚠️/🔎 in the spec and remains so; the statements here about vendor behaviour are the spec's, not new measurements.
