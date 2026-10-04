<!-- GENERATED from features.yaml by the feature-catalog skill. Edit the source, then regenerate; never edit this file by hand. -->

# Feature catalog

> Every feature of the G3 Network product, for every user (driver app,
> web portal for fleets, administrators and customer care, background
> system), broken down into capabilities and tracked per surface
> (backend, app, portal). Source: [`features.yaml`](features.yaml);
> Vietnamese workbook for business readers: `features.xlsx`. Feature
> codes are stable; the old `F-A1`-style codes are mapped at the end.

## Progress

A feature is done when every surface it needs is done. A cell shows
*done / features that need this surface*.

| Domain | Features | Done | Backend | App | Portal |
|---|---|---|---|---|---|
| [ACC — Accounts & access](domains/identity.md) | 22 | 0 | 0/22 | 0/12 | 0/19 |
| [VEH — Vehicles](domains/vehicles.md) | 5 | 0 | 0/5 | — | 0/5 |
| [BAT — Batteries](domains/batteries.md) | 1 | 0 | 0/1 | — | 0/1 |
| [WAR — Warranties](domains/warranties.md) | 1 | 0 | 0/1 | — | 0/1 |
| [DEV — Telematics devices](domains/telematics.md) | 8 | 0 | 0/8 | — | 0/7 |
| [MON — Vehicle & battery monitoring](domains/telemetry.md) | 19 | 0 | 0/18 | 0/10 | 0/16 |
| [RTE — Range & route planning](domains/routing.md) | 7 | 0 | 0/6 | 0/5 | 0/1 |
| [DRV — Drivers](domains/drivers.md) | 7 | 0 | 0/7 | 0/5 | 0/5 |
| [FLT — Fleet management](domains/fleet.md) | 8 | 0 | 0/8 | — | 0/8 |
| [STN — Charging network](domains/charging_stations.md) | 15 | 0 | 0/15 | 0/6 | 0/12 |
| [CHG — Charging sessions](domains/charging_sessions.md) | 8 | 0 | 0/8 | 0/3 | 0/5 |
| [POL — Charging policy & warranty](domains/policy.md) | 6 | 0 | 0/6 | 0/2 | 0/5 |
| [PAY — Plans, payments & invoices](domains/billing.md) | 13 | 0 | 0/13 | 0/6 | 0/10 |
| [NTF — Notifications](domains/notifications.md) | 8 | 0 | 0/8 | 0/3 | 0/5 |
| [SUP — Support & rescue](domains/support.md) | 7 | 0 | 0/7 | 0/4 | 0/5 |
| [MNT — Maintenance & asset lifecycle](domains/maintenance.md) | 10 | 0 | 0/10 | 0/2 | 0/10 |
| [SAF — Driver safety & camera](domains/safety.md) | 9 | 0 | 0/9 | 0/3 | 0/8 |
| [CRB — Carbon & green transition](domains/carbon.md) | 11 | 0 | 0/11 | 0/2 | 0/7 |
| [PLT — Platform, data & compliance](domains/platform.md) | 7 | 0 | 0/7 | — | 0/4 |
| [TMS — Transport management integration (Phase 2)](domains/tms.md) | 6 | 0 | 0/6 | 0/1 | 0/4 |
| **Total** | **178** | **0** | **0/176** | **0/64** | **0/138** |

## Domains and releases

| Code | Domain | Backend domain | Scope | P1.0 | P1.1 | P1.5 | P2 |
|---|---|---|---|---|---|---|---|
| ACC | [Accounts & access](domains/identity.md) | `identity` | Organizations, people, logins, roles and what each person may see and do; consent and audit of personal data. | 18 | 4 |  |  |
| VEH | [Vehicles](domains/vehicles.md) | `vehicles` | The truck's profile, owner and model, and its activation at handover. | 5 |  |  |  |
| BAT | [Batteries](domains/batteries.md) | `batteries` | Each truck battery managed as an asset of its own: its model, owner and the trucks it has been fitted to. | 1 |  |  |  |
| WAR | [Warranties](domains/warranties.md) | `warranties` | The warranties of each truck, battery and device: their periods and limits, and why a warranty was voided. | 1 |  |  |  |
| DEV | [Telematics devices](domains/telematics.md) | `telematics` | The on-board devices that send vehicle data: registry, mapping to vehicles, health, security and remote configuration. | 7 | 1 |  |  |
| MON | [Vehicle & battery monitoring](domains/telemetry.md) | `telemetry` | Live and historical vehicle data, battery and safety alerts, battery health, trips and operating reports. | 16 | 2 |  | 1 |
| RTE | [Range & route planning](domains/routing.md) | `telemetry` | How far and how long a truck can still go, whether it reaches its destination, where and when to charge, and roads it may not use. | 6 | 1 |  |  |
| DRV | [Drivers](domains/drivers.md) | `drivers` | Driver profiles, vehicle assignment, shifts, driving time and per-driver reports. | 7 |  |  |  |
| FLT | [Fleet management](domains/fleet.md) | `fleet` | Groups of trucks inside an organization, their managers, the live fleet map, geofences and fleet reports. | 8 |  |  |  |
| STN | [Charging network](domains/charging_stations.md) | `charging_stations` | Stations and chargers, their live status over OCPP, faults, the station map, queues, reservations and load. | 13 | 1 | 1 |  |
| CHG | [Charging sessions](domains/charging_sessions.md) | `charging_sessions` | Starting a charge, the session record, receipts, history, energy per customer and reconciliation. | 7 |  | 1 |  |
| POL | [Charging policy & warranty](domains/policy.md) | `policy` | Warranty-linked charging rules, violation detection with evidence, and warranty risk for drivers, fleets and the warranty team. | 6 |  |  |  |
| PAY | [Plans, payments & invoices](domains/billing.md) | `billing` | What customers buy (features, plans, add-ons), how they pay for charging and subscriptions, tariffs and e-invoices. | 11 | 2 |  |  |
| NTF | [Notifications](domains/notifications.md) | `notifications` | Delivering alerts and messages to the right people on the right channels (app, portal, push, SMS, e-mail). | 7 | 1 |  |  |
| SUP | [Support & rescue](domains/support.md) | `support` | Support tickets, SOS, the support queue and SLA, and dispatching repair and rescue partners. | 4 | 3 |  |  |
| MNT | [Maintenance & asset lifecycle](domains/maintenance.md) | `maintenance` | Maintenance reminders and bookings, repair records, maintenance costs, depreciation and component lifetime. | 1 |  | 6 | 3 |
| SAF | [Driver safety & camera](domains/safety.md) | `scoring` | Driver safety scoring, the driver-monitoring camera, in-cab ADAS alerts, evidence clips and the camera's legal obligations. | 9 |  |  |  |
| CRB | [Carbon & green transition](domains/carbon.md) | `carbon` | Emissions avoided by electric trucks, green charging, fleet greenhouse gas inventory and carbon-credit evidence. | 11 |  |  |  |
| PLT | [Platform, data & compliance](domains/platform.md) | — | The data pipeline and warehouse, system health, data retention, data sovereignty and company-wide dashboards. | 6 |  | 1 |  |
| TMS | [Transport management integration (Phase 2)](domains/tms.md) | — | Shipments, shippers and depots, built in Phase 2 by integrating with the customer's transport management system (TMS), never replacing it. |  |  |  | 6 |

## How to read a feature

- **Status** per surface: ⬜ todo, 🚧 doing, ✅ done. A surface the feature does not need is not listed.
- **Surfaces**: Backend (this repository), App (driver mobile / vehicle app), Portal (web portal: fleet, administration, customer-care console).
- **Priority**: Must, Should, Could (MoSCoW).
- **Release**: P1.0 launch, P1.1 about 90 days later, P1.5 2026–27, P2 Phase 2.
- **Offer**: *Internal only*; *Included in every plan*; *To be priced*. Prices come with the pricing work; internal-only features are never put in a plan.
- **Related tables** stay empty until the database review is finished.
- **Priority and release** follow the PRD for features it lists and the phase-1 data sheet for features it does not; where both name a release, the data sheet (the newer document) wins.

### Roles

| Code | Role | Vai trò |
|---|---|---|
| `HEAD_ADMIN` | Head administrator (internal) | Quản trị trưởng (nội bộ) |
| `CO_ADMIN` | Co-administrator (internal) | Đồng quản trị (nội bộ) |
| `ORG_ADMIN` | Organization administrator | Quản trị tổ chức |
| `SALES` | Sales | Kinh doanh |
| `ACCOUNTANT` | Accountant | Kế toán |
| `CUSTOMER_CARE` | Customer care | CSKH |
| `OPERATIONS` | Operations | Vận hành |
| `MAINTENANCE` | Maintenance | Bảo trì |
| `WARRANTY` | Warranty | Bảo hành |
| `FLEET_MANAGER` | Fleet manager | Quản lý đội xe |
| `DISPATCHER` | Dispatcher | Điều vận |
| `DRIVER` | Driver | Tài xế |
| `TECHNICIAN` | Partner technician | Kỹ thuật viên đối tác |
| `SYSTEM` | System (automatic) | Hệ thống (tự động) |
| `EXTERNAL` | External system | Hệ thống bên ngoài |

## Non-functional requirements

| Code | Category | Requirement | Target | Features |
|---|---|---|---|---|
| NF-01 | Performance | Vehicle data latency | ≤30 s p95 (target ≤10 s) while online | [MON-01](domains/telemetry.md#mon-01), [MON-04](domains/telemetry.md#mon-04), [DEV-03](domains/telematics.md#dev-03) |
| NF-02 | Performance | Charger status latency (OCPP) | ≤30 s; status accuracy ≥99% | [STN-03](domains/charging_stations.md#stn-03), [STN-04](domains/charging_stations.md#stn-04) |
| NF-03 | Availability | Platform uptime | ≥99.5% per month; battery and station alerts are the top-priority flow | [MON-04](domains/telemetry.md#mon-04), [PLT-03](domains/platform.md#plt-03) |
| NF-04 | Scale | Concurrent vehicles and devices | From 300 (2026) to 1,200+ (2029) without an architecture change | [MON-01](domains/telemetry.md#mon-01) |
| NF-05 | Security | Encryption and key management | TLS 1.2+ in transit, encrypted at rest, secrets in a vault; cards tokenized | [PAY-06](domains/billing.md#pay-06), [STN-11](domains/charging_stations.md#stn-11) |
| NF-06 | Security | Device identity and authentication | Certificate (mTLS) or unique per-device token, revocable | [DEV-08](domains/telematics.md#dev-08), [DEV-03](domains/telematics.md#dev-03), [VEH-05](domains/vehicles.md#veh-05), [DEV-07](domains/telematics.md#dev-07) |
| NF-07 | Security | Penetration testing | Mandatory before Gate 2; zero critical findings to go live | platform-wide |
| NF-08 | Privacy & compliance | Personal-data protection | Decree 13/2023 and Law 91/2025/QH15: consent at activation, purpose notice, minimal collection, data-subject rights, access audit | [ACC-17](domains/identity.md#acc-17), [ACC-18](domains/identity.md#acc-18), [ACC-19](domains/identity.md#acc-19), [MON-10](domains/telemetry.md#mon-10) |
| NF-09 | Reliability | Buffering on signal loss | ≥48 h store-and-forward on the device; no record lost | [DEV-03](domains/telematics.md#dev-03), [MON-18](domains/telemetry.md#mon-18), [DEV-06](domains/telematics.md#dev-06) |
| NF-10 | Data integrity | Three-way reconciliation | Charger ↔ vehicle ↔ payment kWh deviation <1%; alert on mismatch | [CHG-06](domains/charging_sessions.md#chg-06), [CHG-05](domains/charging_sessions.md#chg-05), [PAY-11](domains/billing.md#pay-11) |
| NF-11 | Data integrity | Immutable records | Session and violation records are append-only | [CHG-02](domains/charging_sessions.md#chg-02), [POL-02](domains/policy.md#pol-02) |
| NF-12 | Usability | Driver app usability | Vietnamese, outdoor-readable, large text, high contrast, one-handed; core actions ≤3 taps | [MON-03](domains/telemetry.md#mon-03), [CHG-01](domains/charging_sessions.md#chg-01) |
| NF-13 | Compatibility | Supported platforms | Android 10+ / iOS 15+; portal on Chrome, Edge, Safari | platform-wide |
| NF-14 | Observability | Monitoring and logging | Central logs, metrics, ops alerting; alert on ingestion stop | [PLT-03](domains/platform.md#plt-03) |
| NF-15 | Backup & recovery | Backup and disaster recovery | RPO ≤15 min, RTO ≤4 h; recovery drill twice a year | platform-wide |
| NF-16 | Data lifecycle | Retention and schema versioning | Hot 12 months / cold 5 years; versioned, backward-compatible schemas | [PLT-01](domains/platform.md#plt-01), [PLT-04](domains/platform.md#plt-04) |
| NF-17 | Localization | Language and formats | Vietnamese by default; VND, km, kWh; ready for Lao and Chinese | platform-wide |
| NF-18 | Maintainability | Modular code, tests, API docs | Clear module boundaries; tests for critical flows (battery alert, charging, reconciliation, payment); OpenAPI | platform-wide |
| NF-19 | Pricing transparency | Displayed price = billed price | 100% match; tariff log per session kept ≥5 years | [PAY-09](domains/billing.md#pay-09), [PAY-10](domains/billing.md#pay-10), [CHG-03](domains/charging_sessions.md#chg-03) |
| NF-20 | Forecast quality | Accuracy and retraining | Range error ≤10% p50 / ≤15% p90; station-load forecast measured weekly; every model retrainable | [RTE-01](domains/routing.md#rte-01), [RTE-05](domains/routing.md#rte-05), [RTE-06](domains/routing.md#rte-06), [STN-08](domains/charging_stations.md#stn-08) |
| NF-21 | Integration | Partner integration | Standard repair/rescue intake API; ≤15-minute acceptance; swapping partners needs no code change | [SUP-04](domains/support.md#sup-04), [SUP-05](domains/support.md#sup-05), [SUP-06](domains/support.md#sup-06) |
| NF-22 | Security & compliance | Data governance | Encryption, role-based access, audit log, retention policy, driver consent at activation | [ACC-14](domains/identity.md#acc-14), [ACC-17](domains/identity.md#acc-17), [ACC-18](domains/identity.md#acc-18), [PLT-04](domains/platform.md#plt-04) |

## Old feature codes

Where each code of the old `feature-list.md` went.

| Old code | Features |
|---|---|
| F-A1 | [MON-01](domains/telemetry.md#mon-01), [MON-02](domains/telemetry.md#mon-02) |
| F-A2 | [MON-04](domains/telemetry.md#mon-04) |
| F-A3 | [MON-07](domains/telemetry.md#mon-07) |
| F-A4 | [MON-05](domains/telemetry.md#mon-05), [MON-06](domains/telemetry.md#mon-06) |
| F-A5 | [FLT-05](domains/fleet.md#flt-05), [MON-02](domains/telemetry.md#mon-02), [MON-10](domains/telemetry.md#mon-10) |
| F-A6 | [MON-14](domains/telemetry.md#mon-14) |
| F-A7 | [RTE-01](domains/routing.md#rte-01) |
| F-A8 | [DRV-07](domains/drivers.md#drv-07) |
| F-A9 | [MON-13](domains/telemetry.md#mon-13) |
| F-B1 | [POL-01](domains/policy.md#pol-01) |
| F-B2 | [CHG-02](domains/charging_sessions.md#chg-02) |
| F-B3 | [POL-02](domains/policy.md#pol-02) |
| F-B4 | [POL-05](domains/policy.md#pol-05) |
| F-B5 | [POL-03](domains/policy.md#pol-03) |
| F-B6 | [POL-06](domains/policy.md#pol-06) |
| F-C1 | [STN-01](domains/charging_stations.md#stn-01) |
| F-C2 | [STN-04](domains/charging_stations.md#stn-04) |
| F-C3 | [STN-07](domains/charging_stations.md#stn-07) |
| F-C4 | [STN-09](domains/charging_stations.md#stn-09) |
| F-C5 | [STN-14](domains/charging_stations.md#stn-14) |
| F-C6 | [CHG-05](domains/charging_sessions.md#chg-05) |
| F-C7 | [STN-08](domains/charging_stations.md#stn-08) |
| F-C8 | [PAY-09](domains/billing.md#pay-09) |
| F-D1 | [STN-06](domains/charging_stations.md#stn-06) |
| F-D2 | [RTE-03](domains/routing.md#rte-03) |
| F-D3 | [RTE-02](domains/routing.md#rte-02) |
| F-D4 | [MON-03](domains/telemetry.md#mon-03) |
| F-D5 | [MON-18](domains/telemetry.md#mon-18) |
| F-D6 | [RTE-05](domains/routing.md#rte-05) |
| F-E1 | [FLT-01](domains/fleet.md#flt-01), [FLT-02](domains/fleet.md#flt-02), [FLT-04](domains/fleet.md#flt-04) |
| F-E2 | [FLT-06](domains/fleet.md#flt-06) |
| F-E3 | [FLT-08](domains/fleet.md#flt-08) |
| F-E4 | [DRV-01](domains/drivers.md#drv-01), [DRV-02](domains/drivers.md#drv-02) |
| F-F1 | [ACC-01](domains/identity.md#acc-01), [ACC-03](domains/identity.md#acc-03), [ACC-13](domains/identity.md#acc-13), [ACC-14](domains/identity.md#acc-14), [ACC-18](domains/identity.md#acc-18) |
| F-F2 | [VEH-01](domains/vehicles.md#veh-01), [VEH-05](domains/vehicles.md#veh-05) |
| F-F3 | [NTF-01](domains/notifications.md#ntf-01), [NTF-02](domains/notifications.md#ntf-02), [NTF-03](domains/notifications.md#ntf-03) |
| F-F4 | [MNT-01](domains/maintenance.md#mnt-01), [PAY-12](domains/billing.md#pay-12) |
| F-G1 | [DEV-01](domains/telematics.md#dev-01), [DEV-02](domains/telematics.md#dev-02), [DEV-03](domains/telematics.md#dev-03) |
| F-G2 | [STN-03](domains/charging_stations.md#stn-03) |
| F-G3 | [PLT-01](domains/platform.md#plt-01), [PLT-02](domains/platform.md#plt-02) |
| F-H1 | [CHG-01](domains/charging_sessions.md#chg-01), [PAY-06](domains/billing.md#pay-06) |
| F-H2 | [CHG-04](domains/charging_sessions.md#chg-04), [PAY-07](domains/billing.md#pay-07), [PAY-08](domains/billing.md#pay-08) |
| F-H3 | [PAY-08](domains/billing.md#pay-08), [PAY-11](domains/billing.md#pay-11) |
| F-H4 | [PAY-03](domains/billing.md#pay-03) |
| F-I1 | [SUP-01](domains/support.md#sup-01) |
| F-I2 | [SUP-02](domains/support.md#sup-02) |
| F-I3 | [MNT-02](domains/maintenance.md#mnt-02) |
| F-I4 | [SUP-05](domains/support.md#sup-05) |
| F-J1 | [DEV-04](domains/telematics.md#dev-04), [DEV-05](domains/telematics.md#dev-05) |
| F-J2 | [DEV-07](domains/telematics.md#dev-07) |
| F-J3 | [DEV-05](domains/telematics.md#dev-05), [DEV-06](domains/telematics.md#dev-06) |
| F-K1 | [SAF-01](domains/safety.md#saf-01) |
