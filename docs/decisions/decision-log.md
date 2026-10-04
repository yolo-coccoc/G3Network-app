# Decision log

> Every project-level decision in one place: what was decided, why, when, and
> where the full reasoning lives, plus the [questions still open](#open-questions).
> Gathered on 2026-10-03 from the planners (`docs/planners/`, including
> `done/`), the rules files (`.claude/rules/`), the former
> `tech-decisions.md` and `open-questions.md` rules (both merged here on
> 2026-10-04), `docs/decisions/deferred-resolved.md`, the domain-model design
> notes and the owner's design-review decisions of 2026-10-02/03.

## How to use this log

- **IDs are stable**: `<AREA>-<NN>`. A new decision takes the next number in
  its area. A decision is never rewritten to mean something else: a change of
  mind is a **new entry** that supersedes the old one, and the old one moves to
  its area's *Superseded* table.
- **Status**: ✅ confirmed · ⏳ interim / open (a working choice until decided) ·
  📦 deferred (recorded in `deferred.md`, item number given).
- **Source** points to where the detail lives (planner section and its own
  decision ID such as `D12`, rules file, or DBML note). Planners keep their
  local `D1..Dn` tables; this log is the cross-project index of them.
- Questions still waiting for an answer are listed under
  [Open questions](#open-questions) below (technical and vendor questions)
  and in the DBML `open_decisions` (D3, D5, D6: data-model business
  questions). A feature's own open questions stay in the feature catalog.

| Area | Code | Area | Code |
|---|---|---|---|
| Process & ways of working | PR | Telemetry | TM |
| Tooling & testing | TT | Telematics devices | TX |
| Infrastructure & security | IS | Vehicles | VH |
| Code conventions | CV | Drivers | DR |
| Data model & database | DM | Fleet | FL |
| Identity, people & access | ID | Support | SP |
| Billing & pricing | BL | Charging stations | CS |
| Notifications & messaging | NT | OCPP gateway | CO |
| | | Charging sessions | CE |

---

## Open questions

Not decided yet. For each one, keep the interim proposal and build
accordingly (PR-07); an answer becomes a decision entry above or below and
the question is removed here. Numbers are stable ("open question 4").

1. **CI/CD**: which platform to use (GitHub Actions, GitLab CI, Jenkins...)? Not yet decided. Interim decision (2026-10-01): local pre-commit hook (`make check`) only; remote is GitHub, so GitHub Actions is the natural candidate when CI is set up.
2. **Secrets management for production** (Vault, AWS/GCP Secrets Manager, or just `.env` + CI secrets)? Not yet decided.
3. **Local connection protocol** between the Telematics device and the vehicle screen (BLE/Wi-Fi Direct/CAN...) — out of current scope since `vehicle-app/` has no source yet; must be finalized before starting to code the `core/` module of the vehicle app.
4. **Willdigits charger — information required from the vendor** (OCPP 1.6J integration). Not yet answered; interim proposal: build against the OCPP 1.6J standard and the real logs (raw message log + post-boot `GetConfiguration`), assuming no vendor-specific behaviour. Blocking requests: (a) OCPP Implementation Guide / Interface Document; (b) mapping of the 80 internal error codes to `vendorErrorCode` (CSV/JSON); (c) DC meter brand/model/accuracy class/tamper seal; (d) confirmation of `wss://` and the authentication mechanism (Basic Auth or client certificate). Other: written list of supported OCPP profiles; multi-level HMI permissions; whether VIN Autocharge runs over OCPP; offline buffer capacity; whether `DataTransfer` is used. Details: `docs/design/specifications/charging-station-specification-summary.md` §6.2.
5. **Non-software blockers for the charger project**: (a) truck inlet standard — CCS2 or GB/T for Tri-Ring EVT-262/400/825; (b) DC meter verification in Vietnam (decides whether kWh invoicing is legal, or a service-fee model is needed); (c) warranty and technical-support provider in Vietnam. Interim proposal: nothing in code; the backend stores meter readings as reported and does not claim they are legally verified.
Answered and removed: 6 (ID-43). The next new question is number 7.

---

## PR — Process & ways of working

| ID | Decision | Why | Status | Date | Source |
|---|---|---|---|---|---|
| PR-01 | Commit straight to `master`; no feature branches or PRs; unrelated changes in separate commits (e.g. `feat` + companion `docs`). | Solo project. | ✅ | – | repo-conventions.md |
| PR-02 | Commit messages follow Conventional Commits (`feat(policy): ... (F-B3)`). | Consistent, searchable history. | ✅ | – | repo-conventions.md |
| PR-03 | No new component (middleware, library, config, infra) on the agent's own initiative: ask first; build only what a planner or prompt requests. | Keep the system deliberate and small. | ✅ | – | repo-conventions.md |
| PR-04 | "Needed later" work never leaves a placeholder or TODO in source: it is recorded in `deferred.md` (purpose, role, reason, related feature); existing placeholders are removed and moved there. | Placeholders rot and blur what is real. | ✅ | – | repo-conventions.md; backend-charging-mvp-ideal.md §5 |
| PR-05 | Deferred production groups (reconnect, retry/idempotency, reconciliation, status history, auth/payment) are reopened only through a planner + `deferred.md`, never by growing the MVP gradually. | Avoid half-built reliability. | ✅ | 2026-08-02 | backend-charging-mvp-ideal.md §5 |
| PR-06 | Every feature and new domain cites its feature code; a feature is built in this order: `start-feature` (planner + DBML design, owner confirms) → code → `finish-task`. | Keep spec, design and code consistent. | ✅ | – | repo-conventions.md; CLAUDE.md |
| PR-07 | For an undecided item, keep the interim proposal and build accordingly. | Changing it later barely affects structure. | ✅ | – | Open questions (this log) |
| PR-08 | A planner records, per step: goal/scope, decisions, files changed, checks (static/smoke/integration kept apart), actual outcome, deferrals; unrun checks stay `[ ]`. | The build may diverge from the prompt; results need evidence. | ✅ | – | backend-telemetry-ingestion.md §Conventions; _TEMPLATE.md |
| PR-09 | Requirement figures not confirmed by devices or the business (telemetry frequency, retention, fleet size, throughput, SLA) are never used as acceptance criteria; no performance figures are published before a benchmark. | No invented numbers. | ✅ | – | backend-telemetry-ingestion.md §Step 0, §Summary |
| PR-10 | Standing rules live in `CLAUDE.md` / `.claude/rules/`, never in agent memory. | Reviewed, durable, discoverable. | ✅ | – | collaboration-conventions.md |
| PR-11 | **Design first, refactor in bulk**: the whole database is designed and reviewed in the DBML before any implementation; built tables that differ from the target (profile/state split, renames) are refactored together afterwards. | One coherent design instead of piecemeal migrations. | ✅ | 2026-10-03 | Design review; database.md |
| PR-12 | All decisions are gathered in this log; a new, detailed feature catalog (`docs/product/features/`) replaces `feature-list.md`, which is archived after the backend status sync. | Decisions and scope were scattered and incomplete. | ✅ (sync ⏳) | 2026-10-03 | Design review |
| PR-13 | While reviewing tables, designing or developing, always check whether the feature catalog must change and update it in the same piece of work; record owner decisions in this log. | New ideas keep changing scope; a stale list misleads. | ✅ | 2026-10-03 | collaboration-conventions.md |
| PR-14 | **Docs and tooling layout**: `docs/` is organized by the question each part answers — `product/` (what we build: feature catalog, business inputs), `design/` (how: architecture incl. stack and directory tree, domain model, external specifications), `decisions/` (why: this log incl. open questions, `deferred.md`), `planners/`, `reports/`, `archive/`; `docs/README.md` maps it. `.claude/rules/` holds only how-we-work rules. One fact has one home: progress = feature catalog, current system = `architecture.md`, decisions and open questions = this log. `future.md` became `decisions/deferred.md` with item numbers kept. | The old layout repeated progress, decisions and structure in several places and mixed rules with facts. | ✅ | 2026-10-04 | Owner request |

## TT — Tooling & testing

| ID | Decision | Why | Status | Date | Source |
|---|---|---|---|---|---|
| TT-01 | Backend: Python 3.12 + FastAPI, async SQLAlchemy, Pydantic v2. | Finalized stack. | ✅ | – | former tech-decisions |
| TT-02 | `uv` manages the environment; `pyproject.toml` + `uv.lock` are the only source of dependencies. | One source of truth. | ✅ | – | former tech-decisions |
| TT-03 | Ruff is the only formatter and linter (line 88; rules `I`, `ICN003`); a hook formats every Python file the agent edits. | Replaced Black + isort. | ✅ | 2026-10-01 | former tech-decisions |
| TT-04 | mypy strict on `app` and `tests` is the type gate; Pyright is advisory. | One authoritative checker. | ✅ | – | former tech-decisions |
| TT-05 | Quality gate = local pre-commit hook running `make check` (lint, import-linter, mypy, smoke tests, domain-model check); no CI yet (GitHub Actions is the natural candidate). | CI platform undecided. | ⏳ open question 1 | 2026-10-01 | former tech-decisions; Open questions (this log) |
| TT-06 | Small, contract-focused pytest suite (pytest + pytest-asyncio only; no coverage/snapshot/factory libraries); tests target behaviours, not files or methods. | Fast, meaningful tests. | ✅ | – | backend-automated-tests.md §2–3, §8 |
| TT-07 | Smoke tests (no PostgreSQL/EMQX/Docker) by default; PostgreSQL integration tests only with `RUN_DB_INTEGRATION=1`; the report states which scope ran. | Run anywhere; real-DB behaviour still covered. | ✅ | – | backend-automated-tests.md; backend-runtime-conventions.md |
| TT-08 | Tests live per domain (`tests/<domain>/test_*_smoke.py`) with shared `builders.py` / `fakes.py`; cross-cutting tests at top level. | The single file had grown to 3,821 lines. | ✅ | 2026-10-01 | backend-automated-tests.md §4 |
| TT-09 | PostgreSQL integration tests cover drivers, fleet, support (partial unique indexes, filters, `ST_Covers`, SOS alerts). | Behaviour only PostgreSQL shows. | ✅ | 2026-10-01 | deferred-resolved.md §85 |
| TT-10 | A smoke test fails when a domain module is missing from its import-linter contract. | Contracts can't silently go stale. | ✅ | 2026-10-01 | backend-happy-path-completion.md §4 |
| TT-11 | Simulators live in `simulator/`, go only through the HTTP API and MQTT/OCPP (never the DB), follow the source schemas exactly, run the happy path only, and add no production dependency. | Exercise the real flow; no drift. | ✅ | 2026-07-28 | telematic-simulator.md; backend-charging-mvp-ideal.md Step 5 |
| TT-12 | A separate OCPP 1.6J simulator (scenarios boot/status/session, answers GetConfiguration) next to the untouched 2.0.1 one. | Test every step before hardware arrives. | ✅ | 2026-09-24 | backend-ocpp16-charger-integration.md Step 3 |
| TT-13 | E2E runs use real PostgreSQL/TimescaleDB and EMQX, unique message IDs per case, and record command/input/logs/query/result; never claim "no message loss"; never inject failures into a DB with important data. | Evidence, not impressions. | ✅ | 2026-07-28 | backend-telemetry-ingestion.md §Step 15 |
| TT-14 | Real-charger bring-up follows the spec's phases with raw-log evidence; acceptance items stay pending until proven; command probes use an external tool (e.g. SteVe). | No command channel/TLS in the backend yet. | ⏳ waiting for hardware | 2026-09-24 | backend-ocpp16-charger-integration.md Step 10 |
| TT-15 | **Domain model tooling**: the whole database is designed in one DBML file (`docs/design/domain-model/domain-model.dbml`); a script in the `domain-model` skill generates Markdown views (L1 domain map, L2 diagrams, L3 columns) and a Vietnamese Excel workbook, and `check` (in `make check`) compares built tables with the SQLAlchemy models. | One source, many views; design can't drift from code. | ✅ | 2026-09-29 | domain-model skill; database.md |
| TT-16 | Every DBML column carries an English meaning, a Vietnamese meaning and an example; `check` fails without them. Foreign-key examples reuse the target row's example ID. | A readable design for business and developers. | ✅ | 2026-09-30 | domain-model skill |
| TT-17 | The Excel export is for business readers: Vietnamese, no build-progress columns, freezes only the title and header rows, links every table to its sheet, rewritten only when its content changes. | BOD readability; usable scrolling. | ✅ | 2026-10-01 | domain-model skill |
| TT-18 | Tables are numbered in design order; main and state tables share one sequence, a generated history table is `<N>.h`; overviews count main / state / history tables. | Count and reference tables clearly. | ✅ | 2026-10-03 | domain-model skill |
| TT-19 | **Feature catalog tooling**: one YAML source (`features.yaml`) generates a Markdown checklist per domain plus a front page, and a Vietnamese Excel (one sheet per domain); `check` (in `make check`) validates codes, roles, surfaces, dependencies, DBML tables, decision and deferred.md references. Features were restructured before numbering (`<PREFIX>-<NN>` per domain, old `F-xx` codes mapped); each feature has a status per surface (backend / app / portal), all "todo" until the backend sync; related tables stay empty until the database review. | Same single-source pattern as the domain model; the old list mixed spec and backend status and was not broken down. | ✅ | 2026-10-03 | feature-catalog skill |
| TT-20 | Web portal and vehicle app stacks are only proposals (portal: React + Vite + pnpm, TanStack Query + Zustand, Tailwind + shadcn/ui; vehicle app: Flutter + Riverpod); no rule applies until their first task starts, when the stack is decided. | No source exists for either. | ⏳ | – | former tech-decisions.md |

## IS — Infrastructure & security

| ID | Decision | Why | Status | Date | Source |
|---|---|---|---|---|---|
| IS-01 | Develop directly on the host; only `db` (PostgreSQL+TimescaleDB+PostGIS) and `broker` (EMQX) run in Docker. | Infra needs no code changes. | ✅ | – | former tech-decisions; dev-environment.md |
| IS-02 | No reverse proxy / API gateway in dev; each component on its own port (revisit with a production compose). | Simplicity. | ✅ | – | former tech-decisions |
| IS-03 | Separate processes: API, telemetry ingestion, OCPP gateway, device-health monitor, sharing one DB/session configuration. | Independent lifecycles. | ✅ | – | architecture.md |
| IS-04 | Secrets only through `.env` (`.env.example` has no real values); every setting defined in `config.py`, namespaced by component; code never reads `os.getenv`. | No leaked secrets; one config source. | ✅ | 2026-07-30 | repo-conventions.md; deferred-resolved.md §24 |
| IS-05 | OCPP in dev runs without TLS/authentication in an isolated environment; production must decide its own security profile (vendor to confirm `wss://` + auth). | Vendor support unknown. | ⏳ 📦 deferred.md 73 | 2026-09-24 | former tech-decisions; ocpp16 planner D12 |
| IS-06 | EMQX 5.5 with default security in dev; ACLs (set via Dashboard/REST, not `acl.conf`) and topic-vs-payload serial checks come later. | MVP; EMQX 5 has no acl.conf. | 📦 | – | backend-telemetry-ingestion.md §Step 4, 6 |
| IS-07 | Raw OCPP frames (they contain RFID idTags, later VINs) are never exposed by an API or copied into application logs. | Privacy and security. | ✅ 📦 read API/retention: deferred.md 79 | 2026-09-24 | ocpp16 planner Step 1; database.md |
| IS-08 | API responses never carry payment, authorization, raw payloads or credentials; admin-only filters stay hidden until authorization exists. | No auth yet. | ✅ | 2026-07-31 | backend-charging.md §3.13 |
| IS-09 | `/api/v1` prefix, `GET /health`, CORS open in dev only. | Dev convenience. | ✅ | – | backend-crud-vehicles.md Step 8 |
| IS-10 | Endpoints have no authentication until the identity domain is built. | No identity exists yet. | ⏳ | 2026-09-18 | backend-crud-drivers.md §2 |
| IS-11 | Before go-live: keep a sealed emergency `HEAD_ADMIN` account offline (every use logged), so an admin compromise can be fixed when the owner is unreachable. | Continuity; production DB access will be restricted. | ⏳ practice | 2026-10-03 | Design review |

## CV — Code conventions

| ID | Decision | Why | Status | Date | Source |
|---|---|---|---|---|---|
| CV-01 | Each `backend/app/domains/*` directory is one bounded context; its public surface is `service.py` + the DTOs/enums of `types.py` + `exceptions.py`; other domains never import anything else from it. Enforced by import-linter. | Bounded-context isolation. | ✅ | – | domain-boundaries.md |
| CV-02 | Cross-domain calls take/return primitives or frozen dataclass DTOs, never ORM models or HTTP schemas. | Decoupling. | ✅ | – | domain-boundaries.md |
| CV-03 | Domain edges are one-directional and registered in the edge table in the same change; the only bidirectional pair is `telematics ↔ telemetry` (ingestion one way, device-health the other). | Avoid cycles. | ✅ | 2026-09-17 | domain-boundaries.md |
| CV-04 | Layers: router = HTTP only; service = business rules (no FastAPI, no commit); repository = queries only (no commit, may flush); schemas never import models. | Clear responsibilities. | ✅ | – | backend-runtime-conventions.md |
| CV-05 | Only the entry boundary owns the session and transaction (`Depends(get_db, scope="function")` for HTTP, `async_session_factory` for workers); one business operation = one atomic transaction by default. | With the default scope FastAPI committed after the response, answering before writes were saved. | ✅ | 2026-10-01 | backend-runtime-conventions.md; happy-path §5 |
| CV-06 | Domain exceptions inherit one shared base (`NotFoundError` 404, `ConflictError` 409, `InvalidInputError` 400, `UpstreamUnavailableError` 502); `app/api/main.py` maps them; routers don't catch them. | One consistent mapping. | ✅ | – | backend-coding-conventions.md §6 |
| CV-07 | PATCH = partial update with `exclude_unset`; "not sent" and `null` both mean "don't change" unless a field has its own confirmed clear contract. | No accidental clearing. | ✅ | – | backend-runtime-conventions.md |
| CV-08 | Validation happens in the service; a DB unique constraint is the last line of defence and its `IntegrityError` becomes a domain error. | Clear errors. | ✅ | – | backend-runtime-conventions.md |
| CV-09 | No preemptive batching (grouped counts, bulk lookups, `IN (...)`) in the MVP: per-item first, batch only after a benchmark. | Avoid premature optimization. | ✅ | – | backend-runtime-conventions.md |
| CV-10 | All I/O is async; one component owns each external client's lifecycle; topics/QoS/sizes/timeouts come from settings. | Consistency. | ✅ | – | backend-runtime-conventions.md |
| CV-11 | UTC everywhere (`timestamptz`), "now" only from `app.libs.common.clock.utc_now()`; incoming timestamps must carry a timezone. | One time source. | ✅ | – | backend-runtime-conventions.md |
| CV-12 | Report days/weeks/months are cut in `APP_REPORT_TIMEZONE` (Asia/Ho_Chi_Minh); stored timestamps stay UTC. | Business calendar is local. | ✅ | 2026-10-01 | happy-path D4 |
| CV-13 | Structured logging via `extra`, no f-strings in log calls, `logger.exception()` for unexpected errors; `except Exception` only at process/task boundaries. | Machine-readable logs. | ✅ | – | backend-runtime-conventions.md |
| CV-14 | Docstrings and comments are complete and in **English**; a stale docstring is a bug. | Maintainability. | ✅ (supersedes CV-S1) | – | backend-runtime-conventions.md |
| CV-15 | `__init__.py` holds only a docstring; import directly from modules. | No hidden re-exports. | ✅ | – | repo-conventions.md |
| CV-16 | Naming: `<Object><Role>` classes (`VehicleModel`, `VehicleCreateRequest`, `VehicleReference`), `_by_<field>` lookups, positional single parameters, keyword-only for 2+ same-typed ones, `<domain>_endpoint` handlers. | Names say what and where. | ✅ | – | backend-coding-conventions.md |
| CV-17 | **`_id` always means a system ID** (generated by us); a primary key is `<singular>_id`, never a bare `id`; real-world identifiers are named for what they are (`tax_code`, `vin`, `phone_number`); external protocol IDs keep their source prefix (`ocpp_evse_id`). | A bare `id` makes wrong joins silent; no confusion between ours and real-world IDs. | ✅ | 2026-10-03 | backend-coding-conventions.md §8.4 |
| CV-18 | New optional parameters on shared service functions are keyword-only with `None` defaults, so existing callers stay unchanged. | Backward compatibility (2.0.1 path). | ✅ | 2026-09-24 | ocpp16 planner Steps 5–6 |
| CV-19 | Literal routes (`/nearby`, `/activation-summary`) are registered before `/{id}` routes. | Otherwise the path parameter swallows them. | ✅ | 2026-09-17 | station-search planner §3.1 |

## DM — Data model & database

| ID | Decision | Why | Status | Date | Source |
|---|---|---|---|---|---|
| DM-01 | One PostgreSQL 16 with TimescaleDB (time series) and PostGIS (geo). | Finalized stack. | ✅ | – | former tech-decisions |
| DM-02 | Every table has an internal ID primary key (UUID for main tables, BIGINT identity where volume demands); business keys are never primary keys; FKs reference internal IDs. | Stable keys; distributed-ready. | ✅ | – | backend-runtime-conventions.md |
| DM-03 | Bootstrap phase: one baseline migration `0001_baseline_schema`, edited in place and applied with `make db-reset`; frozen into immutable migrations once real data must be kept. Revision IDs ≤ 32 characters. | No data to preserve yet; a 37-char ID broke `alembic_version`. | ✅ | 2026-10-01 | database.md; former tech-decisions |
| DM-04 | Alembic's `include_object` filter keeps autogenerate/`alembic check` away from PostGIS/TimescaleDB-owned objects; `make db-check` must report no drift. | Reflect only app-managed schema. | ✅ | 2026-10-01 | deferred-resolved.md §12 |
| DM-05 | Hypertables (1-day chunks, time column in the PK): `vehicle_telemetry`, `charging_session_events`, `charging_session_measurements`, `charging_ocpp_messages`; everything else relational. | TimescaleDB needs the partition key in unique indexes. | ✅ | – | database.md; backend-charging.md §3.7 |
| DM-06 | "At most one live row" is a **partial** unique index (driver assignments, fleet memberships, live telematic per vehicle, memberships, role assignments…), so closed or soft-deleted rows are unlimited. | Keep history without blocking reuse. | ✅ | 2026-09-18 | database.md; deferred-resolved.md §82 |
| DM-07 | History-shaped relationships use open/close tables (`assigned_at`/`unassigned_at`, `joined_at`/`left_at`) with `ON DELETE RESTRICT`, not a current-value column. | Keeps history; a parent can't vanish under it. | ✅ | 2026-09-18 | backend-crud-drivers.md §2 |
| DM-08 | Nullable without a server default when an existing/historical row genuinely has no value; never fabricate one. | Honest data. | ✅ | – | database.md |
| DM-09 | GPS is `geography(Point, 4326)`; a GIST index only where a spatial query needs it (`charging_stations.location`), not on write-heavy or input-only columns. Areas are `geography(POLYGON, 4326)` with `ST_Covers`. | Correct distances; no wasted write cost. | ✅ | 2026-09-17 | deferred-resolved.md §9; database.md |
| DM-10 | Enums store member names, except protocol mirrors (connector status) which store the protocol's values; widening = `ADD VALUE`, downgrade rebuilds the type. | PostgreSQL can't drop enum values. | ✅ | 2026-09-24 | database.md; ocpp16 planner Step 5 |
| DM-11 | Derived state is computed at read time, never stored (`is_online`, `is_silent`, availability counts). | No stale flags. | ✅ | 2026-10-01 | database.md |
| DM-12 | Alert-producing features share one `notifications` table (type + JSONB payload), never a table per alert. | Reuse without new tables. | ✅ | 2026-09-17 | backend-notifications.md §1 |
| DM-13 | Energy is stored as canonical Wh; energy and power use NUMERIC, never float. | Exactness for billing. | ✅ | 2026-07-31 | backend-charging.md §2 |
| DM-14 | Soft delete for administered records (topology, vehicles, drivers…); sessions and history are never deleted; unique identity checks include soft-deleted rows where reuse would blur history. | History integrity. | ✅ | 2026-07-31 | backend-charging.md §3.7 |
| DM-15 | **Change history**: decided per table (on/off). When on, a change to any column copies the **whole old row** into `<singular>_history` (every source column, nullable, plus `history_id`, `changed_at`, `changed_by`), written by a trigger in the same transaction; `@untracked` only for machine-updated columns. History tables are generated by the domain-model tool, never hand-written, and not reviewed one by one. Append-only tables and open/close histories need none. | Audit who changed what, without per-column work. | ✅ (`change_reason`: DM-21) | 2026-10-02/03 | database.md |
| DM-16 | **Profile vs state**: a profile table holds what a thing is and the *decisions* about it (incl. business status, even when set by a rule); a 1:1 `<singular>_state` table holds *observations* written often by devices or activity (`last_seen_at`, `last_login_at`, device-reported status/firmware), with no history. | One table, one question; clean history; cheap updates. | ✅ | 2026-10-03 | database.md |
| DM-17 | Built tables mixing profile and state (`charging_stations`, `charging_connectors`, `telematics`) get their target split designed now and refactored in bulk; the `SET updated_at = updated_at` trick is interim until then. | See PR-11. | ⏳ | 2026-10-03 | database.md |
| DM-18 | Every organization-owned row carries `organization_id` (the tenant column), even where it could be derived. | One uniform data-access filter. | ✅ | 2026-10-03 | DBML identity notes |
| DM-19 | Every status decided by a person or a business rule has a `status_reason` (free text, NULL when nothing to explain); earlier reasons come from the change history; an ended open/close row uses it for why it ended (no `end_reason`). Observed statuses get none. Applied to `organizations`, `users`, `memberships`; the other decided statuses get it as their tables are reviewed. | One consistent way to answer "why", without a second column per case. | ✅ | 2026-10-04 | database.md |
| DM-20 | Refines DM-15: only open/close tables whose row is only ever closed (assignments) skip change history; one whose live row carries changing decisions (`memberships`) is tracked. | `database.md` exempted memberships while the design tracked them. | ✅ | 2026-10-04 | database.md |
| DM-21 | Every history table gets a **`change_reason varchar(200)` NOT NULL** column: why the row was changed. The app sets it for the transaction together with the acting user, and the trigger copies it into the history row; a change to a tracked table without a reason fails. A person types the reason for an administrative decision (e.g. locking an account); a routine action (e.g. accepting an invitation) gets a fixed text from the app. Generated by the domain-model tool like the other history columns. | "Who changed it and when" is not enough for an audit; an optional reason is left empty. | ✅ | 2026-10-04 | database.md |

## ID — Identity, people & access

| ID | Decision | Why | Status | Date | Source |
|---|---|---|---|---|---|
| ID-01 | `identity` is the foundational domain: every domain may depend on it, it depends on none (a driver profile points to its membership, never the reverse). | Avoid cycles. | ✅ | – | domain-boundaries.md |
| ID-02 | **Organizations**: every party people act for — customers (companies, owner-drivers, guests' personal organizations), our own internal organization(s), repair/rescue partners — is one row in `organizations`, stored the same way. Any organization can own vehicles, drivers, sessions. | One model for all parties; our own trucks work like a customer's. | ✅ | 2026-10-03 | DBML `organizations` |
| ID-03 | G3 is an organization like any other; "internal" means "us", with no special name in the model. `is_internal = TRUE` gives its users access to every organization's data (still limited by role); outsourced staff acting for us are its members. | Data access needs only one flag. | ✅ | 2026-10-03 | DBML `organizations.is_internal` |
| ID-04 | No organization type column: a partner is recognised by having a `repair_partners` profile; customers vs partners differ only in what "related data" means per feature. | A type added nothing to access control. | ✅ | 2026-10-03 | Design review |
| ID-05 | `legal_form` (COMPANY / INDIVIDUAL) is declared at sign-up; the tax code is checked against it (company 10/13 digits; individual: for now the 12-digit CCCD, which is personal data); it drives e-invoice and privacy rules. | The tax code alone can't tell them apart reliably. | ✅ | 2026-10-03 | DBML `organizations` |
| ID-06 | Organization status: ACTIVE / SUSPENDED (temporary, logins blocked, vehicle data still collected) / CLOSED (no contract, no new data, data kept for the legal period, reopened only with a new contract); soft delete only for rows created by mistake. | Clear lifecycle and audit. | ✅ | 2026-10-02 | DBML `organizations` |
| ID-07 | Each customer organization may have one account manager (`account_manager_id`, our SALES user); reassignments are kept in its history. | Sales ownership without an extra table. | ✅ | 2026-10-03 | DBML `organizations` |
| ID-08 | **Users**: one person = one account across all organizations; login = phone number + password (other methods later); e-mail optional; `full_name`; account `status` INVITED/ACTIVE/LOCKED (only our admins lock a whole account); `created_by` recorded. | One identity per human. | ✅ | 2026-10-03 | DBML `users` |
| ID-09 | **One person, several organizations** (former open decision D9): `memberships` (user × organization, INVITED/ACTIVE/LOCKED, joined/left) says where someone belongs; after login a person with several picks one (last one remembered). | Guests hired as drivers, drivers at two companies, accounting firms. | ✅ | 2026-10-03 | DBML `memberships` |
| ID-10 | **Roles** are job titles = bundles of features, one fixed list in code (enum, not a table), held per membership; a person may hold several. List: HEAD_ADMIN, CO_ADMIN (internal only), ORG_ADMIN, SALES, ACCOUNTANT, CUSTOMER_CARE, OPERATIONS, MAINTENANCE, WARRANTY, FLEET_MANAGER, DISPATCHER, DRIVER, TECHNICIAN. | Same job, same bundle; combinations cover special cases. | ✅ | 2026-10-03 | DBML `user_role_assignments` |
| ID-11 | **Access rule**: features = role ∩ plan for customers, the whole role for internal users (internal-only features such as issuing invoices are never put in a plan); data = the organization's own rows, every organization for internal users, narrowed to assigned fleets for fleet-level roles. | Two checks, each with one source. | ✅ | 2026-10-03 | Design review; database.md |
| ID-12 | HEAD_ADMIN (the owner) has full permissions; CO_ADMIN does daily administration but cannot manage admins or `is_internal`; ORG_ADMIN manages only its own organization's users and roles. An organization keeps at least one active ORG_ADMIN. | Least privilege. | ✅ | 2026-10-03 | Design review |
| ID-13 | **Profiles** store facts only (no permissions) about one membership in one job; at most one per job type; added only when the system must act on that job's data. A role that needs a profile requires an active one (DRIVER → driver profile with a valid licence); a profile may outlive the role (former drivers keep their history). | Roles = permissions, profiles = facts. | ✅ | 2026-10-03 | database.md |
| ID-14 | Everyone who uses or receives anything from the system is a user; there is no separate contact list (`organization_contacts` dropped). | Recipients follow roles. | ✅ | 2026-10-03 | Design review |
| ID-15 | Onboarding: our sales creates a company's organization, subscription and first ORG_ADMIN; the ORG_ADMIN invites office staff; a fleet manager registers drivers by phone and drivers claim the account with phone + OTP; bulk import as a service; guests self-register (phone OTP) and get a personal INDIVIDUAL organization where they are ORG_ADMIN. | Scales without making us a bottleneck. | ✅ | 2026-10-03 | Design review |
| ID-16 | Invites, sign-up, password reset and phone changes use one-time codes/links (`one_time_codes`: hashed, expiring, single use, rate-limited per phone), never random passwords sent by SMS. | A sent password is a working credential. | ✅ | 2026-10-03 | DBML `one_time_codes` |
| ID-17 | Passwords live in `user_credentials` as one-way hashes; a change revokes the old row and adds a new one (old hashes allow "don't reuse" checks); hashes are never copied into user history. | Security; future login methods are new rows. | ✅ | 2026-10-03 | DBML `user_credentials` |
| ID-18 | `user_state` holds login observations (last login/activity, last organization, failed-login count, temporary lockout) and is created with the user. | Profile/state rule; brute-force protection. | ✅ | 2026-10-03 | DBML `user_state` |
| ID-19 | Push notifications use Firebase Cloud Messaging tokens stored per device (`user_devices`), refreshed at login, removed on logout, on "unregistered" and when stale. | Standard FCM practice. | ✅ | 2026-10-03 | DBML `user_devices` |
| ID-20 | Consent to personal-data processing is recorded per purpose and document version, with withdrawal, in `user_consents` (Decree 13/2023, Law 91/2025/QH15). | The law asks us to prove consent. | ✅ | 2026-10-03 | DBML `user_consents` |
| ID-21 | Every access to personal or location data is written to an append-only audit log. | NF-08. | ✅ | – | DBML `access_audit_logs` |
| ID-22 | Login history lives in `access_audit_logs`, not in a new table: it also records account security events (LOGIN_SUCCESS, LOGIN_FAILED, LOGIN_LOCKED, LOGOUT, PASSWORD_CHANGED, PHONE_CHANGED) with the IP address and a text snapshot of the device/app (no link to `user_devices`, whose rows are removed at logout). `user_state` keeps only the latest values. | Answer "who logged in, from where" and alert on a new device, without another table. | ✅ | 2026-10-04 | DBML `access_audit_logs`, `user_state` |
| ID-23 | Brute-force protection: a short per-account lockout after repeated wrong passwords, plus a per-device/IP rate limit in the API; password reset by OTP keeps working while locked. | A per-account lock alone lets anyone who knows a phone number lock its owner out. | ✅ | 2026-10-04 | DBML `user_state.login_locked_until` |
| ID-24 | `user_state.last_active_at` is written at most once every 5 minutes per user, not on every request. | One write per API call on the same row is wasteful; minutes are precise enough. | ✅ | 2026-10-04 | DBML `user_state` |
| ID-25 | Internal organizations are always COMPANY; partners may be COMPANY or INDIVIDUAL (a small workshop is often a household business taxed under the owner's citizen ID). | "Partners always COMPANY" excluded real roadside workshops. | ✅ | 2026-10-04 | DBML `organizations.legal_form` |
| ID-26 | A membership's `created_at` is when the person was added or invited; `joined_at` is when they accepted (NULL while INVITED). An unaccepted invitation stays INVITED until resent or cancelled; no automatic cleanup. | An invited person has not joined yet. | ✅ | 2026-10-04 | DBML `memberships` |
| ID-27 | Recycled phone numbers: a password reset by OTP on an account inactive for a long time (e.g. 90 days) needs an extra check by customer care or the organization admin before it succeeds. | Carriers give old numbers to new subscribers, who could otherwise take over the account with the OTP alone. | ✅ | 2026-10-04 | DBML `users.phone_number` |
| ID-28 | A user's phone number is unique among accounts not deleted; the e-mail is unique among them ignoring upper/lower case. | A deleted account must not block its number forever; `Binh@x.vn` and `binh@x.vn` are one address. | ✅ | 2026-10-04 | DBML `users` indexes |
| ID-29 | No per-user language yet; a `users` language column is added when the first non-Vietnamese user needs one (NF-17). | Nobody needs it now. | ✅ | 2026-10-04 | Design review |
| ID-30 | A successful password reset also clears the login lockout (`failed_login_count` = 0, `login_locked_until` = NULL). | Someone who just reset their password must be able to log in at once. | ✅ | 2026-10-04 | DBML `user_state` |
| ID-31 | Keep only the last 5 revoked password hashes per user (for the reuse check); delete older ones. | Old hashes are sensitive and have no other use. | ✅ | 2026-10-04 | DBML `user_credentials` |
| ID-32 | Login sessions are stored server-side, one row per logged-in device, together with that device's push token (`user_devices`), so one device or all of them can be logged out; a password change or an account lock ends every session. | Refresh tokens must be revocable; push tokens already live and die with the device's login. | ✅ (table designed in the table 6 review) | 2026-10-04 | Design review |
| ID-33 | Each organization has **exactly one** active ORG_ADMIN (enforced by a unique index on role assignments). The role is handed over in one transaction, never shared; the ORG_ADMIN's membership cannot be locked or removed before a handover; when the admin is gone, our CO_ADMIN appoints the next one. Supersedes the "at least one ORG_ADMIN" part of ID-12. | One clear owner of each customer's user management. | ✅ | 2026-10-04 | Design review; DBML `user_role_assignments` |
| ID-34 | Suspending or closing an organization blocks its members' logins through the organization's status but leaves their memberships unchanged, so everyone continues if a new contract is signed. Memberships keep only `created_at` (added/invited), `joined_at` (accepted) and `left_at` (ended); no separate `invited_at`. | No re-invitations after a renewal; no column that always repeats `created_at`. | ✅ | 2026-10-04 | DBML `memberships` |
| ID-35 | `user_credentials` stays password-only for now. When other login methods come: Google/Zalo store the provider's account number (no secret; `secret_hash` becomes optional), OTP login uses one-time codes (no credential row), and a provider account is linked only while the user is logged in, never by matching e-mail. | Phone + password is the launch method; the extension path is known. | ✅ | 2026-10-04 | Design review |
| ID-36 | `user_devices` becomes `user_sessions`: one row per login of one person on one device or browser, holding the refresh-token hash, the optional push token and the organization shown on that device (switching needs no new login; push is not filtered by it). Ending a session deletes the row. `expires_at` slides forward on use; idle lifetimes are settings: 90 days driver app, 7 days customer portal, 1 day internal staff. | Implements ID-32; a row is a login, not a device. | ✅ | 2026-10-04 | DBML `user_sessions` |
| ID-37 | One-time codes: SMS only; a code dies after 5 wrong attempts (`failed_attempt_count`); only the newest unexpired code per phone and purpose is checked; rows are deleted 1 day after expiry (a short-lived working table, kept in PostgreSQL rather than adding Redis or relying on the SMS provider's verification). | A 6-digit code must not be guessable; old codes have no value once the audit log holds the events. | ✅ | 2026-10-04 | DBML `one_time_codes` |
| ID-38 | Legal acceptance works at three levels: a company accepts the data processing agreement on its own behalf (it is responsible for its drivers' data, we process it); an employed driver acknowledges the privacy notice (no consent, nothing to withdraw); an individual customer consents per purpose. Required texts are a condition of use: withdrawing means leaving the service, recorded on the account. `user_consents` is append-only (no `withdrawn_at`, no `channel`: IP and device are kept instead) and points to the exact version in a new `legal_documents` table. Marketing consent is designed when marketing exists. Supersedes the per-purpose withdrawal of ID-20. | Employees follow their company; the exact accepted text must always be provable. | ✅ (legal basis: ID-43) | 2026-10-04 | DBML `user_consents`, `legal_documents` |
| ID-39 | `legal_documents` stores only final texts (drafting happens outside the system): adding a row publishes it and takes effect at `created_at`; rows are never edited or deleted; only HEAD_ADMIN/CO_ADMIN publish, after the legal adviser approves; Vietnamese only until a second language exists. | No draft workflow in the database; publishing forces everyone to re-accept, so it is restricted. | ✅ | 2026-10-04 | DBML `legal_documents` |
| ID-40 | `user_role_assignments` keeps `organization_id` (tenant-column rule DM-18), and the database guarantees it matches the membership's organization with a two-column foreign key to `memberships (membership_id, organization_id)`; the one-ORG_ADMIN rule (ID-33) stays a unique index. Each assignment records `granted_by`/`revoked_by`. Ending a membership revokes its roles in the same transaction; HEAD_ADMIN/CO_ADMIN only in internal organizations; TECHNICIAN for partners. | Writing the organization twice is safe only if a mismatch is impossible. (Dropping the column was considered and rejected because it broke DM-18.) | ✅ | 2026-10-04 | DBML `user_role_assignments` |
| ID-41 | `access_audit_logs` actions are two families: on data (VIEW, EXPORT) and on a user account (security events). A view is logged once per screen opened, not per refresh; an export requires a `reason`; personal-data changes are not logged here (the history tables hold them); no per-person "data subject" column; entries are kept forever, older months compressed. | Useful audit without millions of duplicate rows; no case needs the data-subject lookup. | ✅ | 2026-10-04 | DBML `access_audit_logs` |
| ID-42 | Action-specific facts in `access_audit_logs` go in one `details` JSONB column (e.g. `{"reason": ...}` for EXPORT, `{"failure": ...}` for LOGIN_FAILED), with the keys per action listed in its note; it replaces the `reason` column of ID-41. May be revisited. | A column used by one action was awkward; same pattern as the notifications payload (DM-12). | ✅ | 2026-10-04 | DBML `access_audit_logs` |
| ID-43 | Answers open question 6: for employed drivers, the company's data processing agreement (company = controller, G3 = processor) plus each driver's acknowledged privacy notice is enough legal basis; no per-driver consent. Holds while we sell SaaS; reviewed again when a PaaS offering comes. | The owner judges it sufficient for the SaaS model; a PaaS offering may change who processes the data. | ✅ | 2026-10-04 | Design review; ID-38 |
| ID-44 | Answers D7: a role grants the **same features** to internal and customer users; what differs between them is **data reach** (ID-11), not features. The feature list of each role, including access to personal data such as location history and camera clips, is set in a permission-granting step when nearly all features are implemented. | The full feature list is needed to grant permissions sensibly; deciding now would be guesswork. | ✅ (grant step ⏳) | 2026-10-04 | Design review; DBML `open_decisions` |

## BL — Billing & pricing

| ID | Decision | Why | Status | Date | Source |
|---|---|---|---|---|---|
| BL-01 | **Pricing direction**: a catalog of a few dozen features, each with a list price and pricing unit (per vehicle/month, per organization/month, per user, per message); plans are bundles (public plans for most customers, private custom plans for one customer); add-ons on top of a plan; agreed prices copied onto subscription/add-on rows. Replaces the single pricing-unit question (former D11). | Customers' needs differ; sales changes prices without developers. | ✅ (tables designed in the billing review) | 2026-10-03 | DBML billing notes |
| BL-02 | Plans are data (`subscription_plans` + `plan_features`), so a new plan needs no developer; feature behaviour stays in code. | Commercial flexibility. | ✅ | 2026-10-03 | DBML `plan_features` |
| BL-03 | A default free plan (GUEST: charging, wallet, receipts, history) applies to any organization without a subscription (`subscription_plans.is_default`). | Guests need features without buying. | ✅ | 2026-10-03 | DBML `subscription_plans` |
| BL-04 | Companies buy through our sales team (contract-led); individuals buy self-service in the app. | B2B contracts vs retail. | ✅ | 2026-10-03 | Design review |
| BL-05 | SMS is a sellable feature (quota and/or per message); OTP SMS is our operating cost. | SMS costs money per message. | ⏳ direction | 2026-10-03 | Design review |
| BL-06 | Money is never computed by telemetry or sessions: F-C6 has no cost field; a flat per-kWh cost setting is used only for F-A6 estimates until tariffs exist. | Money belongs to billing. | ✅ 📦 deferred.md 26, 60 | 2026-09-17 | operating-energy-reports planner |
| BL-07 | Meter readings are stored as reported and not claimed legally verified (DC meter verification in Vietnam decides whether kWh invoicing is legal). | Unresolved legal question. | ⏳ open question 5 | – | Open questions (this log); ocpp16 planner §7 |

## NT — Notifications & messaging

| ID | Decision | Why | Status | Date | Source |
|---|---|---|---|---|---|
| NT-01 | `notifications` is a leaf domain; producers call its public service. | No cycles. | ✅ | 2026-09-17 | domain-boundaries.md |
| NT-02 | `notification_id` (BIGINT, increasing) doubles as the poll cursor; the fleet portal polls `?after_id=` about every 10 s. | Minutes of latency are acceptable; cheap. | ✅ | 2026-10-03 | backend-notifications.md §3.1; Design review |
| NT-03 | A message type belongs to a service (feature); a user receives it when the service is in their role and plan. It always appears in the app and fleet portal; push / SMS / e-mail are switched per service by the **organization**; e-mail only when an address is on file; no per-user channel choice. Marketing comes later and needs consent. | Simple, controlled delivery. | ✅ (settings table designed in the notifications review) | 2026-10-03 | Design review |
| NT-04 | Mark-as-read is idempotent; unread = `read_at IS NULL`. | Safe retries. | ✅ | 2026-09-17 | backend-notifications.md |
| NT-05 | Threshold alerts fire once per **crossing** (previous above, current at/below); a skipped range raises one alert, the most severe; rising values never alert. | No trip concept yet; no repeats. | ✅ 📦 per-trip dedup | 2026-09-17 | backend-notifications.md §2 |
| NT-06 | Battery-alert payloads freeze the nearest available station and distance at alert time. | Describes the moment of crossing. | ✅ | 2026-09-17 | backend-notifications.md §2 |

## TM — Telemetry

| ID | Decision | Why | Status | Date | Source |
|---|---|---|---|---|---|
| TM-01 | Ingestion is per message: MQTT consumer → in-memory queue → worker → one transaction per message. | Lower latency; one bad message can't affect others. | ✅ | 2026-07-30 | telemetry-ingestion planner §Step 16 |
| TM-02 | QoS 0, no retain, no retry/DLQ/persistent queue/dedup; a full queue drops with a warning; shutdown doesn't drain; a DB error rolls back, stops the worker and the process exits non-zero. | MVP proves the flow; reliability later. | ✅ 📦 deferred.md 25 (batching), reliability items | 2026-07-28 | telemetry-ingestion planner; backend-runtime-conventions.md |
| TM-03 | Validation (JSON + Pydantic) happens in the consumer before enqueue; invalid messages are logged and dropped; the queue carries `TelemetryEnvelope` (validated + raw dict). | Keep bad data out; keep `raw_payload`. | ✅ | – | telemetry-ingestion planner §Step 5, 8 |
| TM-04 | Topic `g3network/telematics/{telematic_serial}/telemetry`; payload carries no internal IDs; `mqtt-spec.md` is the contract; `recorded_at` must be timezone-aware; `received_at` set per message by the backend. | Devices identify by serial. | ✅ | 2026-07-30 | telemetry-ingestion planner §Step 4 |
| TM-05 | Messages from unknown or unassigned devices (or devices of soft-deleted vehicles) are skipped. | Telemetry needs a vehicle. | ✅ | 2026-10-01 | telemetry-ingestion planner; happy-path D11 |
| TM-06 | `vehicle_telemetry`: BIGINT identity + `recorded_at` PK, unique (telematic_id, recorded_at), `raw_payload` JSONB kept verbatim, `message_uuid` indexed but not unique, `location` geography without index. | Hypertable rules; reprocessing; no dedup yet. | ✅ | – | telemetry-ingestion planner §Step 2 |
| TM-07 | Liveness is judged from the backend `received_at` (not device time); a vehicle is online when its newest `received_at` is within 300 s, computed at read time. | Device clocks can skew; silence threshold too coarse for a map. | ✅ | 2026-10-01 | happy-path D2; future-resolved §35 |
| TM-08 | Alert detection runs inside the ingestion transaction, after the insert; alert side effects don't change ingestion counters. | Same transaction; honest metrics. | ✅ | 2026-09-17 | backend-notifications.md §3.3 |
| TM-09 | Anomalies: high battery temperature (crossing rule, re-arms on recovery), sudden voltage drop (absolute ≥ 50 V delta), new fault codes as one generic DEVICE_FAULT; level anomalies alert on a first reading, drops don't; thresholds are unvalidated engineering defaults. | Fire-safety first reading; no vendor catalog. | ✅ 📦 deferred.md 42–45 | 2026-09-17 | backend-anomaly-detection.md §2 |
| TM-10 | SOH and cycle count are telemetry columns; SOH alert uses the crossing rule (no first-reading exception). | Same shape; capacity fade isn't a safety event. | ✅ | 2026-09-17 | activation-soh planner §2 |
| TM-11 | F-A6/F-C6 energy is computed from telemetry SOC deltas (one window-function query, its own range cap); derived rates are `None` when undefined; unknown vehicle = 404. | Sessions have no vehicle link yet. | ✅ 📦 station-metered F-C6: deferred.md 62 | 2026-09-17 | operating-energy-reports planner |
| TM-12 | Fleet-wide telemetry views and geofence detection live in `telemetry` (`telemetry → fleet` edge), never `fleet → telemetry`. | Avoid a cycle. | ✅ | 2026-10-01 | happy-path D7 |
| TM-13 | History API: required timezone-aware window, max 7 days, `limit` (default 500, max 2000) without offset; trip replay is a bounded history query, not trip segmentation. | Hypertable-friendly; no trip concept. | ✅ 📦 deferred.md 46 | 2026-09-17 | telemetry-query-api planner §2.2 |
| TM-14 | MQTT client: `aiomqtt`; ingestion settings namespaced `TELEMETRY_*`, MQTT settings `MQTT_*`; no metrics, structured JSON logs only. | One client; minimal MVP. | ✅ | 2026-07-28 | telemetry-ingestion planner §Step 7, 13 |

## TX — Telematics devices

| ID | Decision | Why | Status | Date | Source |
|---|---|---|---|---|---|
| TX-01 | "Telematic" = the physical device (`telematics` domain, CRUD); "telemetry" = its data (`telemetry` domain, no device CRUD). | Different things. | ✅ | 2026-07-28 | crud-telematics planner |
| TX-02 | `telematic_serial` is the unique business key; `vehicle_vin` is an API input resolved to `vehicle_id`, not stored; an unknown VIN returns 404; explicit `null` unassigns; a vehicle with a live device returns 409. | Clear mapping errors. | ✅ | 2026-10-01 | future-resolved §18, §83 |
| TX-03 | Soft-deleted devices keep their `vehicle_id` as history; one live device per vehicle via a partial unique index. | History without blocking replacement. | ✅ | 2026-10-01 | future-resolved §82 |
| TX-04 | Config push (F-J2): its own route, publish-then-record (fail closed, 502 when the broker is down), short-lived MQTT client with a random client ID, QoS 1, not retained; INACTIVE devices refused; fleet-wide push loops device by device and reports per-device results. | No ack topic; honest state; no batching. | ✅ 📦 ack/rollback/audit: deferred.md 52–59 | 2026-09-17 / 10-01 | telematics-config-push planner; happy-path D9 |
| TX-05 | Device-silence monitor: periodic worker, one alert per silence episode, dedup by timestamps (survives restarts), interval much smaller than threshold. | No in-process state. | ✅ 📦 power vs signal loss: deferred.md 50–51 | 2026-09-17 | activation-soh planner §2–3 |
| TX-06 | `telematics.last_seen_at` removed; device liveness derived from telemetry. | Nothing left to keep in sync. | ✅ | 2026-10-01 | future-resolved §14 |

## VH — Vehicles

| ID | Decision | Why | Status | Date | Source |
|---|---|---|---|---|---|
| VH-01 | Activation is a one-way ladder PENDING → DEVICE_ASSIGNED → ACTIVATED (first telemetry); no reset; existing rows start PENDING. | Honest provisioning state. | ✅ | 2026-09-17 | activation-soh planner §2 |
| VH-02 | `battery_capacity_kwh` is nullable; reports fall back to a flagged default. | Don't fabricate specs. | ✅ 📦 vendor catalog: deferred.md 61 | 2026-09-17 | operating-energy-reports planner |
| VH-03 | Each truck (and each driver profile) has exactly one owning organization; cooperating parties declare the owner themselves and their terms aren't modelled. | Our responsibility ends at ownership. | ✅ | 2026-10-03 | DBML vehicles notes |
| VH-04 | The local protocol between the telematics device and the vehicle screen is out of scope until the vehicle app starts. | No vehicle-app source. | ⏳ open question 3 | – | Open questions (this log) |

## DR — Drivers

| ID | Decision | Why | Status | Date | Source |
|---|---|---|---|---|---|
| DR-01 | Driver ↔ vehicle is an assignment history; one active vehicle per driver and one active driver per vehicle; reassigning auto-closes the previous assignment; assigning a vehicle held by someone else is a 409. | MVP 1:1 driving. | ✅ | 2026-09-18 | crud-drivers planner §2 |
| DR-02 | Soft-deleting a driver closes the active assignment and sets INACTIVE; unknown VIN on assign is 404. | No orphan holds; explicit errors. | ✅ | 2026-09-18 | crud-drivers planner §2 |
| DR-03 | A driver profile belongs to a membership (one person in one organization); a person driving for two companies has one profile in each. | ID-09, ID-13. | ✅ | 2026-10-03 | DBML `drivers` |
| DR-04 | Empty-trip detection (F-A9) is suspended until a trip concept exists. | No trip/ignition signal. | 📦 deferred.md 67 | 2026-09-18 | crud-drivers planner §2 |

## FL — Fleet

| ID | Decision | Why | Status | Date | Source |
|---|---|---|---|---|---|
| FL-01 | `fleet` owns `fleet_vehicle_memberships` (open/close); a vehicle is in at most one fleet at a time; the dead `vehicles.fleet_id` was dropped. | History; no `vehicles → fleet` cycle. | ✅ | 2026-09-18 | crud-fleet planner §2; future-resolved §10 |
| FL-02 | A fleet is a named group of vehicles **inside** an organization and can sit under another fleet (`parent_fleet_id`), so each customer models its own structure (region > branch > depot) as data. Closes former D2. | Reorganising never changes the design. | ✅ | 2026-10-03 | DBML `fleets` |
| FL-03 | A large organization can limit fleet-level roles to some fleets (`fleet_user_assignments`, in the fleet domain); an assignment covers the fleet and everything below; none = whole organization. | Multi-level managers. | ✅ | 2026-10-03 | DBML `fleet_user_assignments` |
| FL-04 | Matrix structures (a truck in several groups) and custom group labels are not modelled for now. | No customer needs them yet. | ✅ | 2026-10-03 | Design review |
| FL-05 | Geofences belong to a fleet and apply to its current members; detection runs per reading in ingestion. | The designed owner didn't exist yet. | ✅ | 2026-10-01 | future-resolved §47 |
| FL-06 | Membership rules: unknown VIN 404, vehicle in another fleet 409, re-add idempotent; closing a membership by ID works even for soft-deleted vehicles; deleting a fleet closes all its memberships. | Clear errors; no orphans. | ✅ | 2026-10-01 | crud-fleet planner §3.1; future-resolved §84 |
| FL-07 | KPI dashboard (F-E2), charging & warranty report (F-E3) and per-driver efficiency (F-A8) are not built. | Need cross-domain DTOs, session attribution and `policy`. | 📦 deferred.md 62, 72 | 2026-09-18 | crud-fleet planner §2 |

## SP — Support

| ID | Decision | Why | Status | Date | Source |
|---|---|---|---|---|---|
| SP-01 | Tickets and SOS share one `support_cases` table (`case_type`). | One lifecycle. | ✅ | 2026-09-18 | support-cases planner §2 |
| SP-02 | Vehicle context (VIN, location, error code) is supplied by the app and snapshotted; no `support → telemetry` edge. | The driver's live fix is more trustworthy. | ✅ | 2026-09-18 | support-cases planner §2 |
| SP-03 | The SLA deadline is copied onto each case; breach is computed at read time; no monitor yet. | Config changes never rewrite history. | ✅ 📦 deferred.md 70 | 2026-09-18 | support-cases planner §2 |
| SP-04 | CLOSED/CANCELLED cases are terminal (409); status changes stamp their timestamps once, back-filling skipped stages. | Clean timeline. | ✅ | 2026-09-18 | support-cases planner §2 |
| SP-05 | An SOS records its channel (hotline allowed; location required only in-app) and raises a CRITICAL `SOS_ALERT` in the same transaction. | Right SLA for phone SOS. | ✅ | 2026-10-01 | happy-path D12 |
| SP-06 | Partner directory/dispatch (F-I4) and maintenance booking (F-I3) are deferred; partners get both app accounts and a future API channel (`api_clients`). | Scope; partners' own software. | 📦 deferred.md 68, 69, 71 | 2026-10-03 | support-cases planner; Design review |

## CS — Charging stations

| ID | Decision | Why | Status | Date | Source |
|---|---|---|---|---|---|
| CS-01 | `charging_stations` owns topology and OCPP; `charging_sessions` owns session data and never calls back (`charging_stations → charging_sessions`). | One-way flow; don't merge. | ✅ | 2026-07-31 | former tech-decisions; backend-charging.md §1 |
| CS-02 | Topology is pre-provisioned through the admin API (N EVSE × N connector, positive OCPP IDs); OCPP messages never auto-create it. | Controlled topology. | ✅ | 2026-07-31 | backend-charging.md §3.1 |
| CS-03 | OCPP 1.6J gun *n* = EVSE *n*, connector 1; connector 0 (the whole charger) is stored on the station, not as topology. | Guns stay distinguishable; `> 0` invariant kept. | ✅ | 2026-09-24 | ocpp16 planner D3 |
| CS-04 | Connector status: a 10-value enum (2.0.1's 5 + 1.6J's), stored as reported with error code / vendor code / info, latest report wins. | Don't lose status or errors. | ✅ | 2026-09-24 | ocpp16 planner D4, Step 5 |
| CS-05 | A connector is free only when `Available`; a station is available when not deleted, OPERATIONAL and at least one connector is `Available` (online not required). | Connector status is the availability signal. | ✅ 📦 stale status: deferred.md 76 | 2026-10-01 | ocpp16 planner D4; happy-path D3 |
| CS-06 | Charger device info and liveness are columns on the station today; `is_online` is derived from `last_seen_at` (180 s); device writes don't touch `updated_at`. | No 1:1 table yet (see DM-17 for the target split). | ✅ (interim) | 2026-09-24 | ocpp16 planner D5, Step 4 |
| CS-07 | Nearby search: `ST_DWithin` + KNN on the GIST index, radius capped at 200 km, driver-facing response without `ocpp_identity`. | Index use; safe defaults. | ✅ | 2026-09-17 | station-search planner §3.1 |
| CS-08 | Fault alerting and the vendor error-code catalog come later. | Needs the vendor's code mapping. | 📦 deferred.md 75 | 2026-09-24 | ocpp16 planner §6 |

## CO — OCPP gateway

| ID | Decision | Why | Status | Date | Source |
|---|---|---|---|---|---|
| CO-01 | Support OCPP 1.6J **and** 2.0.1 (python-`ocpp` library); one adapter class per protocol, chosen by the negotiated subprotocol (2.0.1 preferred, HTTP 426 only if neither is offered). | First real charger (Willdigits) speaks 1.6J; keep tested 2.0.1. | ✅ | 2026-09-24 | ocpp16 planner D1, D2, Step 2 |
| CO-02 | Every frame is stored verbatim (TEXT) in `charging_ocpp_messages` by a connection wrapper in its own transaction, inbound before parsing; a storage failure ends the connection. | Capture even unparseable frames; never lose evidence silently. | ✅ | 2026-09-24 | ocpp16 planner D8, Step 1 |
| CO-03 | Max frame size 1 MiB; oversized frames are refused, not truncated. | Large GetConfiguration replies. | ✅ | 2026-09-24 | ocpp16 planner Step 1 |
| CO-04 | BootNotification is always Accepted (unknown stations are already refused at the handshake), with the configured heartbeat interval; firmware changes are logged. | Pre-provisioned topology. | ✅ | 2026-09-24 | ocpp16 planner Step 4 |
| CO-05 | The only CSMS-initiated call is a post-boot GetConfiguration, scheduled from an `@after` hook (never awaited in a handler) and stored as append-only snapshots; calls use `suppress=False`. | Avoid deadlock; keep config history; no silent CALLERRORs. | ✅ 📦 command channel: deferred.md 74 | 2026-09-24 | ocpp16 planner D10, §2.2, Step 8 |
| CO-06 | Every idTag is accepted for now (no authorization). | No tag registry; blocking would stop all charging. | ✅ 📦 deferred.md 26, 62 | 2026-09-24 | ocpp16 planner D7 |
| CO-07 | Separate pure 1.6J MeterValues normaliser (never shared with 2.0.1); energy register only in Wh/kWh (×1000), other units/garbage raise; vendor measurands stored as sent; unstorable extras skipped with a counted warning. | Unit hazards; no silent loss. | ✅ | 2026-09-24 | ocpp16 planner Step 7b |
| CO-08 | Schema validation is skipped only for MeterValues and StopTransaction (they validate themselves). | One vendor value shouldn't drop a whole message. | ✅ ⏳ others after real logs | 2026-09-24 | ocpp16 planner Step 7b |
| CO-09 | OCPP payloads are handled as plain dicts with explicit helpers (no dict/dataclass dual mode). | The library never builds the dataclasses. | ✅ | 2026-09-17 | charging-ingest-fixes planner §2 |
| CO-10 | Strict timestamps (timezone required) until real-charger logs show otherwise; unknown transactions/connectors stay loud (CALLERROR). | Don't guess vendor behaviour. | ⏳ | 2026-09-24 | ocpp16 planner D11, §7 |
| CO-11 | Build against the OCPP 1.6J standard and real logs, assuming no vendor-specific behaviour, until the vendor answers the open requests. | Vendor documents missing. | ⏳ open question 4 | – | Open questions (this log) |
| CO-12 | Charging start now: the user scans the QR on the charger, the backend starts the charge remotely, and the session records who started it and which organization pays; RFID cards and VIN Autocharge come later for prepaid enterprise (VIP) charging. Closes former D4. | Guests and companies use the same flow first. | ✅ | 2026-10-03 | DBML `charging_credentials`, sessions notes |

## CE — Charging sessions

| ID | Decision | Why | Status | Date | Source |
|---|---|---|---|---|---|
| CE-01 | MVP happy path only: in order, no duplicates, pre-provisioned topology; 2.0.1 Started → Updated/MeterValues → Ended, 1.6J Start → MeterValues → Stop; sessions are `active`/`completed`. | Ideal-conditions MVP. | ✅ 📦 reliability: deferred.md 27–28 | 2026-08-02 | former tech-decisions; mvp-ideal planner |
| CE-02 | Correctness guards: persist `seq_no`; events for a COMPLETED session raise; a sample older than the stored watermark never overwrites a newer reading (time-based, never value-based); unit-aware energy normalization. | Don't write numbers nothing can vouch for. | ✅ | 2026-09-17 | charging-ingest-fixes planner |
| CE-03 | 1.6J `transactionId` comes from a PostgreSQL integer sequence; sessions are looked up by transaction ID from the database; `meterStop` is always stored as the charger's closing reading. | Survives restarts; keep the authoritative number. | ✅ | 2026-09-24 | ocpp16 planner D6, D14, Step 6 |
| CE-04 | One `charging_session_measurements` hypertable holds every measurand for both protocols (energy as canonical Wh); `/meter-values` is the energy-only view. | One place for measurements. | ✅ | 2026-09-24 | ocpp16 planner D9, Step 7a |
| CE-05 | A new StartTransaction on a connector with an active session only logs a warning; orphan/back-fill policies wait for real logs. | Don't guess. | ⏳ | 2026-09-24 | ocpp16 planner Step 6, D14 |
| CE-06 | MeterValues without a transaction ID are not attributed (kept in the raw log). | Station metering needs tariffs. | 📦 deferred.md 77 | 2026-09-24 | ocpp16 planner Step 7b |
| CE-07 | Station energy (F-C5): deltas between consecutive readings, booked in the bucket of the later reading; unknown station returns zero. | Correct across slot boundaries; sessions don't own station existence. | ✅ | 2026-10-01 | happy-path D5; station-search planner §2 |
| CE-08 | Session APIs are read-only and paginated with stable ordering; no raw payloads. | Monitoring only. | ✅ | 2026-08-04 | mvp-ideal planner Step 6 |
| CE-09 | Authorization, remote control, pricing, payment and debt are out of the MVP. | Business features not decided. | 📦 deferred.md 26 | 2026-07-31 | backend-charging.md §2 |

---

## Superseded decisions (kept for history)

| ID | Was decided | Replaced by | Date | Source |
|---|---|---|---|---|
| CV-S1 | New docstrings/comments written in Vietnamese. | CV-14 (English only) | – | crud-telematics planner Step 3 |
| TT-S1 | Black + isort for formatting. | TT-03 (Ruff) | 2026-10-01 | former tech-decisions |
| DM-S1 | Legacy migration chain, then a reset + per-feature revisions (0001–0026). | DM-03 (single baseline) | 2026-08-26 → 10-01 | mvp-ideal planner Step 2; database.md |
| TM-S1 | Batch ingestion (window + bulk insert), later kept dormant in source. | TM-01; batch code removed, design kept in deferred.md 25 | 2026-07-30 → 10-01 | telemetry-ingestion planner §Step 16 |
| TM-S2 | Telemetry latitude/longitude as two DOUBLE columns. | DM-09 (geography point) | 2026-09-17 | future-resolved §9 |
| TM-S3 | `/latest` and fleet lists never show online/offline. | TM-07 (read-time `is_online`) | 2026-10-01 | happy-path D2 |
| TX-S1 | Unknown/soft-deleted VIN on telematics leaves the device unassigned (201). | TX-02 (404) | 2026-10-01 | future-resolved §83 |
| TX-S2 | `last_seen_at` on telematics, updated forward-only. | TX-06 (derived from telemetry) | 2026-10-01 | future-resolved §14 |
| VH-S1 | Vehicle columns `team_id`, `max_range_km`, duplicate plate → 400. | Current vehicles model (no team_id; 409 conflicts) | – | crud-vehicles planner |
| FL-S1 | One fleet level only, no `parent_fleet_id`. | FL-02 (nested fleets) | 2026-10-03 | crud-fleet planner §2 |
| FL-S2 | Convert `vehicles.fleet_id` into a UUID FK. | FL-01 (membership table) | 2026-09-18 | future-resolved §10 |
| NT-S1 | "Nearest available station" = nearest operational station. | CS-05 (≥ 1 Available connector) | 2026-10-01 | happy-path D3 |
| CS-S1 | Nearby search availability from maintenance status only. | CS-05 | 2026-10-01 | happy-path D3 |
| CO-S1 | Gateway accepts only `ocpp2.0.1`; a connection registry. | CO-01 | 2026-09-24 | backend-charging.md Step 5 |
| CO-S2 | Adapter accepts both dict and dataclass payloads. | CO-09 | 2026-09-17 | charging-ingest-fixes planner |
| CO-S3 | Redacted JSONB raw payload. | CO-02 (verbatim TEXT log) | 2026-09-24 | backend-charging.md §3.14 |
| CE-S1 | Five-state session machine with idempotency and reconciliation. | CE-01 (MVP happy path) | 2026-08-02 | backend-charging.md §3.11 |
| CE-S2 | Energy-only `charging_session_meter_values` table. | CE-04 (unified measurements) | 2026-09-24 | backend-charging.md §3.9 |
| CE-S3 | Per-connection transaction→session map. | CE-03 (database lookup) for 1.6J | 2026-09-24 | mvp-ideal planner Step 4 |
| BL-S1 | "Customer" = one vehicle for F-C6. | ID-02 (organizations) | 2026-10-03 | operating-energy-reports planner |
| BL-S2 | One pricing unit for the whole SaaS (former D11). | BL-01 (per-feature pricing) | 2026-10-03 | DBML open decisions |
| ID-S2 | An organization keeps at least one active ORG_ADMIN (part of ID-12). | ID-33 (exactly one) | 2026-10-04 | Design review |
| ID-S3 | Consent recorded per purpose and document version, with withdrawal, in `user_consents` (ID-20). | ID-38 (three levels, append-only, `legal_documents`) | 2026-10-04 | Design review |
| ID-S1 | `customer_accounts` with an account type; `organization_type` CUSTOMER/G3/PARTNER; separate contacts table; `roles` table; DRIVER not a role; one organization per user. | ID-02…ID-14 | 2026-10-02/03 | Design review |

## Inconsistencies found while gathering

- `backend-crud-vehicles.md` mixes `PUT` and `PATCH` for vehicle updates, and leaves open whether a licence plate may change. The code uses PATCH (CV-07); the plate question is still open.
- `backend-crud-vehicles.md` Step 11 asks for a per-domain `.md` (e.g. `vehicles.md`) beside each domain's code; no domain has one. The DBML views and planners now cover that role.
- `backend-ocpp16-charger-integration.md` / `charging-ingest-fixes.md` list a pre-existing telemetry test bug (`test_telemetry_repository_round_trip_rolls_back` using old lat/lon columns) as unfixed.
