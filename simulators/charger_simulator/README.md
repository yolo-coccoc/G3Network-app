# Charger simulator

A simulated OCPP 1.6J charger. Starting it is like switching a charger on: it
connects to the central system, sends its startup message
(`BootNotification`), then a `Heartbeat` every interval, and reconnects by
itself when the connection drops. It runs until you stop it (Ctrl+C).

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
