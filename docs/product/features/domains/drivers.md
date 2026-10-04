<!-- GENERATED from features.yaml by the feature-catalog skill. Edit the source, then regenerate; never edit this file by hand. -->

# DRV — Drivers

*Tài xế* · [← Feature catalog](../README.md)

Driver profiles, vehicle assignment, shifts, driving time and per-driver reports. Backend domain: `drivers`.

## Checklist

- [ ] **DRV-01** [Driver profiles](#drv-01) — Backend ⬜ · Portal ⬜
- [ ] **DRV-02** [Driver check-in](#drv-02) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **DRV-03** [Driver work schedule](#drv-03) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **DRV-04** [Driver identification per shift](#drv-04) — Backend ⬜ · App ⬜
- [ ] **DRV-05** [Driving-time warning](#drv-05) — Backend ⬜ · App ⬜
- [ ] **DRV-06** [Driving and rest compliance report](#drv-06) — Backend ⬜ · Portal ⬜
- [ ] **DRV-07** [Driver charging-efficiency report](#drv-07) — Backend ⬜ · App ⬜ · Portal ⬜

## Features

<a id="drv-01"></a>

### DRV-01 Driver profiles

*Hồ sơ tài xế* · Must · P1.0 · To be priced

Keep each driver's facts for one organization: licence number, class and expiry, and status.

**Value:** Only drivers with a valid licence are assigned to trucks.

**Users:** Fleet manager, Organization administrator

**Capabilities:**

- Add, edit, search and deactivate drivers
- Licence expiry reminder
- A driver profile belongs to one membership; a former driver keeps their history

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [ACC-12](identity.md#acc-12)  
**Needed by:** [ACC-10](identity.md#acc-10), [DRV-02](#drv-02), [SUP-01](support.md#sup-01)  
**Also touches:** `identity`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-E4 · Data IN-37 · Data OUT-34 · Decision ID-13 · Decision DR-03  
**Old codes:** F-E4

<a id="drv-02"></a>

### DRV-02 Driver check-in

*Nhận xe* · Must · P1.0 · To be priced

The driver checks in to the truck they are about to drive by scanning its QR code or picking it in the app; the system always knows who is at the wheel and keeps the full history.

**Value:** Drivers and trucks switch daily in short-haul logistics; every event on a truck can still be traced to the driver at that time.

**Users:** Driver, Fleet manager, Dispatcher, System (automatic)

**Capabilities:**

- Check in by scanning the truck's QR code or picking a nearby truck in the app, refused when the phone is far from the truck; a manager can check a driver in from the portal
- Any active driver may drive any organization's truck; a warning when the driver is from another organization
- One driver at the wheel per truck; the session ends at check-out, when another driver takes over, when the driver takes another truck, or after the truck has not moved for the organization's auto-end time (default 2 hours)
- Alert the portal when a truck drives with nobody checked in
- Driver alerts go to the driver checked in and to the portal
- History per driver and per truck

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [DRV-01](#drv-01), [VEH-01](vehicles.md#veh-01)  
**Needed by:** [CHG-07](charging_sessions.md#chg-07), [DRV-03](#drv-03), [DRV-04](#drv-04), [DRV-07](#drv-07), [FLT-07](fleet.md#flt-07)  
**Also touches:** `vehicles`, `telemetry`, `notifications`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-E4 · Data IN-55 · Data OUT-34 · Decision DR-07 · Decision NT-07 · Decision ID-45  
**Old codes:** F-E4

<a id="drv-03"></a>

### DRV-03 Driver work schedule

*Lịch làm việc của tài xế* · Should · P1.0 · To be priced

Plan drivers' working days and shifts.

**Value:** Dispatchers see who is available and plan rest properly.

**Users:** Dispatcher, Fleet manager, Driver

**Capabilities:**

- Plan shifts per driver and truck
- Driver sees their schedule in the app

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [DRV-02](#drv-02)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-55

**Open questions:**

- Scope of the work schedule to be clarified (Hùng Võ).

<a id="drv-04"></a>

### DRV-04 Driver identification per shift

*Định danh tài xế theo ca* · Must · P1.0 · To be priced

Record who is actually driving each shift, by face, card or app login, with shift start and end.

**Value:** Legal driving-time records and fair per-driver reports.

**Users:** Driver, System (automatic)

**Capabilities:**

- Start and end a shift by face, card or app
- Alert when the person driving is not the assigned driver

**Status:** Backend ⬜ · App ⬜

**Depends on:** [DRV-02](#drv-02), [SAF-03](safety.md#saf-03)  
**Needed by:** [DRV-05](#drv-05), [SAF-01](safety.md#saf-01), [SAF-05](safety.md#saf-05), [SAF-08](safety.md#saf-08)  
**Also touches:** `scoring`  
**Related tables:** — (after the database review)  
**Sources:** Data IN-58

**Open questions:**

- Two drivers, one truck: one app or two?

<a id="drv-05"></a>

### DRV-05 Driving-time warning

*Cảnh báo thời gian lái liên tục* · Must · P1.0 · To be priced

Remind the driver before reaching 4 hours of continuous driving or 10 hours a day, and suggest a rest stop, preferably a charging station on the route.

**Value:** Safer drivers and compliance with driving-time law; rest doubles as a charging stop.

**Users:** Driver, System (automatic)

**Capabilities:**

- Continuous and daily driving time per driver
- Warning before each limit
- Rest-stop suggestion favouring charging stations

**Status:** Backend ⬜ · App ⬜

**Depends on:** [DRV-04](#drv-04), [MON-12](telemetry.md#mon-12), [STN-06](charging_stations.md#stn-06)  
**Needed by:** [DRV-06](#drv-06)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-59 · Data OUT-19

<a id="drv-06"></a>

### DRV-06 Driving and rest compliance report

*Báo cáo tuân thủ thời gian lái & nghỉ* · Must · P1.0 · To be priced

Per driver: shifts over 4 hours continuous, 10 hours a day and 48 hours a week.

**Value:** Fleet managers prove compliance and spot overworked drivers.

**Users:** Fleet manager

**Capabilities:**

- Violations per driver and period
- Export for inspections

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [DRV-05](#drv-05)  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-39

<a id="drv-07"></a>

### DRV-07 Driver charging-efficiency report

*Báo cáo hiệu quả sạc theo tài xế* · Should · P1.0 · To be priced

Per driver: share of energy charged in off-peak or renewable hours, electricity cost per km, savings vs peak-hour charging and fleet ranking; a manager view and a driver's own view.

**Value:** Encourages cheap, green charging habits.

**Users:** Fleet manager, Driver

**Capabilities:**

- Weekly and monthly report per driver
- Fleet ranking
- CSV/PDF export

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [CHG-07](charging_sessions.md#chg-07), [PAY-09](billing.md#pay-09), [DRV-02](#drv-02)  
**Also touches:** `charging_sessions`, `billing`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-A8 · Data OUT-32  
**Old codes:** F-A8

**Open questions:**

- Is the report tied to a reward or competition programme, or informational only?
