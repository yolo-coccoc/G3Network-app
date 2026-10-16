# Web portal UI rules

> Read this file before building or changing a portal screen. There is no
> designer: these rules replace one (CV-25 in the
> [decision log](../../docs/decisions/decision-log.md)). Code structure,
> imports and API patterns are in
> [web-portal-conventions.md](./web-portal-conventions.md).

## 1. Components and styling

- **Ant Design components only.** Before writing a custom component, look for
  an Ant Design one (or a wrapper in `shared/ui`); a custom component is
  composed from Ant Design parts.
- **No hand-picked colors, sizes or fonts.** Colors, spacing, radius and font
  come from theme tokens (`shared/ui/theme.ts`: one G3 brand color, compact
  density, Be Vietnam Pro) read through `theme.useToken()` or component
  props. No hex values, no `px` margins, no CSS files or `style` props for
  layout outside `shared/ui`; layout uses `Flex`, `Space`, `Row`/`Col`,
  `Card`, `Descriptions`.
- Icons only from `@ant-design/icons`.
- Light theme only until a dark theme is asked for.
- Ant Design 6.6 deprecates `List`: show a few items as stacked `Card`s; its
  replacement `Listy` is a virtual list, for long lists only.
- Static help text of a form field goes in `Form.Item`'s `extra`, never
  `help` (`help` replaces the field's validation errors).
- Filter controls sit in `FilterField` (`shared/ui`), which sets their
  width; a screen never sizes them itself.

## 2. Status colors

One map, `shared/ui/status.ts`, gives every backend status value one of Ant
Design's status presets; every screen shows a status through
`<StatusTag value=... />`, never by choosing a color.

| Preset | Meaning | Examples |
|---|---|---|
| `success` | working, done, available | `ACTIVE`, `HEALTHY`, `Available`, `ACCEPTED`, `COMPLETED`, `SUCCEEDED`, `BILLED` |
| `processing` | in progress, waiting for the system | `PENDING`, `Charging`, `IN_PROGRESS` |
| `warning` | needs attention soon | `SILENT`, `ATTENTION`, `ON_HOLD`, `SuspendedEV`, `SuspendedEVSE` |
| `error` | broken, refused, blocked | `Faulted`, `FAILED`, `REJECTED`, `ERROR`, `TIMEOUT`, `LOCKED`, `SUSPENDED` |
| `default` | ended, out of use or no data | `INACTIVE`, `CLOSED`, `CANCELLED`, `VOIDED`, `ABANDONED`, `NOT_SENT`, `NOT_MOUNTED`, `NO_DATA`, `Unavailable` |

A derived flag (`is_online`, `is_silent`) is shown through the same map (online = `success`, offline = `default`). A status not in the map is added there (with its i18n label) before a screen
shows it. The same value means the same color everywhere.

## 3. Standard states

Every data area handles all four states with the shared components:

- **Loading**: `Skeleton` inside the page template's content area (a table
  shows its own loading row); never a blank page or a full-page spinner.
  `QueryState` (`shared/ui`) is the standard wrapper of one query: skeleton,
  error state with retry, then the content.
- **Empty**: `<EmptyState>` with a one-line explanation and, when the user
  may act, the primary action ("Add a vehicle").
- **Error**: `<ErrorState>` with the mapped message and a retry button;
  `403` → "no permission", `404` → "not found" (web-portal-conventions.md §8).
- **Success feedback**: `message.success` after a mutation; a form stays on
  screen with field errors on `422`.

## 4. Reasons and confirmations

- Every **destructive or audited action** opens the shared `ReasonDialog`:
  soft delete (`?reason=`), a decided status change (`status_reason`), an
  update of a tracked table (`reason`), an export of personal or location
  data (ACC-18), a charger command (CS-29), a lock, a void, a handover.
- The dialog states what will happen, asks for a reason (required, at most
  200 characters, as the backend's `reason` type), and uses a danger button
  for destructive actions. No `window.confirm`, no action without the dialog.

## 5. Page templates

Every screen inside the shell starts from one of five templates in
`src/shared/ui/templates/`; a screen that fits none is discussed with the
owner before a sixth is added. A page outside the shell (login, organization
picker) uses `CenteredCardLayout` (`shared/ui`), which is not a template.

| Template | For | Layout |
|---|---|---|
| **List** | browsing and filtering records | `PageHeader` (title, primary action) → filter bar (search, selects, date range; state in search params) → `DataTable` (server pagination; identifier column first, status as `StatusTag`, row actions last) |
| **Detail** | one record | `PageHeader` (title = the record's identifier, status tag, actions) → summary `Descriptions` → `Tabs` for related data (history, periods, related lists) |
| **Create / edit form** | creating or changing one record | `PageHeader` → one `Card` with a vertical Ant Design `Form` (labels above, required marks, help text from i18n) → Save / Cancel at the bottom; edit asks for a reason when the table is tracked |
| **Live map / dashboard** | watching many things now | `PageHeader` with scope picker (fleet, organization) → statistic row (`Statistic` cards) → map or charts → side list synced with the map; polls (conventions §9) and shows the last refresh time |
| **Settings** | configuring an organization or a feature | `PageHeader` → grouped `Card`s, each one form section with its own Save; changes need a reason when the table is tracked |

Common to all: the page title is also the browser title; breadcrumbs only
on detail and form pages; content width follows the template, not the page.

## 6. Screenshot review

- Every new or changed page gets a Playwright screenshot (Chromium, fixed
  viewport, mocked API, Vietnamese) committed with the page.
- Before finishing, compare it with its template's reference screenshot,
  the sample page of that template in `web-portal/e2e/__screenshots__/`
  (List: vehicle list, Detail: vehicle detail, Form: vehicle create, Live
  map: live map, Settings: organization settings): same header, spacing,
  table and state components, status colors from the map. Differences are
  fixed or explained in the planner.
- Look at the Vietnamese screenshot specifically for clipped diacritics and
  long labels that wrap badly.

## 7. Text

- Vietnamese first: write the Vietnamese label as a person in that job would
  say it, not as a word-for-word translation of the English key.
- Labels are short nouns ("Biển số", "Trạng thái"); buttons are verbs
  ("Thêm xe", "Lưu"); messages say what happened and what to do next.
- Dates and times as `dd/MM/yyyy HH:mm` in Vietnam time; money as
  `1.250.000 ₫`; empty values as `—`.
