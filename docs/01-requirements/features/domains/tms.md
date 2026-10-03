<!-- GENERATED from features.yaml by the feature-catalog skill. Edit the source, then regenerate; never edit this file by hand. -->

# TMS — Transport management integration (Phase 2)

*Tích hợp quản lý vận tải (Phase 2)* · [← Feature catalog](../README.md)

Shipments, shippers and depots, built in Phase 2 by integrating with the customer's transport management system (TMS), never replacing it. No backend domain yet.

## Checklist

- [ ] **TMS-01** [Shipments and shippers](#tms-01) — Backend ⬜ · Portal ⬜
- [ ] **TMS-02** [TMS integration and trip milestones](#tms-02) — Backend ⬜
- [ ] **TMS-03** [Depot directory](#tms-03) — Backend ⬜ · Portal ⬜
- [ ] **TMS-04** [Shipper carbon tracing (Scope 3)](#tms-04) — Backend ⬜ · Portal ⬜
- [ ] **TMS-05** [Shipper emission certificates](#tms-05) — Backend ⬜ · Portal ⬜
- [ ] **TMS-06** [Driver operational reports to dispatch](#tms-06) — Backend ⬜ · App ⬜

## Features

<a id="tms-01"></a>

### TMS-01 Shipments and shippers

*Đơn hàng & chủ hàng* · Could · P2 · To be priced

Shippers, shipments and trips, linking trip ↔ truck ↔ driver ↔ shipment ↔ shipper.

**Value:** Knowing which order a truck is serving for which customer.

**Users:** Dispatcher, Fleet manager

**Capabilities:**

- Shipment data received from the customer's TMS
- Link shipments to trips through the reserved keys

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [MON-11](telemetry.md#mon-11), [PLT-01](platform.md#plt-01)  
**Needed by:** [TMS-02](#tms-02), [TMS-04](#tms-04)  
**Related tables:** — (after the database review)  
**Sources:** Phase 2 T1 · Prerequisite 6

<a id="tms-02"></a>

### TMS-02 TMS integration and trip milestones

*Tích hợp TMS & mốc tác nghiệp chuyến* · Could · P2 · To be priced

Two-way API and webhooks with TMS; geofence events for operational milestones (empty container pick-up, factory arrival and departure, port arrival).

**Value:** Shippers get milestones automatically; the G3 app integrates with TMS instead of replacing it.

**Users:** External system, Dispatcher

**Capabilities:**

- Webhooks for position and milestones
- Shippers query GPS through the API

**Status:** Backend ⬜

**Depends on:** [TMS-01](#tms-01), [FLT-05](fleet.md#flt-05), [ACC-21](identity.md#acc-21)  
**Needed by:** [TMS-06](#tms-06)  
**Related tables:** — (after the database review)  
**Sources:** Phase 2 T2 · Prerequisite 6

<a id="tms-03"></a>

### TMS-03 Depot directory

*Danh mục depot* · Could · P2 · To be priced

Shared service points for dispatching: container depots, shipping-line depots and virtual depots with location, services and opening hours.

**Value:** Dispatchers plan container moves around known depots.

**Users:** Dispatcher, Operations

**Capabilities:**

- Depot points with services and hours

**Status:** Backend ⬜ · Portal ⬜

**Related tables:** — (after the database review)  
**Sources:** Phase 2 T3

<a id="tms-04"></a>

### TMS-04 Shipper carbon tracing (Scope 3)

*Truy vết carbon tới chủ hàng (Scope 3)* · Could · P2 · To be priced

Allocate emissions down the chain shipper → shipment → trip → truck → kWh, and report intensity per shipper (gCO₂e/tonne-km) for their Scope 3.

**Value:** Turns the transport company's Scope 1 & 2 into its customers' Scope 3.

**Users:** Fleet manager, External system

**Capabilities:**

- Emission allocation per shipment
- Audit-ready Scope 3 report per shipper

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [TMS-01](#tms-01), [CRB-08](carbon.md#crb-08)  
**Needed by:** [TMS-05](#tms-05)  
**Related tables:** — (after the database review)  
**Sources:** Phase 2 T4 · Phase 2 T6

<a id="tms-05"></a>

### TMS-05 Shipper emission certificates

*Chứng thư giảm phát thải cho chủ hàng* · Could · P2 · To be priced

PDF certificates with a lookup code confirming the CO₂ of completed shipments, delivered to shippers through the API.

**Value:** A selling point for transport companies to their shippers.

**Users:** Fleet manager, External system

**Capabilities:**

- Certificate per period and shipper
- Public lookup by code

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [TMS-04](#tms-04)  
**Related tables:** — (after the database review)  
**Sources:** Phase 2 T5 · Phase 2 T7

<a id="tms-06"></a>

### TMS-06 Driver operational reports to dispatch

*Báo cáo tác nghiệp của tài xế về điều hành* · Could · P2 · To be priced

Drivers send operational reports to their dispatch office so its TMS can optimize dispatching.

**Value:** Better dispatching from first-hand field information.

**Users:** Driver, Dispatcher

**Capabilities:**

- Report forms in the driver app
- Forward to the TMS

**Status:** Backend ⬜ · App ⬜

**Depends on:** [TMS-02](#tms-02)  
**Related tables:** — (after the database review)  
**Sources:** Phase 2 T8
