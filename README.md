# G3Network

G3Network is a driver assistance and electric truck fleet management system.
This repo currently holds the backend MVP, the local infrastructure and the
start of the web portal (`web-portal/`: shell and five sample pages). The
vehicle app has no source in this repo yet.

- Command reference: `make help` (the Makefile is the source of truth).
- AI coding agents: start with [CLAUDE.md](./CLAUDE.md).
- Documents: [docs/README.md](./docs/README.md) maps them all — progress in
  the [feature catalog](./docs/product/features/README.md), what exists today
  in [architecture.md](./docs/design/architecture.md), decisions in the
  [decision log](./docs/decisions/decision-log.md).

## Prerequisites

- Docker with the `docker compose` plugin (Docker Desktop or Docker Engine), running
- [`uv`](https://docs.astral.sh/uv/) — it also provisions Python 3.12, so no
  system Python is needed
- Node.js 22 (`web-portal/.nvmrc`) with corepack, which provides the pnpm
  version pinned in `web-portal/package.json`
- `make` and `git`

Only for working with Claude Code in this repo (optional):

- Node.js/npm — the Context7 MCP server runs through `npx`
- `npm install -g pyright` — the Pyright LSP plugin needs `pyright-langserver`
  on `PATH`

Shared VS Code settings live in `.vscode/` (ruff on save, mypy, pytest); the
recommended extensions are listed in `.vscode/extensions.json`.

All commands below run from the repository root.

## Quick start

```bash
make setup
```

`make setup` is safe to re-run. It:

1. creates `backend/.env` and `infra/.env` from their `.env.example` files
   (an existing `.env` is never overwritten), and writes a generated
   `IDENTITY_TOKEN_SECRET_KEY` into `backend/.env` when it is empty (the API
   refuses to start without one);
2. runs `uv sync` in `backend/` — runtime dependencies plus the `dev`
   dependency group (ruff, mypy, pytest, import-linter, pytest-cov) — and
   `pnpm install` in `web-portal/`;
3. points git at `.githooks/`, so every commit runs `make check`;
4. starts PostgreSQL and EMQX (`infra/docker-compose.yml`) and waits until
   PostgreSQL accepts connections;
5. applies the database migration (`alembic upgrade head`);
6. runs `make check`.

Manual equivalent, if you prefer to run the steps yourself:

```bash
cp backend/.env.example backend/.env
cp infra/.env.example infra/.env
# then set IDENTITY_TOKEN_SECRET_KEY in backend/.env to the output of:
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
make backend-install     # uv sync
make portal-install      # pnpm install in web-portal/
make install-hooks
make infra-up
make db-migrate          # once PostgreSQL is up
make check
```

## Daily development

Start the infrastructure if it isn't running (`make infra-up`), then run each
component you need in its own terminal and keep it running:

| Terminal | Command | What it runs |
|---|---|---|
| 1 | `make backend-dev` | API server on port 8000, auto-reload |
| 2 | `make telemetry-dev` | MQTT telemetry ingestion (consumer + worker) |
| 3 | `make charging-ocpp-dev` | OCPP gateway on port 9000 (2.0.1 and 1.6J) |
| 4 | `make telematics-monitor-dev` | Device-health monitor: alerts when a device stops reporting |
| 5 | `make telematics-status-dev` | T-Box status-report ingestion (health reports into `telematic_status_reports`) |
| 6 | `make driving-sessions-autoend-dev` | Ends driving sessions whose truck has not moved for the organization's auto-end time |

Once per database (after `make db-reset`), set `IDENTITY_BOOTSTRAP_ADMIN_PHONE` and `IDENTITY_BOOTSTRAP_ADMIN_PASSWORD` in `backend/.env` and run `make identity-bootstrap`: it creates the internal organization and the first HEAD_ADMIN, because no endpoint can.


Only the API is needed for plain CRUD work; start the others when you touch
their flow.

Addresses once everything is up:

- Web portal: <http://localhost:5173> (`make portal-dev`, see below)
- API and Swagger: <http://localhost:8000/docs> (ReDoc: <http://localhost:8000/redoc>)
- API health check: <http://localhost:8000/health>
- OCPP endpoint: `ws://localhost:9000/ocpp/<station-code>`
- EMQX dashboard: <http://localhost:18083>

## Web portal

`make setup` installs the portal's workspace (or run `make portal-install`).
Then, with the infrastructure up and a bootstrap admin created
(`make identity-bootstrap`, above):

```bash
make backend-dev        # terminal 1: the API on :8000
make portal-dev         # terminal 2: the portal on :5173, /api proxied to :8000
```

Open <http://localhost:5173> and log in with `IDENTITY_BOOTSTRAP_ADMIN_PHONE` /
`IDENTITY_BOOTSTRAP_ADMIN_PASSWORD`. Use Chrome or Firefox: the refresh token
is a `Secure` cookie, which Safari refuses on `http://localhost`. The live
map's style comes from `VITE_MAP_STYLE_URL` in `web-portal/.env` (copy
`web-portal/.env.example`; empty means the MapLibre demo style).

Page flows and screenshots run with Playwright against a mocked API (no
backend needed):

```bash
make portal-e2e-install   # once per machine: downloads Chromium
make portal-e2e           # make portal-e2e-update rewrites the screenshots
```

How to build portal screens: [web-portal-conventions.md](.claude/rules/web-portal-conventions.md)
and [web-portal-ui.md](.claude/rules/web-portal-ui.md).

## Checks

```bash
make check                      # the gate: ruff lint + format check, import-linter,
                                # mypy (app + tests), smoke tests, domain-model and
                                # feature-catalog checks, portal checks (Prettier,
                                # ESLint, tsc, Vitest, API client freshness)
make format                     # format with ruff (backend) and Prettier (portal)
make api-client                 # regenerate the portal's API client after an API change
make backend-test-integration   # PostgreSQL integration tests (needs make infra-up)
make coverage                   # smoke tests with a coverage report (backend/htmlcov/)
make audit                      # known-vulnerability check of locked dependencies (pip-audit)
make db-check                   # database built by the migration matches the models
```

There is no CI yet: the pre-commit hook enabled by `make setup` (or
`make install-hooks`) runs `make check` before every commit. Run
`make backend-test-integration` (and `make db-check` after `make db-reset`)
when you change the schema or a repository query.

The schema is a single baseline migration (`0001_baseline_schema`) during the
bootstrap phase: `make db-migrate` applies it to an empty database,
`make db-reset` wipes the database and rebuilds it from the baseline. See
[.claude/rules/database.md](.claude/rules/database.md).

## Current backend scope

What each feature's backend has built, and what is still missing, is in the
[feature catalog](./docs/product/features/README.md); the components, domains
and directory tree that exist today are in
[docs/design/architecture.md](./docs/design/architecture.md). Deferred work is
in [docs/decisions/deferred.md](./docs/decisions/deferred.md).

## Device traffic

Charger and driver-app traffic can come from the standalone simulators in
[`simulators/`](./simulators/README.md): an OCPP 1.6J charger with two guns
and a sample car, a phone page that plays the driver app (scan, charge, stop,
receipt, wallet top-up), and a charger monitor (TT-24 to TT-27 in the
[decision log](./docs/decisions/decision-log.md)). A real charger connects as
described in the next section. No simulator plays a T-Box: vehicle telemetry
comes from a real device or any MQTT client that publishes the contract in
[`mqtt-spec.md`](./docs/design/specifications/mqtt-spec.md) (needs
`make telemetry-dev` and the vehicle and telematics device provisioned through
the API). Results can be read through the APIs in Swagger.

## Connecting a real charging station

Register the station, EVSE and connector in Swagger first, with an OCPP
station code matching the device's configuration. Then configure the
charger:

```text
OCPP URL:   ws://<backend-host-address>:9000/ocpp/<station-code>
Protocol:   ocpp1.6 or ocpp2.0.1 (the gateway accepts both)
```

For an OCPP 1.6J charger register one EVSE per gun (EVSE `1` / connector `1`
for gun 1, EVSE `2` / connector `1` for gun 2, …): 1.6J connector `n` maps to
EVSE `n`. Connector `0` (the whole charger) needs no registration. Change the
charger's default HMI password before connecting it. The gateway currently
uses plain `ws://` without authentication (development mode), and the
charger must be able to reach the machine running the backend.

## Stopping

Press `Ctrl+C` in each terminal, then stop the containers:

```bash
make infra-down
```

Data is kept. `make infra-reset` (removes the volumes) and `make db-reset`
(rebuilds the schema) both delete all data.

## Repository layout

```text
backend/      FastAPI backend, Alembic migration, tests (see backend/README.md)
web-portal/   Web portal: React SPA and its own pnpm workspace (generated API client)
driver-app/   Reserved for the driver mobile app (README only)
infra/        Docker Compose for PostgreSQL/TimescaleDB/PostGIS and EMQX
docs/         Product, design, decisions, planners, reports (see docs/README.md)
.claude/      Rules, skills, agents and hooks for AI coding agents (see CLAUDE.md)
simulators/   Standalone charger, phone and charger-monitor simulators (see simulators/README.md)
.githooks/    Pre-commit hook (runs make check)
.vscode/      Shared VS Code settings
```
