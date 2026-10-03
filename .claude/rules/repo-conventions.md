# Repo-wide conventions

> Read this file when committing, opening a PR, deferring/removing a
> component, or considering adding a new dependency/infra piece. Currently
> only `backend/` and `infra/` have active source; the rules below apply to
> any part that has source, including `web-portal/`/`vehicle-app/` once they
> start having code.

- Commit messages follow **Conventional Commits**: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`, `perf:`.
  - Example: `feat(policy): add charging violation reconciliation endpoint (F-B3)`
- **Work directly on `master`** — solo project, no feature branches, no PR-before-merge step. Commit straight to `master` (still following the commit-message convention above; split unrelated changes into separate commits, e.g. a `feat` commit and its companion `docs` commit). This replaces the earlier feature-branch/PR convention — if a branch from that period still exists locally, merge it into `master` and delete it.
- Never commit secrets — all sensitive config goes through `.env` (an `.env.example` template already exists and contains no real values).
- When adding a new feature, cross-check the corresponding feature code in `docs/product/feature-list.md` (e.g. `F-C1`, `F-B2`) to keep code and spec consistent.
- **`__init__.py` files contain NO code**: every `__init__.py` in the repo only contains a module docstring — it must NEVER import or export anything. Files that need to import from each other must import directly from the module (e.g. `from app.domains.telematics.models import TelematicModel` instead of `from app.domains.telematics import TelematicModel`).
- **Do NOT add a new component on your own initiative** (middleware, library, config, infrastructure...) — **ask first**. Only implement what's explicitly requested in a planner or prompt.
- **When skipping/removing a component** for the reason "not needed right now, but definitely needed later" (e.g. middleware between frontend and backend, a caching layer, rate limiting...):
  - **DO NOT** leave a placeholder in the source code.
  - **MUST** record it in `docs/decisions/deferred.md` with a full description of: the component, its purpose, its role in the system, the reason for deferring it, and which planner/feature it relates to.
  - If an old placeholder already exists in the source, remove it entirely and move the information into deferred.md.
- **Where files go**: the layout is in
  [`docs/design/architecture.md`](../../docs/design/architecture.md#directory-structure)
  (code, tooling) and [`docs/README.md`](../../docs/README.md) (documents);
  read them before creating a new file or directory. Don't create a file or
  directory before a concrete task needs it: no empty directories for a
  future domain (`identity`, `policy`, `billing`, `scoring`), no placeholder
  for `web-portal/` or `vehicle-app/`, no `backend/Dockerfile`,
  `infra/docker-compose.prod.yml`, `scripts/` or root `.env.example` until a
  task asks for one.
- **One fact, one home** in the docs: progress per feature lives only in the
  feature catalog, what exists today only in `docs/design/architecture.md`,
  decisions and open questions only in `docs/decisions/decision-log.md`,
  deferred work only in `docs/decisions/deferred.md`. Link to the home
  instead of copying the fact.
