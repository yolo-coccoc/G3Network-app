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
| OCPP Gateway (charging station communication) | Lives **inside the `charging_stations` domain** (`charging_stations/ocpp/` directory), supports **OCPP 2.0.1** (`ocpp.v201`) **and OCPP 1.6J** (`ocpp.v16`), both via the MobilityHouse library (GitHub repo name `python-ocpp`, but the pip package/`pyproject.toml` dependency is **`ocpp`**). One adapter class per protocol, chosen by the negotiated WebSocket subprotocol (`ocpp2.0.1` / `ocpp1.6`); the first real charger (Willdigits DC, 240–480 kW) speaks 1.6J; runs via its own entrypoint | The gateway owns the WebSocket/OCPP lifecycle; the `charging_sessions` domain receives session events via a public service and doesn't own the WebSocket. Production technical status is not part of the active MVP. Dev mode still allows no TLS/no authentication; the production security profile is undecided (see `docs/02-planners/backend-ocpp16-charger-integration.md`, D12) |
| Charging MVP ideal | The local MVP assumes the station/EVSE/connector are always online/active, messages arrive in order without duplicates; only the happy path `Started → Updated/MeterValues → Ended` is kept | The reliability path (retry, DLQ, out-of-order...) is recorded in `docs/01-requirements/future.md` item 27 |
| OCPP security MVP | Development allows OCPP connections without TLS/without authentication in an isolated environment; production must finalize its own security profile before real deployment | Don't treat dev mode as a production security profile or OCPP certification criterion |
| Charging business boundary | Split into **`charging_stations`** (station/EVSE/connector topology and OCPP) and **`charging_sessions`** (receiving/storing events, meter samples, and session lifecycle) | The charging MVP currently only manages pre-provisioned topology/OCPP and stores happy-path sessions; technical status, authorization, remote control business logic, pricing, payment, and debt are deferred; do not merge these back into a single `charging` domain |
| Reverse proxy / API Gateway | **Not used in the dev environment** (each component runs on its own port on the host, called directly via `localhost`) | reconsider (Traefik/Nginx) when building `docker-compose.prod.yml` |
| Development environment | **Dev directly on the host**; only the infrastructure pieces that don't need direct code changes are Dockerized (`db`, `broker`) | see [dev-environment.md](./dev-environment.md) |

## Out of current scope

The Web Portal (React + Vite + pnpm, TanStack Query + Zustand, Tailwind +
shadcn/ui) and Vehicle App (Flutter + Riverpod) have proposed technology
choices but **have no active source in the repo yet** — no rule applies to
either part until implementation starts; finalize the decision at that point
instead of relying on this old proposal.
