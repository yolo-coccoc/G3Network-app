<!-- GENERATED from features.yaml by the feature-catalog skill. Edit the source, then regenerate; never edit this file by hand. -->

# MON — Vehicle & battery monitoring

*Giám sát xe & pin* · [← Feature catalog](../README.md)

Live and historical vehicle data, battery and safety alerts, battery health, trips and operating reports. Backend domain: `telemetry`.

## Checklist

- [ ] **MON-01** [Vehicle data ingestion](#mon-01) — Backend ⬜
- [ ] **MON-02** [Live vehicle status](#mon-02) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **MON-03** [Driver home screen](#mon-03) — Backend ⬜ · App ⬜
- [ ] **MON-04** [Tiered battery alerts](#mon-04) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **MON-05** [Battery safety alerts](#mon-05) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **MON-06** [Powertrain and component fault alerts](#mon-06) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **MON-07** [Battery health and cycles](#mon-07) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **MON-08** [Cell-level battery diagnostics](#mon-08) — Backend ⬜ · Portal ⬜
- [ ] **MON-09** [Vehicle operating state](#mon-09) — Backend ⬜ · Portal ⬜
- [ ] **MON-10** [Location history and route replay](#mon-10) — Backend ⬜ · Portal ⬜
- [ ] **MON-11** [Trips](#mon-11) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **MON-12** [Stops and parking log](#mon-12) — Backend ⬜ · Portal ⬜
- [ ] **MON-13** [Load status and empty-trip detection](#mon-13) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **MON-14** [Vehicle operating report](#mon-14) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **MON-15** [Abnormal consumption alert](#mon-15) — Backend ⬜ · Portal ⬜
- [ ] **MON-16** [Auxiliary load usage](#mon-16) — Backend ⬜ · Portal ⬜
- [ ] **MON-17** [Alert threshold settings](#mon-17) — Backend ⬜ · Portal ⬜
- [ ] **MON-18** [Driver app offline mode](#mon-18) — App ⬜
- [ ] **MON-19** [Consumption root-cause analysis (AI)](#mon-19) — Backend ⬜ · Portal ⬜

## Features

<a id="mon-01"></a>

### MON-01 Vehicle data ingestion

*Thu nhận dữ liệu xe* · Must · P1.0 · Included in every plan

Receive and store every truck's data stream (battery, motor, speed, odometer, GPS, fault codes) within 30 seconds.

**Value:** The foundation of every monitoring, alert and report feature.

**Users:** System (automatic)

**Capabilities:**

- Validate and store each message with its schema version
- Skip data from unknown or unassigned devices
- Battery safety signals: cell voltage, imbalance, insulation, contactor, over/under voltage and temperature
- Instantaneous consumption rate
- ≤30 s p95 while online; scale from 300 to 1,200+ vehicles

**Status:** Backend ⬜

**Depends on:** [DEV-03](telematics.md#dev-03)  
**Needed by:** [CHG-06](charging_sessions.md#chg-06), [CRB-03](carbon.md#crb-03), [DEV-04](telematics.md#dev-04), [FLT-05](fleet.md#flt-05), [MNT-01](maintenance.md#mnt-01), [MON-02](#mon-02), [MON-04](#mon-04), [MON-05](#mon-05), [MON-06](#mon-06), [MON-07](#mon-07), [MON-09](#mon-09), [MON-10](#mon-10), [MON-14](#mon-14), [MON-16](#mon-16), [PLT-01](platform.md#plt-01), [POL-02](policy.md#pol-02), [RTE-01](routing.md#rte-01), [SAF-01](safety.md#saf-01), [SAF-08](safety.md#saf-08)  
**Also touches:** `telematics`, `vehicles`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-A1 · Data IN-1 · Data IN-2 · Data IN-6 · Data IN-7 · Data IN-12 · Data IN-13 · Data IN-14 · Data IN-17 · NF-01 · NF-04  
**Old codes:** F-A1

<a id="mon-02"></a>

### MON-02 Live vehicle status

*Trạng thái xe realtime* · Must · P1.0 · To be priced

Show a truck's latest battery level, position, speed and whether it is online right now.

**Value:** Drivers and managers always know where a truck is and how much energy it has.

**Users:** Driver, Fleet manager, Dispatcher, Customer care

**Capabilities:**

- Latest reading per truck with the time it was received
- Online flag computed when read

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [MON-01](#mon-01)  
**Needed by:** [FLT-04](fleet.md#flt-04), [MON-03](#mon-03), [STN-07](charging_stations.md#stn-07)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-A1 · PRD F-A5  
**Old codes:** F-A1, F-A5

<a id="mon-03"></a>

### MON-03 Driver home screen

*Màn hình chính của tài xế* · Must · P1.0 · Included in every plan

Three big numbers: battery %, kilometres left and the nearest available station, plus the estimated driving time left.

**Value:** Answers the driver's main question at a glance: how far can I still go?

**Users:** Driver

**Capabilities:**

- Battery %, range and nearest available station
- Estimated driving time left
- Large text, high contrast, one-handed use; core actions in ≤3 taps

**Status:** Backend ⬜ · App ⬜

**Depends on:** [MON-02](#mon-02), [RTE-01](routing.md#rte-01), [STN-06](charging_stations.md#stn-06)  
**Needed by:** [MON-18](#mon-18)  
**Also touches:** `charging_stations`  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-1 · PRD F-D4 · NF-12  
**Old codes:** F-D4

**Open questions:**

- Home-screen content and thresholds to be confirmed by interviewing 8-12 drivers (prerequisite 5).

<a id="mon-04"></a>

### MON-04 Tiered battery alerts

*Cảnh báo pin phân cấp* · Must · P1.0 · Included in every plan

Alert the driver at 30% (early), 20% (main) and 10% (critical) battery, with the distance to the nearest available station and a navigation button; fleet managers also get the 20% and 10% alerts.

**Value:** Prevents trucks running out of energy on the road.

**Users:** Driver, Fleet manager, System (automatic)

**Capabilities:**

- Detect each threshold crossing within 30 s
- Nearest available station and distance, frozen at alert time
- One alert per threshold per trip; works while the app is in the background

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [MON-01](#mon-01), [STN-06](charging_stations.md#stn-06), [NTF-01](notifications.md#ntf-01)  
**Also touches:** `notifications`, `charging_stations`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-A2 · Data OUT-2 · NF-01 · NF-03 · deferred.md 38  
**Old codes:** F-A2

**Open questions:**

- Thresholds 30/20/10% to be confirmed by driver interviews.

<a id="mon-05"></a>

### MON-05 Battery safety alerts

*Cảnh báo an toàn pin* · Must · P1.0 · Included in every plan

Real-time alerts for high battery temperature, sudden voltage drop, cell/module faults, cell imbalance, insulation and cooling problems, with a data snapshot.

**Value:** Fire-safety: problems are caught before they become dangerous.

**Users:** Driver, Fleet manager, Operations, System (automatic)

**Capabilities:**

- High temperature and sudden voltage drop detectors
- Cell/module, imbalance, insulation, contactor and cooling alerts
- Event log with the data snapshot at detection
- Re-alert or escalate while a condition persists

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [MON-01](#mon-01), [MON-17](#mon-17), [NTF-01](notifications.md#ntf-01)  
**Also touches:** `notifications`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-A4 · Data IN-7 · Data IN-8 · Data OUT-4 · deferred.md 43 · deferred.md 44  
**Old codes:** F-A4

<a id="mon-06"></a>

### MON-06 Powertrain and component fault alerts

*Cảnh báo lỗi hệ truyền động & thiết bị ngoài pin* · Must · P1.0 · To be priced

Alert on motor, auxiliary battery, controller board, brake, tyre (TPMS), light, door and air-conditioning faults, with severity and whether the truck can keep driving; a fleet board lists trucks that need a workshop.

**Value:** Drivers know what to do; managers plan workshop visits before breakdowns.

**Users:** Driver, Fleet manager, Maintenance, System (automatic)

**Capabilities:**

- Translate fault codes through the catalog: severity, drivability, advice
- Component status signals beyond the battery
- Fleet board of component faults and trucks needing a workshop

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [MON-01](#mon-01), [VEH-06](vehicles.md#veh-06), [NTF-01](notifications.md#ntf-01)  
**Also touches:** `vehicles`, `notifications`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-A4 · Data IN-9 · Data IN-10 · Data IN-16 · Data OUT-4 · Data OUT-27 · deferred.md 42 · deferred.md 45  
**Old codes:** F-A4

<a id="mon-07"></a>

### MON-07 Battery health and cycles

*Sức khỏe pin & số chu kỳ* · Should · P1.0 · To be priced

Track pack state of health (SOH), charge cycles and capacity fade over time, and alert when SOH drops below a threshold.

**Value:** Protects the most expensive part of the truck and supports warranty decisions.

**Users:** Fleet manager, Warranty, Maintenance, System (automatic)

**Capabilities:**

- Daily SOH, cycle count and estimated usable capacity
- SOH alert below a configurable threshold
- SOH and cycle definitions agreed with the manufacturer

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [MON-01](#mon-01)  
**Needed by:** [FLT-06](fleet.md#flt-06), [MON-08](#mon-08)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-A3 · Data IN-4 · Data IN-5 · Prerequisite 1  
**Old codes:** F-A3

<a id="mon-08"></a>

### MON-08 Cell-level battery diagnostics

*Chẩn đoán pin cấp cell* · Must · P1.0 · To be priced

Show SOH and voltage per cell, weak cells and imbalance, and recommend replacing single cells instead of the whole pack, with a cost estimate.

**Value:** Large savings on battery repairs.

**Users:** Fleet manager, Maintenance, Warranty

**Capabilities:**

- Per-cell SOH and voltage
- Weak-cell and imbalance detection
- Single-cell replacement recommendation with cost estimate

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [MON-07](#mon-07), [VEH-04](vehicles.md#veh-04)  
**Also touches:** `vehicles`  
**Related tables:** — (after the database review)  
**Sources:** Data IN-4 · Data OUT-26 · Prerequisite 1

**Open questions:**

- Cell-level SOH needs the manufacturer's cell formula and cell data on the bus.

<a id="mon-09"></a>

### MON-09 Vehicle operating state

*Trạng thái vận hành xe* · Must · P1.0 · To be priced

Know whether each truck is off, driving, parked or charging, plus gear, brake and accelerator position.

**Value:** Fleet maps, utilization and alerts depend on knowing what the truck is doing.

**Users:** Fleet manager, Dispatcher, System (automatic)

**Capabilities:**

- Operating state from ignition, speed and charging signals
- State changes kept as events

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [MON-01](#mon-01)  
**Needed by:** [DEV-06](telematics.md#dev-06), [FLT-04](fleet.md#flt-04), [FLT-06](fleet.md#flt-06), [MON-11](#mon-11), [MON-12](#mon-12)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-15 · Data OUT-24

<a id="mon-10"></a>

### MON-10 Location history and route replay

*Lịch sử vị trí & xem lại hành trình* · Must · P1.0 · To be priced

Replay where a truck went over a chosen period on a map; history kept at least 6 months.

**Value:** Resolves disputes, supports investigations and repossession.

**Users:** Fleet manager, Dispatcher, Customer care

**Capabilities:**

- Points of a period in order, for drawing the route
- Access is logged (location is personal data)

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [MON-01](#mon-01), [ACC-18](identity.md#acc-18)  
**Needed by:** [SAF-06](safety.md#saf-06)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-A5 · NF-08  
**Old codes:** F-A5

<a id="mon-11"></a>

### MON-11 Trips

*Chuyến đi* · Must · P1.0 · To be priced

Cut the data stream into trips (start, stops, end) with distance, energy and duration per trip; each trip gets an ID for later shipment linking.

**Value:** Many reports, alerts and the Phase 2 shipment link need trips.

**Users:** Driver, Fleet manager, System (automatic)

**Capabilities:**

- Trip detection rule agreed with operations
- Per-trip km, kWh, kWh/km and cost
- Trip history and costs in the driver app

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [MON-09](#mon-09)  
**Needed by:** [CRB-05](carbon.md#crb-05), [FLT-07](fleet.md#flt-07), [MON-13](#mon-13), [MON-15](#mon-15), [TMS-01](tms.md#tms-01)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-14 · Data OUT-17 · Data IN-40 · deferred.md 46

**Open questions:**

- What exactly is one trip? (to be defined by operations)

<a id="mon-12"></a>

### MON-12 Stops and parking log

*Nhật ký dừng & đỗ xe* · Must · P1.0 · To be priced

Record every stop and parking period with its place and duration.

**Value:** Needed for driving-time rules, regulator reporting and trip analysis.

**Users:** Fleet manager, System (automatic)

**Capabilities:**

- Stop and park events with coordinates and duration
- Count and total time of stops per day and per trip

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [MON-09](#mon-09)  
**Needed by:** [DRV-05](drivers.md#drv-05)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-60

<a id="mon-13"></a>

### MON-13 Load status and empty-trip detection

*Trạng thái tải & phát hiện chạy rỗng* · Should · P1.0 · To be priced

Know whether a truck runs loaded or empty, from its load sensor or the driver's declaration, infer it from consumption, and flag mismatches.

**Value:** Measures empty kilometres (wasted cost) and feeds Phase 2 backhaul planning.

**Users:** Driver, Fleet manager, System (automatic)

**Capabilities:**

- Load weight from the on-board sensor when available
- Driver declares loaded/empty in ≤2 taps; weight only if the sensor is missing
- Infer load from consumption vs the model curve (≥90% accuracy target)
- Flag trips where sources disagree

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [MON-11](#mon-11), [VEH-03](vehicles.md#veh-03)  
**Needed by:** [CRB-08](carbon.md#crb-08), [RTE-01](routing.md#rte-01)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-A9 · Data IN-18 · Data IN-50 · Prerequisite 3 · deferred.md 67  
**Old codes:** F-A9

**Open questions:**

- Does the Sany/Tri-Ring load sensor output on the CAN bus or only on the dashboard (prerequisite 3)?
- When declaration and inference keep disagreeing, is there a follow-up (reminder, escalation)?

<a id="mon-14"></a>

### MON-14 Vehicle operating report

*Báo cáo hiệu quả vận hành xe* · Must · P1.0 · To be priced

Km per day, kWh used, kWh/km and cost/km per truck, by day, week or month, with CSV export and a configurable electricity price.

**Value:** Shows the real running cost of each electric truck.

**Users:** Fleet manager, Driver, Accountant

**Capabilities:**

- Distance, energy and rates per truck over a period
- Daily, weekly and monthly breakdown; CSV export
- Cost from the applicable tariff instead of one flat price

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [MON-01](#mon-01), [VEH-03](vehicles.md#veh-03), [PAY-09](billing.md#pay-09)  
**Needed by:** [FLT-06](fleet.md#flt-06), [FLT-07](fleet.md#flt-07)  
**Also touches:** `billing`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-A6 · Data OUT-17 · deferred.md 60 · deferred.md 64  
**Old codes:** F-A6

<a id="mon-15"></a>

### MON-15 Abnormal consumption alert

*Cảnh báo tiêu thụ điện bất thường* · Could · P1.1 · To be priced

Alert when a truck uses clearly more energy than its normal level for the vehicle or route, showing the likely factors (load, terrain, weather, auxiliary loads, driving).

**Value:** Catches technical faults and wasteful driving early.

**Users:** Fleet manager, System (automatic)

**Capabilities:**

- Statistical norm per vehicle and route
- Alert above the norm with related factors

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [MON-11](#mon-11), [MON-16](#mon-16), [MON-17](#mon-17)  
**Needed by:** [MON-19](#mon-19)  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-28

<a id="mon-16"></a>

### MON-16 Auxiliary load usage

*Thời gian dùng phụ tải điện* · Could · P1.1 · To be priced

Track how long the air-conditioning, lights, heaters and other auxiliary loads run.

**Value:** Explains high consumption that is not caused by driving.

**Users:** Fleet manager, System (automatic)

**Capabilities:**

- On/off time per auxiliary load
- Share of energy used by auxiliary loads per trip

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [MON-01](#mon-01)  
**Needed by:** [MON-15](#mon-15)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-11

<a id="mon-17"></a>

### MON-17 Alert threshold settings

*Cấu hình ngưỡng cảnh báo* · Should · P1.0 · Internal only

Configure alert thresholds: battery levels, pack and cell temperature and SOH, abnormal consumption per vehicle or route, and the severity table.

**Value:** Thresholds follow the manufacturer and operations, not developers.

**Users:** Operations

**Capabilities:**

- Default thresholds, overridable per model or fleet
- Severity table shared by every alert
- History of threshold changes

**Status:** Backend ⬜ · Portal ⬜

**Needed by:** [MON-05](#mon-05), [MON-15](#mon-15)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-39 · deferred.md 43

<a id="mon-18"></a>

### MON-18 Driver app offline mode

*Chế độ ngoại tuyến của app tài xế* · Should · P1.0 · Included in every plan

When the phone loses signal, keep showing the last battery level and the downloaded station map, keep local threshold alerts working, and sync actions when back online.

**Value:** Drivers in remote areas are never left without information.

**Users:** Driver

**Capabilities:**

- Cached data shown with its time
- Local battery threshold alerts
- Queue actions and sync later

**Status:** App ⬜

**Depends on:** [MON-03](#mon-03), [STN-06](charging_stations.md#stn-06)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-D5 · Data OUT-18 · NF-09  
**Old codes:** F-D5

<a id="mon-19"></a>

### MON-19 Consumption root-cause analysis (AI)

*Phân tích nguyên nhân tiêu thụ bất thường (AI)* · Could · P2 · To be priced

Explain the causes of unusually high consumption from real driving and equipment usage with a trained model.

**Value:** Actionable advice instead of a bare alert.

**Users:** Fleet manager

**Capabilities:**

- Train on 6-12 months of consumption alerts and data
- Rank causes per abnormal trip

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [MON-15](#mon-15)  
**Related tables:** — (after the database review)  
**Sources:** Phase 2 A1
