---
name: feature-catalog
description: Add, change, split, re-prioritize or report on product features using the single YAML source (docs/product/features/features.yaml) and its generated Markdown checklists and Vietnamese Excel. Use when a new idea, table review or implementation changes what a feature does, who uses it, what it depends on or its build status; when a feature is finished on the backend, app or portal; when filling in a feature's related tables after the database review; or when someone asks what features exist, their progress, or how to present them (e.g. to the BOD).
---

# Feature catalog

Every product feature, for every user and every surface, lives in **one
file**: `docs/product/features/features.yaml`. Everything else in that
directory is generated from it. Never hand-edit a generated file (a hook
refuses it).

| File | Content | Audience |
|---|---|---|
| `features.yaml` | Source of truth: roles, domains, features, non-functional requirements | Whoever changes the scope |
| `README.md` | Progress per domain and surface, domain index with release counts, legend, roles, non-functional requirements, old-code → new-code map | Everyone |
| `domains/<key>.md` | One domain: a checklist, then every feature in detail (capabilities, status, depends on / needed by, tables, sources, questions) | Product, developers |
| `features.xlsx` | **In Vietnamese.** `Tổng quan` (domain index with links, progress, legend, roles), one sheet per domain (one row per feature), `Phi chức năng` | BOD, BA |

## Commands (run from the repository root)

```bash
# Regenerate every view (refuses while the source breaks a rule)
uv run --project backend --with pyyaml --with openpyxl python .claude/skills/feature-catalog/scripts/feature_catalog.py generate
# Verify the source's rules and that every view is current (part of make check)
make feature-catalog-check
```

## Rules the source follows

- **Codes** are `<PREFIX>-<NN>`; the prefix is the domain. A code is stable:
  a removed feature's code is never reused, a split keeps the old code on the
  main part. The old `F-A1` codes stay in `old_codes`.
- **One feature = one thing a user can do or receive**, broken down in
  `capabilities` (English/Vietnamese pairs). A platform quality is a
  non-functional requirement (`nfrs`), not a feature.
- **`surfaces`** lists only the surfaces the feature needs (`backend`, `app`
  = driver mobile/vehicle app, `portal` = web portal for fleets, admins and
  support), each with a status `todo | doing | done`. A feature is done when
  every listed surface is done. Update the status in the same change that
  finishes the work.
- **`offer`**: `internal` (staff only, never in a plan — decision ID-11),
  `included` (every organization whatever its plan), `tbd` (to be priced).
- **`users`** are role codes from `roles` (the job titles of the identity
  design plus `SYSTEM` and `EXTERNAL`).
- **`related_tables`** stays empty until the database review is finished;
  then each name must exist in the DBML.
- **`sources`** start with a known prefix: `PRD F-xx`, `Data IN-n` /
  `Data OUT-n` (phase-1 data sheet), `Phase 2 Xn`, `Prerequisite n`,
  `Decision XX-NN` (must exist in `docs/decisions/decision-log.md`),
  `deferred.md n` (must exist in deferred.md or deferred-resolved.md),
  `Design review <date>`, `NF-nn`.
- Every text has its Vietnamese twin (`name_vi`, `what_vi`, `value_vi`, the
  second element of each pair). Keep Vietnamese natural and short: it is
  what the BOD reads.

`check` also refuses unknown roles/surfaces/statuses/priorities/releases/
offers, dependencies on missing features or cycles, unknown backend domains
in `touches`, and sheet names over Excel's 31 characters (set `sheet_vi` on
the domain).

## When to update it

Check whether the catalog must change whenever a table review, a new idea or
an implementation changes scope (see collaboration-conventions.md): add or
split a feature, move a capability, change a dependency or priority, add a
question, or fill `related_tables`. Say so in the reply when nothing needs
to change.
