# Planner: OCPP 1.6J Integration for the Willdigits DC Charger (F-G2, F-C2, F-B2)

> Feature code: F-G2 (Charging station integration), F-C2 (Real-time
> connector status), F-B2 (Charging-session logging) — extends all three
> from OCPP 2.0.1-only to OCPP 1.6J as well
> Status: 🚧 In progress — Step 0 **done** and Step 1 (raw OCPP message log)
> **done** on 2026-09-24; Step 2 (version-aware gateway) is next
> Created: 2026-09-24
>
> Inputs:
> [`charging-station-specification-summary.md`](../03-specifications/charging-station-specification-summary.md)
> (what the device is/does) and
> [`charging-station-spec-vs-current-system.md`](../04-responses/charging-station-spec-vs-current-system.md)
> (31 mismatches, IDs `A1…E3` are referenced throughout this planner)

## 1. Goal

Make the backend able to **connect a real Willdigits charger, accept its
OCPP 1.6J traffic, and store the data the spec calls mandatory**, without
breaking the existing OCPP 2.0.1 path.

Concretely, after this planner:

1. A charger offering `ocpp1.6` connects instead of being rejected with HTTP
   426 (mismatch A1).
2. Every OCPP frame in both directions is stored verbatim (B1).
3. The gateway answers `BootNotification`, `Heartbeat`, `Authorize`,
   `StatusNotification`, `StartTransaction`, `StopTransaction`,
   `MeterValues` (A2, A3), including connector `0`, error codes, boot info
   and an online signal (B2–B6).
4. Sessions carry `idTag`, stop reason and the authoritative `meterStop`
   (B9, B10); `SoC`, power, voltage, current, temperature and
   `Power.Offered` are stored (B7); the 1.6 unit-handling hazard (§2.2 of the
   comparison — a `kWh` sample stored as Wh) cannot occur (B7).
5. The transaction ID is allocated by the CSMS as 1.6J requires (B8) and a
   `MeterValues` message finds its session from the database, not from
   per-connection memory (C1 for 1.6).
6. The charger's real configuration (`SupportedFeatureProfiles` etc.) is
   captured automatically after each boot (B12, part of A6).
7. A 1.6J simulator exists so all of this is testable before the hardware
   arrives (E1, E2).

**Not the goal** (see §6): TLS/authentication, remote commands from the
API, fault alerting, offline/orphan-session handling, real `Authorize`
validation, billing. Those stay deferred; several need vendor answers or
real-charger logs first.

## 2. Scope decisions

> **All 14 decisions were confirmed on 2026-09-24 (Step 0).** D4 and D9 differ
> from the original recommendation (the user chose the wider status enum and a
> single measurements table); the rows record both the choice and the
> alternative that was rejected, so a later reviewer can revisit it cheaply.

| # | Decision | Rejected alternative → why |
|---|---|---|
| **D1** | ✅ *Confirmed 2026-09-24.* **Add OCPP 1.6J alongside 2.0.1**; keep the 2.0.1 path working | Replace 2.0.1 with 1.6J → throws away tested code and contradicts F-G2's "1.6J minimum, **2.0.1-ready**" |
| **D2** | ✅ *Confirmed 2026-09-24.* **One adapter class per protocol**, chosen by the negotiated subprotocol; the 1.6J adapter lives in its own module | Extend `OCPPChargePoint` with 1.6 handlers → the two protocols have incompatible payload shapes (no EVSE, no `TransactionEvent`, flat `unit`); mixing them in one class hides which contract each handler assumes (same lesson as F-B2 Step 0) |
| **D3** | ✅ *Confirmed 2026-09-24.* **Topology mapping for 1.6J:** connector `n ≥ 1` → EVSE `n`, connector `1`; connector `0` is charger-level state stored on `charging_stations` (no topology row, keeps the `> 0` CHECK constraints) | One EVSE holding both guns → cannot tell guns apart by EVSE alone; allow connector `0` in the topology tables → breaks the deliberate `> 0` invariant and pollutes connector counts (`connector_count`, F-D1) |
| **D4** | ✅ *Confirmed 2026-09-24 (the wider enum was chosen over the original recommendation).* **Widen `ChargingConnectorStatus` to carry 1.6J's nine statuses**, keeping the existing 2.0.1 `Occupied` (the 2.0.1 gateway still writes it) — so **10 values**: `Available, Occupied, Reserved, Unavailable, Faulted` + `Preparing, Charging, SuspendedEV, SuspendedEVSE, Finishing`. Error fields are added beside `status`; there is **no** second status column. Consequences: "busy" = **any value other than `Available`** (and not `Reserved`/`Unavailable`/`Faulted`); API consumers now see the extra values for 1.6 stations; F-D1/F-A2 don't read connector status today (`future.md` #49), so nothing regresses, but #49 must use this busy rule when it is built | Original recommendation — keep the 5-value canonical status and add a raw-label column → would keep 2.0.1 and 1.6 stations looking identical to readers; keep only 5 values → loses `Preparing`/`Charging`/`Suspended*` and the error codes (B4) |
| **D5** | ✅ *Confirmed 2026-09-24.* **Charger-level state as nullable columns on `charging_stations`** (device info, `last_seen_at`, connector-0 status). `is_online` is **derived at read time** from `last_seen_at`; no background sweeper | A `charge_point` table → one row per station is a 1:1 split with no benefit; a sweeper worker → this is the deferred reliability path (`future.md` #27) and unnecessary for a derived flag |
| **D6** | ✅ *Confirmed 2026-09-24.* **The CSMS allocates the 1.6 `transactionId` from a PostgreSQL sequence** (integer range) and stores it as the existing `ocpp_transaction_id` string | Timestamp/UUID-derived IDs → 1.6 requires an integer; in-memory counter → lost on restart, reused after restart |
| **D7** | ✅ *Confirmed 2026-09-24.* **`Authorize` and `StartTransaction` accept every `idTag`** in this planner; the `idTag` is stored on the session. Real validation stays in `future.md` #26/#62 | Reject unknown tags → there is no tag registry to check against and the vendor's Autocharge/VIN behaviour is unknown; would block all charging |
| **D8** | ✅ *Confirmed 2026-09-24.* **Raw log = its own hypertable, verbatim frame as `TEXT`**, written **by a wrapper around the WebSocket connection**, in **its own transaction, before the frame is parsed** (inbound) / after it is sent (outbound). Read via SQL only — no API | Log inside each handler → misses unparseable/unhandled frames (exactly the ones the spec wants); `JSONB` → not verbatim; an API → nothing needs it yet |
| **D9** | ✅ *Confirmed 2026-09-24 (a single table was chosen over the original recommendation, and the follow-up chose a **full merge**).* **One table, `charging_session_measurements`, holds every measurand for both protocols — including the energy register.** The old `charging_session_meter_values` is **replaced**: its rows are copied across and the table is dropped; the 2.0.1 gateway's energy writer switches to the new table with **unchanged behaviour**; the `/meter-values` API keeps its contract as a filter on the energy measurand. The session aggregate (`meter_end_wh`, `energy_delivered_wh`) is still updated by the service, not derived from this table | Original recommendation — energy stays in the old table and only other measurands go to a new one → two places for measurements; 1.6J-only new table with the old one kept for 2.0.1 → energy history split by protocol and `/meter-values` empty for 1.6J sessions |
| **D10** | ✅ *Confirmed 2026-09-24.* **The only CSMS-initiated call is a post-boot `GetConfiguration`** (no key), sent from a separate task. No HTTP→charger command channel now | Build the command channel now → cross-process design (the gateway is a separate OS process) that needs its own decision (`future.md` new item); Phase 1's other probes (`ChangeConfiguration`, `TriggerMessage`) can use an external OCPP test tool (the handover doc suggests SteVe) |
| **D11** | ✅ *Confirmed 2026-09-24.* **Keep the strict timestamp rule** (must carry a timezone) for now; revisit only with real-charger logs (Step 10) | Accept naive timestamps as UTC now → guessing at vendor behaviour; the raw log (Step 1) makes the real behaviour visible first |
| **D12** | ✅ *Confirmed 2026-09-24.* **No TLS/authentication change** in this planner (dev mode per `tech-decisions.md`) | Add `wss://` + Basic Auth now → the vendor has not said what it supports (spec §6.2 #4); implementing the wrong scheme wastes the work |
| **D13** | ✅ *Confirmed 2026-09-24.* **2.0.1 handlers are not changed** except for (a) the raw-log wrapper, (b) negotiation, (c) `last_seen_at`/protocol version updates, (d) its energy samples are written to the unified measurements table (D9; behaviour identical) | Give 2.0.1 the new fields too → no 2.0.1 hardware exists; recorded as parity work in `future.md` |
| **D14** | ✅ *Confirmed 2026-09-24.* **Reliability (`future.md` #27) is not reopened wholesale.** The 1.6 path avoids the in-memory session map by design (looks the session up by `transactionId`); orphan-session, back-fill and clock policies wait for real logs | Build reconnect/back-fill/orphan cleanup speculatively → the spec says to replace assumptions with real logs before fixing design |

### 2.1 Convention notes that shape the steps

- **Domain boundaries:** no new edge. `charging_stations` (the OCPP adapter)
  keeps calling `charging_sessions`' **public `service.py`**;
  `charging_sessions` never calls back. New public functions in
  `charging_sessions` return frozen dataclasses defined in its `types.py`.
- **One transaction per OCPP message**, owned by the gateway (entry
  boundary). `service.py`/`repository.py` never commit. The raw-log wrapper
  is a separate boundary and owns **its own** transaction, so a handler
  rollback never erases the evidence of what arrived.
- **No new dependency.** `ocpp` 2.1.0 already ships `ocpp.v16`; `websockets`
  already exists.
- **No batching.** Per-sample inserts, following
  `backend-runtime-conventions.md` ("Query batching").
- **Migrations are immutable** once merged; each step that changes the schema
  adds its own revision (head today is `0020_fleet`; numbers below assume
  nothing else lands first). Revision strings ≤ 32 characters. Nullable
  columns have no server default, following `0012`/`0017`.
- **No placeholders:** anything skipped is recorded in `future.md` (§6), not
  left as a TODO.

### 2.2 Library facts this design depends on (verified 2026-09-24)

- `python-ocpp`'s receive loop is **sequential** (`start()` awaits
  `route_message`, which awaits the handler). A handler that `await`s its own
  `self.call(...)` therefore **deadlocks until the response timeout**, because
  the response frame can't be read while the handler is still running. Any
  CSMS-initiated call must be scheduled as a **separate task** (Step 8).
- An action with no handler is answered with `CALLERROR NotImplemented`.
- 1.6 nested objects arrive as snake_cased plain dicts, like 2.0.1 (F-B2
  Step 0), and 1.6 `SampledValue` has a flat `unit` (not `unit_of_measure`).
- 1.6 schema: `BootNotification` requires only `chargePointVendor` and
  `chargePointModel`; `StatusNotification.timestamp` is optional;
  `MeterValues.transactionId` is optional; `StopTransaction.reason`,
  `idTag`, `transactionData` are optional.

## 3. Target state

### 3.1 Data flow

```text
Willdigits charger ──ws (ocpp1.6)──▶ OCPP gateway (charging_stations/ocpp)
                                        │  RecordingConnection (raw log, own txn)
                                        ├─▶ OCPP16ChargePoint ─┐
Simulators ──ws (ocpp2.0.1)──▶          └─▶ OCPP201ChargePoint ─┤ one txn per message
                                                               ▼
                       charging_stations service      charging_sessions service
                       (station/boot/status/config)   (sessions/meter/measurements)
```

### 3.2 Schema delta

| Table | Change | Step / migration |
|---|---|---|
| `charging_ocpp_messages` **(new, hypertable)** | `message_id`, `occurred_at`, `station_id`, `ocpp_subprotocol`, `direction`, `raw_frame TEXT` | 1 / `0021_charging_ocpp_raw_log` |
| `charging_stations` | + `ocpp_protocol_version`, `vendor`, `model`, `serial_number`, `firmware_version`, `last_boot_at`, `last_seen_at` | 4 / `0022_charging_station_device` |
| `charging_stations` | + `charger_status`, `charger_status_updated_at`, `charger_error_code`, `charger_vendor_error_code` (connector 0) | 5 / `0023_charging_status_details` |
| `charging_connectors` | `status` enum gains 5 values (`Preparing`, `Charging`, `SuspendedEV`, `SuspendedEVSE`, `Finishing`); + `error_code`, `vendor_error_code`, `status_info` | 5 / `0023_charging_status_details` |
| `charging_sessions` | + `id_tag`, `stop_reason`, `meter_stop_wh`; new integer sequence for 1.6 transaction IDs | 6 / `0024_charging_session_fields` |
| `charging_session_measurements` **(new, hypertable)** | `measurement_id`, `sampled_at`, `session_id`, `measurand`, `value`, `unit`, `context`, `phase`, `location` | 7a / `0025_charging_measurements` |
| `charging_session_meter_values` **(dropped)** | rows copied into `charging_session_measurements` as `Energy.Active.Import.Register`, unit `Wh` | 7a / `0025_charging_measurements` |
| `charging_station_configuration_entries` **(new)** | `entry_id`, `station_id`, `capture_id`, `captured_at`, `config_key`, `value`, `is_readonly` | 8 / `0026_charging_config_snapshots` |

After this planner the database has **four** hypertables (telemetry, session
events, **raw OCPP messages, measurements**); measurements *replaces* the
former session meter-values hypertable.

### 3.3 OCPP 1.6J coverage after this planner

| Message | Direction | After this planner |
|---|---|---|
| BootNotification | CP→CSMS | ✅ Step 4 (`Accepted`, `currentTime`, `interval`) |
| Heartbeat | CP→CSMS | ✅ Step 4 |
| StatusNotification | CP→CSMS | ✅ Step 5 (connectors 0/1/2, error codes) |
| Authorize | CP→CSMS | ✅ Step 6 (accept-all, D7) |
| StartTransaction / StopTransaction | CP→CSMS | ✅ Step 6 |
| MeterValues | CP→CSMS | ✅ Step 7b (+ `StopTransaction.transactionData`) |
| GetConfiguration | CSMS→CP | ✅ Step 8 (post-boot, automatic only) |
| Everything else (`RemoteStart/Stop`, `ChangeConfiguration`, `TriggerMessage`, `Reset`, `UnlockConnector`, `ChangeAvailability`, Smart Charging, Firmware, Reservation, `DataTransfer`, `FirmwareStatusNotification`…) | both | ⛔ deferred (§6); inbound ones get `CALLERROR NotImplemented` and are visible in the raw log |

### 3.4 Milestones

| Milestone | After | What it proves |
|---|---|---|
| **M1 — "charger connects and is recorded"** | Step 4 | The sample charger (if it is on site) can connect, is answered `Accepted`, and **every frame it sends is in the raw log**. Step 10 Phase 1 can already start; the findings feed Steps 5–8 |
| **M2 — "a full session is stored"** | Step 7b | Status, session, meter and measurements from a real or simulated session |
| **M3 — "charger's real spec is captured"** | Step 8 | `SupportedFeatureProfiles` and every configuration key stored per boot |

## 4. Implementation order

Each step lists **Goal/scope**, **Contract/decisions**, a **Prompt**, **Main
files**, **Checks** (static / smoke / integration, following the template in
`backend-telemetry-ingestion.md`) and an **Actual result** to fill in when
the step is done. A step is finished only when its checks pass; the
deferred pieces it creates must be recorded in `future.md` in the same change.

### Step 0 — Confirm scope and decisions (documentation only)

**Goal/scope:** Get an explicit yes/no/alternative on D1–D14. Lock the
contract in writing so later steps don't reopen it. No source change.

**Prompt:**

```text
Read CLAUDE.md, the OCPP spec summary, the spec-vs-system comparison,
backend-charging-mvp-ideal.md, backend-charging-ingest-fixes.md, and
future.md items 26, 27, 28, 31, 32, 37, 49, 62, 66.

Walk through decisions D1-D14 in this planner with me. For each one that I
change, update this planner (section 2 and every step that depends on it).
Then:
- update tech-decisions.md so it says which OCPP versions are supported;
- update the F-G2 status line in feature-list.md (1.6J now planned, not
  "blocked on a procurement decision");
- record the vendor requests from the spec summary (section 6.2) in
  open-questions.md;
- add the new deferred items listed in section 6 to future.md (numbered
  after the current last item).
Do not change active logic.
```

**Checks:**

- [x] Every decision D1–D14 is marked *confirmed* or replaced in §2 (done 2026-09-24)
- [x] `tech-decisions.md`, `feature-list.md` (F-G2, plus a note on "Items needing confirmation" #11), `open-questions.md` (new items 4 and 5) updated (2026-09-24)
- [x] §6's new deferred items exist in `future.md` (#73–#80) with description, purpose, reason, related planner, date
- [x] No source/migration/config file changed

**Actual result (completed 2026-09-24):** Step 0 is done; no source, migration
or config file was touched.

- All 14 decisions were confirmed one at a time. Twelve matched the original
  recommendation. Two were changed by the user: **D4** (widen the connector
  status enum to the nine 1.6J statuses instead of keeping five + a raw
  label — `Occupied` is kept for 2.0.1, so ten values) and **D9** (one
  `charging_session_measurements` table for everything, with a **full merge**
  that replaces `charging_session_meter_values`). D9's merge made Step 7 split
  into 7a (behaviour-neutral refactor + data-preserving migration, verified on
  the 2.0.1 simulator) and 7b (1.6J MeterValues); the planner and its schema
  delta, hypertable count (now four) and traceability tables were updated.
- Documentation updated: `.claude/rules/tech-decisions.md` (OCPP gateway now
  1.6J + 2.0.1), `docs/01-requirements/feature-list.md` (F-G2 status, plus a
  one-line answer on "Items needing confirmation" #11 for the first
  hardware), `.claude/rules/open-questions.md` (items 4–5: vendor requests and
  non-software blockers), `docs/01-requirements/future.md` (items #73–#80).
- Note: `.claude/` is not tracked by git (`repo-conventions.md`), so the
  `tech-decisions.md` and `open-questions.md` edits exist locally only.
- Next: Step 1 (raw OCPP message log).

### Step 1 — Raw OCPP message log (B1)

**Goal/scope:** Store every OCPP frame, both directions, verbatim, so the
very first real connection produces evidence — including frames the gateway
can't parse or has no handler for. Applies to both protocols. No read API
(SQL only) and no retention policy this round.

**Contract/decisions:**

- Table `charging_ocpp_messages`, TimescaleDB hypertable on `occurred_at`:
  `message_id` (UUID), `occurred_at` (received time for inbound, sent time
  for outbound; PK part), `station_id` (FK `RESTRICT`),
  `ocpp_subprotocol` (`ocpp1.6`/`ocpp2.0.1`), `direction`
  (`CP_TO_CSMS`/`CSMS_TO_CP` — a Python enum, values stored like
  `ChargingConnectorStatus`), `raw_frame` (`TEXT NOT NULL`, exactly the
  string received/sent). Index `(station_id, occurred_at)`.
- **Wrapper, not handler:** `RecordingConnection` wraps the accepted
  `ServerConnection` and is what gets handed to `ChargePoint`. Its `recv()`
  persists the frame **before returning it** to the library; its `send()`
  persists **after** the underlying send succeeds. Verify first that
  `python-ocpp` only uses `recv()`/`send()` on the connection.
- The wrapper opens **its own** `session_factory.begin()` per frame, so a
  handler's rollback never removes the record of what arrived. A failure to
  persist raises and ends the connection handler (a DB outage is
  `future.md` #31 territory; no silent "log and continue").
- Size bound: restore `CHARGING_MAX_RAW_PAYLOAD_BYTES` (currently
  commented out in `config.py`) as an active setting, rename to
  `CHARGING_OCPP_MAX_MESSAGE_BYTES`, and pass it to `serve(max_size=…)`.
  Default `1048576` (the WebSocket library's own default), **not** the old
  commented `65536`: a full `GetConfiguration` reply from a real charger can
  be large (Step 8). Oversized frames are refused by the WebSocket library
  (close code 1009) instead of being stored truncated.
- Frames can contain RFID `idTag`s and later VINs: the table has **no API**
  and is not logged to application logs.

**Prompt:**

```text
Implement the raw OCPP message log (planner backend-ocpp16-charger-integration,
Step 1, decision D8).

1. Model ChargingOcppMessageModel + a direction enum in charging_stations,
   a repository insert function and a service function record_ocpp_message.
2. Migration 0021_charging_ocpp_raw_log: create the table, make it a
   hypertable (follow 0004's pattern), add the (station_id, occurred_at)
   index; downgrade drops it.
3. In charging_stations/ocpp add RecordingConnection wrapping the websockets
   ServerConnection: recv() records then returns; send() sends then records;
   each record uses its own async_session_factory.begin() transaction.
   First confirm in the python-ocpp source that only recv()/send() are used.
4. Wire it into OCPPServer._handle_connection for the existing 2.0.1
   adapter. Resolve station_id once per connection.
5. Replace the commented CHARGING_MAX_RAW_PAYLOAD_BYTES with the active
   CHARGING_OCPP_MAX_MESSAGE_BYTES setting and pass it as serve(max_size=).
Follow backend-runtime-conventions.md (docstrings, no commit in
service/repository, __init__.py stays docstring-only).
```

**Main files:**

- `backend/app/domains/charging_stations/models.py`, `types.py`, `repository.py`, `service.py`
- `backend/app/domains/charging_stations/ocpp/raw_log.py` (new: `RecordingConnection`)
- `backend/app/domains/charging_stations/ocpp/ocpp_server.py`
- `backend/app/libs/common/config.py`, `backend/.env.example`
- `backend/app/libs/db/migrations/versions/0021_charging_ocpp_raw_log.py`

**Checks:**

- [x] Static: `black`, `isort`, `ruff check`, `mypy` (127 source files), `compileall`, `git diff --check` — all clean
- [x] Smoke (fake connection, `tests/test_ocpp_raw_log_smoke.py`): inbound frame is persisted **before** `recv()` returns; outbound is persisted **after** `send()`; a persistence failure propagates and the frame is not passed on; a frame that failed to send, or a closed connection, records nothing; each frame uses its own transaction; a binary frame is stored decoded but returned unchanged; `occurred_at` is timezone-aware; the service normalises to UTC, keeps the frame text untouched, and rejects a naive timestamp or a bad subprotocol
- [x] Integration (`RUN_DB_INTEGRATION=1`, temporary database): upgrade → downgrade → upgrade reaches head `0021_charging_ocpp_raw_log`; the table exists and **is a TimescaleDB hypertable**
- [x] Live (real gateway, dev database, existing 2.0.1 simulator): the 10 rows for one session are exactly the 5 CALLs and 5 CALLRESULTs in order with matching unique IDs, and the session result is unchanged (`completed`, 1000 → 1500 Wh, 500 Wh delivered)
- [x] Live negative cases: an unparseable line, an unhandled action (`BootNotification` → outbound `CALLERROR NotImplemented`) and a handler that fails and rolls back (`InternalError`; **no** session created) are all in the log; original whitespace is preserved
- [x] Live size limit (`CHARGING_OCPP_MAX_MESSAGE_BYTES=2048`): a small frame is logged, an oversized one closes the connection with code **1009** and nothing oversized is stored

**Actual result (implemented and verified 2026-09-24):** Step 1 is done.

- New hypertable `charging_ocpp_messages` (migration `0021_charging_ocpp_raw_log`, enum `chargingocppmessagedirection`), model `ChargingOcppMessageModel`, `OcppMessageDirection` in `types.py`, repository `insert_ocpp_message`, service `record_ocpp_message`, and a new domain exception `ChargingOcppMessageInputError` (added for the metadata validation; not in the original plan).
- `ocpp/raw_log.py::RecordingConnection` wraps the accepted WebSocket; `ocpp_server.py` resolves the station once per connection and hands the wrapper to the existing 2.0.1 adapter. Verified from the library source first that `ChargePoint` only calls `recv()` and `send()`. If a station is soft-deleted between handshake and connection setup, the connection is closed with code 1008 (this race was **not** exercised).
- `CHARGING_OCPP_MAX_MESSAGE_BYTES` (default 1 MiB) replaces the commented `CHARGING_MAX_RAW_PAYLOAD_BYTES` and is passed to `serve(max_size=…)`; `.env.example` and the settings docstring updated.
- Deviations from the plan: (1) the migration first created the enum explicitly and then again via `create_table`; it now follows the `0004`/`0018`/`0019` precedent (the table creates the type, the downgrade runs `DROP TYPE`); (2) the stale migration-head pins were updated now rather than in Step 9 — `test_migrations_smoke.py` and `test_postgres_integration.py` (the latter was already stale at `0017`), plus a hypertable assertion; (3) the tests live in `test_ocpp_raw_log_smoke.py`.
- The dev database was migrated to `0021`. All test data (a throw-away station and its 15 log rows and session) was removed afterwards.
- **Known unrelated failure, not fixed:** `test_telemetry_repository_round_trip_rolls_back` still fails with the old `latitude`/`longitude` column error already documented in `backend-charging-ingest-fixes.md` §5 (telemetry domain, out of scope for the current charging focus).
- **Not checked:** a database outage while logging (by design it ends the connection handler — `future.md` #31), and behaviour with a real 1.6J charger (no hardware yet).

**Future:** raw-log read API and retention policy → `future.md` #79. Step 11 must also update `database.md` (new table/hypertable and enum) and note in `future.md` #27 that raw payload auditing is now done.

### Step 2 — Version-aware gateway (A1)

**Goal/scope:** Stop rejecting 1.6J clients. Negotiate the subprotocol and
dispatch each connection to the right adapter. After this step a 1.6J
client connects; its messages still get `NotImplemented` until Steps 4–7.

**Contract/decisions:**

- `SUPPORTED_SUBPROTOCOLS = ("ocpp2.0.1", "ocpp1.6")` (server preference
  order). `select_ocpp_subprotocol` returns the first supported protocol the
  client offers, else `None`.
- `_process_request` returns `426` **only** when the client offers none of
  them; the message lists the supported set.
- Shared parsing helpers (`OcppPayload`, `parse_ocpp_timestamp`) move to a new
  `ocpp/parsing.py` so the two adapters and the server don't import each
  other in a cycle; `ocpp_server.py` and the existing tests import from the
  new module.
- Adapter classes: rename the existing `OCPPChargePoint` →
  `OCPP201ChargePoint` (touched by this task, per the gradual-rename rule);
  add `OCPP16ChargePoint` in `ocpp/ocpp16_charge_point.py` with **no handlers
  yet**, built on `ocpp.v16.ChargePoint`.
- The 1.6 path/identity contract is unchanged: `/ocpp/{identity}` (the
  charger's URL field + Charger ID must be set so it builds exactly that —
  confirmed in Step 10).

**Prompt:**

```text
Make the OCPP gateway protocol-aware (planner Step 2, decisions D1, D2).

1. Add SUPPORTED_SUBPROTOCOLS and update select_ocpp_subprotocol,
   _process_request and serve(subprotocols=...) so ocpp1.6 is accepted
   alongside ocpp2.0.1; 426 only if neither is offered.
2. Move OcppPayload and parse_ocpp_timestamp into ocpp/parsing.py; update
   imports in ocpp_server.py and tests.
3. Rename OCPPChargePoint to OCPP201ChargePoint; create
   ocpp/ocpp16_charge_point.py with an empty OCPP16ChargePoint(ChargePoint
   from ocpp.v16); _handle_connection picks the adapter from
   connection.subprotocol.
4. Update module docstrings that still say "2.0.1 only", and
   directory-structure.md for the new files.
Do not add any 1.6 handler in this step.
```

**Main files:**

- `backend/app/domains/charging_stations/ocpp/ocpp_server.py`
- `backend/app/domains/charging_stations/ocpp/parsing.py` (new)
- `backend/app/domains/charging_stations/ocpp/ocpp16_charge_point.py` (new)
- `backend/tests/test_schema_smoke.py` (imports), a new gateway test module

**Checks:**

- [ ] Static checks clean
- [ ] Smoke — handshake matrix: offers `ocpp1.6` → passes protocol check; offers both → 2.0.1 selected; offers neither/none → `426` naming both protocols; bad path → `404`; unknown identity → `404`
- [ ] Regression: the existing 2.0.1 simulator still completes `Started → Updated/MeterValues → Ended`
- [ ] Live: a bare 1.6 client connects with `ocpp1.6` (no more `426`) and a `BootNotification` comes back as `CALLERROR NotImplemented`; the frame pair is in the raw log

**Actual result:** *(to be filled in)*

### Step 3 — OCPP 1.6J charge-point simulator (E1)

**Goal/scope:** A simulator that speaks 1.6J so every later step can be run
end-to-end before hardware is available. This step delivers the skeleton
(connect, boot, heartbeat, answer `GetConfiguration`); later steps add
their messages to it. The 2.0.1 simulator is left untouched.

**Contract/decisions:**

- New `simulator/ocpp16_charge_point_simulator.py`, built on
  `ocpp.v16.ChargePoint` in client mode (dependency already present).
- Scenario-driven CLI: `--url`, `--identity`, `--connectors 2`,
  `--scenario {boot,session,…}`; happy path only, like the existing
  simulator (no retry/random failure).
- It **responds to CSMS calls** (`GetConfiguration`) from a configurable key
  table that mimics the spec's §4.3 keys (including `SupportedFeatureProfiles`
  and read-only entries).
- Timestamps are ISO-8601 UTC with `Z`, matching the existing simulator.
- `Makefile`: `charging-ocpp16-sim`; `charging-ocpp-seed` gains a way to
  provision the 1.6 mapping (EVSE per gun, D3), e.g. `--protocol 1.6`.

**Prompt:**

```text
Create the OCPP 1.6J simulator skeleton (planner Step 3). Use ocpp.v16
ChargePoint as a client; no new dependency. Connect with subprotocol ocpp1.6
to /ocpp/{identity}, send BootNotification then a Heartbeat, and answer an
incoming GetConfiguration from a key table. Add the Makefile target and the
seed-script option that provisions one EVSE per gun (connector 1 each) for a
1.6 station. Happy path only.
```

**Main files:** `simulator/ocpp16_charge_point_simulator.py` (new), `simulator/seed_charging_topology.py`, `Makefile`

**Checks:**

- [ ] `make charging-ocpp16-sim` connects (Step 2) and prints the server's replies
- [ ] No production dependency added

**Actual result:** *(to be filled in)*

### Step 4 — Charger identity and liveness: Boot, Heartbeat (A2, B6) — **M1**

**Goal/scope:** Answer the first messages a real charger sends and record
who it is and that it is alive. After this step the sample charger can be
connected and **all of its traffic is in the raw log**.

**Contract/decisions:**

- Migration `0022_charging_station_device`, nullable, no server default:
  `ocpp_protocol_version` (`String(20)`), `vendor`, `model`,
  `serial_number`, `firmware_version` (`String(100)`), `last_boot_at`,
  `last_seen_at` (`DateTime(timezone=True)`).
- `BootNotification` handler: reads `charge_point_vendor`,
  `charge_point_model` (the only required fields); everything else optional
  (`firmware_version`, `charge_point_serial_number` /
  `charge_box_serial_number`). Persists via a new service function; replies
  `Accepted`, `current_time` = now (UTC, ISO-8601 `Z`), `interval` =
  `CHARGING_OCPP_HEARTBEAT_INTERVAL_SECONDS` (new setting, default `60`,
  the spec's 60–300 range). A **firmware change** relative to the stored
  value is logged as a structured `WARNING` (baseline check from the spec);
  alerting is deferred.
- `Heartbeat` handler replies `current_time` only.
- **Liveness:** `last_seen_at` (and `ocpp_protocol_version`) are updated by
  the raw-log wrapper's own transaction for **every inbound frame** — any
  frame proves the charger is alive and this works for 2.0.1 too, with no
  change to the other handlers.
- **Online is derived, not stored:** `is_online = last_seen_at is not None
  and now − last_seen_at ≤ CHARGING_OFFLINE_TIMEOUT_SECONDS` (re-enable the
  commented setting, default `180`, = 3× the heartbeat interval). No
  sweeper (D5).
- Station response schema gains the device fields, `last_seen_at`,
  `is_online`. Unprovisioned stations still never get this far (handshake
  reject).

**Prompt:**

```text
Implement BootNotification and Heartbeat for OCPP 1.6J and the station
device/liveness fields (planner Step 4, decision D5).

1. Migration 0022_charging_station_device + model columns + response schema
   fields (is_online is computed in the service mapper, not stored).
2. service.record_charger_boot(...) and a repository update; log a WARNING
   when firmware_version changes from a previously stored non-null value.
3. OCPP16ChargePoint.on_boot_notification / on_heartbeat returning
   call_result.BootNotification(status=Accepted, ...) / Heartbeat.
4. Extend the raw-log wrapper's transaction so each inbound frame also
   updates last_seen_at and ocpp_protocol_version.
5. Settings: CHARGING_OCPP_HEARTBEAT_INTERVAL_SECONDS (60) and re-enable
   CHARGING_OFFLINE_TIMEOUT_SECONDS (180); document both in .env.example.
6. Extend the 1.6 simulator to send Boot + periodic Heartbeat.
Every helper needs a full docstring; 1.6 optional fields may be missing.
```

**Main files:** `charging_stations/{models,repository,service,schemas}.py`, `ocpp/ocpp16_charge_point.py`, `ocpp/raw_log.py`, `config.py`, `.env.example`, migration `0022_…`, `simulator/ocpp16_charge_point_simulator.py`

**Checks:**

- [ ] Static checks clean
- [ ] Smoke: boot with only the two required fields; boot with all fields; heartbeat returns `current_time`; firmware-change warning logged; `is_online` boundary (just inside / just outside the timeout); 2.0.1 traffic also updates `last_seen_at`
- [ ] Integration: migration cycle
- [ ] Live: simulator Boot + Heartbeat → `GET /api/v1/charging-stations/{id}` shows vendor/model/firmware and `is_online = true`; after the timeout (set small via env) it flips to `false`
- [ ] **M1 reached** — record it here

**Actual result:** *(to be filled in)*

### Step 5 — StatusNotification: connectors 0/1/2, full 1.6 status, errors (B2, B3, B4, B5, C5-partial)

**Goal/scope:** Store what a 1.6 charger says about each gun and about
itself: the full 1.6 status, `errorCode`, `vendorErrorCode`, `info`,
plus connector `0` as charger-level state. Fault **alerting** is out of
scope; this step only stops the data being thrown away.

**Contract/decisions:**

- Migration `0023_charging_status_details`:
  - PostgreSQL enum `chargingconnectorstatus`: add the five 1.6 values
    (`ALTER TYPE … ADD VALUE`, which must run outside a transaction block —
    use Alembic's `autocommit_block`). PostgreSQL cannot drop enum values, so
    the **downgrade** converts the five new values to `Occupied`, then
    recreates the type without them (rename-old / create-new / alter column /
    drop-old) — verify with the upgrade → downgrade → upgrade cycle.
  - `charging_connectors`: `error_code` (`String(50)`), `vendor_error_code`
    (`String(100)`), `status_info` (`String(50)`) — all nullable.
  - `charging_stations`: `charger_status` (reuse the widened
    `chargingconnectorstatus` PG enum, `create_type=False`),
    `charger_status_updated_at`, `charger_error_code`,
    `charger_vendor_error_code` — nullable (connector `0`).
- **Topology (D3):** `resolve_ocpp16_topology(identity, connector_id)` for
  `n ≥ 1` finds the EVSE with `ocpp_evse_id == n` and its connector with
  `ocpp_connector_id == 1`; `n == 0` is handled as charger-level and needs no
  topology row. Provisioning uses the existing CRUD (seed script from Step 3).
- **Status vocabulary (D4):** the enum members' *values* are the exact 1.6
  labels (`Available`, `Preparing`, `Charging`, `SuspendedEV`,
  `SuspendedEVSE`, `Finishing`, `Reserved`, `Unavailable`, `Faulted`) plus
  the existing 2.0.1 `Occupied`, so the adapter converts with
  `ChargingConnectorStatus(status)` exactly like the 2.0.1 path — no mapping
  function. An unknown label raises (loud). `types.py`'s docstring, which
  says the enum is 2.0.1's exact list, must be rewritten. **Busy rule:** a
  gun is free only when `Available`; `Preparing`/`Charging`/`Suspended*`/
  `Finishing`/`Occupied` are busy; `Suspended*` are normal, not faults.
- `timestamp` is **optional** in 1.6: absent → use the received time.
- `errorCode` is stored as sent (`NoError` included). All 16 standard codes
  and any vendor string are accepted; unknown `status` values still raise
  (loud, like today).
- `update_connector_status` gains **keyword-only optional** parameters for the
  new fields (defaults `None`), so the 2.0.1 call site is unchanged.

**Prompt:**

```text
Implement OCPP 1.6J StatusNotification (planner Step 5, decisions D3, D4).

1. Migration 0023_charging_status_details with the connector and station
   columns above; models, response schemas (connector and station).
2. Widen ChargingConnectorStatus (enum + PG type, with the downgrade
   handling described above), resolve_ocpp16_topology in
   charging_stations.service, and
   OCPP16ChargePoint.on_status_notification handling connector_id 0
   (station-level) and n>=1 (connector). Missing timestamp -> received time.
3. Extend update_connector_status with optional keyword-only error_code,
   vendor_error_code, status_info; add update_charger_status for
   connector 0. Keep the 2.0.1 handler's call unchanged.
4. Simulator: send StatusNotification for 0, 1 and 2, including a Faulted
   with errorCode and vendorErrorCode.
Do not add any alerting.
```

**Main files:** `charging_stations/{models,types,repository,service,schemas}.py`, `ocpp/ocpp16_charge_point.py`, migration `0023_…`, `simulator/ocpp16_charge_point_simulator.py`, `simulator/seed_charging_topology.py`

**Checks:**

- [ ] Static checks clean
- [ ] Smoke: all 9 statuses are accepted and stored as sent, `Occupied` still works for 2.0.1; all 16 error codes stored; connector `0` writes the station columns and never touches topology; missing `timestamp`; unknown status raises; unprovisioned connector → error; 2.0.1 `StatusNotification` unaffected
- [ ] Regression: `GET /charging-stations/nearby` results unchanged; the 2.0.1 simulator's `Occupied` status still stores
- [ ] Integration: migration cycle
- [ ] Live: simulator statuses for 0/1/2 → connector API shows the exact 1.6 `status` + error fields; station shows `charger_status`

**Actual result:** *(to be filled in)*

**Future:** fault alerting and the vendor 80-code catalog; stale-status invalidation and availability from `is_online` → `future.md` #75 and #76 (extends #37/#49).

### Step 6 — Transactions: Authorize, StartTransaction, StopTransaction (B8, B9, B10)

**Goal/scope:** The 1.6 session lifecycle: CSMS-allocated integer
`transactionId`, `idTag`, stop reason, and the authoritative `meterStop`,
feeding the existing session service.

**Contract/decisions:**

- Migration `0024_charging_session_fields`: `charging_sessions.id_tag`
  (`String(20)` — the 1.6 limit), `stop_reason` (`String(30)`),
  `meter_stop_wh` (`Numeric(24, 3)`), all nullable; plus a PostgreSQL
  **sequence** (integer range, no cycle) for 1.6 transaction IDs.
- **Public service additions in `charging_sessions`** (no new domain edge):
  `allocate_ocpp16_transaction_id(db)`; `ingest_transaction_event` gains
  **optional keyword-only** `id_tag`, `stop_reason`, `meter_stop_wh` (default
  `None`, 2.0.1 call unchanged). Sequence gaps after a rolled-back
  transaction are acceptable and documented.
- `Authorize`: reply `id_tag_info.status = Accepted` for every tag (D7).
- `StartTransaction`: resolve topology (Step 5 resolver), allocate the ID,
  `ingest_transaction_event(STARTED, transaction_id=str(id),
  meter_start_wh=Decimal(meter_start), id_tag=…)`, reply
  `transaction_id=<int>`, `id_tag_info=Accepted`. `meter_start` is an
  integer in Wh per 1.6.
- `StopTransaction`: look the session up by `(station, str(transaction_id))`
  (existing repository function), `ingest_transaction_event(ENDED,
  meter_end_wh=Decimal(meter_stop), meter_end_sampled_at=timestamp,
  stop_reason=reason, meter_stop_wh=…)`. **`meter_stop_wh` is always stored**
  (authoritative closing reading); the aggregate's `meter_end_wh` follows the
  existing forward-in-time watermark rule (F-B2), so a stale timestamp never
  overwrites a newer reading but the closing register is still kept.
  `transaction_data` is handled in Step 7b.
- **Not done here (D14):** an ACTIVE session already on that connector when
  a new `StartTransaction` arrives is left alone with a structured
  `WARNING` — orphan policy waits for real logs. An unknown
  `transactionId` on Stop raises `ChargingSessionNotFoundError` → `CALLERROR`
  (loud, like the current MVP).
- Session response schema gains the three fields.

**Prompt:**

```text
Implement OCPP 1.6J Authorize/StartTransaction/StopTransaction (planner Step 6,
decisions D6, D7).

1. Migration 0024_charging_session_fields (three nullable columns + an integer
   sequence); model, schemas, repository (next-value function).
2. charging_sessions.service: allocate_ocpp16_transaction_id, and optional
   keyword-only id_tag / stop_reason / meter_stop_wh on
   ingest_transaction_event; always store meter_stop_wh; keep the F-B2
   guards (COMPLETED is terminal, stale sample never overwrites).
3. OCPP16ChargePoint handlers for authorize, start_transaction,
   stop_transaction returning the 1.6 confirmations.
4. Simulator: Authorize -> StartTransaction -> ... -> StopTransaction.
Keep the 2.0.1 path byte-for-byte compatible.
```

**Main files:** `charging_sessions/{models,repository,service,schemas,types}.py`, `charging_stations/ocpp/ocpp16_charge_point.py`, migration `0024_…`, simulator

**Checks:**

- [ ] Static checks clean
- [ ] Smoke: two `StartTransaction`s get different integer IDs; start reply carries the ID; stop stores `meter_stop_wh` and `stop_reason`; stale stop timestamp keeps the closing register but not the aggregate overwrite; second Stop on a COMPLETED session refused; unknown `transactionId` errors; `id_tag` > 20 chars rejected at the boundary; 2.0.1 tests unchanged
- [ ] Integration: migration cycle; sequence exists
- [ ] Live: simulator start/stop → session `completed`, `energy_delivered_wh` = stop − start, `id_tag`, `stop_reason`, `meter_stop_wh` present in `GET /charging-sessions/{id}`

**Actual result:** *(to be filled in)*

### Step 7 — Unified measurement storage, then 1.6J MeterValues (B7, C1-for-1.6, C8) — **M2**

Two sub-steps. **7a is a behaviour-neutral refactor** that touches the working
2.0.1 path and a data migration, so it is verified on its own (with the
2.0.1 simulator) before **7b** adds any 1.6J code. Do not start 7b until 7a's
checks pass.

#### Step 7a — Unify meter storage (D9)

**Goal/scope:** Replace `charging_session_meter_values` with the single table
`charging_session_measurements` for both protocols. No new measurands are
stored yet and no 1.6 code is added; the observable 2.0.1 behaviour and the
`/meter-values` API must not change.

**Contract/decisions:**

- Migration `0025_charging_measurements` (**data-preserving**, not a reset):
  1. create hypertable `charging_session_measurements(measurement_id UUID,
     sampled_at timestamptz — both PK, session_id FK `RESTRICT`,
     measurand String(60) NOT NULL, value Numeric(24, 6) NOT NULL, unit
     String(20), context String(30), phase String(10), location String(20))`,
     index `(session_id, measurand, sampled_at)`;
  2. copy every old row: `measurement_id = meter_value_id`, `sampled_at`,
     `session_id`, `measurand = 'Energy.Active.Import.Register'`,
     `value = value_wh`, `unit = 'Wh'`;
  3. drop `charging_session_meter_values`.
  **Downgrade:** recreate the old table/hypertable, copy back **only** the
  energy-register rows, drop the new table. Any non-energy rows are lost on
  downgrade — state this in the migration docstring.
- **Energy row convention:** `measurand = Energy.Active.Import.Register`,
  `value` = canonical **Wh**, `unit = 'Wh'`. Other measurands keep the value and
  unit as sent.
- `ChargingSessionMeterValueModel` → `ChargingSessionMeasurementModel`;
  repository `insert_meter_value` → `insert_measurement`; the list/count
  queries behind `/meter-values` filter on the energy measurand and still
  return `{sampled_at, value_wh}` — the response schema is **unchanged**.
- `ingest_meter_values(db, session_id, sample)` keeps its public signature
  and behaviour (aggregate watermark rule, COMPLETED refusal); only its insert
  target changes. `MeterSampleInput` gains an optional `context` (default
  `None`). F-C5 is unaffected (it reads the session aggregate).

**Prompt:**

```text
Unify charging meter storage (planner Step 7a, decision D9).

1. Migration 0025_charging_measurements: create the hypertable, copy every
   row from charging_session_meter_values as the energy measurand in Wh,
   drop the old table; a downgrade recreates it and copies energy rows back.
2. Rename the model/repository functions, point ingest_meter_values and the
   /meter-values list/count queries at the new table filtered on the energy
   measurand. Keep every public signature and response schema unchanged.
3. Update existing tests that referenced the old model/table.
Do not add any 1.6J code or store any new measurand in this step.
```

**Main files:** `charging_sessions/{models,repository,service,types}.py`, `charging_stations/ocpp/ocpp_server.py` (only if it names the old model), tests, migration `0025_…`

**Checks:**

- [ ] Static checks clean
- [ ] Integration: migrate to `0024`, insert energy rows, upgrade to `0025` → row count and values equal; downgrade restores them; new table is a hypertable and the old one is gone
- [ ] Regression: run the 2.0.1 simulator **before and after** the change on the same inputs; `/charging-sessions/{id}`, `/events`, `/meter-values` return identical values
- [ ] No reference to `charging_session_meter_values` remains in code (`rg`)

**Actual result:** *(to be filled in)*

#### Step 7b — 1.6J MeterValues and extra measurands

**Goal/scope:** Store `SoC`, power, voltage, current, temperature and
`Power.Offered` (and any vendor-named measurand), keep the energy register
driving the session aggregate, and remove the in-memory session map for 1.6.

**Contract/decisions:**

- **New 1.6 normaliser** `normalize_v16_sampled_value` (separate from the
  2.0.1 function — never reuse it, comparison §2.2): default measurand
  `Energy.Active.Import.Register`, default unit `Wh`, flat `unit` field;
  energy register in `Wh`/`kWh` → canonical Wh, **any other energy unit
  raises** (loud, as today); a `kWh` sample **must** become ×1000 (explicit
  regression test).
- **New input type** `MeasurementInput` (frozen dataclass in `types.py`:
  `sampled_at`, `measurand`, `value: Decimal`, `unit`, `context`, `phase`,
  `location`). Public service `ingest_measurements(db, session_id, samples)`
  inserts per item (no batching).
- **Routing:** the energy register → `ingest_meter_values` (aggregate update
  **and** a measurement row, with `context`); every other measurand →
  `ingest_measurements`. Unknown/vendor names (🔎 `Voltage.Demand`,
  `Current.Demand`) are stored as-is. `format = SignedData` or a non-numeric
  non-energy value is **skipped with a structured `WARNING` including a
  count** (C8) — never silently, never failing the whole message. An
  unparseable **energy** value raises.
- **Session lookup by `transactionId` (fixes C1 for 1.6):** new public
  `charging_sessions.service.resolve_session_by_transaction(db, station_id,
  transaction_id)` returning a frozen `TransactionSessionReference`
  dataclass (`types.py`). No `_session_by_evse` dict on the 1.6 adapter.
  `MeterValues` **without** a `transactionId` (clock-aligned / outside a
  transaction) is not attributed to a session: it stays in the raw log and is
  counted in a `DEBUG` log; station-level metering is deferred.
- `StopTransaction.transaction_data` goes through the same function
  (`context = Transaction.End` normally). Existing guards apply: a sample for
  a `COMPLETED` session is refused.
- Read API: `GET /api/v1/charging-sessions/{session_id}/measurements`
  (paginated, optional `measurand` filter), matching the existing monitoring
  endpoints; no raw payload. `/meter-values` stays as the energy-only view.
- 🔎 Treat `SoC = 0` at session start as *unknown* when consuming the data
  later; storage keeps the value as sent.
- No migration in 7b (the table and its `context` column exist from 7a).

**Prompt:**

```text
Implement OCPP 1.6J MeterValues and measurement storage (planner Step 7b,
decision D9). Step 7a must already be merged and verified.

1. normalize_v16_sampled_value in the 1.6 adapter (separate from the 2.0.1
   function): flat unit, defaults, energy Wh/kWh only, loud on unknown energy
   unit, structured warning + count for skipped non-energy samples.
2. charging_sessions: MeasurementInput and TransactionSessionReference
   (types.py), resolve_session_by_transaction, ingest_measurements (per
   item), context passed to the energy path, and the /measurements endpoint.
3. OCPP16ChargePoint.on_meter_values and handling of
   StopTransaction.transaction_data through the same function. No in-memory
   session map.
4. Simulator: MeterValues with Energy (including one kWh sample), SoC, power,
   voltage, current, temperature, Power.Offered, and a vendor-named
   measurand.
```

**Main files:** `charging_stations/ocpp/ocpp16_charge_point.py`, `charging_sessions/{repository,service,schemas,types,router}.py`, simulator

**Checks:**

- [ ] Static checks clean
- [ ] Smoke (normaliser matrix): Wh default; explicit `kWh` → ×1000 (**pins the comparison §2.2 hazard**); unknown energy unit raises; SoC/power/V/A/T/Power.Offered routed to measurements; vendor measurand stored; `SignedData` and non-numeric skipped with warning; `transactionId` absent → not attributed; COMPLETED session refused; reconnect scenario — a **new** connection's `MeterValues` for an ACTIVE transaction is accepted (C1)
- [ ] Live: simulator session → `energy_delivered_wh` correct after a kWh sample; `/measurements` returns every simulated measurand and `/meter-values` still returns the energy samples; kill and restart the simulator connection mid-session and confirm `MeterValues` still land
- [ ] **M2 reached** — record it here

**Actual result:** *(to be filled in)*

**Future:** station-level (non-transaction) metering and 15-minute clock-aligned data; 2.0.1 measurement parity (storing non-energy measurands from 2.0.1) → `future.md` #77 and #78.

### Step 8 — Post-boot GetConfiguration capture (B12, part of A6) — **M3**

**Goal/scope:** Automatically capture the charger's real configuration,
including `SupportedFeatureProfiles`, after every boot. This is the **only**
CSMS-initiated call in this planner (D10).

**Contract/decisions:**

- **No awaiting `call()` inside a handler** (§2.2): after the
  `BootNotification` handler has returned its `Accepted` response, the
  adapter starts an `asyncio.create_task(...)` that sends
  `GetConfiguration()` with **no key**. The task reference is kept on the
  adapter and **cancelled in the connection's `finally`** (shutdown rule:
  cancel and await); its failure is logged with `logger.exception` at the
  task boundary and never affects the connection.
- Response timeout: re-enable `CHARGING_OCPP_REQUEST_TIMEOUT_SECONDS`
  (default `30`) and pass it as the adapter's `response_timeout`.
- Migration `0026_charging_config_snapshots`: append-only
  `charging_station_configuration_entries(entry_id, station_id FK, capture_id
  UUID, captured_at, config_key String(100), value Text NULL, is_readonly
  Boolean)`; index `(station_id, captured_at)`. One `capture_id` groups the
  keys of one capture; a new snapshot every boot preserves history (tamper
  detection, spec §6 storage table). `unknownKey` values are logged, not
  stored.
- Read API: `GET /api/v1/charging-stations/{station_id}/configuration`
  returns the **latest** capture (so `SupportedFeatureProfiles` is readable
  without SQL).
- A charger that answers `CALLERROR`/times out is logged; nothing else
  changes.
- **Not done:** `ChangeConfiguration` (e.g. lowering
  `MeterValueSampleInterval` to ≤ 30 s), `TriggerMessage`, on-demand
  GetConfiguration — see §6 (command channel).

**Prompt:**

```text
Implement the post-boot GetConfiguration capture (planner Step 8, decision D10).

1. Migration 0026_charging_config_snapshots, model, repository, service, and
   the read-only latest-configuration endpoint.
2. In OCPP16ChargePoint, after on_boot_notification returns, schedule a task
   that calls GetConfiguration() (never await call() inside a handler -
   python-ocpp's receive loop is sequential and would deadlock). Keep the
   task reference; cancel it on disconnect; log failures at the task
   boundary.
3. Re-enable CHARGING_OCPP_REQUEST_TIMEOUT_SECONDS and pass response_timeout
   to the ChargePoint.
4. Simulator answers GetConfiguration with the spec section 4.3 key table.
```

**Main files:** `charging_stations/{models,repository,service,schemas,router}.py`, `ocpp/ocpp16_charge_point.py`, `ocpp/ocpp_server.py` (task cleanup), `config.py`, migration `0026_…`, simulator

**Checks:**

- [ ] Static checks clean
- [ ] Smoke: the Boot handler returns **before** `GetConfiguration` is sent (a fake charge point proves it is not awaited inline); a timeout/`CALLERROR` is logged and the connection survives; the task is cancelled when the connection closes; entries and `is_readonly` mapped correctly; two boots → two captures, latest endpoint returns the second
- [ ] Integration: migration cycle
- [ ] Live: simulator boot → `configuration` endpoint lists every key incl. `SupportedFeatureProfiles`; the raw log shows the CSMS→CP `GetConfiguration` and the CP→CSMS result
- [ ] **M3 reached** — record it here

**Actual result:** *(to be filled in)*

### Step 9 — End-to-end regression and automated tests (E2)

**Goal/scope:** Lock the behaviour with a small, fast suite and one complete
scripted run, following `backend-automated-tests.md` (simple pytest, no new
test library, DB integration only when `RUN_DB_INTEGRATION=1`).

**Contract/decisions:**

- Unit/smoke tests live with the existing modules
  (`test_schema_smoke.py`, `test_service_smoke.py`) or one new
  `test_ocpp_gateway_smoke.py` for handshake/adapter behaviour using fake
  connections; tests describe behaviour (`test_…_rejects_…`).
- `test_postgres_integration.py`: update the expected migration head to
  `0026_charging_config_snapshots` and assert the four hypertables.
- One scripted E2E, `make charging-ocpp16-sim`, must produce:
  station device fields; connector statuses for 0/1/2; a `completed`
  session with correct energy; measurements; raw-log rows for every frame in
  both directions; one configuration capture.

**Prompt:**

```text
Add the automated tests and E2E run for the OCPP 1.6J work (planner Step 9).
Cover: handshake matrix; RecordingConnection ordering/failure; boot/heartbeat;
status mapping; start/stop; the normaliser matrix incl. the kWh case; the
"boot returns before GetConfiguration" guard; reconnect-then-MeterValues.
Update the integration migration-head assertion. Then run the full E2E from a
clean database and record the evidence (SQL output summary) here.
```

**Checks:**

- [ ] `uv run pytest` green (default scope), and `RUN_DB_INTEGRATION=1 uv run pytest` green — state which scope ran and why if not
- [ ] E2E evidence recorded (rows per table, endpoints returning expected values)
- [ ] The legacy 2.0.1 simulator still passes

**Actual result:** *(to be filled in)*

### Step 10 — Real-charger bring-up and findings (operational)

**Goal/scope:** Run the spec's test plan (Phases 1–3) on the real Willdigits
unit **as soon as M1 is reached** (no need to wait for Step 9), record what
is confirmed, and convert the findings into scoped follow-ups. Mostly
operational; code changes only if a finding is a bug in Steps 1–8.

**Procedure:**

1. On site: change the default HMI password **first**
   (`77777777`); set the URL to `ws://<host>:9000/ocpp/` and the Charger ID
   to the provisioned identity; provision station + EVSE-per-gun in the
   system beforehand.
2. Phase 1 — connect, no charging: capture `BootNotification`, the
   `GetConfiguration` snapshot (Step 8; `SupportedFeatureProfiles`), plug/
   unplug status timing. Anything the charger sends that the gateway has no
   handler for (e.g. `FirmwareStatusNotification`, `DiagnosticsStatusNotification`,
   `DataTransfer`) appears in the raw log as an unanswered CALL — list them.
3. Phase 2 — real sessions: start at the charger; verify `SoC`; reconcile the
   three figures `(meter_stop_wh − meter_start_wh)`, the charger screen kWh,
   and the sum of stored energy samples; compare `Power.Offered` on both
   guns.
4. Phase 3 — edge cases from the spec table (unplug mid-session, network loss
   5 min, E-stop, `Reset` mid-session, wrong clock…). Some probes need
   commands this planner does **not** implement (`ChangeConfiguration`,
   `TriggerMessage`, `Reset`) — use an external OCPP test tool (the handover
   doc suggests SteVe) against the same charger for those, or defer them.
5. Fill in the spec summary's confirmation table (§5.4) and the
   acceptance checklist (§5.3); append findings to the comparison document.

**What can and cannot be verified through this backend** (spec acceptance
checklist): items 1–7, 10, 17–20 can be read from the stored data (raw log,
snapshot, sessions, measurements); items 8, 9, 12–16 need remote commands or
TLS and are **not testable here** — record them as pending, do not mark them
passed.

**Decisions to take from the findings (each may create a follow-up planner):**

- Timestamp policy (D11) — does the firmware send offsets? clock behaviour?
- Orphan sessions and back-fill policy (C2, C3, D14).
- Whether stop reasons / vendor error codes surface in `StopTransaction.reason`
  or `vendorErrorCode` (spec §4.7) — needed for the vendor code catalog.
- Which unhandled inbound actions deserve trivial ACK handlers.
- Whether `ChangeConfiguration` for the meter interval is needed (acceptance
  item 5) and therefore whether the command channel moves up.

**Checks:**

- [ ] Each finding is written down with the raw-log evidence (message pair/time), not paraphrased
- [ ] Findings that reopen a decision update §2 and/or `future.md`
- [ ] Spec summary confirmation/acceptance tables updated; nothing marked passed without evidence

**Actual result:** *(to be filled in)*

### Step 11 — Final review, docs, and recording deferred items

**Prompt:**

```text
Run the final checks for the OCPP 1.6J work:

1. compileall, Black, isort, Ruff, mypy; git diff --check.
2. alembic upgrade -> downgrade -> upgrade on the dev DB through 0026.
3. rg audits: cross-domain imports of models/repository, commit()/rollback()
   in service/repository, HTTPException outside routers, datetime.utcnow,
   float energy, __init__.py containing code, Vietnamese in comments.
4. Cross-check that every skipped item in section 6 has a future.md entry.
5. Update the documents listed below, then commit following Conventional
   Commits on master, splitting feat / test / docs commits.
```

**Documents to update:**

- `docs/01-requirements/feature-list.md`: F-G2, F-C2, F-B2 status text (F-C2's note that 1.6J statuses are "all folded into `Occupied`" is no longer true — rewrite it)
- `docs/00-status/overview.md`, `docs/00-status/architecture.md` (diagram now shows 1.6J and 2.0.1)
- `.claude/rules/directory-structure.md` (new `ocpp/` modules, new tables), `database.md` (new head, four hypertables — `charging_session_meter_values` replaced by `charging_session_measurements`, the new columns' nullable rationale, the sequence), `tech-decisions.md`, `open-questions.md`, `domain-boundaries.md` (state explicitly that **no new edge** was added)
- `docs/01-requirements/future.md`: resolution notes on #27/#28/#37 partial resolution; #49 must state the busy rule from D4; new items from §6
- `docs/02-planners/backend-charging-mvp-ideal.md`: a note that its "device always online" assumption no longer holds for the 1.6J path
- The comparison document: mark which mismatches are closed

**Planner completion criteria:**

- A 1.6J charger connects, is answered, and every frame is stored (M1).
- A complete simulated 1.6J session is stored with statuses, energy, `idTag`, stop reason, `meterStop` and the mandatory measurands (M2).
- The charger's configuration is captured per boot (M3).
- The 2.0.1 simulator and tests still pass.
- Every deferred piece is in `future.md`; every check result and environment limit (what was and was not run) is recorded in this planner.

**Actual result:** *(to be filled in)*

## 5. Traceability

### 5.1 Mismatch → step

| Mismatch (comparison doc) | Addressed by |
|---|---|
| A1 subprotocol/version | Step 2 |
| A2 Boot/Heartbeat | Step 4 |
| A3 message shapes | Steps 4–7 |
| A4 URL/identity path | Step 10 (verify what the charger builds) |
| A5 TLS/auth | ⛔ deferred (D12) |
| A6 CSMS commands | Step 8 (only post-boot `GetConfiguration`); rest ⛔ deferred |
| B1 raw log | Step 1 |
| B2 connector 0 · B3 EVSE mapping | Step 5 (D3) |
| B4 status granularity · B5 error codes | Step 5 (widened enum + error fields); alerting ⛔ deferred |
| B6 charger registry | Step 4 |
| B7 measurands | Steps 7a–7b |
| B8 transaction ID | Step 6 (D6) |
| B9 idTag / reason | Step 6 (VIN linkage ⛔ deferred, `future.md` #62) |
| B10 `meterStop` | Step 6 |
| B11 tariff · B13 immutability · B14 per-gun power | ⛔ unchanged / deferred |
| B12 configuration history | Step 8 |
| C1 reconnect mapping | Step 7b (1.6 only; 2.0.1 unchanged) |
| C2 back-fill · C3 orphans · C4 clock | Step 10 findings, then decide (D11, D14) |
| C5 stale status | Step 4 exposes `is_online`; invalidation ⛔ deferred |
| C6/C7 error policy/duplicates | unchanged (`future.md` #31/#66) |
| C8 silent skips | Step 7b (warning + count) |
| E1 simulator · E2 tests | Steps 3, 9 |
| E3 public endpoint/TLS infra | ⛔ deferred with A5 |

### 5.2 Spec acceptance checklist → what this planner enables

| Spec item | Enabled by | Note |
|---|---|---|
| 1 stable connection ≥ 24 h | Steps 1, 4 | read heartbeat continuity from the raw log / `last_seen_at` |
| 2 BootNotification | Step 4 | |
| 3 GetConfiguration + profiles | Step 8 | |
| 4 StatusNotification 0/1/2, ≤ 30 s | Step 5 | latency = charger `timestamp` vs raw-log `occurred_at` |
| 5 MeterValues ≤ 30 s | Step 7b | adjusting the interval needs `ChangeConfiguration` (deferred) |
| 6 energy measurand | Step 7b | |
| 7 `SoC` | Step 7b | |
| 8, 9 RemoteStart/Stop | ⛔ | no command channel |
| 10 kWh agreement < 1 % | Steps 6, 7 | three figures from stored data (Step 10) |
| 11 HMI password changed | Step 10 (procedure) | not system-verifiable |
| 12 `wss://` · 13 Smart Charging · 14 Remote Trigger · 15 GetDiagnostics · 16 Reservation | ⛔ | need TLS / commands |
| 17 offline back-fill | Step 10 | observe first, then design |
| 18 unplug mid-session | Steps 6, 10 | stop reason stored |
| 19 dual-gun load sharing | Step 7b | `Power.Offered` stored |
| 20 vendor error-code table | Step 5 (store) | mapping needs the vendor's table |

## 6. Out of scope and the path back

Everything below must have (or already has) a `future.md` entry; Step 0
created the new ones (#73–#80, recorded 2026-09-24) and Step 11 verifies them.

| Deferred piece | `future.md` | Why deferred / what unblocks it |
|---|---|---|
| Transport security for real chargers (`wss://`, Basic Auth / client cert, VPN/private APN, per-charger credentials) | #73 | Vendor hasn't confirmed support (spec §6.2 #4) — related to NF-05 and `tech-decisions.md` |
| CSMS remote commands over OCPP + the cross-process command channel (`RemoteStart/Stop`, on-demand `Get/ChangeConfiguration`, `TriggerMessage`, `Reset`, `UnlockConnector`, `ChangeAvailability`, Smart Charging, firmware/diagnostics, reservation) | #74 (relates to #26) | Needs an HTTP→gateway-process channel decision; profile support unconfirmed |
| Fault alerting and the charger error-code catalog (`errorCode`/`vendorErrorCode` → notifications, tickets, gun blocking, billing suspension of suspect sessions) | #75 | Needs the vendor's 80-code mapping (spec §6.2 #2) |
| Stale-status invalidation on disconnect; availability derived from `is_online` and connector status | #76 (extends #37, #49) | Business decision on "available" |
| Reliability path: back-fill, orphan sessions, dedup by `seq_no`, reconnect for 2.0.1 | #27, #32, #66 | Real-charger findings (Step 10) |
| Real `Authorize` validation, VIN/vehicle linkage on sessions | #26, #62 | No tag registry / identity contract |
| Non-transaction (station-level, clock-aligned) metering | #77 | Needs the tariff (time-of-use) design |
| OCPP 2.0.1 parity for the new fields (boot info, stop reason, measurements, idToken) | #78 | No 2.0.1 hardware |
| Raw-log read API and TimescaleDB retention | #79 | Nothing consumes it yet |
| Timestamp policy for chargers omitting a timezone | decided in Step 10 | Needs real logs (D11) |
| Tariff versioning per session, per-gun power/connector standard in the directory | #26 (tariff) / #80 (per-gun) | Billing domain / hardware detail |

Do not reopen these on your own by adding placeholders to the new code.

## 7. Risks and open questions

- **The charger may not behave as the spec assumes** (whether it proceeds
  without an `Accepted` boot, timestamp form, transaction-ID handling,
  URL construction). Steps 1 and 10 exist so these become observations, not
  guesses; expect §2 to change after Step 10.
- **`meter_protocol = none` on the sample unit** (🔎 spec §4.6): if the DC
  meter isn't read over a meter protocol, `meterStart/meterStop` may not be
  legally usable. This planner stores the numbers; it cannot answer the
  legality question. It is a vendor/procurement decision (spec §6.2 #3).
- **A `CALLERROR` makes the charger retry.** For permanent errors (unknown
  transaction, unknown connector) the charger may retry up to its
  `TransactionMessageAttempts`. Kept loud on purpose for now; revisit with
  real logs.
- **Only one unavoidable behaviour change to existing code:** the rename
  `OCPPChargePoint → OCPP201ChargePoint` and moving two helpers; everything
  else for 2.0.1 is additive and covered by the untouched 2.0.1 simulator and
  tests.
- **Migration numbering** assumes nothing else lands before `0021`; renumber
  if it does (revision strings must stay ≤ 32 characters).
