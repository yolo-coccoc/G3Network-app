# Charger simulator

How the three simulators fit together, and the test-day checklist: [../README.md](../README.md).

A simulated OCPP 1.6J dual-gun DC charger (240 kW shared by two guns).
Starting it is like switching a charger on: it connects to the central system,
sends its startup message (`BootNotification`) and the status of each gun,
then a `Heartbeat` every interval, and reconnects by itself when the
connection drops. It runs until you stop it (Ctrl+C).

## Charging (the phone QR flow)

It follows the happy path our backend supports (decision TT-26): a charge is
started by the phone app's QR scan and stopped from the app, on the charger's
screen, or by unplugging. On its page (`http://127.0.0.1:8090/`), per gun:

- **Plug in / Unplug** (the physical action), with the car's battery level at
  plug-in. The car is a sample EV (75 kWh, up to 150 kW) whose battery
  management drives the charge: full power to 80 %, then less, and nothing at
  100 % (the gun goes `SuspendedEV`).
- **Stop** (the charger's screen).
- Live readings: SoC, power, power offered, voltage, current, stage, energy,
  charging time, transaction.

What the charger sends: plug in -> `Preparing`; the backend's
`RemoteStartTransaction` -> `Authorize` -> `StartTransaction` -> `Charging`
(scan first and plug in within `ConnectionTimeOut`, or plug in first);
`MeterValues` every `MeterValueSampleInterval` with the measurands in
`MeterValuesSampledData`; a stop -> `StopTransaction` (`Remote`, `Local` or
`EVDisconnected`) -> `Finishing`, then `Available` once unplugged. The speed
buttons (x1, x10, x60) make the charge itself run faster; the OCPP intervals
stay in real seconds. Not simulated: offline buffering, RFID cards,
Autocharge, faults and the E-stop.

## Configuration

It reports its configuration when asked (`GetConfiguration`) and applies
changes (`ChangeConfiguration`): 22 OCPP 1.6 Core keys, listed in
`configuration.py`. An unknown key is answered `NotSupported`; a read-only key
(`NumberOfConnectors`, `SupportedFeatureProfiles`, `GetConfigurationMaxKeys`)
or a value of the wrong type is `Rejected`. A new `HeartbeatInterval` takes
effect at once. The configuration starts from the defaults at every start.

The folder is self-contained: copy it anywhere. It needs Python 3.12 and two
libraries, and reads only its own `.env`.

## Set up (once)

```bash
uv venv --python 3.12
uv pip install --python .venv -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
```

Without uv: `python3.12 -m venv .venv`, then `.venv/bin/pip install -r requirements.txt`.

In `.env`, set at least `CHARGER_SERVER_URL` (e.g. `ws://<server ip>:9000/ocpp/`)
and `CHARGER_ID` (the server must know this charger). Every key is explained in
`.env.example`.

## Run

```bash
.venv/bin/python charger_simulator.py       # Windows: .venv\Scripts\python charger_simulator.py
```

Then open the page it prints (`http://127.0.0.1:8090/` by default). Like a
charger's screen, it shows the charger and its connection. The OCPP messages it
sends and receives are written to the console only; watching that traffic
belongs to a monitoring page on the server side.

`--config other.env` runs a second charger with its own settings (give it
another `CHARGER_ID` and `CHARGER_UI_PORT`).
