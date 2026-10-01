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
