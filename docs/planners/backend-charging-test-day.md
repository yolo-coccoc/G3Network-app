# Planner: Charging test day — gateway fixes and test tooling (STN-03, CHG-01, CHG-02)

> Feature codes: STN-03 (Charger connection, OCPP), CHG-01 (Start charging by
> QR code), CHG-02 (Charging session record)
> Status: 🚧 In progress — Steps 0–6 done 2026-10-12; waiting for the real-charger test day (section 6)
> Created: 2026-10-12
>
> Inputs: an external review of the repository (2026-10-12: six charging
> findings, five operational notes), the OCPP 1.6J planner
> ([backend-ocpp16-charger-integration.md](./backend-ocpp16-charger-integration.md),
> Step 10 is the real-charger procedure this planner prepares),
> the refactor planner ([backend-refactor-implementation.md](./backend-refactor-implementation.md),
> RV-BL9 and CV-22 already fixed the review's pending-scan and logging
> findings), decisions CO-10, CO-08, CE-05, CE-12, CE-17, CE-24
> ([decision-log.md](../decisions/decision-log.md)).

## 1. Goal

Get the backend ready for one **test day with the real charger** whose only
aim is to prove that a charging session runs end to end: the driver plugs in,
the backend starts the charge remotely after a QR scan, measurements stream
in, the session ends and is billed. There is no mobile app or portal yet, so
the day is driven by command-line tools that call the real HTTP API.

Done means:

1. The gateway no longer fails a session on the review's reproducible
   findings (naive timestamps, strict schemas, case-changed tokens, repeated
   or unknown transaction messages).
2. Staff can close a session the charger will never stop (orphan).
3. The tools below exist and a full rehearsal against the 1.6J simulator
   passes on the test server.
4. A test-day runbook (section 6) says what to run, in which order, and what
   evidence to keep.

Not part of it: an automatic orphan policy, TLS or charger authentication,
reading the QR content format, the 2.0.1 path (no 2.0.1 hardware).

## 2. Scope decisions

| # | Decision | Recommended | Alternative (rejected) | Status |
|---|---|---|---|---|
| D1 | A timestamp without a timezone | **Read it as UTC** and log a warning; the raw log keeps the original. Supersedes D11 of the 1.6J planner and CO-10 → **CO-16** | Keep refusing it (CO-10) → a charger that sends local time without an offset can start no session at all, and we only learn it at the station | ✅ Confirmed 2026-10-12 |
| D2 | A `StopTransaction` or `MeterValues` for a transaction that is unknown (including `0`) or already completed | **Always confirm it**, change nothing, log a warning; the frame stays in the raw log. Unknown connectors still fail loudly → **CO-16** | CALLERROR (today) → OCPP 1.6 delivers transaction messages until they are confirmed, so a lost answer on 4G or a late reading makes the charger retry forever and blocks its queue | ✅ Confirmed 2026-10-12 |
| D3 | Library schema check on `BootNotification`, `StartTransaction`, `StatusNotification` | **Skip it, as for StopTransaction and MeterValues**; the handler validates the fields it uses (types, required fields), and stores free text truncated to its column → **CO-17** | Keep the 1.6 schema (CO-08) → a model over 20 characters, a serial over 25, `info` over 50 or `meterStart` sent as `12345670.0` is refused; a refused boot keeps the charger out of service | ✅ Confirmed 2026-10-12 |
| D4 | QR token alphabet | **Upper-case letters and digits only** (`A-Z0-9`, still 12 characters, about 62 bits; single-use and valid for 5 minutes) → **CE-28** | Case-insensitive comparison → touches three repository queries and the unique lookup; a charger that upper-cases the idTag (1.6 `IdToken` is case-insensitive) is then still matched by accident only | ✅ Confirmed 2026-10-12 |
| D5 | A session the charger will never stop (reboot, power loss mid-session) | **Staff force-close endpoint** `POST /charging-sessions/{id}/force-close`, internal staff only (CHG-02 roles + `internal_only`), `ACTIVE` sessions only (a PENDING scan already has the stop, RV-BL9) → **CE-29** | Wait for the orphan policy (D14 of the 1.6J planner, CE-05) → the gun and the driver stay blocked for good during the test; a manual SQL fix → bypasses the bill and the session-ended hooks | ✅ Confirmed 2026-10-12 |
| D6 | How a force-closed session is settled | **Use the existing rule.** The session completes with `ended_at` = now, `stop_reason` = `StaffClosed`, **no closing reading**; billing then falls back to the newest outlet measurement (CE-12) → BILLED, or ON_HOLD "no closing reading" when there is none. A late `StopTransaction` afterwards is confirmed and ignored (D2); its `meterStop` stays in the raw log for staff | Always ON_HOLD → needs a new field on the session-ended event and a new billing rule, and staff must release every force-closed bill by hand; the measurement fallback is already the agreed rule for a session without a closing reading | ✅ Confirmed 2026-10-12 (owner: "the recommended choice") |
| D7 | Test drivers | **A seed script** creates N active users with the `DRIVER` role in the internal organization in-process, the way `make identity-bootstrap` creates the first admin (hashed password, membership, role); their wallets are credited through the staff endpoint `POST /wallets/{user_id}/adjustments`, so the ledger has a proper line | Raw SQL → wrong password hash, a wallet balance without ledger lines; the invitation flow → needs the one-time code from the API log for every account | ✅ Confirmed 2026-10-12 |
| D8 | How the tools reach the backend | **Only through the HTTP API**, standard library only, in `simulator/`, reusing `api_client.py`; every tool takes `--api-url` | Read or write the database directly → would not test what the app will use | ✅ Confirmed 2026-10-12 |
| D9 | What the simulated scan sends | **`--charger <code>`** (OCPP identity or registered serial) **or `--qr "<raw text>"`** sent exactly as read; the tool never parses the QR content | Parse URLs in the tool → hides the gap if the real QR does not match (CO-14, open question 4) | ✅ Confirmed 2026-10-12 |
| D10 | Test server | **The development VM (B)**: only `A:9000 → B:9000` is forwarded to the public IP; the API stays on `localhost`; every tool runs on B over SSH, inside `tmux` | Expose the API port → not needed, and the API would be on the internet | ✅ Confirmed 2026-10-12 |

### 2.1 Out of scope (deferred)

- Automatic orphan policy (close on reboot, on a new `StartTransaction` for
  the same gun, or after silence), back-fill, clock policy: still waits for
  real-charger logs (D14 of the 1.6J planner, CE-05, `deferred.md` 27).
- Recording who force-closed a session in the database (there is no
  `ended_by` column, CE-24); the endpoint logs the staff member and the
  reason (`deferred.md` 101).
- `wss://` and charger authentication (STN-11, D12 of the 1.6J planner).
- Reading the vendor's QR content format (open question 4, CO-14).

## 3. Design

### 3.1 Domain and boundaries

No new cross-domain edge.

- Gateway fixes (D1–D4): `charging_stations/ocpp/` (1.6J adapter, `parsing.py`)
  and `charging_sessions/service.py` (token alphabet, the "already completed"
  answer the adapter needs).
- Force-close (D5–D6): `charging_sessions` router and service; it reuses the
  completion path, so the session-ended hooks (billing) run as for a normal
  stop.
- Tools: `simulator/` (outside `app/`, not subject to the import contracts).

### 3.2 Data model

No schema change. `StaffClosed` fits `charging_sessions.stop_reason`
(varchar(30)).

### 3.3 API / interfaces

| Method | Path | Request | Response | Errors |
|---|---|---|---|---|
| POST | `/api/v1/charging-sessions/{session_id}/force-close` | `{reason: str 1..200}` | the session (`COMPLETED`) | 404 `ChargingSessionNotFoundError`; 409 `ChargingSessionStateError` (not `ACTIVE`); 403 not internal staff |

OCPP 1.6J behaviour changes (D1–D3): naive timestamps accepted; StopTransaction
and MeterValues always confirmed; Boot/Start/Status validated by the handler.

### 3.4 Tools

| Tool | Kind | Purpose |
|---|---|---|
| `seed_charging_topology.py` + `--connector-standard`, `--price-per-kwh` | run once | Provision the real charger (one EVSE per gun, location, tariff) |
| `seed_test_drivers.py` (`make charging-test-drivers-seed`) | run once | N drivers with a password and a credited wallet (D7) |
| `driver_app.py scan --driver N (--charger <code> \| --qr "<raw>") [--gun N]` | live until the session ends | Stand-in for the app: scan, live status / kWh / kW / SoC, Ctrl+C asks whether to stop, then receipt, bill and balance; writes a JSON-lines evidence file |
| `driver_app.py stop <session_id>` | run once | Stop from any terminal |
| `station_console.py messages --follow` | live until Ctrl+C | OCPP traffic feed (`/charging-stations/{id}/ocpp-messages`), idTag masked |
| `station_console.py status \| config \| set <key> <value> \| force-close <id> --reason …` | run once | Connection and connector status, configuration snapshot, `ChangeConfiguration` through the command endpoint, D5 |

Tokens are masked in everything the tools print or write (IS-07).

## 4. Steps

### Step 0 — Decisions and planner (documentation only)
- [x] This planner (written 2026-10-12); D6 confirmed by the owner (2026-10-12, recommended option)
- [x] Decision log: CO-16 (supersedes CO-10 → CO-S5), CO-17 (supersedes CO-08 → CO-S6), CE-28, CE-29 (force-close) written 2026-10-12; `deferred.md` 101 (who force-closed); D11 of the 1.6J planner and the `ocpp-reference` skill marked superseded
- [x] Feature catalog: CHG-02 gains the capability "Staff closes a session the charger never stopped", its backend goes back to `doing` until Step 1 ships it; STN-03 cites CO-16/CO-17, CHG-01 cites CE-28; regenerated, `feature-catalog-check` OK (178 features)

### Step 1 — Gateway fixes and force-close (code, tests)
- [x] D1 naive timestamps as UTC with a warning: new `parse_ocpp_timestamp_assuming_utc` in `ocpp/parsing.py` (returns whether a timezone was sent; the 2.0.1 adapter keeps the strict `parse_ocpp_timestamp`), used by every 1.6J handler and by `ocpp16_measurements.py` (counts `naive_timestamps`); the adapter logs `OCPP timestamp without a timezone read as UTC`
- [x] D2 `StopTransaction` / `MeterValues` for an unknown (`0` included) or no-longer-ACTIVE transaction: confirmed, nothing stored, warning `Transaction message confirmed without changes` (`reason` = `unknown_transaction` / `session_not_active`)
- [x] D3 Boot, Status and Start skip the library schema (`skip_schema_validation=True`); `_require_text` / `_fit_text` / `_to_int` in the adapter cut texts to their columns (device fields 100, `errorCode` 50, `vendorErrorCode` 100, `info` 50) and read `"1"` / `12345670.0`
- [x] D4 `QR_TOKEN_ALPHABET` (`A-Z0-9`) in `charging_sessions/types.py`, used by `create_pending_session`
- [x] D5/D6 `POST /charging-sessions/{id}/force-close` (`force_close_session`, CHG-02 roles, internal only); completes through the shared `_run_completed_session_hooks`; the session is read `FOR UPDATE`, and so is the stop path (`get_session_by_transaction(..., for_update=True)`), so a force-close and a late stop never interleave
- [x] The station energy total leaves out a session whose stop reading is below its start reading (`get_station_energy_summary`); the series already skipped negative deltas
- [x] One regression test per item: smoke tests in `test_ocpp16_measurements_smoke.py`, `test_ocpp16_status_smoke.py`, `test_ocpp16_transaction_smoke.py`, `test_ocpp16_boot_smoke.py`, `test_charging_sessions_service_smoke.py` (old strict-rule tests rewritten to the new rule; test fakes of the two session lookups accept the new `for_update` keyword); PostgreSQL `test_postgres_charging_test_day.py` (orphan → force-close → BILLED from `LAST_MEASUREMENT`, late stop confirmed and ignored, second force-close 409, driver no longer has a charge open; backwards meter left out of the station total, bill ON_HOLD). `make check`: 1083 passed; `make backend-test-integration`: 59 passed (the 1.6J end-to-end expectation updated to the simulator's new readings, `[1250, 1500, 1500]`)

### Step 2 — Test drivers and charger seed
- [x] `seed_test_drivers.py` + `make charging-test-drivers-seed ARGS=...`: drivers `+849990000NN` (default password `TestDay-2026`, or `CHARGING_TEST_DRIVER_PASSWORD` / `--password`), created in-process like the bootstrap (ACTIVE user, membership in the internal organization, `DRIVER` only), wallet topped up to `--balance-vnd` through `POST /wallets/{id}/adjustments`; idempotent
- [x] `seed_charging_topology.py --connector-standard` (`--price-per-kwh` already existed); `make charging-ocpp16-seed IDENTITY= STANDARD= PRICE=`

### Step 3 — `driver_app.py`
- [x] `scan | watch | stop | sessions` (`make driver-app ARGS=...`), shared helpers in `simulator/test_day_common.py` (masking, evidence log under `backend/logs/test-day/`, git-ignored; re-login on 401); `api_client.py` gained `ApiError`, `login()`, `resolve_api_url()` (`--api-url`, then `G3_API_URL`)

### Step 4 — `station_console.py`
- [x] `status | messages [--follow] | config | set | commands | sessions | force-close` (`make station-console ARGS=...`)

### Step 5 — Simulator edge cases and rehearsal
- [x] 1.6J simulator: answers `RemoteStopTransaction`; charges longer with `--samples N --sample-interval S --energy-step-wh W` and until the app stops it with `--stop-on-remote`; edge flags `--naive-timestamps`, `--uppercase-id-tag`, `--long-texts` (boot fields and status `info` over the 1.6 limits), `--float-meter-start`, `--resend-stop`, `--no-stop`
- [x] Rehearsal (`e2e-sim` skill, 2026-10-12) on a scratch database (`g3network_rehearsal`, API :8765, gateway :9765, `APP_DEBUG=false`; dropped afterwards): results in section 5

### Step 6 — Wrap-up and runbook
- [x] `finish-task`: `make check` passed; `convention-reviewer`: no blocker or major, one minor fixed (the driver seed now warns when it falls back to the public default password); `docs-sync`: architecture.md, dev-environment.md, README, the `e2e-sim` and `ocpp-reference` skills, both scenario files, pointers in the 1.6J planner, CHG-02 backend back to `done`; commits split (code, tools, docs)
- [x] Section 6 written

## 5. Evidence

**Rehearsal, 2026-10-12** (charger `WD-REHEARSAL-01`, two CCS2 guns, 3,500 VND/kWh + 8 % VAT, two seeded drivers with 10,000,000 VND):

| Run | What | Result |
|---|---|---|
| A | Scan gun 1 with every firmware quirk on (`--naive-timestamps --uppercase-id-tag --long-texts --float-meter-start --resend-stop`), charge until `driver_app stop` | Boot, Start, Status all accepted; transaction 1 `Remote`, 4.000 kWh, bill 14,000 + 1,120 VAT = **15,120 VND** BILLED from `METER_STOP`; the re-sent stop was confirmed (one `Transaction message confirmed without changes`); 0 CALLERROR |
| B | Scan without a gun, the charger ends the session itself | `driver_app` ended on its own: `EVDisconnected`, 1.2 kWh, 4,536 VND |
| C | Orphan (`--no-stop`) on gun 2 | Driver 2 refused while it was open (409 "You already have a charge open"); `station_console force-close` → `COMPLETED` / `StaffClosed`, 1.5 kWh BILLED from `LAST_MEASUREMENT`; a second force-close 409; driver 2 could scan again; that scan cancelled with `driver_app stop` → ABANDONED, bill VOID |
| D | Two drivers on the two guns at once (`--sessions 2 --stop-on-remote`) | Transactions 4 (gun 1) and 5 (gun 2), both `Remote`, billed separately (4,536 and 6,048 VND); 0 CALLERROR |
| Console | `status`, `set MeterValueSampleInterval 30`, `set NumberOfConnectors 3` | Live charger and gun status; ACCEPTED for a writable key, REJECTED for a read-only one |

Gateway log: only warnings (`OCPP timestamp without a timezone read as UTC` ×22, one confirmed repeat stop, a firmware change and a serial mismatch from `--long-texts`); no error. No QR token appears in any log or evidence file.

## 6. Test-day runbook

Written for the person running the day and the tester at the station. All
commands run **on the development VM (B)**, from the repository root, inside one
`tmux` session; the laptop is only an SSH terminal (`ssh -p 10022 <user>@<A's
public IP>`). Reach it over a phone hotspot if the station has no Wi-Fi.

### 6.1 One-time preparation (the day before)

1. **Code:** B runs the committed `master`; `make setup` once if needed.
2. **`backend/.env` on B:**
   - `APP_DEBUG=false` (it echoes SQL, frames and QR tokens otherwise);
   - `IDENTITY_BOOTSTRAP_ADMIN_PHONE` / `IDENTITY_BOOTSTRAP_ADMIN_PASSWORD` set (the console and the seeds log in as this admin);
   - `CHARGING_OCPP_HOST=0.0.0.0`, `CHARGING_OCPP_PORT=9000` (defaults).
3. **Port forward:** forward **A:9000 → B:9000** with the same tool as the SSH forward (A:10022 → B:22). Forward nothing else: the API stays on B's `localhost`.
4. **Database:** decide whether the day uses the dev database or a fresh one. A fresh one: `make db-reset` (wipes everything), then `make identity-bootstrap`.
5. **Seed the real charger** with the `chargePointId` that will be typed into its screen (prefer one that is hard to guess, `ws://` has no authentication):
   `make charging-ocpp16-seed IDENTITY=<chargePointId> STANDARD=IEC_62196_T2_COMBO PRICE=3500`
   (`GBT_DC` if the delivered unit is GB/T: check the plug).
6. **Seed the drivers:** `export CHARGING_TEST_DRIVER_PASSWORD=<a password of your own>` (the default is public, in the source), then `make charging-test-drivers-seed ARGS="--count 2 --balance-vnd 10000000"`. Keep the variable set in the `driver` window: `driver_app` logs in with it.
7. **Rehearse** with the simulator under the same identity, before the real charger is on site (section 6.4 with `make charging-ocpp16-sim SCENARIO=session IDENTITY=<chargePointId> ARGS="--stop-on-remote --samples 600 --sample-interval 5"` in place of the charger).
8. **Reachability from outside** (gateway running, from the laptop **on mobile data**, ideally the charger SIM's carrier):
   ```bash
   curl -i -N --max-time 5 -H "Connection: Upgrade" -H "Upgrade: websocket" \
     -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
     -H "Sec-WebSocket-Protocol: ocpp1.6" http://<A public IP>:9000/ocpp/REACHABILITY-TEST
   ```
   `404 Unknown OCPP station identity` = reachable. A timeout or "connection refused" = the forward or a firewall blocks it.

### 6.2 Morning: start the backend (tmux on B)

| Window | Command |
|---|---|
| `api` | `cd backend && uv run uvicorn app.api.main:app --port 8000` (no `--reload`: an edit must not restart the API mid-test) |
| `gateway` | `make charging-ocpp-dev` |
| `console` | `make station-console ARGS="messages --follow --charger <chargePointId>"` |
| `driver` | split in two for the two-gun test |
| `cmd` | one-shot commands |

Run `driver_app` directly rather than through `make` (`cd backend && uv run python ../simulator/driver_app.py ...`): under `make`, Ctrl+C makes `make` report "Interrupt" even when the charge was left running.

### 6.3 Phase 1 — connect, no charging

1. On the charger: change the default HMI password first; set the server URL to **`ws://<A public IP>:9000/ocpp/`** and the Charger ID to `<chargePointId>` (some firmware wants the full URL with the ID at the end).
2. In `console`: BootNotification, Heartbeat, the post-boot GetConfiguration and StatusNotification for connectors 0, 1, 2 appear. `make station-console ARGS="status --charger <id>"` shows online, vendor, model, firmware, gun statuses. Write down whether the timestamps carry a timezone (a `OCPP timestamp without a timezone read as UTC` warning in the gateway window means they don't).
3. `make station-console ARGS="config --charger <id>"`: record `SupportedFeatureProfiles`, `AuthorizeRemoteTxRequests`, `MeterValueSampleInterval`, `MeterValuesSampledData`, `StopTxnSampledData`.
4. Set the readings we need, each must answer ACCEPTED:
   `make station-console ARGS="set MeterValueSampleInterval 30 --charger <id>"`,
   `set MeterValuesSampledData Energy.Active.Import.Register,Power.Active.Import,SoC`,
   `set StopTxnSampledData Energy.Active.Import.Register`.
5. **Read the QR code** on the charger's screen with a phone QR reader app; photograph it and write down the raw text (open question 4).
6. Plug and unplug a gun: the status goes Available → Preparing → Available.

### 6.4 Phase 2 — a real session

1. Plug the gun into the car.
2. In `driver`: `uv run python ../simulator/driver_app.py scan --driver 1 --charger <chargePointId> --gun 1`
   (or `--qr "<raw text from step 6.3.5>"` to test the real QR content).
3. Start on the charger's screen if it asks. `driver` shows PENDING → ACTIVE, then kWh / kW / SoC every 5 s; `console` shows RemoteStartTransaction → StartTransaction → MeterValues.
4. Stop from the "app": Ctrl+C → `y` (or `driver_app stop <session_id>` from any window). `driver` prints the receipt, the bill and the balance.
5. Compare the three figures: the charger screen's kWh, the receipt's kWh, and the bill. Photograph the screen.
6. Repeat once stopping **at the charger** (its stop button) and once by **unplugging** at the car side if allowed: `driver` must end by itself.

### 6.5 Phase 3 — edge cases

| Case | How | Expected |
|---|---|---|
| Two guns | Two `driver` panes, `--driver 1 --gun 1` and `--driver 2 --gun 2` (two cars, or one car per gun in turn) | Two sessions, two bills |
| Network loss | Unplug the charger's network (or 4G antenna) for 2 minutes mid-session, then restore | The charger reconnects; readings it queued arrive; the session ends normally. Record whether queued MeterValues are re-sent |
| Charger restart | Power-cycle mid-session | If the session stays ACTIVE after the reboot: `make station-console ARGS="sessions --status ACTIVE --charger <id>"`, then `force-close <session_id> --reason "..."`; the bill comes from the last reading |
| Refused start | Scan, then do not plug in / cancel on the screen | The scan ends ABANDONED (after 5 minutes, or at once with `driver_app stop`), bill VOID |

### 6.6 Evidence to keep

- `backend/logs/test-day/*.jsonl` (every API call of the tools, tokens masked) and the gateway window's log;
- the raw OCPP log: `make station-console ARGS="messages --limit 500 --charger <id>"` (or the `charging_ocpp_messages` table);
- photos: the QR code, the charger screen at the end of each session, the configuration screens;
- for each finding: time, session ID, what was expected, what happened (the message pair from the raw log), then record it under Step 10 of the [1.6J planner](./backend-ocpp16-charger-integration.md).

### 6.7 After the day

Close port 9000 on A again (no authentication on `ws://`).
