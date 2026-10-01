---
name: new-domain
description: Scaffold a new backend bounded context (domain) in this repo the way the existing ones are built — module files, router registration, import-linter contract, tests package, DBML tables, planner, feature-list and rules updates. Use when a task introduces a new domain under backend/app/domains/ (e.g. identity, policy, billing, scoring).
argument-hint: <domain_name> <feature codes, e.g. F-H1 F-H2>
---

# New domain

Arguments: `$ARGUMENTS` (domain name in `snake_case`, then feature codes).
Read `.claude/rules/domain-boundaries.md`, `.claude/rules/directory-structure.md`
and `.claude/rules/backend-coding-conventions.md` first. Only create a domain
for a concrete task with confirmed feature codes — never a placeholder.

**Reference implementation: `backend/app/domains/fleet/`** (CRUD, soft
delete, pagination, an open/close history table, one cross-domain edge to
`vehicles`). Mirror its structure, docstring density and naming; read each
file before writing its counterpart.

## Steps

1. **Design** — add the tables to `docs/01-requirements/domain-model/domain-model.dbml`
   as `@status planned` under the new domain, regenerate the views
   (`domain-model` skill). Agree the design before writing code.
2. **Planner** — `docs/02-planners/backend-<domain>.md`: feature codes,
   status line, decisions, steps, test evidence (copy the shape of
   `docs/02-planners/done/backend-crud-fleet.md`).
3. **Module files** — `backend/app/domains/<domain>/` with only the files
   the task needs (no empty placeholders): `__init__.py` (**docstring only**),
   `models.py`, `types.py`, `exceptions.py`, `schemas.py`, `repository.py`,
   `service.py`, `router.py`. Names: `<Object>Model`, `<Object>CreateRequest`,
   `<Object>Response`, `<Object>Reference`, `<Object>NotFoundError`,
   `<verb>_<object>_endpoint`.
4. **Wire it up**
   - Router: import and `app.include_router(...)` in `backend/app/api/main.py`.
   - Models: import the model module in `backend/app/libs/db/migrations/env.py`.
   - Schema: hand the change to the `schema-change` agent (or follow
     `.claude/rules/database.md`): models + `.dbml` (`@status built`) +
     `0001_baseline_schema.py` + `make db-reset`.
5. **Boundaries** — every call into another domain goes through its
   `service.py`. Add a row per new edge to the table in
   `.claude/rules/domain-boundaries.md`, and add an import-linter contract for
   the new domain in `backend/pyproject.toml` (copy an existing
   `[[tool.importlinter.contracts]]` block) **and** add
   `"app.domains.<domain>"` to every other contract's `source_modules`.
   `cd backend && uv run lint-imports` must report all contracts kept.
6. **Tests** — `backend/tests/<domain>/__init__.py` (docstring only) plus
   `test_<domain>_service_smoke.py` and `test_<domain>_schema_smoke.py`.
   Put record builders shared with other domains in `tests/builders.py`
   (`build_<object>_record`); use `fake_db_session()` and `FakeSessionFactory`.
7. **Docs** — `feature-list.md` (status + **Backend domain:**),
   `docs/00-status/overview.md`, `.claude/rules/directory-structure.md` (tree
   and the "no source yet" list). The `docs-sync` agent can do this pass.
8. **Gate** — `make check` and `make backend-test-integration`.
