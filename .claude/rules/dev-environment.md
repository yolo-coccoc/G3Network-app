# Development environment

> Read this file when you need to run/start a local service, or adjust
> environment variables. Detailed Makefile commands and a quick setup guide
> are in [README.md](../../README.md).

**Principle:** develop **directly on the host**. Only the infrastructure
pieces that don't need frequent direct code changes are Dockerized.

## Running on the host

| Component | Tool |
|---|---|
| Backend (`api`, and the entrypoints under `telemetry/ingestion`, `charging_stations/ocpp`) | `uv` — `uv run uvicorn app.api.main:app`, `uv run python -m app.domains.telemetry.ingestion.entrypoint`... |

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

Basic commands:
- `make infra-up` — Start infrastructure (PostgreSQL)
- `make backend-install` — Install dependencies
- `make backend-dev` — Run the backend server
- `make db-migrate` — Run database migrations
- `make db-reset` — Clear the database and rebuild it from the baseline migration
- `make db-check` — Verify the rebuilt database matches the models (`alembic check`)
- `make check` — The full local gate (ruff, import-linter, mypy, smoke tests, domain-model check); `make format` fixes formatting; `make install-hooks` makes git run `make check` before each commit
- `make backend-test-integration` — PostgreSQL integration tests (needs `make infra-up`)
- `make charging-ocpp-dev` — Run the OCPP gateway (accepts 2.0.1 and 1.6J on port 9000)
- `make charging-ocpp-seed` / `make charging-ocpp-sim` — Provision and run the OCPP 2.0.1 simulator
- `make charging-ocpp16-seed` / `make charging-ocpp16-sim` — Provision (one EVSE per gun) and run the OCPP 1.6J simulator (`--scenario boot|status|session`)

See [README.md](../../README.md) for detailed setup and quick-start
instructions.

## Environment variables

- There are 2 separate sample files: `backend/.env.example` and `infra/.env.example` (there's no shared root-level `.env.example`). Both point to `localhost` (not internal Docker service names), e.g. `DATABASE_URL=postgresql://...@localhost:5432/...`, `MQTT_HOST=localhost`.
- OCPP gateway settings (`backend/.env.example`): `CHARGING_OCPP_HOST`/`CHARGING_OCPP_PORT`, `CHARGING_OCPP_MAX_MESSAGE_BYTES` (largest accepted frame, default 1 MiB), `CHARGING_OCPP_HEARTBEAT_INTERVAL_SECONDS` (interval returned to a 1.6J charger, default 60), `CHARGING_OFFLINE_TIMEOUT_SECONDS` (no frame for this long = offline, default 180) and `CHARGING_OCPP_REQUEST_TIMEOUT_SECONDS` (wait for a charger's answer to a request the gateway sends, default 30).

## Claude Code tooling (checked in under `.claude/` and `.mcp.json`)

- **Hooks** (`.claude/settings.json`, scripts in `.claude/hooks/`): every
  Python file the agent writes under `backend/` or `simulator/` is
  import-sorted and formatted with ruff (unused imports are left alone); edits
  to the generated `docs/01-requirements/domain-model/` views are refused
  (edit the `.dbml` and regenerate).
- **Agents** (`.claude/agents/`): `docs-sync` (bring docs in line with a code
  change), `convention-reviewer` (read-only rules review of a diff),
  `schema-change` (model + DBML + baseline migration + rebuild + verify).
- **Skills** (`.claude/skills/`): `domain-model`, `ocpp16-reference`,
  `e2e-sim` (run the stack with the simulators and check the data),
  `new-domain`, `finish-task`.
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
