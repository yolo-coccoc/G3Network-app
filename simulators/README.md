# Simulators and the test day: how to run and use them

Three standalone tools stand in for what is not built yet, so a charge can be
run end to end against the real backend:

| Tool | Stands in for | Runs on | Page |
|---|---|---|---|
| [charger_simulator](./charger_simulator/README.md) | a charger (OCPP 1.6J, 2 guns, a sample car) | your laptop (or the VM) | `http://127.0.0.1:8090/` |
| [phone_simulator](./phone_simulator/README.md) | the driver's mobile app | the VM | `http://112.137.129.163:8093/` (public) |
| [charger_monitor](./charger_monitor/README.md) | the staff portal: traffic, configuration, chargers | the VM | `http://127.0.0.1:8095/` (through a port forward) |

Each tool reads only its own `.env` (copied from its `.env.example`; never
committed). Passwords are not written here: each section says which `.env`
holds them.

## 1. The picture

```
Laptop                                  A 112.137.129.163 (public)          VM B 192.168.122.160
charger_simulator ──ws:// :9000──────▶  forward 9000 ─────────────────────▶  OCPP gateway :9000 ─┐
browser ──────────http :8093─────────▶  forward 8093 ─────────────────────▶  phone_simulator ────┤──▶ API :8000 ──▶ PostgreSQL
browser ──VS Code port forward :8095─▶  (SSH, port 10022) ─────────────────▶  charger_monitor ────┘     (VM only)
```

- The **API (8000)** listens only on the VM. The phone and the monitor run on
  the VM, so they reach it directly. From a laptop it needs an SSH tunnel
  (`ssh -p 10022 -N -L 8000:127.0.0.1:8000 <user>@112.137.129.163`).
- The **gateway (9000)** and the **phone page (8093)** are public through A's
  port forwards; `ws://` and the phone page have **no password**, so open
  those forwards only while testing.
- The **monitor (8095)** stays private: open it through VS Code's **Ports**
  tab (forward 8095) or `ssh -p 10022 -N -L 8095:127.0.0.1:8095 <user>@112.137.129.163`.

## 2. Start everything (in this order)

Use **fresh terminals or tmux windows**: a terminal that once loaded other
settings (an old `source ...` of test-day variables) keeps them and points the
backend at the wrong database.

**On the VM** (`cd ~/Workspace/G3Network-app` first):

| # | What | Command | Ready when |
|---|---|---|---|
| 1 | Database and broker | `make infra-up` (once after a VM restart) | `docker ps` shows `g3network-db` healthy |
| 2 | API | `cd backend && uv run uvicorn app.api.main:app --port 8000` | `Application startup complete` |
| 3 | OCPP gateway | `APP_DEBUG=false make charging-ocpp-dev` | `OCPP gateway started ... port 9000` |
| 4 | Charger monitor | `cd simulators/charger_monitor && python3 charger_monitor.py` | `logged in; N charger(s) visible` |
| 5 | Phone simulator | `cd simulators/phone_simulator && python3 phone_simulator.py` | `phone on ... simulated bank on` |

`APP_DEBUG=false` keeps the gateway from printing every SQL query; heartbeats
and frames are not printed either way. Watch the traffic in the monitor.

**On the laptop** (the charger simulator):

```bash
cd G3Network-app && git pull
cd simulators/charger_simulator
uv venv --python 3.12 && uv pip install --python .venv -r requirements.txt   # once
cp .env.example .env                                                        # once, then edit
.venv/bin/python charger_simulator.py          # Windows: .venv\Scripts\python charger_simulator.py
```

In its `.env`: `CHARGER_SERVER_URL=ws://112.137.129.163:9000/ocpp/` and
`CHARGER_ID=` one of the simulator chargers below. The page shows **online**
and both guns **Available** once it is connected.

## 3. Accounts and chargers

| Account | Phone | Password | Used for |
|---|---|---|---|
| Test Driver | `+84911000001` | `driver01` | the phone (prefilled) |
| Test Driver 2 | `+84911000002` | `driver02` | a second phone / a second charge |
| HEAD_ADMIN | `+84900000001` | in `simulators/charger_monitor/.env` | the monitor, the phone's simulated bank, force-close |

| Charger ID | Location | What it is |
|---|---|---|
| `SIM-OCPP16-001` | OCPP 1.6J Simulator depot | the everyday simulator charger |
| `SIM-TESTDAY-01` | G3 test-day station | a simulator charger at the test-day location, to rehearse it |
| `G3WD-DHR75E` | G3 test-day station | **the real charger** (label `WD-01`); never use this ID in a simulator |

Both locations charge 3,500 VND/kWh + 8 % VAT. Chargers, guns, locations and
prices are managed in the monitor's **Chargers** tab.

**After a database reset** (`make db-reset`) all of this is gone. Rebuild it:

1. `make identity-bootstrap` with `IDENTITY_BOOTSTRAP_ADMIN_PHONE` /
   `IDENTITY_BOOTSTRAP_ADMIN_PASSWORD` in `backend/.env` set to the same
   account as `MONITOR_ADMIN_*` and `PHONE_BANK_ADMIN_*`. If the passwords
   differ, the monitor and the bank cannot log in. Fix either side, or reset
   the HEAD_ADMIN's password with the SMS code flow
   (`/auth/otp/send` with purpose `PASSWORD_RESET`, then `/auth/password/reset`;
   the code is in the API log as `fake_sms_sent`).
2. Monitor, **Chargers**: register the three chargers of the table above
   (two `IEC_62196_T2_COMBO` guns each; `WD-01`: 240 kW, 1000 V, 300 A per
   gun) with the price. Leave the real charger's serial and the test-day
   address as placeholders until the test day.
3. Sign up the test drivers on the phone numbers above
   (`/auth/otp/send` with purpose `SIGN_UP`, then `/auth/sign-up`), and top
   them up from the phone.

## 4. A full charge

1. **Charger page:** on Gun 1, set the car's battery % and press **Plug in**
   (the gun goes `Preparing`). The speed buttons (x10, x60) make the charge
   itself faster.
2. **Phone:** log in, **Scan QR to charge**, type the charger ID (e.g.
   `SIM-TESTDAY-01`), choose **Gun 1**, **Start charging**.
3. The phone shows **Waiting**, then **Charging** (battery %, energy, power,
   cost). The charger page shows `Charging`; the monitor's **Monitoring** tab
   shows `RemoteStartTransaction`, `Authorize`, `StartTransaction`,
   `MeterValues`.
4. **Phone:** **Stop charging**, then the **Receipt** (energy, price, VAT,
   total, paid from the wallet).
5. **Charger page:** **Unplug** (the gun goes `Available`).

Other ways to end a charge: **Stop** on the charger page (the screen), or
**Unplug** while charging. Scanning first and plugging in afterwards also works,
within `ConnectionTimeOut` (60 s).

**Top up a wallet:** phone, **Wallet**, choose an amount, **Get VietQR
code**, **Pay with bank app (simulated)**. A charge needs a balance of at
least 0 VND; one that costs more leaves the wallet negative, and the next scan
is refused until a top-up.

Readings reach the phone every `MeterValueSampleInterval` (60 s by default):
set it lower in the monitor's **Configuration** tab (**Send**) for faster
updates. The simulator goes back to its defaults when restarted.

## 5. Test-day checklist

### The day before
- [ ] `backend/.env` has `CHARGING_PENDING_SESSION_TIMEOUT_SECONDS=60`, so a
      scan the charger forgets (after its `ConnectionTimeOut`) does not block
      the driver for 5 minutes; restart the API and the gateway after changing it.
- [ ] Rehearse a full charge on `SIM-TESTDAY-01` (section 4).
- [ ] Top up both test drivers.
- [ ] A's port forwards for **9000** and **8093** are open; from a phone on
      mobile data, `http://112.137.129.163:8093/` opens the phone page.

### At the charger
- [ ] Everything in section 2 is running; the monitor is open on **Monitoring**.
- [ ] Monitor, **Chargers**, **Edit** `G3WD-DHR75E`: the serial number from
      the nameplate, the location's real address; **Save changes**.
- [ ] On the charger's screen: server URL `ws://112.137.129.163:9000/ocpp/`,
      Charger ID `G3WD-DHR75E` (some firmware wants the full URL with the ID at
      the end). Change the default HMI password first.
- [ ] Monitor: the charger turns **online**; **Monitoring** shows
      `BootNotification` (vendor, model, firmware), `StatusNotification` for
      connectors 0, 1, 2 and the heartbeats.
- [ ] **Configuration**, **Get**: save or photograph the whole list, and note
      which keys are read-only. Then **Send** `MeterValueSampleInterval = 30`
      and, if needed, `ConnectionTimeOut = 60`.
- [ ] Read the QR code on the charger's screen with any QR app; write down
      its exact content.
- [ ] Charge on gun 1 (section 4, charger `G3WD-DHR75E`), stop from the phone.
      Compare the kWh on the charger's screen, the receipt and the bill.
- [ ] Charge again and stop **at the charger**; then once more and stop by
      **unplugging** (if allowed).
- [ ] Charge on gun 2 (with Test Driver 2 if gun 1 is busy).
- [ ] Scan first, plug in second; note whether the charger needs **Start**
      pressed on its screen.

### What to record
- Each finding: the time, the charger and gun, what was expected, what
  happened. The monitor's **Monitoring** tab shows the messages (click a row
  for the full frame).
- Photos: the QR code, the charger's screen at the end of each charge, its
  configuration screens.

### After
- [ ] Close A's port forwards for **9000** and **8093** (no password on either).

## 6. When something goes wrong

| What you see | Likely cause | What to do |
|---|---|---|
| Charger page: **offline**, "cannot reach the server" | gateway not running, or port 9000 not forwarded | start the gateway (section 2, step 3); check A's forward |
| Charger page: "HTTP 404" | the charger ID is not registered | check the ID in the monitor's **Chargers** tab (exact spelling) |
| Gateway or API: `database "..." does not exist` | the terminal still holds old settings | close it and open a fresh one |
| Phone: a scan is refused | charger offline, gun busy, wallet negative, no price at the location | read the phone's message; top up, or check the **Chargers** tab (price) |
| Phone: **Waiting** never ends | the gun was not plugged in in time | wait for it to end (or **Cancel**), plug in, scan again |
| A charge stays **Charging** although the charger rebooted or lost power | the charger never sent its stop | **force-close it** (below) |
| Monitor: "backend API problem" | the API stopped | start the API (section 2, step 2) |

**Force-closing a stuck charge.** Only our staff can do it; it ends the
charge now and bills it from the last reading (or puts the bill on hold).
Ask Claude Code on the VM ("the charge on gun 1 of `G3WD-DHR75E` is stuck,
force-close it"), or do it yourself in the API's own page:

1. VS Code **Ports**: forward `8000`; open `http://127.0.0.1:8000/docs`.
2. **POST `/api/v1/auth/login`**, *Try it out*, body
   `{"phone_number": "+84900000001", "password": "<HEAD_ADMIN password>", "platform": "WEB"}`,
   *Execute*; copy `access_token`.
3. **Authorize** (top right), paste the token.
4. **GET `/api/v1/charging-sessions`** with `status = ACTIVE`: copy the
   stuck charge's `session_id`.
5. **POST `/api/v1/charging-sessions/{session_id}/force-close`**, body
   `{"reason": "Charger rebooted mid-session"}`. Expected: `200`,
   `status: COMPLETED`, `stop_reason: StaffClosed`.

`409` means the charge is no longer `ACTIVE`; `401` means the token expired
(15 minutes): repeat steps 2 and 3.
