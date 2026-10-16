# G3 Network web portal — clickable prototype

A clickable prototype of the web portal described in the
[feature catalog](../docs/product/features/README.md): fleet management, vehicle
and battery monitoring, the charging network, support, billing, sustainability
and administration, for customers (fleet owners) and for our own teams.

It runs on **mock data only**: it is not connected to the backend, and every
number, name, price and model specification in it is invented for
illustration. It does not decide the portal's stack (still open, TT-20 in
[decision-log.md](../docs/decisions/decision-log.md)); it is plain HTML, CSS and
JavaScript with no build step and no dependencies.

## Open it

Open `index.html` in Chrome, Edge, Firefox or Safari (double-click works; no
server needed). Or serve the folder:

```bash
cd web_portal_prototype && python3 -m http.server 8080   # http://localhost:8080
```

On the login screen, pick a **persona** (one per role) or log in with a phone
number from the mock data. The violet button in the top bar switches persona at
any time; **VI/EN** switches language (Vietnamese is the default, NF-17); the
moon switches the dark theme.

Demo links log straight in: `index.html#/map?as=tcxFm&legal=skip`
(`as` = persona key, `lang=en|vi`, `legal=skip` skips the data processing
agreement shown to a customer's organization administrator).

| Persona key | Person | Organization | Role(s) | Shows |
|---|---|---|---|---|
| `head` | Trần Minh Quân | G3 Network (internal) | Head administrator | Every screen, every organization |
| `coadmin` | Lê Thu Hà | G3 Network | Co-administrator | Users, roles, audit |
| `sales` | Phạm Gia Bảo | G3 Network | Sales | Onboarding, plans, ownership transfer |
| `ops` | Đặng Quốc Việt | G3 Network | Operations | Stations, devices, vehicles, system health |
| `care` | Huỳnh Thảo My | G3 Network | Customer care | Support queue, SOS, rescue partners |
| `warranty` | Bùi Đức Thịnh | G3 Network | Warranty | Charging policy, warranty risk |
| `accountant` | Ngô Thị Lan | G3 Network | Accountant | Invoices, revenue, reconciliation |
| `maintenance` | Hồ Văn Lực | G3 Network | Maintenance | Batteries, repairs |
| `tcxAdmin` | Nguyễn Hoàng Phúc | Tân Cảng Xanh (customer) | Organization admin | Members, roles, settings; accepts the DPA |
| `tcxFm` | Lê Minh Khoa | Tân Cảng Xanh + Đồng Nai Xanh | Fleet manager / dispatcher | Organization picker after login |
| `tcxFmLimited` | Phan Thanh Tùng | Tân Cảng Xanh | Fleet manager, dispatcher | Limited to Depot Cái Mép (FLT-03) |
| `tcxDispatch` | Trần Ngọc Mai | Tân Cảng Xanh | Dispatcher | Live map, trips |
| `tcxAcc` | Vũ Thị Hương | Tân Cảng Xanh | Accountant | Charging costs, invoices |
| `mnAdmin` | Đỗ Quang Huy | Miền Nam EV | Organization admin + fleet manager | A small fleet |

## What it shows

**Prototype → Feature coverage** (`#/coverage`) lists every catalog feature
that has a portal surface, the screen(s) that show it, and its backend status.
Each screen also shows its feature codes as chips (green = backend done,
amber = doing, grey = todo; hover for the name); the sidebar checkbox hides
them for a cleaner demo.

Behaviour that follows the backend's rules:

- **Data reach (ID-50):** internal staff see every organization (with an
  organization filter in the top bar); a customer sees only its own data; a
  fleet-limited manager sees only the trucks of their fleets and the fleets
  below (FLT-03).
- **Menus follow roles (ACC-14):** the sidebar shows only the screens of the
  persona's roles.
- **Decided statuses ask for a reason** (`status_reason`, change history), and
  exports of location or personal data ask for a reason that lands in the
  access audit log (ACC-18).
- **Live data:** trucks move and batteries drain or charge every 3 s, and a new
  alert arrives about every 50 s (the portal polls notifications, NTF-01).
- Enum values and field names match the backend (`app/domains/*/types.py`,
  the DBML), e.g. OCPP connector statuses, notification types, bill statuses.

Changes you make (a new fleet, a locked member, a voided warranty…) live in
memory and are gone after a reload.

## Layout

```
web_portal_prototype/
├── index.html            # loads the scripts below, in order
├── css/styles.css        # the only stylesheet (light + dark tokens)
├── js/i18n.js            # t("English", "Tiếng Việt"), enum labels, number/date formats
├── js/features.js        # GENERATED: portal features from features.yaml (feature_snapshot.py)
├── js/data.js            # seeded mock data shaped like the backend (G3.db)
├── js/ui.js              # UI kit: icons, badges, tables, charts, SVG map, modals, toasts
├── js/app.js             # personas and data scope, router, shell, login, live tick
├── js/pages/*.js         # one file per area; each registers its screens
└── feature_snapshot.py   # refreshes js/features.js from the catalog
```

Refresh the feature snapshot after the catalog changes:

```bash
cd backend && uv run python ../web_portal_prototype/feature_snapshot.py
```

## Adding a screen

A screen is registered from a file in `js/pages/` (added to `index.html`):

```js
G3.page({
  id: "vehicles",                       // unique; used by G3.canSee("vehicles")
  path: "/vehicles",                    // hash route; "/vehicles/:id" gives ctx.params.id
  title: ["Vehicles", "Hồ sơ xe"],
  roles: ["OPERATIONS", "FLEET_MANAGER"], // or "*"; the head administrator sees everything
  features: ["VEH-01", "VEH-05"],       // catalog codes shown as chips and on the coverage page
  nav: { group: "assets", order: 1, icon: "truck" }, // omit for detail pages
  navParent: "vehicles",                // (detail pages) highlights that menu entry
  render(ctx) { return "<html string>"; },          // ctx = { params, query, page }
  mount(root, ctx) { /* bind events; G3.onTick(fn) for live updates */ },
});
```

Rules the screens follow:

- Text is written in both languages at the call site with `t(en, vi)`; enum
  values go through `G3.label(value)` / `G3.ui.badge(value)`.
- Data is always read through `G3.scope` (`filter`, `vehicles`, `byVehicle`),
  never straight from `G3.db`, so customers and limited managers see only
  their own rows.
- Filters live in the URL query (`G3.setQuery({ status: "ACTIVE" })`), so a
  filtered view can be linked and survives a re-render.
- Forms use `G3.ui.formModal`, decisions with a reason `G3.ui.reasonModal`,
  exports `G3.ui.downloadCsv` (or `G3.ui.auditedExport` for personal and
  location data). A change updates `G3.db` in memory, then calls `G3.render()`
  and `G3.ui.toast(...)`.

## Not covered

The prototype has no real authentication, API calls, push notifications,
maps (the map is a schematic SVG of the Hồ Chí Minh City – Đồng Nai – Bà Rịa
corridor, not a tile map) or file uploads. The driver mobile app is out of
scope; screens that the catalog gives only to the app are not shown.
