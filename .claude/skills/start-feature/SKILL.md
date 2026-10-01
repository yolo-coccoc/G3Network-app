---
name: start-feature
description: Start work on a product feature in this repo the agreed way — look up its feature code, gather everything already decided about it, write a planner from the template, design the schema in the DBML, and stop for the owner's confirmation before any code. Use when the user asks to build, implement, start or plan a feature (by code like F-A7 or by name), or to resume one that has a planner.
argument-hint: <feature code(s), e.g. F-A7 F-A8>
---

# Start a feature

Arguments: `$ARGUMENTS` (one or more feature codes, or a feature name to look
up). This skill ends with a **design for the owner to confirm** — write no
application code until the scope decisions are confirmed.

## 1. Gather what is already known (read, don't guess)

1. `docs/01-requirements/feature-list.md` — the feature's entry: actor, output,
   priority/release, **Backend domain**, **Status**, dependencies, "items
   needing confirmation". If the code doesn't exist, stop and ask.
2. `docs/00-status/overview.md` — what the owning domain already does.
3. `docs/02-planners/` and `docs/02-planners/done/` — an existing planner for
   this feature or domain (`grep -ril "<F-XX>" docs/02-planners`). If one is in
   progress, **resume it** instead of creating a new one.
4. `docs/01-requirements/future.md` — items mentioning the feature code or
   domain (deferred pieces that may now be in scope, or blockers).
5. `.claude/rules/open-questions.md` — undecided areas the feature touches.
6. `docs/01-requirements/domain-model/` — planned tables for it (`@status
   planned`, `@features <F-XX>` in the DBML).
7. Specs in `docs/03-specifications/` if the feature integrates a device or
   protocol (for OCPP load the `ocpp16-reference` skill).
8. `.claude/rules/domain-boundaries.md` — which domain owns the data and which
   edges already exist.

## 2. Write the planner

- Copy `docs/02-planners/_TEMPLATE.md` to
  `docs/02-planners/backend-<short-topic>.md`; fill sections 1–3 and the
  steps in 4 from what you gathered. Status `📋 Planned`.
- Section 2 lists every decision the owner must make, each with a
  recommendation and the rejected alternative. Typical ones: which domain
  owns it (new domain → `new-domain` skill), API shape, what is deferred,
  anything that changes existing API behaviour.
- Anything "needed later" → a `future.md` item (full template), referenced
  from section 2.1 — never a TODO in code.
- Respect the rules: no preemptive batching, no new dependency or component
  unless the owner asks, MVP/POC scope.

## 3. Design the data model (if the feature stores anything)

Add or adjust the tables in `domain-model.dbml` as `@status planned`
(meanings, examples, Vietnamese text — the `domain-model` skill describes the
conventions) and regenerate the views; `make domain-model-check` must pass.
Do not touch models or the migration yet.

## 4. Stop and ask

Present a short summary: the goal, the decisions table (with your
recommendations), the schema delta, the new cross-domain edges, and the
deferred items. Ask the owner to confirm or change the decisions (use the
question tool for discrete choices). Record the answers in the planner
(`✅ Confirmed <date>`), set the status to `🚧 In progress`.

## 5. After confirmation (the build loop)

For each step of the planner: implement → tests → `make check` → tick the
step with its evidence. Schema changes go through the `schema-change` agent.
Finish with the `finish-task` skill (gate, integration tests, convention
review, `e2e-sim` where behaviour crosses processes, `docs-sync`, commit).
