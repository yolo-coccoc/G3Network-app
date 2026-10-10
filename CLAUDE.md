# CLAUDE.md — Electric Truck Driver Support System

> Orientation document for AI coding agents and developers working in this
> monorepo. The project already has code under active development. When code,
> planners, requirements, and this document disagree, determine the most
> recent decision — don't default to copying an existing pattern if it
> violates convention. Update the relevant document (this file, or a detail
> file under `.claude/rules/`) once a decision is confirmed.

## Where things are

Two kinds of material, kept apart:

- **Rules — how we work** live in `.claude/rules/` (table below). Open the
  file a task touches; no need to read them all every time.
- **Facts — what the project is** live in `docs/`, one home per fact
  ([`docs/README.md`](./docs/README.md) maps every folder):

| Question | Home |
|---|---|
| What do we build, and how far along is it? | [Feature catalog](./docs/product/features/README.md) (`features.yaml` + generated views; holds the backend status per feature since the 2026-10-10 sync — the old [`feature-list.md`](./docs/product/feature-list.md) is superseded by it for the features the refactor-and-build run built) |
| What exists today: components, stack, domains, database, directory layout? | [`docs/design/architecture.md`](./docs/design/architecture.md) |
| What does the database look like (built and planned)? | [Domain model](./docs/design/domain-model/overview.md) (`domain-model.dbml` + generated views) |
| External contracts (OCPP charger, MQTT telematics)? | [`docs/design/specifications/`](./docs/design/specifications/) |
| Why was something decided, and what is still open? | [Decision log](./docs/decisions/decision-log.md) (includes the open questions) |
| What was deliberately left for later? | [`docs/decisions/deferred.md`](./docs/decisions/deferred.md) (closed items, numbers kept: `deferred-resolved.md`) |
| How was a domain built, and what was tested? | [`docs/planners/`](./docs/planners/) (finished ones in `done/`; new ones from `_TEMPLATE.md`) — read the relevant planner before resuming or extending a domain |

Generated views (domain model, feature catalog) are never edited by hand:
edit the source, then regenerate with the matching skill. The `.claude/`
directory (rules, skills, agents, hooks, shared settings) is tracked in git;
only `.claude/settings.local.json` and `.claude/worktrees/` are ignored.

### Rules

| Topic | File | Read when |
|---|---|---|
| Collaboration conventions | [collaboration-conventions.md](.claude/rules/collaboration-conventions.md) | Every prompt |
| Repo-wide conventions | [repo-conventions.md](.claude/rules/repo-conventions.md) | Commit, secrets, where a new file goes, deferring/removing a component |
| Domain boundaries (backend) | [domain-boundaries.md](.claude/rules/domain-boundaries.md) | Adding a new domain, or calling across two domains |
| Backend convention — naming/DTO/mapping | [backend-coding-conventions.md](.claude/rules/backend-coding-conventions.md) | Naming classes/functions/variables, distinguishing Request/Response/DTO/value object |
| Backend convention — transaction/time/layer/worker | [backend-runtime-conventions.md](.claude/rules/backend-runtime-conventions.md) | Writing service/repository code, managing session/transaction, background workers, checklist before finishing a backend task |
| Database | [database.md](.claude/rules/database.md) | Writing/reviewing a migration, changing the schema |
| Development environment and agent tooling | [dev-environment.md](.claude/rules/dev-environment.md) | Running a local service, environment variables, choosing a skill/agent, a hook or MCP server misbehaves |

A platform choice or a new technology: check the decision log first (and the
stack table in `architecture.md`). An area that is not decided yet: keep the
interim proposal listed under the decision log's open questions.

`web-portal/` and `vehicle-app/` have no active source yet — they'll get
their own conventions once the first task for that part starts; don't write
them in advance.

---

## How a feature gets built

Workspace setup is one command: `make setup` (see README.md). Then, for each
piece of work, use the project's skills and agents in this order — each one
holds the detailed steps, so follow it rather than improvising:

1. **`start-feature <F-XX>`** — gather what the feature list, planners,
   deferred.md, open questions and the DBML already say; write the planner from
   `docs/planners/_TEMPLATE.md`; design tables in the DBML; **stop for the
   owner to confirm the scope decisions**. No code before that.
2. **`new-domain`** — only if the feature needs a new bounded context.
3. **`schema-change` agent** — any table/column/index/enum change (model +
   DBML + baseline migration + `make db-reset` + `make db-check`).
4. Implement the planner's steps; `make check` after each; tick the step with
   its evidence. Load **`ocpp-reference`** for charger/OCPP/CSMS work.
5. **`e2e-sim`** — when behaviour crosses processes (MQTT ingestion, OCPP
   gateway, API).
6. **`finish-task`** — gate, integration tests, **`convention-reviewer`**
   agent, **`docs-sync`** agent, then commit on `master` (the pre-commit hook
   runs `make check` again).

---

## Always-applicable rules

- For every prompt, regardless of topic, follow
  [collaboration-conventions.md](.claude/rules/collaboration-conventions.md).
- Before starting any backend task, read
  [repo-conventions.md](.claude/rules/repo-conventions.md) and
  [domain-boundaries.md](.claude/rules/domain-boundaries.md) — both are short
  and apply to every task regardless of topic (unlike the other files in the
  table above, which you only need to open when the task touches that specific
  topic).

Standing rules (behavioral or coding) belong here or under `.claude/rules/`
— never in auto-memory. See "Where rules live" in
[collaboration-conventions.md](.claude/rules/collaboration-conventions.md).
