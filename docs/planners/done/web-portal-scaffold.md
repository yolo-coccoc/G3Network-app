# Planner: Web portal scaffold (ACC-04, ACC-14, VEH-01, FLT-04, ACC-23)

> Feature codes: ACC-04 (Login and sessions), ACC-14 (Feature access control),
> VEH-01 (Vehicle registry), and through the sample pages FLT-04 (live map)
> and ACC-23 (Organization settings, ID-54) — portal surface; plus the shell every later
> portal feature uses.
> Status: ✅ Done 2026-10-17 — scope confirmed 2026-10-16 (D1–D15; D11 amended by ID-54); Steps 1–8 done
> Created: 2026-10-16
>
> Inputs: TT-28, CV-24, CV-25, IS-13 and open questions 12–14 in
> [decision-log.md](../../decisions/decision-log.md);
> [web-portal-conventions.md](../../../.claude/rules/web-portal-conventions.md),
> [web-portal-ui.md](../../../.claude/rules/web-portal-ui.md);
> [scenario/web-portal.md](../../scenario/web-portal.md) (flows and routes);
> `web_portal_prototype/` (screens, reference only); the identity routes in
> `backend/app/domains/identity/router.py` (`/auth/login`, `/auth/refresh`,
> `/auth/logout`, `GET /auth/me`, `POST /auth/organization`).

## 1. Goal

Start the portal so that every later screen is "add a page from a template",
not "set up the frontend". Done means:

- a self-contained `web-portal/` (TT-31): the app and its own pnpm
  workspace, with a generated `packages/api-client/`
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
| D11 | Tabs in the scaffold | `tabs.ts` gets only tabs that have a page (no empty tabs): **Fleet** (vehicle list, live map) and **Administration** (organization settings). The other four tabs (Customer care, Operations, Maintenance, Billing) arrive with their first page. Feature lists: Fleet `VEH-01` (vehicle registry) and `FLT-04` (fleet list and live map, the code `GET /telemetry/fleets/{id}/vehicles/latest` checks). Administration: **the organization settings endpoint has no feature code** — `organization_service.update_organization_settings` checks the role (`ORG_ADMIN`, or internal `HEAD_ADMIN`/`CO_ADMIN`; ID-45), which conventions §7 forbids the portal to do. Recommended: add "organization settings" as a capability of `ACC-01` (Organization registry) in `features.yaml` and let the tab and route check `ACC-01`; the backend check stays as it is. | All six tabs now with "coming soon" pages (placeholders, PR-04) | ✅ Confirmed 2026-10-16; **the ACC-01 part superseded the same day by ID-54**: ORG_ADMIN does not hold ACC-01, so the Administration tab checks the new **ACC-23 Organization settings** (ORG_ADMIN, CO_ADMIN, HEAD_ADMIN), and `/auth/me` now lists every feature the routers allow (it held only the `ACC-*` codes, so `VEH-01` and `FLT-04` never reached the portal) |
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

Repository additions, all inside `web-portal/` (TT-31; the repository root
keeps no Node file):

```text
web-portal/
  package.json, pnpm-workspace.yaml, pnpm-lock.yaml, .nvmrc, .prettierignore   (root project = the app; no .prettierrc, Step 3)
  vite.config.ts, tsconfig.json, eslint.config.js, playwright.config.ts, .env.example, index.html
  src/{app,routes,features,shared}, e2e/
  packages/api-client/  openapi.json, src/schema.d.ts (generated), src/index.ts (client factory), package.json
```

## 4. Steps

- [x] **Step 1 — OpenAPI export and generated client (D3, D6).** Root
  workspace files, `packages/api-client`, `make api-client`,
  `make api-client-check`. Done 2026-10-16. Root `package.json`
  (`packageManager: pnpm@12.11.2`, the `latest` tag; Node `>=22 <23`),
  `pnpm-workspace.yaml` (`packages/*`; the app is the root project),
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
  **Moved 2026-10-16 (TT-30):** the owner asked for the frontend in one
  folder, so the workspace files and `packages/` moved from the repository
  root to `frontend/` (`git mv`); the Makefile runs pnpm as `cd frontend &&
  corepack pnpm`. Evidence: `pnpm install --frozen-lockfile` in `frontend/`
  reused the unchanged lockfile; `make api-client` regenerated byte-identical
  files; `make api-client-check` OK. **Moved again the same day (TT-31):**
  `frontend/` renamed `web-portal/`, which is now the app's own workspace
  root (`package.json` name `@g3/web-portal`; the app's `src/` goes there in
  Step 3, not in a nested `web-portal/web-portal/`); `driver-app/` created
  with a README only. Evidence: `pnpm install --frozen-lockfile` reused the
  lockfile; `make api-client-check` OK.
- [x] **Step 2 — Backend web cookie mode (D4).** `identity` schemas, router,
  session service; smoke tests; scenario docs and IS-13 updated. Own
  commit `feat(identity): …`. Done 2026-10-16. The service decides from the
  session's stored platform and returns `IssuedSessionTokens` (the body, plus
  the cookie value and lifetime for a `WEB` session; the cookie's `Max-Age`
  is the time left until the session expires); only the router touches
  cookies (`REFRESH_COOKIE_NAME` `g3_refresh`, `REFRESH_COOKIE_PATH`
  `/api/v1/auth`). `refresh_session` now takes the token itself (body first,
  else cookie; none → `401`), and the refresh body is optional.
  `TokenResponse.refresh_token` is nullable. Not done: clearing the cookie
  when a refresh fails (the error handler builds that response; the portal
  logs out on that `401` anyway). Also changed: the phone simulator's
  simulated bank logs in as `WEB` and required a body refresh token; it now
  accepts none and logs in again on a `401`. Evidence: new
  `tests/identity/test_identity_web_cookie_smoke.py` (6 tests: WEB body
  without token + cookie value and lifetime, ANDROID unchanged, no token →
  `401`, the endpoint reads the cookie and sets an `HttpOnly; Secure;
  SameSite=strict; Path=/api/v1/auth` one, a body token wins over a cookie,
  logout expires the cookie); `make check` (1156 passed); `make
  backend-test-integration` 60 passed (the identity flow now also asserts a
  WEB invitation gets no body token); an HTTP run through FastAPI's test
  client with the service stubbed (scratchpad script, not committed): WEB
  login → `Set-Cookie: g3_refresh=…; HttpOnly; Max-Age=600;
  Path=/api/v1/auth; SameSite=strict; Secure` and `refresh_token: null`;
  a body-less refresh sent the cookie back and got a new one; ANDROID login
  → token in the body, no cookie. `make api-client` regenerated the client
  (`refresh_token?: string | null`).
- [x] **Step 3 — Portal workspace and gate (D1, D2, D8, D9).** Vite app,
  strict TS, ESLint (boundaries, i18n, hooks) + Prettier, Vitest, the make
  targets, `make setup`, the agent format hook. Evidence: `make check`
  passes; a deliberate boundary violation fails lint. Done 2026-10-16.
  Versions (D2, exact pins): react / react-dom 19.3.1, vite 8.3.4,
  @vitejs/plugin-react 6.1.2, vitest 5.0.5, eslint 10.12.0, @eslint/js
  10.0.1, typescript-eslint 8.71.2, eslint-plugin-boundaries 7.2.0,
  eslint-plugin-i18next 6.1.5, eslint-plugin-react-hooks 7.1.1,
  eslint-plugin-react-refresh 0.5.7, prettier 3.9.10, globals 17.13.0,
  @types/node 22.20.5 (the Node 22 line, not the latest 26.x). **eslint is
  10.12.0, not the `latest` 10.13.0**: 10.13.0 was published the same day,
  inside pnpm 12's minimum release age, and pnpm offered to write an
  exclusion into `pnpm-workspace.yaml`; we keep that supply-chain guard on
  and take the newest release it accepts. TypeScript stays 5.9.3 (Step 1).
  Settled while building: TypeScript as in Vite's template, a solution
  `tsconfig.json` referencing `tsconfig.app.json` (`src/`, `vite/client`
  types) and `tsconfig.node.json` (`vite.config.ts`), checked with `tsc -b`;
  Vitest is configured inside `vite.config.ts` (`src/**/*.test.{ts,tsx}`,
  Node environment) and runs with `--passWithNoTests` until Step 4 writes
  the first tests; jsdom and Testing Library are added in Step 4, when a
  hook test needs them. No `.prettierrc`: Prettier's defaults, with
  `.prettierignore` keeping the generated client and the lockfile out.
  `eslint.config.js`: `strictTypeChecked`, react-hooks and react-refresh;
  `boundaries/dependencies` with `default: "disallow"` (elements `app`,
  `routes`, `feature` = `src/features/*`, `shared` = `src/shared/*`;
  `src/main.tsx` is ignored, it only mounts `app/App`;
  `boundaries/no-unknown-files` refuses any other file in `src/`); a
  feature imports another only by a per-edge policy added when the backend
  has that edge; `@g3/api-client` (values) and `openapi-fetch` only in
  `src/shared/api`; hex/rgb/hsl literals, `style` props and CSS imports
  only in `src/shared/ui`; `i18next/no-literal-string` in `jsx-only` mode,
  checking JSX text and the attributes that show text (`title`, `label`,
  `placeholder`, `okText`...), not props such as `type="primary"`. Make:
  `portal-install`, `portal-dev`, `portal-check` (api-client-check, Prettier
  check, ESLint, `tsc -b`, Vitest) in `check`; `make format` also runs
  Prettier; `make setup` already installs the workspace (Step 1).
  `src/app/App.tsx` renders nothing until Steps 4–5. Agent hook:
  `.claude/hooks/format_portal.py` runs the portal's Prettier on every file
  written under `web-portal/` (skips generated files through
  `.prettierignore`). Evidence: `make check` passes (1156 backend tests,
  portal: Prettier, ESLint, `tsc -b`, Vitest, client freshness); `vite build`
  builds; `make portal-dev` serves `index.html` and `/src/main.tsx` and
  proxies `/api/v1/auth/me` to :8000 (502 with the API stopped). Probe files
  (removed afterwards) were refused: feature → feature, shared → feature,
  routes → a feature's internal file (routes → the feature's `index.ts`
  passed), a stray `src/stray.ts`, `createApiClient` from a feature, a hex
  color, a `style` prop, Vietnamese text in JSX and in `title=`; a stray
  file makes `make portal-check` exit 2. The hook reformatted a messy probe
  file and left `schema.d.ts` untouched.
- [x] **Step 4 — Shared foundation.** Theme tokens (D14), i18n setup with
  vi/en catalogs (D7), API client with token refresh (D5) and error mapping
  (D15), formatters, status map, StatusTag, DataTable, ReasonDialog,
  PageHeader, Empty/Error states, the five templates. Evidence: Vitest for
  formatters, error mapping, refresh single-flight, `useHasFeature`. Done
  2026-10-16, commit `375a143` (Steps 4–7 together: the router's types need
  the routes). Libraries (D2, exact, each at least two days old so pnpm's
  minimum release age needs no exclusion): antd 6.6.6, @ant-design/icons
  6.3.4, @tanstack/react-query 5.104.2, @tanstack/react-router 1.170.44 and
  router-plugin 1.168.46, zustand 5.0.15, i18next 26.4.5, react-i18next
  17.0.16, date-fns 4.4.0, date-fns-tz 3.2.0, @fontsource/be-vietnam-pro
  5.3.0, maplibre-gl 6.14.0, react-map-gl 8.1.3; tests: jsdom 30.1.2,
  @testing-library/react 16.3.3, @playwright/test 1.64.1. pnpm reports
  "agent skills awaiting approval" in @tanstack/router-plugin; not approved
  (nobody asked for agent instructions from a package). Settled while
  building: the i18n catalogs type every `t()` key (`CustomTypeOptions`), so
  a missing key fails `tsc`; status labels live once in `common`
  (`status.<VALUE>`), because `StatusTag` (in `shared`) labels every
  domain's values; static help text of a form field goes in `Form.Item`'s
  `extra`, since `help` hides validation errors; `FilterField` (in
  `shared/ui`) gives filter controls their width, because screens may not set
  sizes; `QueryState` renders a query's skeleton/error/content; `useReportApiError`
  puts 422 errors on the form and toasts the rest; Ant Design 6.6 deprecates
  `List`, so lists of a few items are stacked `Card`s; page and page size are
  optional in the URL (`resolvePage` applies 1 and 20), so a link to a list
  needs no search params; the refresh is a raw `fetch` (not the typed
  client) so its 401 never triggers the refresh-and-retry middleware.
- [x] **Step 5 — Shell and access.** Login (`platform: "WEB"`), organization
  picker (`POST /auth/organization` when `organization_id` is null and there
  are several memberships), session store, `/auth/me`, tab bar from
  `tabs.ts` (D11), `_authed` guard, `beforeLoad` feature guards, `<Can>`,
  logout, language switch. Evidence: Vitest for guards; Playwright flow.
  Done 2026-10-16. **Backend gap found and fixed first (ID-54, commit
  `12956f6`)**: `/auth/me` listed only the `ACC-*` codes, so `VEH-01` and
  `FLT-04` never reached the portal; it now lists what the routers allow, and
  the Administration tab checks the new ACC-23 (ORG_ADMIN does not hold
  ACC-01, the D11 choice). Settled: the shell (`app/AppShell.tsx`) wraps the
  router's output through TanStack Router's `InnerWrap` and shows itself
  only inside `/_authed`, so route files never import `app/`; the home-page
  rule (`resolveHomePath`, from the tab registry in `app/tabs.ts`) reaches
  routes through the router context; a tab with several pages opens a
  submenu; a route without its feature redirects to `/no-access` (not to the
  tab's first page: one rule, and the shell keeps the allowed tabs
  reachable); `requireSession` returns the caller with `organization_id` as
  a string; the login page's `?redirect=` accepts portal paths only (no
  open redirect); a refresh refused during use clears the session and
  returns to `/login?redirect=`; switching organization drops the query
  cache. `<Can>` and `requireFeature` take one code or several (any one is
  enough), because an endpoint can serve several features
  (`VEHICLE_READERS`). ESLint: route files may throw the router's
  `Redirect`; `react-refresh/only-export-components` is off for route files
  (the router plugin splits the page component into its own module); every
  feature may import `features/identity` (backend: every domain may call
  `identity`).
- [x] **Step 6 — The five sample pages (D12, D13).** Evidence: each page
  works against mocked responses in Playwright. Done 2026-10-16. List
  `/fleet/vehicles` (search, status filter, paging in the URL); Detail
  `/vehicles/$vehicleId` (summary, ownership-history tab, Edit, Remove with
  the reason dialog); Form `/fleet/vehicles/new` and
  `/vehicles/$vehicleId/edit` (internal staff also pick the owner); Live map
  `/fleet/live` (fleet and sub-fleets in the URL, statistics, MapLibre map
  synced with the side list, polled every 15 s); Settings
  `/administration/organization` (one card per setting, each saved with a
  reason). Settled: **the vehicle edit opens no reason dialog**: its PATCH
  has no `reason` field; a status change requires its `status_reason`, which
  the backend stores as the history reason, and a plain edit gets the
  backend's fixed reason (ID-50 (5)). MapLibre 6 looks for its worker next
  to its own module, which Vite's pre-bundling moves: `MapView` hands it the
  worker Vite bundles (`?worker&url`, `setWorkerUrl`). `react-map-gl` creates
  the map asynchronously, so the first fit to the points happens in
  `onLoad`; later fits only when the set of trucks changes (a poll must not
  reset the user's zoom). `optimizeDeps.entries` scans every source file at
  start-up so a lazily loaded route never forces a reload ("Outdated
  Optimize Dep"). `VITE_MAP_STYLE_URL` in `web-portal/.env.example` (D13).
- [x] **Step 7 — Screenshots (D10).** `make portal-e2e`: login, picker and
  the five pages in Vietnamese; reference screenshots committed; reviewed by
  hand against web-portal-ui.md §5. Evidence: the run and the files. Done
  2026-10-16. 14 Playwright tests (`e2e/*.spec.ts`) against `e2e/mockApi.ts`
  (fixtures typed by `@g3/api-client`, fixed clock, a blank map style),
  plus a check that fails a test on any console error or warning (Ant
  Design reports deprecated props there; two browser notices are ignored:
  "Failed to load resource" for the statuses the tests return on purpose,
  and the software GL renderer's "GPU stall"). Seven screenshots in
  `e2e/__screenshots__/`: login, organization picker, vehicle list, detail,
  create form, live map, organization settings (1440 × 900, `vi-VN`,
  Vietnam time). Reviewed by hand; fixed after review: the map did not fit
  the trucks, the header actions were not centered, the plate link was
  indented, the date picker's Vietnamese placeholder said "Chọn thời điểm"
  (now "Chọn ngày"), and required-field messages were hidden (the `help`
  issue above). **Playwright 1.64 publishes no Chromium for Ubuntu 20.04**
  (this machine): `make portal-e2e-install` downloads the Ubuntu 22.04
  build there (the same Chrome for Testing binary; it runs on glibc 2.31).
  New targets: `portal-e2e`, `portal-e2e-update`, `portal-e2e-install`.
- [x] **Step 8 — Verify and document.** One real-backend run with `e2e-sim`
  (`make backend-dev`, `make portal-dev`, log in as a bootstrap admin, list
  vehicles); `make check`; architecture.md tree and stack row,
  dev-environment.md (portal commands, Node/pnpm), README, the conventions
  files where the build settled a detail, the feature catalog (`portal:
  doing` for ACC-04, ACC-14, VEH-01, and the organization-settings capability
  added to ACC-01 (D11); `feature-catalog` skill), the decision
  log where the build settled a detail. Done 2026-10-17. The local database
  was already on the baseline and empty, so it was not reset; a bootstrap
  HEAD_ADMIN was created with values passed in the shell (not written to
  `backend/.env`), and a model, two trucks and a fleet were provisioned
  through the API. A Playwright script (not committed) drove the real API
  through `make portal-dev`'s proxy: login → vehicle list with the two
  trucks → detail with the current ownership period → reload restores the
  session from the cookie (`g3_refresh`: `HttpOnly`, `Secure`,
  `SameSite=Strict`, `Path=/api/v1/auth`) → live map of the fleet (the truck
  offline, "no data yet") → settings change 120 → 95 with a reason (one
  `organization_setting_history` row with the old value, the reason and the
  actor) → logout (cookie gone; `/fleet/vehicles` sends to login again); no
  console problem. `/auth/me` of the admin lists `VEH-01`, `FLT-04` and
  `ACC-23` (ID-54 holds on the real backend). It found one defect, fixed in
  `fix(web-portal)`: the plate in the list was an anchor without `href`
  (no link role, no keyboard focus, no new tab). The map with the demo
  style was checked by eye (Vietnam centered). The catalog marks the
  organization-settings capability as ACC-23 (ID-54), not ACC-01.

## 5. Verification

- `make check` passes: backend gate (1157 smoke tests), domain-model and
  feature-catalog checks, `make portal-check` (Prettier, ESLint with the
  boundary, i18n and color rules, `tsc -b`, 63 Vitest tests, API client
  freshness).
- `make portal-e2e`: 14 passed, console clean; the seven screenshots
  reviewed by hand.
- Real backend (Step 8): login, session restore, list, detail, live map,
  settings with reason, logout, as described above.
- Deliberate violations refused by lint (Step 3); a stray file fails
  `make portal-check`.

## 6. Deferred / follow-ups

`deferred.md` 106 (portal serving added to it), 107 (created): see §2.1.
Added at the end: 108 (Vietnamese month and day names in the date picker:
needs dayjs's `vi` locale, and `dayjs` as a direct dependency), 109 (the live
map shows the first 100 trucks of a scope; paging or clustering once a fleet
is larger). The production bundle has one chunk over 500 kB (MapLibre, loaded
only by the live map route); part of the serving work of item 106.
