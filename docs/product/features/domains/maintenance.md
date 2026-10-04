<!-- GENERATED from features.yaml by the feature-catalog skill. Edit the source, then regenerate; never edit this file by hand. -->

# MNT — Maintenance & asset lifecycle

*Bảo dưỡng & vòng đời tài sản* · [← Feature catalog](../README.md)

Maintenance reminders and bookings, repair records, maintenance costs, depreciation and component lifetime. Backend domain: `maintenance`.

## Checklist

- [ ] **MNT-01** [Maintenance reminders](#mnt-01) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **MNT-02** [Maintenance booking](#mnt-02) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **MNT-03** [Repair and maintenance records](#mnt-03) — Backend ⬜ · Portal ⬜
- [ ] **MNT-04** [Periodic maintenance report](#mnt-04) — Backend ⬜ · Portal ⬜
- [ ] **MNT-05** [Maintenance and repair cost report](#mnt-05) — Backend ⬜ · Portal ⬜
- [ ] **MNT-06** [Asset cost and depreciation](#mnt-06) — Backend ⬜ · Portal ⬜
- [ ] **MNT-07** [Asset lifecycle alerts](#mnt-07) — Backend ⬜ · Portal ⬜
- [ ] **MNT-08** [Parts inventory report](#mnt-08) — Backend ⬜ · Portal ⬜
- [ ] **MNT-09** [Workshop revenue report](#mnt-09) — Backend ⬜ · Portal ⬜
- [ ] **MNT-10** [Parts price margin report](#mnt-10) — Backend ⬜ · Portal ⬜

## Features

<a id="mnt-01"></a>

### MNT-01 Maintenance reminders

*Nhắc bảo dưỡng* · Should · P1.0 · To be priced

Remind drivers and fleet managers of maintenance by distance or date, checked against the maintenance already done.

**Value:** Fewer breakdowns and warranty kept valid.

**Users:** Driver, Fleet manager, System (automatic)

**Capabilities:**

- Maintenance plan per model (km and time)
- Reminder before the due point
- Skip reminders already covered by a recorded service

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [MON-01](telemetry.md#mon-01), [MNT-03](#mnt-03), [NTF-01](notifications.md#ntf-01)  
**Needed by:** [MNT-02](#mnt-02), [MNT-04](#mnt-04)  
**Also touches:** `telemetry`, `notifications`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-F4 · Data OUT-15  
**Old codes:** F-F4

<a id="mnt-02"></a>

### MNT-02 Maintenance booking

*Đặt lịch bảo dưỡng* · Could · P1.5 · To be priced

Book a slot at a network workshop, usually from a reminder, and cancel it.

**Value:** Convenient for drivers; fills partner workshops.

**Users:** Driver, Fleet manager, Partner technician

**Capabilities:**

- Choose a workshop and a free slot
- Workshop confirms or proposes another slot

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [MNT-01](#mnt-01), [SUP-04](support.md#sup-04)  
**Also touches:** `support`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-I3 · Data IN-54 · deferred.md 69  
**Old codes:** F-I3

<a id="mnt-03"></a>

### MNT-03 Repair and maintenance records

*Hồ sơ sửa chữa – bảo dưỡng – thay thế* · Could · P1.5 · To be priced

Follow each case end to end: fault, record, diagnosis, decision whether the truck may keep driving, workshop, repair or replacement, test and handover; with the warranty file.

**Value:** Complete service history per truck; base for warranty and cost reports.

**Users:** Maintenance, Warranty, Fleet manager, Partner technician

**Capabilities:**

- Case stages with dates and responsible person
- Parts replaced, labour and cost
- Warranty claim file attached

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [VEH-01](vehicles.md#veh-01), [VEH-06](vehicles.md#veh-06)  
**Needed by:** [MNT-01](#mnt-01), [MNT-04](#mnt-04), [MNT-05](#mnt-05), [MNT-07](#mnt-07), [MNT-08](#mnt-08)  
**Also touches:** `vehicles`, `support`  
**Related tables:** — (after the database review)  
**Sources:** Data IN-53 · Data OUT-46

<a id="mnt-04"></a>

### MNT-04 Periodic maintenance report

*Báo cáo bảo dưỡng định kỳ* · Could · P1.5 · To be priced

Which trucks are due soon, due, overdue, or done.

**Value:** Fleet managers keep the whole fleet on schedule.

**Users:** Fleet manager, Maintenance

**Capabilities:**

- Status per truck and fleet
- Export

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [MNT-01](#mnt-01), [MNT-03](#mnt-03)  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-44

<a id="mnt-05"></a>

### MNT-05 Maintenance and repair cost report

*Báo cáo chi phí đại tu – bảo dưỡng – sửa chữa* · Could · P1.5 · To be priced

Overhaul, periodic maintenance and unplanned repair costs, separately, per truck and for the whole fleet (individual and contract customers).

**Value:** Shows the true cost of owning each truck.

**Users:** Fleet manager, Accountant, Maintenance

**Capabilities:**

- Three cost types per truck and fleet
- Period comparison and export

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [MNT-03](#mnt-03)  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-45

<a id="mnt-06"></a>

### MNT-06 Asset cost and depreciation

*Nguyên giá & khấu hao tài sản* · Could · P1.5 · To be priced

Record the purchase cost of trucks, batteries and major components, the depreciation method and period, residual value and component lifetime norms (km or hours).

**Value:** Repair decisions weigh cost against the asset's remaining value.

**Users:** Accountant, Maintenance

**Capabilities:**

- Cost, depreciation and residual value per asset
- Lifetime norms per component

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [VEH-01](vehicles.md#veh-01), [BAT-01](batteries.md#bat-01)  
**Needed by:** [MNT-07](#mnt-07)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-41

<a id="mnt-07"></a>

### MNT-07 Asset lifecycle alerts

*Cảnh báo bất thường vòng đời tài sản* · Could · P1.5 · To be priced

Alert when a component fails earlier than its lifetime norm, when repair costs exceed a share of the residual value, or when a truck nears the end of its depreciation.

**Value:** Spots bad parts, bad suppliers and trucks to replace.

**Users:** Maintenance, Accountant, System (automatic)

**Capabilities:**

- Early failure vs norm
- Repair cost vs residual value

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [MNT-03](#mnt-03), [MNT-06](#mnt-06)  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-47

<a id="mnt-08"></a>

### MNT-08 Parts inventory report

*Báo cáo nhập – xuất – tồn vật tư* · Could · P2 · To be priced

Stock movements of parts and supplies per period, compared with the physical count.

**Value:** Workshop stock under control.

**Users:** Maintenance, Accountant

**Capabilities:**

- Stock in, out and on hand per period
- Reconciliation with stock counts

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [MNT-03](#mnt-03)  
**Needed by:** [MNT-09](#mnt-09), [MNT-10](#mnt-10)  
**Related tables:** — (after the database review)  
**Sources:** Phase 2 X1

<a id="mnt-09"></a>

### MNT-09 Workshop revenue report

*Báo cáo doanh thu xưởng* · Could · P2 · Internal only

Total workshop service revenue per month and year and where it comes from.

**Value:** Workshop business performance.

**Users:** Accountant, Head administrator (internal)

**Capabilities:**

- Revenue by period and source

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [MNT-08](#mnt-08)  
**Related tables:** — (after the database review)  
**Sources:** Phase 2 X2

<a id="mnt-10"></a>

### MNT-10 Parts price margin report

*Báo cáo chênh lệch giá vật tư* · Could · P2 · Internal only

Difference between selling price and cost of parts supplied to customers.

**Value:** Margin control on parts.

**Users:** Accountant

**Capabilities:**

- Margin per part and per period

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [MNT-08](#mnt-08)  
**Related tables:** — (after the database review)  
**Sources:** Phase 2 X3
