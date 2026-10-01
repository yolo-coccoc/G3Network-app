# Unassigned domain implementation ranking

> Ranking of the business domains named in `feature-list.md` that have **no
> active source** in `backend/app/domains/` yet, ordered from easiest to
> most complicated to implement. See
> [domain-boundaries.md](../../.claude/rules/domain-boundaries.md) for the
> rules a new domain must follow, and
> [directory-structure.md](../../.claude/rules/directory-structure.md)'s
> "Out of current scope" list for the current up-to-date set of domains
> still without source. Created: 2026-09-18.

1. **`drivers`** (F-E4) — ✅ Done (see
   `docs/02-planners/done/backend-crud-drivers.md`). Plain CRUD plus one
   assignment relationship to `vehicles`, no new infrastructure needed.
2. **`support`** (F-I1, F-I2) — ✅ Done at MVP/POC scope (see
   `docs/02-planners/done/backend-support-cases.md`). Ticket/SOS case CRUD with
   a stored SLA deadline; F-I3 (booking) and F-I4 (partner directory +
   dispatch routing) deferred — see `future.md` items 68-69.
3. **`fleet`** (F-E1) — ✅ Done at MVP/POC scope (see
   `docs/02-planners/done/backend-crud-fleet.md`). Fleet CRUD plus a
   vehicle-membership history table, mirroring `drivers`'
   assignment-history pattern; F-E2 (KPI dashboard) deferred — see
   `future.md` item 72. F-E3/F-A8 remain hard-blocked on `charging_sessions`
   having no vehicle/driver linkage (`future.md` item 62).
4. **`identity`** (F-F1, auth & RBAC) — technically not hard on its own,
   but it's the foundational domain: every other domain is allowed to
   depend on it once it exists, so it carries the highest blast radius if
   designed wrong early.
5. **`scoring`** — needs a defined scoring/rules engine and historical
   data aggregation across other domains.
6. **`policy`** — charging/business policy logic with real-world edge
   cases (pricing, authorization rules); higher domain-modeling
   complexity than a CRUD domain.
7. **`billing`** — hardest. Payment/invoicing correctness, money
   handling, reconciliation; highest stakes for bugs, and will likely
   need an external payment-gateway integration eventually.

This ranking reflects implementation complexity only, not product
priority/release order — cross-check `feature-list.md`'s **Priority ·
Release** field for each domain's actual features before picking the next
one to build.
