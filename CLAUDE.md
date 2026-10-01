# CLAUDE.md — Electric Truck Driver Support System

> Orientation document for AI coding agents and developers working in this
> monorepo. The project already has code under active development. When code,
> planners, requirements, and this document disagree, determine the most
> recent decision — don't default to copying an existing pattern if it
> violates convention. Update the relevant document (this file, or a detail
> file under `.claude/rules/`) once a decision is confirmed.

- `docs/00-status/overview.md` - Current repo status: quick domain-level
  summary of what's actually implemented (source of truth for progress at a
  glance)
- `docs/00-status/architecture.md` - Technical documentation: components,
  diagram, database, infra
- `docs/01-requirements/feature-list.md` - Whole-company product spec by
  actor (driver app, web portal, CSKH, background system), sourced from the
  product PRD. Each feature also carries this repo's backend implementation
  status and backend domain directly (this repo builds only the backend) —
  it's the **feature-level progress checklist** (source of truth for
  progress per-feature, alongside overview.md's per-domain summary); update
  a feature's status/domain fields in the same change that completes it
- `docs/01-requirements/future.md` - Deferred components (skipped for now to reach MVP sooner);
  closed items move, number kept, to `future-resolved.md`
- `docs/01-requirements/domain-model/` - The whole database design, built and
  planned, in one source file (`domain-model.dbml`), plus generated views:
  `overview.md` (domain map, data ownership, open decisions), one page per
  domain, and `domain-model.xlsx` (Vietnamese, for business readers: a
  "Tổng quan" index sheet, then one sheet per table). Edit only the `.dbml`,
  then regenerate (the local `domain-model` skill has the commands)
- `docs/02-planners/` - One implementation planner per domain/feature (step-by-step build log,
  decisions made, evidence of what was tested); read the relevant planner before resuming work
  on a domain it covers, and add/update a planner when starting a new one.
  Once a planner's status is ✅ Done, move it to `docs/02-planners/done/`
  (it still records the decisions for that domain — read it before extending one)
- `docs/03-specifications/` - External specs and wire contracts (OCPP charger
  docs, the telematics MQTT contract `mqtt-spec.md`)
- `docs/99-archive/` - Point-in-time or superseded documents kept for history;
  never treat them as current

---

## Detailed documentation

This file only holds general direction and the rules that always apply.
Details for each topic live under `.claude/rules/` — open the right file when
a task touches that topic, no need to read them all every time. The
`.claude/` directory (rules, skills, agents) is tracked in git; only
`.claude/settings.local.json` (personal overrides) is ignored.

| Topic | File | Read when |
|---|---|---|
| Finalized tech decisions (backend/infra) | [tech-decisions.md](./.claude/rules/tech-decisions.md) | You need to know/confirm a platform choice, or evaluate a new technology |
| Directory structure (backend/infra as it exists) | [directory-structure.md](./.claude/rules/directory-structure.md) | Before creating a new file/directory, or unsure where a domain/module belongs |
| Domain boundaries (backend) | [domain-boundaries.md](./.claude/rules/domain-boundaries.md) | Adding a new domain, or calling across two domains |
| Development environment | [dev-environment.md](./.claude/rules/dev-environment.md) | Running/starting a local service, adjusting environment variables |
| Backend convention — naming/DTO/mapping | [backend-coding-conventions.md](./.claude/rules/backend-coding-conventions.md) | Naming classes/functions/variables, distinguishing Request/Response/DTO/value object |
| Backend convention — transaction/time/layer/worker | [backend-runtime-conventions.md](./.claude/rules/backend-runtime-conventions.md) | Writing service/repository code, managing session/transaction, background workers, checklist before finishing a backend task |
| Repo-wide conventions | [repo-conventions.md](./.claude/rules/repo-conventions.md) | Commit, branch, PR, secrets, deferring/removing a component |
| Database | [database.md](./.claude/rules/database.md) | Writing/reviewing a migration, changing the schema |
| Open questions | [open-questions.md](./.claude/rules/open-questions.md) | Touching an area that's not yet decided (CI/CD, secrets management, vehicle-app protocol) |
| Agent tooling (hooks, agents, skills, MCP, plugin) | [dev-environment.md](./.claude/rules/dev-environment.md#claude-code-tooling-checked-in-under-claude-and-mcpjson) | Choosing a project agent/skill, or a hook/MCP server misbehaves |
| Collaboration conventions | [collaboration-conventions.md](./.claude/rules/collaboration-conventions.md) | Every prompt — not backend-task-scoped like the rest of this table |

`web-portal/` and `vehicle-app/` have no active source yet — they'll get
their own conventions once the first task for that part starts; don't write
them in advance.

---

## Always-applicable rules

- For every prompt, regardless of topic, follow
  [collaboration-conventions.md](./.claude/rules/collaboration-conventions.md).
- Before starting any backend task, read
  [repo-conventions.md](./.claude/rules/repo-conventions.md) and
  [domain-boundaries.md](./.claude/rules/domain-boundaries.md) — both are short
  and apply to every task regardless of topic (unlike the other files in the
  table above, which you only need to open when the task touches that specific
  topic).

Standing rules (behavioral or coding) belong here or under `.claude/rules/`
— never in auto-memory. See "Where rules live" in
[collaboration-conventions.md](./.claude/rules/collaboration-conventions.md).
