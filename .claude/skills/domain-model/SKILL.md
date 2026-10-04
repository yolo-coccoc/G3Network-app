---
name: domain-model
description: Design, change, or present the database model of this backend using the single DBML source (docs/design/domain-model/domain-model.dbml) and its generated L1/L2/L3 views. Use when adding or changing a table, column, foreign key, or domain; when writing a migration that changes the schema; when designing the tables for a new feature; when someone asks what the data model looks like or how to present it (e.g. to the BOD); or when checking that the design still matches the SQLAlchemy models.
---

# Domain model

The whole database design, built and not yet built, lives in **one file**:
`docs/design/domain-model/domain-model.dbml`. Everything else in
that directory is generated from it. Never hand-edit a generated file.

| File | Level | Audience |
|---|---|---|
| `domain-model.dbml` | Source of truth | Whoever changes the design |
| `overview.md` | **L1**: domain map, counts, data ownership, open decisions | BOD, product |
| `domains/<domain>.md` | **L2**: entity diagram of one domain · **L3**: every column, enum, index, reference | Tech leads, developers |
| `domain-model.xlsx` | **In Vietnamese.** Sheet `Tổng quan`: index of every domain (blue row: Vietnamese title, description, relationships) and its tables (Vietnamese name, data owner, features, description), each linking to its sheet via the table name, the Vietnamese name and a `→ Mở` cell; then legend and open decisions. Then **one sheet per table**: columns with meaning/example/key/references, enum values, indexes, "referenced by" | BOD, BA, anyone without Markdown |

Workbook rules:
- **Everything in it is Vietnamese**: labels are constants in the script
  (`VI_*`, `OVERVIEW_HEADERS`, `TABLE_SHEET_HEADERS`), content comes from the
  DBML's `@vi`/`@vi-name` tags. The Markdown views stay English.
- **No build progress** (built/planned/proposed): progress is tracked
  elsewhere. `@status` still exists in the DBML because `check` needs it.
  A planned column only shows its decision IDs ("xem quyết định D1").
- **Freeze only the title rows + one header row.** A tall frozen area fills
  a laptop window and nothing scrolls; that is why the index comes first on
  `Tổng quan` and the legend/decisions go below it.
- Written only when cell values change (a save embeds a timestamp, so
  byte-level rewrites would churn git); `check` compares values, not bytes.
  An empty string is written as None so the round trip compares equal.
- Sheet names are capped at 31 characters by Excel: long table names are
  shortened via `SHEET_NAME_ABBREVIATIONS`; the full name stays in A1.
- No formulas: it is documentation, not a model.

## Commands (run from the repository root)

```bash
# Regenerate every view from the DBML
uv run --project backend --with pydbml --with openpyxl python .claude/skills/domain-model/scripts/domain_model.py generate

# Verify built tables match backend/app/domains/*/models.py AND the views are current
uv run --project backend --with pydbml --with openpyxl python .claude/skills/domain-model/scripts/domain_model.py check
```

`check` exits 1 on any mismatch, or on any missing meaning, example or
Vietnamese text. Against the models, it compares tables, columns, types,
nullability, primary keys, foreign keys (target + on-delete), and enum
values. It does **not** compare indexes, unique constraints, defaults, or
CHECK constraints; review those by hand against the migration.

Optional Mermaid syntax check (no browser needed): see the header of
`scripts/validate_mermaid.mjs`. Run it after changing the generator, not after
every DBML edit.

## DBML conventions (enforced by the generator)

- **Every table is in exactly one `TableGroup`**, and a group is a backend
  domain named like its `backend/app/domains/<name>/` directory.
  Not-yet-decided features go in the `unassigned` group.
- **Every table note starts with a tag line**, then a one- or two-sentence
  business description:
  `@status built|planned|proposed @owner customer|internal|two-party|undecided @features F-xx,F-yy [@hypertable <col>]`
  - `built`: exists in the models; must match them exactly.
  - `planned`: a feature in `feature-list.md` needs it.
  - `proposed`: not in the feature list, but the model needs it. Say why in the note.
  - `customer`: owned by one organization (the tenant). It carries
    `organization_id` only as its own owner or as the owner at the time it
    was recorded; otherwise it reads it through a customer-owned parent
    (DM-24; the generator warns when it has neither). `internal`: our own data, shared across organizations.
    `two-party`: a G3 asset used by a customer. `undecided`: cite the decision ID.
- **Every column has a meaning and an example**, both in its note:
  `note: 'What the column holds, in one plain sentence. @example 51D-123.45'`.
  `check` fails if either is missing (`generate` only warns). Write the
  meaning for a business reader: unit, what NULL means, allowed values
  (`Values: A | B`) for planned columns. For a foreign key, reuse the example
  ID of the row it points to, so one sample record can be followed across
  tables. Escape an apostrophe as `\'`. Keep examples language-neutral
  (a value, or plain `NULL`); explain NULL in the meaning, not the example.
- **Everything readable also has Vietnamese** (for the Excel export), and
  `check` fails without it:
  - column: `note: 'English meaning. @vi Ý nghĩa tiếng Việt. @example value'`
    (order matters: meaning, `@vi`, `@example`);
  - table note: after the English description, a `@vi-name <Tên bảng>` line
    and a `@vi <Mô tả>` line;
  - domain (`TableGroup`) note: after the English text, `@vi-name <Tên miền>`
    and a `@vi` block with the Vietnamese description and relationship bullets;
  - `open_decisions`: after the English table, a `@vi` block with the same
    table in Vietnamese (`| Mã | Câu hỏi | Ảnh hưởng tới |`).
  Write natural Vietnamese for business readers; keep technical names
  (table/column names, enum values, feature codes) untranslated.
- **A proposed column on a built table** gets a note starting with
  `@planned`, optionally with decision IDs: `@planned D1 D3: why`.
  `check` ignores it. Remove the tag once the migration adds the column.
  A new value of a built enum works the same way:
  `NEW_VALUE [note: '@planned DR-07']`.
- **A built column the target design drops** gets a note starting with
  `@remove` (same forms: `@remove VH-06: why`); so does a value of a built
  enum (`INACTIVE [note: '@remove VH-05']`). It still exists in the code, so
  `check` compares it as usual; the views mark it "to be removed" and the
  generated history table leaves it out. Delete it from the DBML when the
  refactor drops it from the code. Only allowed on built tables.
- **A built table the target design renames** carries the new name in the
  DBML and `@built-as <name in the code>` on its tag line; `check` matches it
  to the code under the old name, and the views show "to be renamed". Drop
  the tag when the refactor renames the table.
  A renamed built **column** works the same way: its note starts with
  `@built-as <name in the code>: meaning`.
- **A built table the target design drops** gets `@remove <decision IDs>` on
  its tag line: it stays in the DBML (check still matches it to the code),
  is struck through in the overview and badged "to be removed". Delete it
  from the DBML when the refactor drops it from the code.
- **Change history (decision D8, `.claude/rules/database.md`)**: decided
  **per table** in the review (on for tables whose changes must be audited,
  e.g. profile details). When on, the source table's tag line says
  `@tracked *`: a change to **any** column (the primary key aside) writes a
  history row. Add `@untracked col1,col2` only for machine-updated columns
  that would flood the history (heartbeat times, live device status); most
  tables have none. The history table `<singular>_history` is **generated
  by the tool** (never written in the DBML; a hand-written `@history-of`
  table is an error): every source column (same type, nullable, no
  pk/unique, examples copied) plus `history_id`, `changed_at`, `changed_by`
  (→ `users`), `change_reason` (NOT NULL), an FK to the source key and an index on (key, `changed_at`).
  The views mark tracked columns with 🔍. History tables are not reviewed
  one by one; only special cases are discussed.
- **Profile vs state (`.claude/rules/database.md`)**: decisions about a
  thing (incl. a business status, even when a business rule sets it) stay in
  its profile table; observations written often by devices or activity
  (`last_seen_at`, `last_login_at`, device-reported status/firmware) go to a
  1:1 `<singular>_state` table with no history. Derived values (`is_online`)
  are never stored. Built tables get their target split designed now and
  are refactored in bulk after the design review.
  A state table is designed by hand and tagged `@state-of <main table>`
  (same domain).
- **Numbering (generated)**: main and state tables share one sequence,
  1, 2, ..., in design order (domain order, then table order); a state
  table takes the number right after its main table. A generated history
  table is `<N>.h` of its source, listed right after it. Overviews count
  main / state / history tables. Numbers follow the DBML order, so
  inserting a table renumbers the ones after it.
- **Every foreign key is a standalone `Ref`**, FK side first, with its
  delete action: `Ref: a.x_id > b.x_id [delete: restrict]`. Use `-` for
  one-to-one. No composite FKs.
- **Built tables use exact PostgreSQL types and enum names** from the
  models (e.g. `timestamptz`, `float8`, `"geography(POINT,4326)"`, enum
  `chargingconnectorstatus`). Planned tables use plain types and list enum
  values in the column note instead of declaring an `Enum`.
- **Domain notes (`TableGroup` Note)** hold the description plus the
  relationship sentences, one bullet each, stating cardinality and time:
  "a vehicle belongs to one account *at a time*".
- **Open decisions** live in the `Note open_decisions` sticky note as a
  table with IDs `D1`, `D2`... Reference the IDs from tags and notes.
  When one is answered, apply it to the model and remove it from the note.

## Workflows

### Designing the tables for a new feature
1. Read the feature in `docs/product/feature-list.md` and the
   relevant planner in `docs/planners/`.
2. List the nouns and check whether each one already exists as a table.
3. For each new entity, decide the owner (`customer` / `internal` / `two-party`)
   and domain. Respect `.claude/rules/domain-boundaries.md`: a `Ref` from
   domain A to domain B is a dependency A → B. Identity must reference no
   other domain; put the FK on the other side instead.
4. Write the relationship sentences in the domain note first, then the
   tables (`@status planned`) and `Ref`s.
5. Anything the design depends on but nobody has decided goes in
   `open_decisions`. Don't guess business answers.
6. `generate`, read the domain page and `overview.md`, and look for
   unexpected arrows on the L1 map (they usually reveal a wrong-direction FK).

### Implementing a planned table (or a migration that changes the schema)
1. Write the SQLAlchemy model and Alembic migration as usual
   (`.claude/rules/database.md`).
2. In the DBML: flip `@status` to `built`, make types and nullability
   exact, add indexes, and drop `@planned` from columns that now exist.
3. `generate`, then `check` must pass. Commit the `.dbml` and the
   regenerated views in the same change as the migration.

### Presenting to the BOD
Use `overview.md` (L1): the domain map, the "at a glance" counts, the data
ownership table, and the open decisions. It renders in GitHub/VS Code. For
slides, take the Mermaid block and the tables from it. Don't show L3.
To hand the design to the BOD or anyone who reviews in Excel, send
`domain-model.xlsx` (Vietnamese): `Tổng quan` is the L1 index, and each
table sheet is the L3 detail.

## Gotchas
- Links to the tenant table (`organizations` via `organization_id`) are left
  out of the L1 map on purpose and summarized in one sentence; the
  generator's `TENANT_TABLE` constant controls this.
- pydbml supports core DBML only. Keep to tables, enums, refs, indexes,
  table groups, and notes (the syntax already used in the file).
- In a DBML string, escape an apostrophe as `\'`.
  The DBML and generated views in `docs/` are the committed record.
