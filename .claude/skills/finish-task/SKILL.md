---
name: finish-task
description: Run this repo's "finish a backend task" checklist before committing — quality gate, convention review, docs sync, commit split. Use when a backend feature, fix or refactor is done and about to be committed, or when the user says "wrap up", "finish", "ready to commit".
---

# Finish a backend task

The checklist from `.claude/rules/backend-runtime-conventions.md`
("Consistency checklist") as steps. Stop and report at the first failure
you can't fix; never weaken a check to make it pass.

1. **Gate** — `make check` from the repo root (ruff lint + format check,
   import-linter, mypy, smoke tests, domain-model check). If formatting
   fails, run `make format` and re-check.
2. **Database** — if a model, the baseline migration, or a repository query
   changed: `make db-reset` then `make backend-test-integration`. A schema
   change must also satisfy `.claude/rules/database.md` (`.dbml` updated,
   views regenerated).
3. **Review** — run the `convention-reviewer` agent on the diff and fix every
   `blocker`/`major` finding (or explain why it doesn't apply).
4. **Manual checks the tools can't do**
   - `__init__.py` files contain only a docstring.
   - No placeholder/TODO for a deferred component — record it in
     `docs/01-requirements/future.md` instead.
   - No batched/bulk query written preemptively.
   - No new dependency, middleware or infrastructure that wasn't requested.
   - Docstrings and comments updated where the logic changed (English).
5. **Prove it** — for ingestion/OCPP/API behaviour, run the relevant flow of
   the `e2e-sim` skill, not only unit tests.
6. **Docs** — run the `docs-sync` agent with the feature codes and a
   summary; review its file list.
7. **Commit** — on `master`, Conventional Commits
   (`feat(<domain>): … (F-xx)`), split unrelated changes (code vs. its `docs`
   commit). The pre-commit hook runs `make check` again. End the message with
   the attribution trailer the session asks for.
8. **Report** — what changed, which checks ran with their results (say
   explicitly if the integration tests were not run, and why).
