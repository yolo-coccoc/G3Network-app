# Web portal conventions

> Read this file before any task under `web-portal/`. Screen
> design rules live in [web-portal-ui.md](./web-portal-ui.md); both apply
> together. Why each choice was made: TT-28, TT-29, CV-24, CV-25, IS-13 in the
> [decision log](../../docs/decisions/decision-log.md). The scaffold is done
> (shell, shared foundation, one sample page per template); how it was built:
> [`docs/planners/done/web-portal-scaffold.md`](../../docs/planners/done/web-portal-scaffold.md).
> A task that settles a detail left open here updates this file in the same
> change.

## 1. What the portal is

One web portal for every staff and customer role (`HEAD_ADMIN`, `CO_ADMIN`,
`ORG_ADMIN`, `SALES`, `ACCOUNTANT`, `CUSTOMER_CARE`, `OPERATIONS`,
`MAINTENANCE`, `WARRANTY`, `FLEET_MANAGER`, `DISPATCHER`, `TECHNICIAN`); what
a person sees is decided by the features of their roles, never by a separate
app per role. It is a static single-page app behind a login, served from the
same origin as the API. It holds **no business rule**: every rule (who may
do what, data reach, state transitions, prices) is the backend's; the portal
shows what the API returns and hides what the user cannot use.

The step-by-step flows and the API call behind each screen are in
[`docs/scenario/web-portal.md`](../../docs/scenario/web-portal.md);
`web_portal_prototype/` shows the screens on mock data (reference only, its
code is never copied).

## 2. Stack

| Concern | Choice |
|---|---|
| Language, UI, build | TypeScript (`strict`), React, Vite; pnpm (through corepack, version pinned in `web-portal/package.json`) |
| UI library | Ant Design v6 (TT-29), themed only through design tokens (`shared/ui/theme.ts`); ProComponents only where they clearly save work; no Tailwind, no shadcn/ui |
| Server data | TanStack Query |
| Routing | TanStack Router, file-based, automatic code splitting |
| Client state | Zustand: session, current organization, UI preferences; nothing else |
| Tables, forms | Ant Design Table (server-side pagination and filters), Ant Design Form; Zod only where a form needs cross-field rules |
| API client | generated from the backend's OpenAPI document into `packages/api-client/` |
| Maps | MapLibre GL through react-map-gl; style URL from `VITE_MAP_STYLE_URL` (provider: open question 13) |
| Charts | Apache ECharts |
| i18n, time, money | react-i18next (Vietnamese default, English second); date-fns-tz; whole VND |
| Tests | Vitest (logic and hooks), Playwright with Chromium (pages and screenshots) |

A library outside this table is a new component: ask first (PR-03).

**Versions.** Every dependency is pinned exactly in `web-portal/package.json`
(no `^`, TT-29). pnpm's minimum release age stays on: a release too new for it is not
installed by adding a `minimumReleaseAgeExclude` to `pnpm-workspace.yaml`;
take the newest release pnpm accepts (the scaffold kept a margin of two days).
TypeScript stays on 5.9.x until
typescript-eslint and openapi-typescript accept 6 or later.

## 3. Workspace layout

The portal is **one self-contained folder, `web-portal/`**, next to
`backend/` (TT-31): the app and its own pnpm workspace in one, with every
dependency, lockfile and package it needs inside it; nothing portal-related
sits at the repository root, and it imports nothing from another top-level
folder (`driver-app/`, `web_portal_prototype/`). Every path in the rest of
this file is relative to `web-portal/`. Run pnpm from inside `web-portal/`
(corepack reads the pinned version from its `package.json`); the Make
targets do this for you.

```text
web-portal/
  package.json, pnpm-workspace.yaml, pnpm-lock.yaml, .nvmrc   workspace root = the app (@g3/web-portal)
  vite.config.ts (Vite + Vitest), index.html, src/            the portal
  tsconfig.json            references tsconfig.app.json (src/), tsconfig.node.json, tsconfig.e2e.json
  eslint.config.js         lint, import boundaries (§5), i18n and color rules
  playwright.config.ts, e2e/   Playwright specs, mockApi.ts, typed fixtures.ts, test.ts
                               (fails on console problems), __screenshots__/
  .prettierignore          generated files Prettier leaves alone (Prettier's defaults, no config file)
  .env.example             VITE_MAP_STYLE_URL; copy to .env (untracked)
  packages/api-client/     generated types + the typed fetch client; never edited by hand
```

`packages/` holds code the app uses but that is not the app (today only the
generated client). The driver app (`driver-app/`, open question 12) gets its
own client and catalogs; nothing is shared across the two folders. The
message catalogs live in `src/shared/i18n/locales/` (TT-29).

## 4. `src/` structure

```text
src/
  main.tsx    mounts app/App; nothing else
  app/        the shell: App (providers: Query, Router, theme, i18n), router.ts,
              AppShell.tsx (top tab bar, organization switcher, language, account
              menu; the notification bell joins it with the inbox),
              tabs.ts (tab registry), routeTree.gen.ts (generated, committed, never edited)
  routes/     organized BY TAB (TanStack Router file routes):
                login.tsx, select-organization.tsx
                _authed.tsx                 guard: a session is required
                _authed/<tab>/...           one folder per tab: fleet, customer-care,
                                            operations, maintenance, billing, administration
                _authed/vehicles/$vehicleId.tsx   ONE shared detail page per entity
  features/   organized BY BACKEND DOMAIN, folder named exactly like
              backend/app/domains/<domain>/ (vehicles, charging_stations, identity...):
                <domain>/api/keys.ts        query-key factory
                <domain>/api/queries.ts     useXxxQuery hooks
                <domain>/api/mutations.ts   useXxxMutation hooks (invalidate their keys)
                <domain>/components/        the domain's tables, forms, cards, panels
                <domain>/permissions.ts     the feature codes this domain's UI checks
                <domain>/search.ts          small helpers beside them (search-param parsing...)
                <domain>/index.ts           the ONLY public surface (re-exports)
  shared/     ui/ (theme, status map, DataTable, ReasonDialog, PageHeader,
                   page templates, empty/loading/error states)
              auth/ (session store, useHasFeature, <Can>, route guards)
              api/ (client instance, token refresh, error mapping)
              lib/ (formatVnd, formatVietnamTime, ...)
              i18n/ (react-i18next setup; locales/{vi,en}/<namespace>.json)
```

- **A tab** is a group of pages for one kind of work, not a role. A list page
  lives under the tab it serves (`_authed/fleet/vehicles.tsx`,
  `_authed/operations/vehicles.tsx` may both exist, built from the same
  `features/vehicles` components with different columns or filters); the
  detail page of an entity exists once, outside the tabs.
- **`tabs.ts`** lists each tab (`id`, label key) and its pages (path, label
  key, the feature codes that open it, the path prefixes that mark it
  active); a tab with several pages opens a submenu. A tab is added when its
  first page is built, never as an empty shell.
- **The shell** (`app/AppShell.tsx`) wraps the router's output through
  TanStack Router's `InnerWrap` and shows itself only inside `/_authed`, so
  route files never import `app/`. What a route needs from `app/` (the query
  client, the home-page rule `resolveHomePath`) comes through the router
  context.
- **Route files stay thin**: read search params, call feature hooks, compose
  feature components into a page template. A page that spans domains (a
  vehicle with its battery and its device health) is composed here. A route
  file exports `Route` and keeps its page component, unexported, beside it.
- **`index.ts` exception to the "no code in `__init__.py`" rule**: the Python
  rule (repo-conventions.md) does not apply here; a feature's `index.ts`
  re-exports its public components, hooks and types, and holds no logic.

## 5. Import rules

Direction: `routes → features → shared → api-client` (and `app` may use all
of them). Enforced by `eslint-plugin-boundaries` in the lint step
(`eslint.config.js`); a violation fails `make check`, and so does a file
under `src/` outside these layers (only `src/main.tsx`, the entry, is
exempt).

- `shared` never imports `features` or `routes`.
- A feature imports another feature only through its `index.ts`, and only
  when the backend has the same edge (domain-boundaries.md): if `vehicles`
  may not call `telemetry` in the backend, `features/vehicles` does not
  import `features/telemetry` either — compose them in the route file.
  Each allowed edge is one policy in `eslint.config.js`, added in the same
  change as the first import that needs it. Every feature may import
  `features/identity`, as every backend domain may call `identity`.
- Nothing outside `shared/api` creates a fetch client or reads tokens.
- `packages/api-client` is imported for types and the client only; generated
  files are never edited.

## 6. Naming

Names mirror the backend so one search finds both sides.

| Thing | Rule | Example |
|---|---|---|
| Feature folder | the backend domain name, snake_case | `features/charging_stations/` |
| Component file and name | PascalCase, `<BusinessObject><Role>` | `VehicleTable.tsx`, `VehicleForm.tsx`, `VehicleStatusTag.tsx` |
| Page component (in a route file) | `<BusinessObject><Template>Page` | `VehicleListPage`, `VehicleDetailPage` |
| Query hook | `use<Object>Query` / `use<Object>ListQuery` | `useVehicleQuery`, `useVehicleListQuery` |
| Mutation hook | `use<Verb><Object>Mutation`, verbs as in the backend service (create, update, softDelete, transfer...) | `useSoftDeleteVehicleMutation` |
| Query-key factory | `<object>Keys` | `vehicleKeys.list(params)`, `vehicleKeys.detail(vehicleId)` |
| Other files | camelCase | `formatVnd.ts`, `tabs.ts` |
| API fields | kept exactly as the API sends them (snake_case); never renamed to camelCase | `vehicle.license_plate` |
| IDs, times, units | as in backend-coding-conventions.md §8.4 | `vehicleId`, `createdAt` for local variables; `_id`, `_at`, `_kwh` in API data |
| Booleans | `is`/`has`/`can` prefix | `isLoading`, `canEdit` |

Comments and identifiers are in English; text the user sees is never written
in code, only as i18n keys (§10).

## 7. Access model

- After login and after every organization switch, the portal reads
  `GET /api/v1/auth/me` (`MeResponse`: `features`, `roles`, `is_internal`,
  `organization_id`, `memberships`). **Access is checked by feature code
  only, never by role name** (ID-44, ID-50): `useHasFeature("VEH-01")`.
  `features` lists every feature the backend's routers let the caller use
  (ID-54).
- Three levels, all reading the same feature list:
  1. **Tab**: shown when the user has any of the features of its pages.
  2. **Route**: `beforeLoad` calls the guards of `shared/auth/guards.ts`:
     `requireSession` (in `_authed.tsx`: no session → `/login?redirect=`,
     no organization picked → `/select-organization`) and `requireFeature`,
     which redirects to `/no-access` (inside the shell, so the allowed tabs
     stay reachable) when the feature is missing.
  3. **Action**: `<Can feature="VEH-02">` around a button or menu item; the
     element is hidden, not disabled.
- `<Can>` and `requireFeature` take one code or several (any one is
  enough), because one endpoint can serve several features.
- A person with several roles sees the union of their features; internal
  users (`is_internal`) see every organization (data reach is the
  organization's, not the role's) and get an organization filter on lists.
- The backend stays the real enforcement: a `403` from the API shows the
  standard "no permission" state, a `404` the "not found" state (an
  out-of-reach record is a `404`, ID-50).
- Each domain's `permissions.ts` names the feature codes its UI checks; a
  code must exist in `docs/product/features/features.yaml`.

## 8. API, auth and errors

- **One client** (`shared/api/client.ts`), typed by `packages/api-client`.
  Base path `/api/v1`, same origin: in development Vite proxies `/api` to
  `localhost:8000`; there is no CORS (IS-13).
- **Tokens (IS-13)**: the access token lives in memory only (never
  `localStorage`/`sessionStorage`); the refresh token is an `HttpOnly`
  cookie the portal never sees. On a `401` the client refreshes once and
  retries the request once; concurrent refreshes, also across tabs, are
  serialized (one refresh in flight). The refresh call is a raw `fetch`, not
  the typed client, so its own `401` never triggers refresh-and-retry. A
  refused refresh clears the session and returns to `/login?redirect=`; the
  login page follows `redirect` to portal paths only (no open redirect).
  Switching organization drops the whole query cache. Login sends
  `platform: "WEB"`. The cookie is `Secure`:
  in development use Chrome or Firefox, which accept it on
  `http://localhost`; Safari does not.
- **Regenerating the client**: after any backend route or schema change, run
  `make api-client` (exports the OpenAPI document and regenerates
  `packages/api-client`) and commit both files; `make api-client-check`, in
  the gate, fails when the committed client is stale.
- **Errors**: domain errors arrive as `{"detail": "<English text>"}` with the
  status of their base class (backend-coding-conventions.md §6); FastAPI
  validation errors (`422`) as a `detail` list with field locations.
  `shared/api/errors.ts` maps them: `400` / `409` → a localized message per
  status with the backend text as secondary detail; `401` → refresh/login;
  `403` → no-permission state; `404` → not-found state; `422` → errors on the
  matching Form fields; `423` (login lockout) and `429` → their own messages;
  `5xx`/network → the standard error state with retry.
- **Reasons**: an update of a tracked table sends `reason` in the body, a
  soft delete `?reason=`; both are collected by the ReasonDialog
  (web-portal-ui.md). The request schema decides: an endpoint with no
  `reason` field gets no dialog (the vehicle `PATCH`: a status change
  carries its `status_reason`, a plain edit gets the backend's fixed reason,
  ID-50).

## 9. Server data and polling

- Every API read is a TanStack Query hook in `features/<domain>/api/`;
  components never call the client directly. Every mutation invalidates the
  keys it affects (at least its feature's list and the touched detail).
- **Lists**: the backend pages with `page` / `page_size` and returns
  `items` / `total` (`API_MAX_PAGE_SIZE` = 100). Page, page size, sort and
  filters live in the route's **search params** (validated by the route), so
  a filtered list is a shareable link; `shared/ui/DataTable` maps them to
  Ant Design Table. Page and page size are optional in the URL:
  `resolvePage` (`shared/lib/listSearch.ts`) applies the defaults (1, 20),
  so a link to a list needs no search params.
- **Polling** (the backend has no push channel to the browser) uses
  `refetchInterval` on the screens that need it only: the live map, the
  notification badge and inbox (`after_id` cursor with `order=asc`, NT-16),
  and a queued charger command until it ends. Intervals are named constants
  in `shared/api/polling.ts`; polling stops while the browser tab is hidden.
- Zustand holds only the session (user, access token, features), the current
  organization and UI preferences (language, table density); never a copy of
  server data.

## 10. i18n, time, money

- Every user-visible string is an i18n key; Vietnamese is the default and
  must always be complete, English second. Catalogs live in
  `src/shared/i18n/locales/{vi,en}/` (JSON, one namespace per feature plus
  `common`). A lint rule refuses string literals in JSX. The Vietnamese
  catalogs type every `t()` key, so a missing key fails `tsc`; a Vitest test
  checks that English has the same keys.
- Backend enum values (statuses, types) are shown through their i18n label,
  never raw. Status labels live once in the `common` namespace
  (`status.<VALUE>`, e.g. `status.ACTIVE`), not per feature, because
  `StatusTag` labels every domain's values.
- The API sends and receives UTC; the portal shows and accepts
  `Asia/Ho_Chi_Minh` (`APP_REPORT_TIMEZONE`) through `shared/lib`
  formatters only. A date range picked in the portal is converted to UTC
  instants before it is sent.
- Money is whole VND (`formatVnd`: `1.250.000 ₫`); energy and units are
  formatted as the API names them (`_kwh`, `_km`).

## 11. Tests and the gate

- **Vitest**: shared logic (formatters, error mapping, token refresh,
  `useHasFeature`, guards) and any non-trivial hook or component logic. Same
  spirit as the backend's lean tests (TT-06): behaviours, not files.
- **Playwright** (Chromium): the main flow of each page and one screenshot
  per page, compared with its template (web-portal-ui.md §6). API responses
  are mocked from fixtures typed by `packages/api-client`, so screenshots are
  deterministic and need no backend.
- The portal's checks (format, lint incl. boundaries, type check, Vitest,
  client freshness) are `make portal-check`, part of `make check`, so the
  pre-commit hook runs them.
- Playwright has its own target, outside the pre-commit hook:
  `make portal-e2e`. `make portal-e2e-update` rewrites the reference
  screenshots after an intended UI change; look at every changed image
  before committing it. `make portal-e2e-install` downloads Chromium once
  per machine (on Ubuntu 20.04 it fetches the Ubuntu 22.04 build: Playwright
  publishes none for 20.04).
- A Playwright test fails on any console error or warning (`e2e/test.ts`);
  Ant Design reports deprecated props there, so they never pile up.

## 12. Workflow and finishing a portal task

- A portal task gets a planner from `docs/planners/_TEMPLATE.md`, named
  `docs/planners/web-portal-<topic>.md`; the owner confirms its scope
  decisions before code, as for the backend.
- A backend change the portal needs (a missing endpoint, a field) is a
  separate backend task with its own commit; the portal never works around a
  missing API with client-side business logic.
- Before committing:
  1. `make check` passes; `make portal-e2e` passes and the screenshots of
     the new or changed pages were looked at.
  2. The client was regenerated if the backend API changed.
  3. Imports follow §5; no string literal in JSX; no hand-picked color.
  4. The feature catalog's `portal` status of each feature touched is updated
     (`feature-catalog` skill); decisions go to the decision log.
  5. Commit messages use the scope `web-portal` (`feat(web-portal): ...`).
