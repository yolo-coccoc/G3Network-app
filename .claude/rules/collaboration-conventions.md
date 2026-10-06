# Collaboration conventions

> Applies to every prompt in this project — not scoped to backend tasks like
> the other files in this directory. Read once, keep applying it for the
> rest of the session.

## English correction for prompts

The user is learning English; their prompts may contain grammar/vocabulary
mistakes or be phrased awkwardly.

For every prompt:
1. Give a short evaluation (a sentence or two).
2. Spot and correct any critical grammar/vocabulary mistakes.
3. Refine the wording — or the whole prompt, if needed — into clear, natural English.
4. Show the corrected version.
5. Only ask a clarifying question if something is still genuinely unclear after the correction; otherwise proceed directly with executing the (corrected) request.

Calibrate the correction level to the user's actual proficiency instead of
over-formalizing: they write fluent, technical, well-structured English
already — typical slips are small: spelling (e.g. "nessesary"), subject-verb
agreement, dropped/wrong articles or plurals, occasional awkward phrasing.
Target regular-communication / medium-writing level — fix real errors and
clunky phrasing, but don't rewrite their voice into something stiffer or
more formal than needed. Keep the evaluation itself short — a couple of
sentences plus the corrected prompt, not a grammar lecture.

## Where rules live

Standing behavioral/collaboration rules and coding conventions belong in
`CLAUDE.md` or a file under `.claude/rules/` — never in auto-memory.
Auto-memory is per-user, unreviewed, and not meant to hold anything the
agent must reliably follow every session; a file here is the durable,
discoverable source of truth. When the user gives an instruction meant to
apply going forward, write or update a rules file (and link it from
`CLAUDE.md` if it's a new topic) instead of saving it as a memory.

## Keep the feature catalog and decision log current

Owner instruction (2026-10-03). New ideas come up while reviewing tables, while
designing and while developing, and they can change the product's scope. So,
in every such task:

- **Check whether the feature catalog must change**
  (`docs/product/features/features.yaml`, `feature-catalog` skill):
  a new or split feature, a capability moved between features, a changed
  dependency, user, priority or release, a new open question, a status that
  is now done, or `related_tables` once the database review fills them.
  Make the change in the same piece of work and regenerate. If nothing needs
  to change, say so in one line; never leave the catalog silently stale.
- **Record a decision** the owner makes in
  `docs/decisions/decision-log.md` (a change of mind is a new entry that
  supersedes the old one).

## Cite the file with every code

Owner instruction (2026-10-04). Whenever a reply mentions a document's code
or ID — a decision (`ID-44`, `VH-05`), a feature (`VEH-05`), an open question
("open question 3", `D3`), a deferred item ("deferred 61"), a planner decision
(`D12`) — also name the file it lives in, as a link (with the line when it
helps), e.g. `VH-05` ([decision-log.md:275](docs/decisions/decision-log.md#L275)).
The reader should never have to search for where a code is defined.

## Show the result after a design change

Owner instruction (2026-10-06). After changing a table's design (the DBML),
show the resulting table before moving on: every column with its type,
whether it is required and its meaning, as a Markdown table (never a code
block), plus the table-level rules (keys, indexes, check constraints, change
history). Show every table the change touched, including new ones.
