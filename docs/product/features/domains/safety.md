<!-- GENERATED from features.yaml by the feature-catalog skill. Edit the source, then regenerate; never edit this file by hand. -->

# SAF — Driver safety & camera

*An toàn lái xe & camera* · [← Feature catalog](../README.md)

Driver safety scoring, the driver-monitoring camera, in-cab ADAS alerts, evidence clips and the camera's legal obligations. Backend domain: `scoring`.

## Checklist

- [ ] **SAF-01** [Driver safety score](#saf-01) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **SAF-02** [Safety event board](#saf-02) — Backend ⬜ · Portal ⬜
- [ ] **SAF-03** [Driver monitoring camera](#saf-03) — Backend ⬜ · Portal ⬜
- [ ] **SAF-04** [In-cab ADAS alerts](#saf-04) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **SAF-05** [Driver evidence clips](#saf-05) — Backend ⬜ · App ⬜
- [ ] **SAF-06** [Accident and insurance evidence pack](#saf-06) — Backend ⬜ · Portal ⬜
- [ ] **SAF-07** [Camera health and tamper alerts](#saf-07) — Backend ⬜ · Portal ⬜
- [ ] **SAF-08** [Data transmission to authorities](#saf-08) — Backend ⬜ · Portal ⬜
- [ ] **SAF-09** [Camera data retention compliance](#saf-09) — Backend ⬜ · Portal ⬜

## Features

<a id="saf-01"></a>

### SAF-01 Driver safety score

*Điểm an toàn lái xe* · Should · P1.0 · To be priced

A 0–100 safety score per driver from hard braking, sudden acceleration, sharp cornering, speeding and over-4-hour driving, normalized per 100 km, merged with camera events where the advanced package is used; a manager view and a driver's own view.

**Value:** Safer driving, fewer accidents; later a basis for insurance pricing.

**Users:** Fleet manager, Driver, System (automatic)

**Capabilities:**

- Event detection from acceleration, speed and route limits
- Configurable weights; fixed thresholds per model at first, recalibrated on real data
- Weekly report and fleet ranking

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [MON-01](telemetry.md#mon-01), [DRV-04](drivers.md#drv-04), [RTE-07](routing.md#rte-07)  
**Needed by:** [SAF-02](#saf-02)  
**Also touches:** `telemetry`, `drivers`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-K1 · Data IN-12 · Data OUT-33  
**Old codes:** F-K1

**Open questions:**

- Should the safety model also meet the 'retrainable' quality bar of NF-20?

<a id="saf-02"></a>

### SAF-02 Safety event board

*Bảng sự kiện an toàn* · Should · P1.0 · To be priced

Safety events per driver and truck, telematics events (basic plan) kept apart from camera/ADAS events (advanced plan), with severity and trends.

**Value:** Managers coach the right drivers on the right behaviour.

**Users:** Fleet manager

**Capabilities:**

- Events by driver, truck and type
- Trend over weeks

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [SAF-01](#saf-01)  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-38

<a id="saf-03"></a>

### SAF-03 Driver monitoring camera

*Camera giám sát người lái* · Must · P1.0 · To be priced

Record the driver continuously in all light (infrared at night), with plate/VIN, GPS, speed and time on each frame, as the law requires.

**Value:** A legal obligation for transport companies, included in the basic plan.

**Users:** System (automatic), Fleet manager

**Capabilities:**

- Integration with the camera provider's platform
- Footage linked to truck, driver and time
- Viewing is logged as personal-data access

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [VEH-01](vehicles.md#veh-01), [ACC-18](identity.md#acc-18)  
**Needed by:** [DRV-04](drivers.md#drv-04), [SAF-04](#saf-04), [SAF-05](#saf-05), [SAF-06](#saf-06), [SAF-07](#saf-07), [SAF-08](#saf-08), [SAF-09](#saf-09)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-56 · Prerequisite 7

**Open questions:**

- Which camera provider (Vietmap?) and who stores the footage?

<a id="saf-04"></a>

### SAF-04 In-cab ADAS alerts

*Cảnh báo ADAS trong cabin* · Should · P1.0 · To be priced

Warn the driver instantly about drowsiness, distraction, phone use and tailgating, only on trucks fitted with ADAS.

**Value:** Prevents accidents; sold as the advanced package.

**Users:** Driver, Fleet manager

**Capabilities:**

- Receive ADAS events from equipped trucks
- Instant in-cab warning; events stored for scoring

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [SAF-03](#saf-03)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-57 · Data OUT-21 · Prerequisite 7 · deferred.md 89

<a id="saf-05"></a>

### SAF-05 Driver evidence clips

*Clip bằng chứng của tài xế* · Should · P1.0 · To be priced

Drivers review the 10–30 second clips around their own events, for complaints or disputes.

**Value:** Fairness and trust: drivers can defend themselves.

**Users:** Driver

**Capabilities:**

- List own events with clips
- Only the driver's own clips

**Status:** Backend ⬜ · App ⬜

**Depends on:** [SAF-03](#saf-03), [DRV-04](drivers.md#drv-04)  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-20

**Open questions:**

- Clip scope and access to be clarified (Hùng Võ).

<a id="saf-06"></a>

### SAF-06 Accident and insurance evidence pack

*Hồ sơ trích xuất phục vụ tai nạn & bảo hiểm* · Should · P1.0 · To be priced

Export clips, route, speed and events around an incident time as one evidence pack.

**Value:** Faster insurance claims and dispute resolution.

**Users:** Fleet manager, Customer care

**Capabilities:**

- Choose truck and time window, export the pack
- Reason required; export is audited

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [SAF-03](#saf-03), [MON-10](telemetry.md#mon-10), [ACC-18](identity.md#acc-18)  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-41

<a id="saf-07"></a>

### SAF-07 Camera health and tamper alerts

*Giám sát & cảnh báo camera* · Must · P1.0 · To be priced

Watch each camera for lost signal, covering, misalignment, power loss, full storage, clock drift and firmware; tell deliberate covering apart from faults and signal loss; report camera uptime and upload success.

**Value:** Keeps the legal camera obligation met.

**Users:** Operations, Fleet manager, System (automatic)

**Capabilities:**

- Camera status and integrity signals
- Covering vs fault vs signal-loss alerts
- Camera uptime and upload success report

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [SAF-03](#saf-03), [NTF-01](notifications.md#ntf-01)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-61 · Data OUT-40 · Data OUT-67

**Open questions:**

- Responsibility of the camera provider (Vietmap) vs ours.

<a id="saf-08"></a>

### SAF-08 Data transmission to authorities

*Truyền dữ liệu về cơ quan quản lý* · Must · P1.0 · To be priced

Send real-time journey data and driver violation data to the authorities and keep the transmission receipts.

**Value:** Meets the transport companies' reporting obligation.

**Users:** System (automatic), External system

**Capabilities:**

- Journey and violation data feeds
- Receipt per transmission (ID, data type, status, time)

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [MON-01](telemetry.md#mon-01), [SAF-03](#saf-03), [DRV-04](drivers.md#drv-04)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-62 · Data OUT-65

<a id="saf-09"></a>

### SAF-09 Camera data retention compliance

*Tuân thủ lưu trữ dữ liệu camera* · Must · P1.0 · Internal only

Check the mandatory retention periods: journey data ≥30 days on the device, ≥1 year on the service server, ≥3 years at the traffic police; driver images ≥3 days on the device; violation data ≥3 months.

**Value:** Avoids penalties at inspections.

**Users:** Operations

**Capabilities:**

- Retention status per data type and storage location
- Alert before data would be missing

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [SAF-03](#saf-03), [PLT-04](platform.md#plt-04)  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-66

**Open questions:**

- Do we already have a storage partner (Vietmap)?
