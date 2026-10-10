# Development environment

> Read this file when you need to run/start a local service, or adjust
> environment variables. Detailed Makefile commands and a quick setup guide
> are in [README.md](../../README.md).

**Principle:** develop **directly on the host**. Only the infrastructure
pieces that don't need frequent direct code changes are Dockerized.

## Running on the host

| Component | Tool |
|---|---|
| Backend (`api`, and the entrypoints under `telemetry/ingestion`, `telematics/ingestion`, `charging_stations/ocpp`, `telematics/monitoring`, `drivers/monitoring`) | `uv` — `uv run uvicorn app.api.main:app`, `uv run python -m app.domains.telemetry.ingestion.entrypoint`... (normally through `make backend-dev`, `make telemetry-dev`, `make charging-ocpp-dev`, `make telematics-monitor-dev`) |
| Simulators (`simulator/`) | run from `backend/` with its venv: `uv run python ../simulator/...`, or the `make charging-ocpp*` targets |

## Running in Docker (`infra/docker-compose.yml`)

| Container | Component | Port (host) |
|---|---|---|
| `db` | PostgreSQL + TimescaleDB + PostGIS | 5432 |
| `broker` | EMQX | 1883 (MQTT), 18083 (dashboard) |

The backend runs on the host and connects to these 2 containers via
`localhost:5432` / `localhost:1883`.

## Common commands

Use the Makefile as the source of truth. See the full command list:

```bash
make help
```

First time on a clone: `make setup` — creates `backend/.env` and
`infra/.env` from the examples (never overwrites), runs `uv sync` (runtime
deps plus the `dev` dependency group), enables the git hook, starts the
infrastructure, waits for PostgreSQL, applies the migration and runs
`make check`. Safe to re-run.

Basic commands:
- `make infra-up` / `make infra-down` — Start / stop PostgreSQL and EMQX (data kept); `make infra-reset` removes the volumes (all data)
- `make infra-logs` — Follow the PostgreSQL and EMQX logs
- `make backend-install` — `uv sync` (runtime dependencies + `dev` group)
- `make backend-dev` — API server (port 8000, auto-reload)
- `make telemetry-dev` — MQTT telemetry ingestion
- `make charging-ocpp-dev` — OCPP gateway (accepts 2.0.1 and 1.6J on port 9000)
- `make telematics-status-dev` — T-Box status-report ingestion (`g3network/telematics/+/status`, own MQTT client id)
- `make telematics-monitor-dev` — Device-health (silence) monitor
- `make driving-sessions-autoend-dev` — Driving-session auto-end worker (`DRIVERS_*` settings)
- `make identity-bootstrap` — Create the internal organization and the first HEAD_ADMIN from `IDENTITY_BOOTSTRAP_*` (idempotent; run once per database)
- `make db-migrate` — Apply the baseline migration to an empty database
- `make db-reset` — Clear the database and rebuild it from the baseline migration (wipes all data)
- `make db-check` — Verify the database built by the migration matches the models (`alembic check`)
- `make check` — The full local gate (`make lint` = ruff check + format check, import-linter, mypy on `app` and `tests`; smoke tests; `make domain-model-check`; `make feature-catalog-check`); `make format` fixes formatting; `make install-hooks` makes git run `make check` before each commit
- `make backend-test` — Smoke tests only; `make backend-test-integration` — PostgreSQL integration tests (needs `make infra-up`)
- `make coverage` — Smoke tests with a coverage report (terminal + `backend/htmlcov/`; no threshold enforced)
- `make audit` — `pip-audit` (via `uvx`) of every locked dependency
- `make charging-ocpp-seed` / `make charging-ocpp-sim` — Provision and run the OCPP 2.0.1 simulator
- `make charging-ocpp16-seed` / `make charging-ocpp16-sim [SCENARIO=boot|status|session]` — Provision (one EVSE per gun) and run the OCPP 1.6J simulator (default scenario `boot`)

See [README.md](../../README.md) for detailed setup and quick-start
instructions.

## Environment variables

- There are 2 separate sample files: `backend/.env.example` and `infra/.env.example` (there's no shared root-level `.env.example`); `make setup` copies each to `.env` if missing. Backend settings point to `localhost` (not internal Docker service names), e.g. `DATABASE_URL=postgresql+asyncpg://...@localhost:5432/...`, `MQTT_HOST=localhost`; `infra/.env` only holds the PostgreSQL credentials read by `docker-compose.yml`.
- Every backend setting is defined (type, validation, default) in `backend/app/libs/common/config.py` and documented with its default in `backend/.env.example`, grouped by component; only `DATABASE_URL` is required. A new setting goes in both, namespaced by component.
- OCPP gateway settings (`CHARGING_OCPP_*`, `CHARGING_OFFLINE_TIMEOUT_SECONDS`): listen address/port, largest accepted frame, the heartbeat interval returned at boot (both protocols), the offline threshold behind the derived `is_online`, the timeout for a charger's answer to a gateway request, and the command channel (`CHARGING_OCPP_COMMAND_POLL_SECONDS`, `CHARGING_OCPP_COMMAND_PICKUP_TIMEOUT_SECONDS`: how often the gateway looks for queued commands, and how long a queued command may wait for a connected charger before it ends `NOT_SENT`).
- Identity settings (`IDENTITY_*`): `IDENTITY_TOKEN_SECRET_KEY` (signs access tokens and keys the one-time-code hash; empty = random per process), token/session lifetimes, login lockout, one-time-code limits, `IDENTITY_SMS_PROVIDER` / `IDENTITY_EMAIL_PROVIDER` (only the logging fake `log` exists: it writes the SMS, codes included, to the log), `IDENTITY_EMERGENCY_ADMIN_PHONE` (ACC-20) and the `IDENTITY_BOOTSTRAP_*` seed values.
- QR charging settings: `CHARGING_PENDING_SESSION_TIMEOUT_SECONDS` (a scan the charger never starts is ABANDONED after this long, default 300), `CHARGING_SESSION_SWEEP_INTERVAL_SECONDS` (how often the gateway's loop sweeps them, default 30) and `BILLING_MIN_BALANCE_VND` (wallet minimum to start a charge, `0` = off, the default until top-ups exist). The sweep runs inside `make charging-ocpp-dev`; there is no separate process.
- `APP_REPORT_TIMEZONE` (default `Asia/Ho_Chi_Minh`) is the calendar for every report or series cut into days/weeks/months/hours; stored and returned timestamps stay UTC.

## Claude Code tooling (checked in under `.claude/` and `.mcp.json`)

- **Hooks** (`.claude/settings.json`, scripts in `.claude/hooks/`): every
  Python file the agent writes under `backend/` or `simulator/` is
  import-sorted and formatted with ruff (unused imports are left alone); edits
  to the generated `docs/design/domain-model/` and
  `docs/product/features/` views are refused (edit the `.dbml` or
  `features.yaml` and regenerate).
- **Agents** (`.claude/agents/`): `docs-sync` (bring docs in line with a code
  change), `convention-reviewer` (read-only rules review of a diff),
  `schema-change` (model + DBML + baseline migration + rebuild + verify).
- **Skills** (`.claude/skills/`): `start-feature` (gather → planner from
  `docs/planners/_TEMPLATE.md` → DBML design → owner confirms), `new-domain`,
  `domain-model`, `feature-catalog` (the feature catalog's YAML source and
  its generated checklists/workbook), `ocpp-reference` (OCPP 1.6J and 2.0.1, OCPI, how other CSMSs model things), `e2e-sim` (run the stack with the
  simulators and check the data), `finish-task`. The order to use them in is
  in CLAUDE.md ("How a feature gets built").
- **MCP servers** (`.mcp.json`, approved via `enabledMcpjsonServers`):
  `postgres` — `postgres-mcp` in `--access-mode=restricted` (read-only
  transactions; a write is refused), pointed at the local dev database or at
  `G3_MCP_DATABASE_URI` when set. Its launcher pins `mcp<2`: postgres-mcp
  1.30 does not pin the `mcp` SDK and breaks on 2.x — drop the pin once a
  fixed release exists. `context7` — current library docs (ocpp, SQLAlchemy,
  FastAPI...); no API key needed.
- **Plugin**: `pyright-lsp@claude-plugins-official` (enabled in
  `.claude/settings.json`) gives the agent type diagnostics after each edit.
  Needs `pyright-langserver` on PATH: `npm install -g pyright`. Pyright finds
  `backend/.venv` on its own; mypy (in `make lint`) stays the gate.
- **Permissions**: `.claude/settings.json` pre-allows only read-only/check
  commands (`make check`, `uv run pytest`, `uv run mypy`, ...). Personal
  overrides go in the untracked `.claude/settings.local.json`.
