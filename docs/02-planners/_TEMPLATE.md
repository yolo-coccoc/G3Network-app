# Planner: <Feature name> (<F-XX>[, <F-YY>])

> Feature code: <F-XX> (<feature title from feature-list.md>)[, ...]
> Status: 📋 Planned — design awaiting confirmation
> Created: <YYYY-MM-DD>
>
> Inputs: <feature-list.md entries, specs in docs/03-specifications/, related
> planners, future.md items this touches>

<!--
How to use: copy to docs/02-planners/backend-<topic>.md (the `start-feature`
skill does this). Keep the numbered sections; delete the guidance comments.
Status values: 📋 Planned · 🚧 In progress (say which steps are done) ·
✅ Done (MVP/POC scope; name what was left out). When ✅ Done, move the file to
docs/02-planners/done/. Dates are absolute (YYYY-MM-DD).
-->

## 1. Goal

<What the feature delivers, for which actor, quoted/paraphrased from
feature-list.md. What "done" means for this backend (this repo builds only the
backend: APIs, ingestion, storage) and what is explicitly not part of it.>

## 2. Scope decisions

<!-- One row per decision the owner must confirm BEFORE code is written.
Record the alternative rejected, so it can be revisited cheaply. -->

| # | Decision | Recommended | Alternative (rejected) | Status |
|---|---|---|---|---|
| D1 | <question> | <choice + why> | <other option + why not> | ❓ Open / ✅ Confirmed <date> |

### 2.1 Out of scope (deferred)

<Each item that is "needed later" goes to docs/01-requirements/future.md with
the full template; list the item numbers here.>

## 3. Design

### 3.1 Domain and boundaries

<Owning domain (existing or new — see `new-domain` skill). New cross-domain
calls: `<from> → <to>.service.<function>` — each becomes a row in
.claude/rules/domain-boundaries.md.>

### 3.2 Data model

<Tables/columns/indexes/enums, designed first in
docs/01-requirements/domain-model/domain-model.dbml (`@status planned`).
"No schema change" if none.>

### 3.3 API / interfaces

| Method | Path | Request | Response | Errors |
|---|---|---|---|---|
| <GET> | </api/v1/...> | <schema> | <schema> | <404 XNotFoundError> |

<MQTT topics / OCPP messages / background workers, if any.>

## 4. Steps

<!-- Small, independently verifiable steps. Tick them off with the date and
the evidence (commands run, results). -->

- [ ] **Step 1 — <title>.** <What changes.> Evidence: <...>
- [ ] **Step 2 — Schema** (if any): `schema-change` agent — models + DBML +
  baseline + `make db-reset` + `make db-check`.
- [ ] **Step N — Verify and document**: `finish-task` skill (make check,
  integration tests, convention review, e2e-sim where relevant, docs-sync).

## 5. Verification

<Tests added (file + what they prove), integration/e2e runs and their
results, manual checks. Filled in as steps complete.>

## 6. Deferred / follow-ups

<future.md item numbers created or touched by this planner.>
