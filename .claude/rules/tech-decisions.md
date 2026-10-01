# Finalized technical decisions (backend & infra)

> Scope: only decisions for the parts that have active source (`backend/`,
> `infra/`). Read this file when you need to know/reconfirm a finalized
> technology choice, or when evaluating a new technology. General rules in
> [`../../CLAUDE.md`](../../CLAUDE.md).

Revisit the table below if something looks off — these are current
decisions, not immutable ones.

| Item | Choice | Notes |
|---|---|---|
| Backend | **Python 3.12 + FastAPI** | finalized |
| Backend environment/dependency management | **`uv`** (`pyproject.toml` + `uv.lock`) | finalized |
| Database | **PostgreSQL 16 + TimescaleDB (time-series) + PostGIS (geo)** | finalized |
| Message broker (IoT data ingest from telematic devices) | **EMQX 5.5** | finalized - Note: EMQX 5.x doesn't use an `acl.conf` file like EMQX 4.x; ACLs are configured via the Dashboard UI or REST API |
| OCPP Gateway (charging station communication) | Lives **inside the `charging_stations` domain** (`charging_stations/ocpp/` directory), supports **OCPP 2.0.1** (`ocpp.v201`) **and OCPP 1.6J** (`ocpp.v16`), both via the MobilityHouse library (GitHub repo name `python-ocpp`, but the pip package/`pyproject.toml` dependency is **`ocpp`**). One adapter class per protocol, chosen by the negotiated WebSocket subprotocol (`ocpp2.0.1` / `ocpp1.6`); the first real charger (Willdigits DC, 240–480 kW) speaks 1.6J; runs via its own entrypoint | The gateway owns the WebSocket/OCPP lifecycle; the `charging_sessions` domain receives session events via a public service and doesn't own the WebSocket. Every frame is stored verbatim (`charging_ocpp_messages`). Connector status from `StatusNotification` is stored as reported (incl. 1.6J `errorCode`/`vendorErrorCode`/`info`; connector `0` on the station); for 1.6J the charger's boot info, configuration and `last_seen_at` are stored, and `is_online` is derived from `last_seen_at` at read time. Status history and fault alerting are not built. Dev mode still allows no TLS/no authentication; the production security profile is undecided (see `docs/02-planners/backend-ocpp16-charger-integration.md`, D12) |
| Charging MVP ideal | The local MVP assumes messages arrive in order without duplicates and topology is pre-provisioned; only the session happy path is kept (2.0.1 `Started → Updated/MeterValues → Ended`, 1.6J `StartTransaction → MeterValues → StopTransaction`). Liveness is observed (derived `is_online`), but nothing reacts to a charger going offline | The reliability path (retry, DLQ, duplicate/out-of-order handling, reconnect/offline recovery) is deferred in `docs/01-requirements/future.md` item 27 |
| OCPP security MVP | Development allows OCPP connections without TLS/without authentication in an isolated environment; production must finalize its own security profile before real deployment | Don't treat dev mode as a production security profile or OCPP certification criterion |
| Charging business boundary | Split into **`charging_stations`** (station/EVSE/connector topology and OCPP) and **`charging_sessions`** (receiving/storing events, meter samples, and session lifecycle) | The charging MVP manages pre-provisioned topology, OCPP (incl. reported status and 1.6J liveness/configuration) and stores happy-path sessions; authorization (every `idTag` is accepted), remote control, pricing, payment and debt are deferred (`future.md` item 26); do not merge these back into a single `charging` domain |
| Reverse proxy / API Gateway | **Not used in the dev environment** (each component runs on its own port on the host, called directly via `localhost`) | reconsider (Traefik/Nginx) when building `docker-compose.prod.yml` |
| Development environment | **Dev directly on the host**; only the infrastructure pieces that don't need direct code changes are Dockerized (`db`, `broker`) | see [dev-environment.md](./dev-environment.md) |
| Formatter and linter | **Ruff** only (format + lint, line length 88; replaced Black + isort on 2026-10-01). Lint rules `I` (import sorting) and `ICN003` (a domain's `service`/`repository` is imported as a module alias) | `make format` / `make lint`; config in `backend/pyproject.toml` |
| Domain-boundary enforcement | **import-linter**: one `forbidden` contract per domain, so other domains import only its `service`, `types` and `exceptions` | run by `make lint`; rules in [domain-boundaries.md](./domain-boundaries.md) |
| Type checking | **mypy** (strict) on `app` and `tests` is the gate | Pyright (editor / Claude Code plugin) is advisory only |
| Schema migrations | **Alembic**, with a **single baseline migration** (`0001_baseline_schema`) edited in place during the bootstrap phase; `make db-reset` rebuilds the database from it | Frozen once real data must be kept; from then on every change is a new, immutable migration — see [database.md](./database.md) |
| Quality gate | **Local pre-commit hook** (`.githooks/pre-commit` → `make check`, enabled by `make setup`/`make install-hooks`); **no CI yet** | Interim decision of 2026-10-01; the CI platform is still open — see [open-questions.md](./open-questions.md) item 1 |

## Out of current scope

The Web Portal (React + Vite + pnpm, TanStack Query + Zustand, Tailwind +
shadcn/ui) and Vehicle App (Flutter + Riverpod) have proposed technology
choices but **have no active source in the repo yet** — no rule applies to
either part until implementation starts; finalize the decision at that point
instead of relying on this old proposal.
