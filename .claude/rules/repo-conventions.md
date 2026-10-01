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
- When adding a new feature, cross-check the corresponding feature code in `docs/01-requirements/feature-list.md` (e.g. `F-C1`, `F-B2`) to keep code and spec consistent.
- **`__init__.py` files contain NO code**: every `__init__.py` in the repo only contains a module docstring — it must NEVER import or export anything. Files that need to import from each other must import directly from the module (e.g. `from app.domains.telematics.models import TelematicModel` instead of `from app.domains.telematics import TelematicModel`).
- **Do NOT add a new component on your own initiative** (middleware, library, config, infrastructure...) — **ask first**. Only implement what's explicitly requested in a planner or prompt.
- **When skipping/removing a component** for the reason "not needed right now, but definitely needed later" (e.g. middleware between frontend and backend, a caching layer, rate limiting...):
  - **DO NOT** leave a placeholder in the source code.
  - **MUST** record it in `docs/01-requirements/future.md` with a full description of: the component, its purpose, its role in the system, the reason for deferring it, and which planner/feature it relates to.
  - If an old placeholder already exists in the source, remove it entirely and move the information into future.md.
