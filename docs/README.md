# Project documents

Every document about the G3 Network product and this repository, organized by
the question it answers. Each fact has **one home**; other documents link to
it instead of repeating it. How-we-work rules for contributors and agents are
not here: they are in [`.claude/rules/`](../.claude/rules/) (see
[CLAUDE.md](../CLAUDE.md)).

| Folder | Answers | Main entry | Kept current? |
|---|---|---|---|
| [`product/`](./product/) | **What** do we build, for whom, and how far along is it? | [Feature catalog](./product/features/README.md) | Yes |
| [`design/`](./design/) | **How** is it built? | [architecture.md](./design/architecture.md), [domain model](./design/domain-model/overview.md) | Yes |
| [`decisions/`](./decisions/) | **Why** is it like this, what is still open, what was left for later? | [decision-log.md](./decisions/decision-log.md) | Yes |
| [`planners/`](./planners/) | How was each piece built and tested, step by step? | the planner of the domain | Active ones yes; `done/` is history |
| [`reports/`](./reports/) | What did an analysis or a progress report say on a given date? | the report | No (dated snapshots) |
| [`archive/`](./archive/) | What did we think before? | — | No (never current) |

## What is in each folder

### `product/` — what we build

| Item | What it is |
|---|---|
| `features/` | The **feature catalog**: every feature for every user, broken into capabilities, with status per surface (backend / app / portal). Source `features.yaml`; `README.md`, `domains/*.md` and the Vietnamese `features.xlsx` are generated (`feature-catalog` skill). The only progress tracker. |
| `feature-list.md` | The old feature list (`F-A1` codes). Still holds the detailed backend status until the backend sync, then moves to `archive/`. |
| `inputs/` | Business files as received, never edited by us (e.g. the phase-1 data inputs/outputs sheet). |

### `design/` — how it is built

| Item | What it is |
|---|---|
| `architecture.md` | What exists today: components and data flow, technology stack, each backend domain, database, local infrastructure, directory layout, what is not built yet. |
| `domain-model/` | The whole database design, built and planned. Source `domain-model.dbml`; `overview.md`, `domains/*.md` and the Vietnamese `domain-model.xlsx` are generated (`domain-model` skill). |
| `specifications/` | External contracts and vendor material: the telematics MQTT contract (`mqtt-spec.md`), the charger specification summary and the vendor documents. |

### `decisions/` — why

| Item | What it is |
|---|---|
| `decision-log.md` | Every project decision (ID, what, why, status, date, source), the open questions, and superseded decisions. |
| `deferred.md` | Work deliberately left for later, with purpose and reason, numbered ("deferred item 27"). |
| `deferred-resolved.md` | Deferred items that were later built or dropped, numbers kept. |

### `planners/` — build logs

One planner per piece of work: goal, decisions, steps with evidence of what
was tested. Active planners sit at the top level, finished ones in `done/`
(still read them before extending that domain). New planners start from
`_TEMPLATE.md`.

### `reports/` — dated snapshots

Analyses that answer one question at one moment (e.g. the charger
specification against the current system) and periodic progress reports.
They are not updated afterwards.

### `archive/` — superseded

Old documents kept for history. Never treat them as current.

## Rules for adding a document

- Find the folder by the question the document answers; if it repeats a fact
  that already has a home, link to the home instead.
- Generated files (`product/features/`, `design/domain-model/` except their
  sources) are never edited by hand: a hook refuses it.
- A decision goes into the decision log in the same change that makes it;
  deferred work goes into `deferred.md`, never into a placeholder in code.
