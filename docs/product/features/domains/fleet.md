<!-- GENERATED from features.yaml by the feature-catalog skill. Edit the source, then regenerate; never edit this file by hand. -->

# FLT — Fleet management

*Quản lý đội xe* · [← Feature catalog](../README.md)

Groups of trucks inside an organization, their managers, the live fleet map, geofences and fleet reports. Backend domain: `fleet`.

## Checklist

- [ ] **FLT-01** [Fleets and fleet hierarchy](#flt-01) — Backend ⬜ · Portal ⬜
- [ ] **FLT-02** [Fleet vehicle membership](#flt-02) — Backend ⬜ · Portal ⬜
- [ ] **FLT-03** [Fleet manager scope](#flt-03) — Backend ⬜ · Portal ⬜
- [ ] **FLT-04** [Fleet list and live map](#flt-04) — Backend ⬜ · Portal ⬜
- [ ] **FLT-05** [Geofences and zone alerts](#flt-05) — Backend ⬜ · Portal ⬜
- [ ] **FLT-06** [Fleet KPI dashboard](#flt-06) — Backend ⬜ · Portal ⬜
- [ ] **FLT-07** [Fleet operating cost report](#flt-07) — Backend ⬜ · Portal ⬜
- [ ] **FLT-08** [Charging and warranty report](#flt-08) — Backend ⬜ · Portal ⬜

## Features

<a id="flt-01"></a>

### FLT-01 Fleets and fleet hierarchy

*Đội xe & cấp bậc đội xe* · Must · P1.0 · To be priced

Group an organization's trucks into named fleets that can sit under other fleets (region, branch, depot).

**Value:** Each customer models its own structure as data; reorganizing changes nothing in the system.

**Users:** Fleet manager, Organization administrator

**Capabilities:**

- Create, rename, move and close fleets
- Nested fleets; reports roll up to parents
- Search by name or code

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [ACC-01](identity.md#acc-01)  
**Needed by:** [FLT-02](#flt-02), [FLT-03](#flt-03)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-E1 · Decision FL-02  
**Old codes:** F-E1

<a id="flt-02"></a>

### FLT-02 Fleet vehicle membership

*Thành viên xe của đội* · Must · P1.0 · To be priced

Add trucks to a fleet and remove them, keeping the history; a truck is in at most one fleet at a time.

**Value:** Reports for a past period use the fleet as it was then.

**Users:** Fleet manager

**Capabilities:**

- Add or remove by VIN; membership history
- Which fleet is this truck in?

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [FLT-01](#flt-01), [VEH-01](vehicles.md#veh-01)  
**Needed by:** [FLT-04](#flt-04), [FLT-05](#flt-05)  
**Also touches:** `vehicles`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-E1 · Decision FL-01 · Decision FL-06  
**Old codes:** F-E1

<a id="flt-03"></a>

### FLT-03 Fleet manager scope

*Phạm vi quản lý đội xe* · Must · P1.0 · To be priced

Limit a fleet manager or dispatcher to some fleets (and everything below them); with no limit they see the whole organization.

**Value:** Large customers run regional managers on one account safely.

**Users:** Organization administrator

**Capabilities:**

- Assign fleets to a member's fleet-level roles
- Assignment covers every fleet below

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [FLT-01](#flt-01), [ACC-13](identity.md#acc-13)  
**Needed by:** [ACC-15](identity.md#acc-15)  
**Also touches:** `identity`  
**Related tables:** — (after the database review)  
**Sources:** Decision FL-03

<a id="flt-04"></a>

### FLT-04 Fleet list and live map

*Danh sách & bản đồ toàn đội xe* · Must · P1.0 · To be priced

Every truck of the fleet on a list and a live map with its operating state (driving, parked, charging, off), position, offline trucks and overnight alerts; filter and search.

**Value:** The fleet manager's main screen: the whole operation at a glance.

**Users:** Fleet manager, Dispatcher

**Capabilities:**

- Latest position and state of every member truck
- Offline trucks and overnight alerts highlighted
- Filter by state, status and search by VIN or plate

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [FLT-02](#flt-02), [MON-02](telemetry.md#mon-02), [MON-09](telemetry.md#mon-09)  
**Also touches:** `telemetry`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-E1 · Data OUT-24  
**Old codes:** F-E1

<a id="flt-05"></a>

### FLT-05 Geofences and zone alerts

*Vùng địa lý & cảnh báo ra/vào vùng* · Must · P1.0 · To be priced

Draw areas on the map and get an alert when a truck enters or leaves one.

**Value:** Know when trucks reach depots, customers or forbidden areas.

**Users:** Fleet manager, Dispatcher, System (automatic)

**Capabilities:**

- Create, edit and delete polygon areas
- Entry and exit alerts per reading
- Areas owned by the organization, applicable to chosen fleets

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [FLT-02](#flt-02), [MON-01](telemetry.md#mon-01), [NTF-01](notifications.md#ntf-01)  
**Needed by:** [TMS-02](tms.md#tms-02)  
**Also touches:** `telemetry`, `notifications`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-A5 · Decision FL-05 · deferred.md 86  
**Old codes:** F-A5

<a id="flt-06"></a>

### FLT-06 Fleet KPI dashboard

*Dashboard KPI đội xe* · Must · P1.0 · To be priced

Km, kWh, kWh/km, cost/km, SOH, utilization and alerts, for the fleet and per truck, over a chosen period, with export.

**Value:** Managers see whether the electric fleet performs as planned.

**Users:** Fleet manager, Organization administrator

**Capabilities:**

- Fleet totals and per-truck rows
- Utilization rate from operating state
- Alert counts per period
- Time filter and export

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [MON-14](telemetry.md#mon-14), [MON-07](telemetry.md#mon-07), [MON-09](telemetry.md#mon-09), [NTF-01](notifications.md#ntf-01)  
**Also touches:** `telemetry`, `notifications`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-E2 · Data OUT-25 · deferred.md 72  
**Old codes:** F-E2

<a id="flt-07"></a>

### FLT-07 Fleet operating cost report

*Báo cáo chi phí vận hành đội xe* · Should · P1.0 · To be priced

Electricity cost and cost per km by truck, driver and route.

**Value:** Shows where money goes and which routes or drivers cost more.

**Users:** Fleet manager, Accountant

**Capabilities:**

- Cost per km by truck, driver and route
- Totals rolled up by fleet; CSV export

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [MON-14](telemetry.md#mon-14), [MON-11](telemetry.md#mon-11), [DRV-02](drivers.md#drv-02)  
**Also touches:** `telemetry`, `drivers`  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-36 · PRD F-A6

<a id="flt-08"></a>

### FLT-08 Charging and warranty report

*Báo cáo sạc & bảo hành theo đội/xe* · Must · P1.0 · To be priced

Charging sessions, charging-policy compliance and warranty status per fleet and truck, filterable, with CSV/PDF export.

**Value:** Fleet managers protect their warranty by fixing bad charging habits.

**Users:** Fleet manager, Warranty

**Capabilities:**

- Sessions per truck and fleet
- Compliance score and violations
- CSV/PDF export

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [CHG-07](charging_sessions.md#chg-07), [POL-04](policy.md#pol-04)  
**Also touches:** `charging_sessions`, `policy`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-E3 · Data OUT-29  
**Old codes:** F-E3
