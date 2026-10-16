# Planner: Web portal scaffold (ACC-04, ACC-14, VEH-01)

> Feature codes: ACC-04 (Login and sessions), ACC-14 (Feature access control),
> VEH-01 (Vehicle registry) — portal surface; plus the shell every later
> portal feature uses.
> Status: 🚧 In progress — scope confirmed 2026-10-16 (D1–D15); Step 1 done
> Created: 2026-10-16
>
> Inputs: TT-28, CV-24, CV-25, IS-13 and open questions 12–14 in
> [decision-log.md](../decisions/decision-log.md);
> [web-portal-conventions.md](../../.claude/rules/web-portal-conventions.md),
> [web-portal-ui.md](../../.claude/rules/web-portal-ui.md);
> [scenario/web-portal.md](../scenario/web-portal.md) (flows and routes);
> `web_portal_prototype/` (screens, reference only); the identity routes in
> `backend/app/domains/identity/router.py` (`/auth/login`, `/auth/refresh`,
> `/auth/logout`, `GET /auth/me`, `POST /auth/organization`).

## 1. Goal

Start the portal so that every later screen is "add a page from a template",
not "set up the frontend". Done means:

- a pnpm workspace with `web-portal/`, a generated `packages/api-client/`
  and the i18n catalogs, all inside `make check`;
- the shell: login against the real `/auth` routes, organization picker,
  top tab bar driven by feature codes from `/auth/me`, route guards, `<Can>`;
- the shared foundation: theme tokens, status map, DataTable, ReasonDialog,
  PageHeader, the standard states, error mapping, token refresh, formatters;
- the five page templates, each proven by one real page (§3.3);
- the two backend changes the portal needs (OpenAPI export, web cookie mode);
- Playwright screenshots of the five pages.

Not part of it: any other feature's screens, the production build and
serving (reverse proxy), the mobile app, the map provider (open question 13).

## 2. Scope decisions

| # | Decision | Recommended | Alternative (rejected) | Status |
|---|---|---|---|---|
| D1 | Ant Design major version | **v6** (current major; context7 indexes 6.5.0). TT-28 says v5, but v5 is the previous major: starting a new codebase on it means a migration later, and v5 needs a compatibility patch for React 19. Tokens and components are the same idea, so no other decision changes. Confirmed: TT-29 supersedes the v5 part of TT-28. | v5 as written in TT-28 | ✅ Confirmed 2026-10-16 |
| D2 | Versions of everything else | The latest stable release of each library on the day of the scaffold, checked with context7, pinned exactly in `package.json` (no `^`), Node 22 LTS (installed: v22.23.1) in `.nvmrc` and `engines`, pnpm through corepack (`packageManager` field; pnpm is not installed on this machine, corepack 0.34.6 is). | Caret ranges (silent upgrades in agent-written code) | ✅ Confirmed 2026-10-16 |
| D3 | API client generator | **openapi-typescript** (types from the OpenAPI document) + **openapi-fetch** (a small typed `fetch` by path). Calls are written by path (`client.GET("/api/v1/vehicles/{vehicle_id}")`), so FastAPI's long generated operation IDs never show up, and nothing generated contains runtime logic. Query keys and hooks stay hand-written per feature, as the conventions require. | `@hey-api/openapi-ts`: generates an SDK and TanStack Query hooks, which would bypass our `keys/queries/mutations` structure and name functions after the operation IDs | ✅ Confirmed 2026-10-16 |
| D4 | Web cookie contract (backend, IS-13) | (a) The **session's stored platform** decides: a `WEB` session never returns its refresh token in the body (`TokenResponse.refresh_token` becomes nullable; Android/iOS unchanged). (b) Cookie `g3_refresh`: `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth`; `Max-Age` = the session's idle lifetime (`IDENTITY_SESSION_PORTAL_DAYS` 7 / `IDENTITY_SESSION_INTERNAL_DAYS` 1). (c) Set by every endpoint that issues tokens for a `WEB` session: login, sign-up, invitation accept, refresh. (d) `POST /auth/refresh` takes the body token (mobile) or, when the body has none, the cookie. (e) Logout clears it. (f) CSRF: `SameSite=Strict` plus same origin is enough (a cross-site page can neither send the cookie nor read the answer); no CSRF token. (g) `Secure` stays on in development: browsers accept secure cookies on `http://localhost`. (h) Smoke tests for each of these. | Keep the refresh token in `localStorage` (readable by any XSS); a CSRF token (no attack it would stop here) | ✅ Confirmed 2026-10-16 |
| D5 | Refresh across browser tabs | A refresh rotates the token (the old one stops working), so two tabs refreshing at once would log one out. The client serializes refreshes with the **Web Locks API** (`navigator.locks`): the second tab waits, then sends the already-rotated cookie (the cookie jar is shared). | A grace period for the old token in the backend (weakens rotation) | ✅ Confirmed 2026-10-16 |
| D6 | OpenAPI export and freshness gate | `make api-client` = dump `app.openapi()` with `uv run python -c` (no server, no database) to `packages/api-client/openapi.json`, then `pnpm --filter @g3/api-client generate` → `src/schema.d.ts`. **Both files are committed.** `make api-client-check` (in `make check`) regenerates into a temporary folder and fails on any difference, like `domain-model-check`. | Generate at build time only (the gate could not see that a backend change broke the portal) | ✅ Confirmed 2026-10-16 |
| D7 | `packages/i18n/` now or later | **Later.** Its only consumer today is the portal, and if the mobile app goes Flutter (open question 12) it cannot import a TypeScript package anyway. Keep the JSON catalogs in `web-portal/src/shared/i18n/locales/{vi,en}/*.json` and move them to `packages/i18n/` when a second client exists (the move is a folder rename). The conventions file is updated and TT-29 supersedes that part of TT-28. | Create `packages/i18n/` now, as the stack prompt says (an empty-consumer package, against "no file before a task needs it") | ✅ Confirmed 2026-10-16 |
| D8 | Lint, format, type check | ESLint flat config: `typescript-eslint` (strict, type-checked), `eslint-plugin-react-hooks`, `eslint-plugin-boundaries` (the import rules of conventions §5), `eslint-plugin-i18next` (`no-literal-string` in JSX), a `no-restricted-syntax` rule against hex colors and `style` layout outside `shared/ui`. **Prettier** formats. `tsc --noEmit` for types. The agent's format hook (`.claude/hooks/`) also runs Prettier on files it writes under `web-portal/` and `packages/`; `make format` covers them. | Biome (one tool, but no boundaries or i18n plugin) | ✅ Confirmed 2026-10-16 |
| D9 | What `make check` runs for the portal | New `make portal-check`: Prettier check, ESLint, `tsc`, Vitest, `api-client-check`; added to `check`, so the pre-commit hook runs it. `make setup` gains `corepack enable` + `pnpm install`. New targets: `portal-install`, `portal-dev` (Vite on :5173, proxy `/api` → :8000), `portal-e2e` (Playwright). | Portal checks outside the gate (agents would skip them) | ✅ Confirmed 2026-10-16 |
| D10 | Playwright: mocked API, outside the pre-commit gate | Screenshots run against API responses mocked with `page.route` from fixtures typed by `packages/api-client`: deterministic, no backend, no seed data. `make portal-e2e` is run by the finish checklist of every portal task, not by the pre-commit hook (browser start-up would slow every commit). One real-backend smoke (login → picker → vehicle list) is run by hand with `e2e-sim` in Step 8. Playwright's Chromium is **not** found on this machine (`~/.cache/ms-playwright` is missing): `pnpm exec playwright install chromium` downloads it (~150 MB). | Screenshots against the real backend (they change with every data change) | ✅ Confirmed 2026-10-16 |
| D11 | Tabs in the scaffold | `tabs.ts` gets only tabs that have a page (no empty tabs): **Fleet** (vehicle list, live map) and **Administration** (organization settings). The other four tabs (Customer care, Operations, Maintenance, Billing) arrive with their first page. Feature lists: Fleet `VEH-01` (vehicle registry) and `FLT-04` (fleet list and live map, the code `GET /telemetry/fleets/{id}/vehicles/latest` checks). Administration: **the organization settings endpoint has no feature code** — `organization_service.update_organization_settings` checks the role (`ORG_ADMIN`, or internal `HEAD_ADMIN`/`CO_ADMIN`; ID-45), which conventions §7 forbids the portal to do. Recommended: add "organization settings" as a capability of `ACC-01` (Organization registry) in `features.yaml` and let the tab and route check `ACC-01`; the backend check stays as it is. | All six tabs now with "coming soon" pages (placeholders, PR-04) | ✅ Confirmed 2026-10-16 |
| D12 | The five sample pages | **List** `/fleet/vehicles` (`GET /vehicles`, filters `status`, `q`); **Detail** `/vehicles/$vehicleId` (`GET /vehicles/{id}`, tab "Ownership periods" from `/vehicles/{id}/ownership-periods`); **Form** `/fleet/vehicles/new` and `/vehicles/$vehicleId/edit` (`POST` / `PATCH /vehicles`, reason on edit; soft delete with `?reason=` from the detail page through the ReasonDialog); **Live map** `/fleet/live` (fleet picker → `GET /telemetry/fleets/{fleet_id}/vehicles/latest`, polled); **Settings** `/administration/organization` (`GET` / `PATCH /organizations/{id}/settings`). Vehicles cannot provide a live map or a settings page, so those two come from `telemetry`/`fleet` and `identity`. | Fake sample pages on mock data (they would not test the real API shapes) | ✅ Confirmed 2026-10-16 |
| D13 | Map tiles until open question 13 is answered | `VITE_MAP_STYLE_URL` in `web-portal/.env` (template `.env.example`), default the free MapLibre demo style for development only; the real provider replaces it, nothing else changes. | Pick Vietmap or Goong now (open question 13 is about price) | ✅ Confirmed 2026-10-16 |
| D14 | Brand color and font | Brand color: the prototype's green `#0e9f6e` (the only G3 color in the repo, `web_portal_prototype/css/styles.css`) unless the owner gives the official one. Font: Be Vietnam Pro, self-hosted through `@fontsource/be-vietnam-pro` (no request to Google Fonts from an internal tool). | Google Fonts link | ✅ Confirmed 2026-10-16 |
| D15 | Error texts | The backend's error `detail` is English free text with no machine code. The portal shows a Vietnamese message per status (400, 404, 409, 423, 429...) and the backend text as secondary detail. Machine-readable error codes in the backend are recorded in `deferred.md` (needed once users see many business errors), not built now. | Translate backend messages by matching their text (breaks on every wording change) | ✅ Confirmed 2026-10-16 |

### 2.1 Out of scope (deferred)

- Production build and serving of the portal (static files behind the
  reverse proxy on the API's origin, cache headers): added to
  `deferred.md` item 106 (production deployment setup).
- Machine-readable error codes in API error bodies (D15): `deferred.md`
  item 107.
- CI running `make check` and `make portal-e2e`: open question 1 in the
  decision log.
- `packages/i18n/` as a shared package: when a second client exists (D7,
  TT-29); no deferred item, it is a folder move.

## 3. Design

### 3.1 Domain and boundaries

No new backend domain and no new cross-domain edge. Backend changes stay in
`identity` (D4) and the Makefile (D6). Portal features in this task:
`features/identity` (login, me, organization picker, organization settings),
`features/vehicles`, `features/fleet` (fleet picker), `features/telemetry`
(live positions). The live-map route composes `fleet` and `telemetry` in the
route file, matching the backend edge `telemetry → fleet`.

### 3.2 Data model

No schema change. The cookie mode reads the session's existing `platform`
column (`user_sessions`).

### 3.3 API / interfaces

Backend changes (D4):

| Method | Path | Change |
|---|---|---|
| POST | `/api/v1/auth/login`, `/sign-up`, `/invitations/accept` | `WEB` session: `Set-Cookie: g3_refresh=…`, `refresh_token: null` in the body |
| POST | `/api/v1/auth/refresh` | `RefreshRequest.refresh_token` optional; falls back to the `g3_refresh` cookie; a `WEB` session answers as above; no token → `401` |
| POST | `/api/v1/auth/logout` | also clears the cookie |

Portal routes (D11, D12): `/login`, `/select-organization`,
`/fleet/vehicles`, `/fleet/vehicles/new`, `/vehicles/$vehicleId`,
`/vehicles/$vehicleId/edit`, `/fleet/live`, `/administration/organization`,
`/no-access`.

Repository additions:

```text
package.json, pnpm-workspace.yaml, pnpm-lock.yaml, .nvmrc, .prettierrc
packages/api-client/    openapi.json, src/schema.d.ts (generated), src/index.ts (client factory), package.json
web-portal/             package.json, vite.config.ts, tsconfig.json, eslint.config.js, playwright.config.ts,
                        .env.example, index.html, src/{app,routes,features,shared}, e2e/
```

## 4. Steps

- [x] **Step 1 — OpenAPI export and generated client (D3, D6).** Root
  workspace files, `packages/api-client`, `make api-client`,
  `make api-client-check`. Done 2026-10-16. Root `package.json`
  (`packageManager: pnpm@12.11.2`, the `latest` tag; Node `>=22 <23`),
  `pnpm-workspace.yaml` (`packages/*` only; `web-portal` joins in Step 3),
  `.nvmrc`, `node_modules/` in `.gitignore`. The dump is a backend module,
  `app/api/openapi_export.py` (`uv run python -m app.api.openapi_export
  <file>`), rather than inline Python in the Makefile, with one smoke test
  (`tests/test_openapi_export_smoke.py`). `packages/api-client`:
  openapi-fetch 0.17.0, openapi-typescript 7.13.0, and **TypeScript 5.9.3,
  not the `latest` 7.0.2**: openapi-typescript accepts only `^5.x` and
  typescript-eslint only `<6.1.0`, so 5.9.3 is the newest version every tool
  takes (D2 applied per tool). The Makefile calls pnpm as `corepack pnpm`
  (no global `corepack enable`); `make setup` runs `pnpm install
  --frozen-lockfile`; `api-client-check` is in `make check` for now and moves
  under `portal-check` in Step 3. Generated sizes: `openapi.json` 0.9 MB,
  `schema.d.ts` 0.7 MB, both committed. Evidence: `make api-client` →
  `openapi.json → src/schema.d.ts`; `make api-client-check` → "OK: the API
  client matches…" and `tsc --noEmit` clean; after changing one route
  summary in `identity/router.py` it printed "✗ packages/api-client is
  stale…" and exited 1; after the revert, OK again; `make check` passes.
- [ ] **Step 2 — Backend web cookie mode (D4).** `identity` schemas, router,
  session service; smoke tests; scenario doc §2 and IS-13 updated. Own
  commit `feat(identity): …`. Evidence: `make check`, the new smoke tests.
- [ ] **Step 3 — Portal workspace and gate (D1, D2, D8, D9).** Vite app,
  strict TS, ESLint (boundaries, i18n, hooks) + Prettier, Vitest, the make
  targets, `make setup`, the agent format hook. Evidence: `make check`
  passes; a deliberate boundary violation fails lint.
- [ ] **Step 4 — Shared foundation.** Theme tokens (D14), i18n setup with
  vi/en catalogs (D7), API client with token refresh (D5) and error mapping
  (D15), formatters, status map, StatusTag, DataTable, ReasonDialog,
  PageHeader, Empty/Error states, the five templates. Evidence: Vitest for
  formatters, error mapping, refresh single-flight, `useHasFeature`.
- [ ] **Step 5 — Shell and access.** Login (`platform: "WEB"`), organization
  picker (`POST /auth/organization` when `organization_id` is null and there
  are several memberships), session store, `/auth/me`, tab bar from
  `tabs.ts` (D11), `_authed` guard, `beforeLoad` feature guards, `<Can>`,
  logout, language switch. Evidence: Vitest for guards; Playwright flow.
- [ ] **Step 6 — The five sample pages (D12, D13).** Evidence: each page
  works against mocked responses in Playwright.
- [ ] **Step 7 — Screenshots (D10).** `make portal-e2e`: login, picker and
  the five pages in Vietnamese; reference screenshots committed; reviewed by
  hand against web-portal-ui.md §5. Evidence: the run and the files.
- [ ] **Step 8 — Verify and document.** One real-backend run with `e2e-sim`
  (`make backend-dev`, `make portal-dev`, log in as a bootstrap admin, list
  vehicles); `make check`; architecture.md tree and stack row,
  dev-environment.md (portal commands, Node/pnpm), README, the conventions
  files where the build settled a detail, the feature catalog (`portal:
  doing` for ACC-04, ACC-14, VEH-01, and the organization-settings capability
  added to ACC-01 (D11); `feature-catalog` skill), the decision
  log where the build settled a detail.

## 5. Verification

Filled in as steps complete.

## 6. Deferred / follow-ups

`deferred.md` 106 (portal serving added to it), 107 (created): see §2.1.
