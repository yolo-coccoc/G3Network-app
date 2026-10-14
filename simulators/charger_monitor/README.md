# Charger monitor

A web page for the OCPP chargers connected to the backend, with two tabs:

- **Monitoring**: for each charger, its connection, the startup message
  (BootNotification and the backend's answer), a heartbeat interval check, and
  a live feed of every message (tokens masked).
- **Configuration**: choose a charger, then **Get** its configuration (shown
  read-only) or **Send** changes: fill in only the settings to change, a blank
  field stays as it is. Each filled field becomes one `ChangeConfiguration`
  command through the backend; the charger's answer (`Accepted`, `Rejected`,
  `NotSupported`, `RebootRequired`) is shown per setting, and the
  configuration is read back afterwards.

It runs next to the backend (on the VM) and reads the backend's OCPP message
log through the HTTP API, logged in as an internal staff account. It needs no
third-party library (Python 3.12 standard library only) and reads only its
own `.env`, so the folder can be copied anywhere.

## Set up (once)

```bash
cp .env.example .env
```

In `.env`, set `MONITOR_ADMIN_PHONE` and `MONITOR_ADMIN_PASSWORD` to an
internal staff account (the bootstrap HEAD_ADMIN works). `MONITOR_API_URL`
defaults to the API on the same machine (`http://127.0.0.1:8000`).

## Run

The backend API must be running (`make backend-dev`), and the OCPP gateway
(`make charging-ocpp-dev`) is what fills the message log. Then:

```bash
python3 charger_monitor.py
```

Open `http://127.0.0.1:8095/`. From a laptop, forward the port first: VS
Code's Remote-SSH does it from the **Ports** tab, or
`ssh -p 10022 -N -L 8095:127.0.0.1:8095 <user>@<server>`.

Every refresh that brings new frames writes one access-audit row on the
backend (reading frame text is audited); the monitor asks for frames only when
new traffic arrived.

The Configuration tab changes chargers, and the page has no login of its own:
keep `MONITOR_UI_HOST=127.0.0.1` and reach it through a port forward. Its
change requests are accepted only as JSON from the page itself, so another
website open in the same browser cannot send them.
