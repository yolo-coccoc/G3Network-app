# Charging test-day tools — context for Claude

You are running the **charging test-day tools** of the G3 Network backend from
a copy of this folder on the user's laptop. The backend itself is **not** here:
it runs on a remote VM. These tools stand in for the pieces that are not built
yet, so a charging session can be tested end to end:

| Tool | Stands in for | Talks to the backend over |
|---|---|---|
| `driver_app.py` | the driver's mobile app (scan the QR, watch the charge, stop, receipt) | HTTP API |
| `station_console.py` | the operator portal (charger status, live OCPP messages, settings, force-close) | HTTP API |
| `ocpp16_charge_point_simulator.py` | the charger (an OCPP 1.6J dual-gun DC charger) | OCPP 1.6J WebSocket |
| `seed_charging_topology.py` | provisioning a charger (location, tariff, guns) | HTTP API |

Shared modules: `g3_api.py` (HTTP client and settings), `test_day_common.py`
(evidence log, token masking, test-driver numbering). Nothing in this folder
imports the backend's code.

The full project documentation (runbook, decisions) is in the backend
repository, not in this folder: `docs/planners/backend-charging-test-day.md`
(section 6 is the test-day runbook).

---

## 1. Where everything runs

```
Laptop (you)  ──SSH tunnel :8000──▶  A 112.137.129.163:10022 ──▶ VM B:22    (API on B's localhost:8000)
Laptop (you)  ──ws:// :9000───────▶  A 112.137.129.163:9000  ──▶ VM B:9000  (OCPP gateway)
```

- **The API (port 8000) is not on the internet.** Reach it through an SSH
  tunnel from the laptop:
  ```bash
  ssh -p 10022 -N -L 8000:127.0.0.1:8000 <user>@112.137.129.163
  ```
  `<user>` is the same account the user's VS Code uses to SSH into the VM
  (ask the user). Keep that command running in its own terminal; the tools
  then use `G3_API_URL=http://localhost:8000`.
- **The OCPP gateway (port 9000) is public:** the simulator connects to
  `ws://112.137.129.163:9000/ocpp/<charger id>` exactly as the real charger
  does.
- **The backend must be running on the VM.** You cannot start it from the
  laptop; ask the user to start it on the VM (two terminals there, each
  first running `cd ~/Workspace/G3Network-app && source backend/.env.testday.local`):
  - API: `cd backend && uv run uvicorn app.api.main:app --port 8000`
  - Gateway: `make charging-ocpp-dev`

## 2. Setup on the laptop (once)

Needs **Python 3.12**. With [uv](https://docs.astral.sh/uv/) (recommended):

```bash
cd charging_test_day            # this folder
uv venv --python 3.12
uv pip install --python .venv -r requirements.txt
```
(`--python .venv` makes uv install into this folder's venv even when another
virtualenv is active in the shell.)

Then run every tool with the venv's Python:
- macOS / Linux: `.venv/bin/python <tool>.py ...`
- Windows: `.venv\Scripts\python <tool>.py ...`

(Without uv: `python3.12 -m venv .venv`, then `.venv/bin/pip install -r requirements.txt`.)
Only the charger simulator needs the two libraries; the other tools use the
standard library. In the examples below, `PY` means that Python.

**Settings** come from `.env` in this folder (template: `.env.example`).
Process environment variables override it. Keys:

| Key | Meaning |
|---|---|
| `G3_API_URL` | the API, `http://localhost:8000` through the tunnel |
| `G3_OCPP_URL` | the gateway, `ws://112.137.129.163:9000` |
| `G3_ADMIN_PHONE`, `G3_ADMIN_PASSWORD` | the bootstrap HEAD_ADMIN (console, topology seed) |
| `CHARGING_TEST_DRIVER_PASSWORD` | password of the test drivers |
| `TEST_DAY_CHARGER_ID` | default charger for `--charger` / `--identity` (`G3WD-DHR75E`) |

`.env` holds passwords: **never print it, paste it into a reply, or commit it.**

**Check before any test:**
```bash
curl -s http://localhost:8000/health        # {"status":"healthy",...}: tunnel and API OK
PY station_console.py status                # charger online? guns Available?
```

## 3. Rules

1. **Never run the simulator as `G3WD-DHR75E` while the real charger is
   connected.** That is the real charger's ID: two connections with one ID
   fight each other. Check first with `PY station_console.py status`: if it
   says `online: yes` with a firmware other than `OCPP_L4.05_SIM` (the
   simulator's; the vendor reads `Willdigits` for both), or the user says the
   real charger is on, use your own simulated charger instead:
   ```bash
   PY seed_charging_topology.py --protocol 1.6 --identity SIM-LAPTOP-01 --display-name "Laptop simulator"
   ```
   and pass `--identity SIM-LAPTOP-01` (simulator) / `--charger SIM-LAPTOP-01`
   (other tools) everywhere. Seeding an identity that already exists fails;
   that is fine, it means it is already there.
2. **Test drivers exist on the server only:** driver 1 = `+84999000001`,
   driver 2 = `+84999000002` (`--driver 1|2`). Each charge debits their wallet;
   they start with 10,000,000 VND. If a scan answers `INSUFFICIENT_BALANCE`,
   ask the user to run `make charging-test-drivers-seed ARGS="--count 2 --balance-vnd 10000000"`
   on the VM (it cannot run here: it writes to the database).
3. A driver can have **one charge open at a time**; finish or stop it before
   the next scan (409 "You already have a charge open").
4. Tokens are masked by the tools (`RK…`); keep it that way in your reports.
5. Leave nothing running when you finish: stop the simulator and any
   `driver_app` watch, and tell the user to stop the gateway on the VM
   (port 9000 is open to the internet and `ws://` has no authentication).

## 4. The tools

### `driver_app.py` (the app)
```bash
PY driver_app.py scan --driver 1 --gun 1                 # scan TEST_DAY_CHARGER_ID's gun 1, then watch
PY driver_app.py scan --driver 1 --charger SIM-LAPTOP-01 --gun 2
PY driver_app.py scan --driver 1 --qr "<raw QR text>"    # what a phone QR reader showed, sent unchanged
PY driver_app.py stop <session_id> --driver 1            # stop from the app (queues RemoteStopTransaction)
PY driver_app.py watch <session_id> --driver 1
PY driver_app.py sessions --driver 1
```
`scan` prints the session (PENDING, price, masked token), then one line every
`--interval` seconds (default 5): status, kWh, kW, SoC, time, `gun: <state>`
(`charging`, `paused: car full or car paused`, `FAULT <code>`…). It ends by
itself on COMPLETED/ABANDONED and prints the receipt, the bill and the wallet.
Interactively, Ctrl+C asks "Stop the charge? [y/N]"; **from a script, use
`driver_app.py stop <id>` instead** (run `scan` in the background, read the
session ID from its output).

### `station_console.py` (the operator)
```bash
PY station_console.py status                              # charger + guns
PY station_console.py messages --limit 40                 # recent OCPP frames (!! = CALLERROR)
PY station_console.py messages --follow                   # live feed (Ctrl+C to end)
PY station_console.py config                              # the charger's reported settings
PY station_console.py set MeterValueSampleInterval 30     # ChangeConfiguration; must say ACCEPTED
PY station_console.py commands --limit 10
PY station_console.py sessions --status ACTIVE
PY station_console.py force-close <session_id> --charger G3WD-DHR75E --reason "..."   # orphan
```

### `ocpp16_charge_point_simulator.py` (the charger)
The model: a remote start (the backend's answer to a scan) only **authorizes**;
the charge starts when the gun is plugged in and started. `Authorize` is sent
first when `AuthorizeRemoteTxRequests=true`; an unused authorization expires
after `ConnectionTimeOut` (60 s). Readings every `MeterValueSampleInterval`
(or `--sample-interval`); a full car goes `SuspendedEV`. Meters persist per
charger in `logs/simulator-state/` (`--fresh-meter` restarts at 1000 Wh).

Scripted (best for you, cross-platform): `--scenario session` with
- `--plug before-scan|after-scan|never`, `--plug-delay S`
- `--start auto|screen`, `--press-start-delay S`
- `--end unplug|local|remote|full|power-loss`, `--samples N` (readings before unplug/local/power-loss)
- `--sample-interval S`, `--sessions 2` (two drivers, guns 1 and 2)
- `--config KEY=VALUE` (e.g. `AuthorizeRemoteTxRequests=true`, `ConnectionTimeOut=20`)
- `--remote-start-requires-gun`, `--id-tag TAG` (a card instead of a scan)
- firmware quirks: `--naive-timestamps --uppercase-id-tag --long-texts --float-meter-start --resend-stop`

It prints one line per event (`STATUS gun 1 -> Charging`, `TRANSACTION …`,
`STOPPING … reason=Remote`, `CALLERROR …`) and exits 1 if the gateway answered
any message with a CALLERROR.

Interactive (for the user): `--scenario charger`, then type `plug 1`,
`start 1`, `stop 1`, `unplug 1`, `full 1`, `card 1 TAG`, `fault 1`, `clear 1`,
`status`, `power-loss`, `quit`, `help`. You can drive it from a command file:
`tail -f cmds.txt | PY ocpp16_charge_point_simulator.py --scenario charger --start screen`
(PowerShell: `Get-Content cmds.txt -Wait | ...`), appending one command per line.

## 5. Test recipes

Run the simulator in the background (output to a file), wait until it prints
`WAITING for RemoteStartTransaction`, then scan. `CHG` is the charger ID
(`G3WD-DHR75E`, or `SIM-LAPTOP-01` per rule 1).

| # | Test | Simulator | Driver side | Expected |
|---|---|---|---|---|
| A | Stop from the app | `--scenario session --identity CHG --end remote --sample-interval 3` | `scan --driver 1 --charger CHG --gun 1` (background); after ~15 s `stop <id> --driver 1` | Remote start Accepted → StartTransaction → Charging → readings → RemoteStop → `reason=Remote`; bill BILLED (`METER_STOP`) |
| B | Stop at the charger | `--start screen --end local --samples 4 --sample-interval 3` | `scan … --gun 1` | Start only after "driver presses start"; `reason=Local`; the scan ends by itself |
| C | Unplug while charging | `--end unplug --samples 4 --sample-interval 3` | `scan … --gun 1` | `reason=EVDisconnected` |
| D | Car full | `--end full --start-soc 97 --battery-kwh 2 --sample-interval 2` | `scan … --gun 1` | `STATUS gun 1 -> SuspendedEV`, driver line `gun: paused: car full…`, power 0 |
| E1 | Scan, never plug | `--plug never --config ConnectionTimeOut=20` | `scan … --gun 1` | Simulator: `AUTHORIZATION EXPIRED` after 20 s; backend ABANDONED after ≤70 s (or `stop <id>` cancels at once, bill VOID) |
| E2 | Plug after the scan | `--plug after-scan --plug-delay 10 --end remote --sample-interval 3` | `scan … --gun 1`, then `stop <id>` | Charge starts after the plug |
| F | Authorize first | `--config AuthorizeRemoteTxRequests=true --end remote --sample-interval 3` | as A | `ACK action=Authorize … Accepted` right after the remote start |
| G | Power loss (orphan) | `--end power-loss --samples 3 --sample-interval 3` | `scan … --gun 1` | Simulator exits; session stays ACTIVE. Then `station_console.py sessions --status ACTIVE` and `force-close <id> --charger CHG --reason "test"` → COMPLETED, `StaffClosed`, bill from `LAST_MEASUREMENT` |
| H | Two guns | `--sessions 2 --end remote --sample-interval 3` | `scan --driver 1 --gun 1` and `scan --driver 2 --gun 2` (both background), then stop each | Two transactions, two bills |
| I | Unknown card | `--id-tag ABC123` | — | `Authorize … Invalid`, `CARD refused` |
| Q | Firmware quirks | `--end remote --sample-interval 3 --naive-timestamps --uppercase-id-tag --long-texts --float-meter-start --resend-stop` | as A | No CALLERROR; the repeated stop is confirmed |
| R | Gun must be named | `--remote-start-requires-gun --remote-start-timeout 20` | `scan --driver 1 --charger CHG` (no `--gun`) | Remote start `Rejected`; the scan ends ABANDONED within seconds; the simulator exits 1 after 20 s ("no RemoteStartTransaction arrived": expected here) |

After each test: `PY station_console.py messages --limit 30` (no `!!` lines
expected) and `PY station_console.py commands --limit 5`.

## 6. Reporting

Report a table: test, expected, observed, pass/fail, session ID. For any
failure include the exact simulator lines, the `driver_app` lines, and the
console's messages around it. Evidence files: `logs/test-day/*.jsonl`
(tokens masked). Do not change the tools' code to make a test pass; describe
the failure instead.

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Could not call … Connection refused` on `localhost:8000` | Tunnel not running, or the API is not started on the VM |
| Simulator: connection refused / timeout to `112.137.129.163:9000` | Gateway not started on the VM, or the network blocks port 9000 |
| Simulator: HTTP 404 at the handshake | The charger ID is not provisioned (seed it, rule 1) |
| `409 … offline` on scan | The simulator is not connected yet: start it first, wait for `WAITING…` |
| `409 You already have a charge open` | Stop or force-close the driver's previous session |
| `409 INSUFFICIENT_BALANCE` | Ask the user to top up (rule 2) |
| `401` | Wrong password in `.env` |
| `ModuleNotFoundError: ocpp` | Use the venv's Python (section 2) |
