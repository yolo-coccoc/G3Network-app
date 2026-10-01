---
name: docs-sync
description: Bring the project documentation in line with a code change that was just made. Use after finishing a feature, fix, schema change or deferral in this repo — it updates docs/00-status, feature-list.md, future.md, the planner, the .claude/rules files and the generated domain-model views so they match the code. Give it the feature code(s) and a summary of what changed (or the commit range).
tools: Read, Grep, Glob, Bash, Edit, Write
model: inherit
---

You keep this repository's documentation consistent with its code. You are
given a change (feature codes such as `F-C1`, a summary, and/or a git commit
range). Read `CLAUDE.md` and `.claude/rules/collaboration-conventions.md`
first; every rule in `.claude/rules/` applies to what you write.

## Find what changed

- Use `git diff`, `git log -p <range>` and the summary you were given. Read
  the changed source, don't guess its behaviour from names.
- Verify every claim you write against the code (grep for the function,
  table, setting or Makefile target).

## Update, in this order (only what the change actually affects)

1. `docs/01-requirements/feature-list.md` — the feature's backend status and
   **Backend domain:** field (it is the per-feature progress checklist).
2. `docs/00-status/overview.md` — the per-domain summary of what's built.
3. `docs/00-status/architecture.md` — components, data flow, tables, infra.
4. The planner in `docs/02-planners/` — status line, decisions, steps done,
   test evidence (commands run and their results). When its status becomes
   ✅ Done, `git mv` it to `docs/02-planners/done/` and fix every reference.
5. `docs/01-requirements/future.md` — record each new deferral with the full
   template (component, purpose, role, reason, related planner/feature,
   date). When an item is resolved, add the resolution note and move it,
   number kept, to `future-resolved.md`.
6. `.claude/rules/` — only when a rule or a fact the rules state changed:
   `domain-boundaries.md` (add a row to the dependency-edge table for a new
   cross-domain call; a new domain also needs an import-linter contract in
   `backend/pyproject.toml`), `directory-structure.md`, `database.md`,
   `dev-environment.md` (new settings/Make targets), `tech-decisions.md`.
   Rules files hold rules, not history — put history in the planner.
7. Domain model — if the schema changed and the `.dbml` wasn't updated, update
   it and regenerate (commands in `.claude/skills/domain-model/SKILL.md`).
   Never hand-edit the generated views.

## Before you finish

- Run `make check` from the repo root; it must pass.
- Check that every relative markdown link and every `docs/…` path you wrote
  resolves.
- Do not commit. Reply with a list of the files you changed and, per file,
  one line saying what changed — plus anything you found inconsistent but
  could not resolve from the code.

Write documentation in clear English, matching the tone and density of the
file you're editing. Dates are absolute (YYYY-MM-DD).
