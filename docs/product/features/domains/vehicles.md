<!-- GENERATED from features.yaml by the feature-catalog skill. Edit the source, then regenerate; never edit this file by hand. -->

# VEH — Vehicles

*Hồ sơ xe* · [← Feature catalog](../README.md)

The truck's profile, owner, model and battery data, and its activation at handover. Backend domain: `vehicles`.

## Checklist

- [ ] **VEH-01** [Vehicle registry](#veh-01) — Backend ⬜ · Portal ⬜
- [ ] **VEH-02** [Vehicle ownership](#veh-02) — Backend ⬜ · Portal ⬜
- [ ] **VEH-03** [Vehicle model catalog](#veh-03) — Backend ⬜ · Portal ⬜
- [ ] **VEH-04** [Battery registry](#veh-04) — Backend ⬜ · Portal ⬜
- [ ] **VEH-05** [Vehicle activation](#veh-05) — Backend ⬜ · Portal ⬜
- [ ] **VEH-06** [Fault code catalog](#veh-06) — Backend ⬜ · Portal ⬜

## Features

<a id="veh-01"></a>

### VEH-01 Vehicle registry

*Danh mục xe* · Must · P1.0 · Included in every plan

Keep every truck's profile: VIN, plate, model, handover date, warranty status and service status.

**Value:** Every alert, session and report points to one well-identified truck.

**Users:** Operations, Fleet manager, Organization administrator

**Capabilities:**

- Create, edit, search and retire vehicles
- VIN and plate unique among live trucks and both editable (typing mistakes, plates that follow the owner); history of changes with a reason
- Service status set by a person: active, under maintenance, decommissioned, with a reason; idle or silent trucks are shown from their data, not stored
- Warranties per truck, battery and device with their own limits; a voided warranty keeps its reason
- Filter by status, model, owner and fleet

**Status:** Backend ⬜ · Portal ⬜

**Needed by:** [DEV-02](telematics.md#dev-02), [DRV-02](drivers.md#drv-02), [FLT-02](fleet.md#flt-02), [MNT-03](maintenance.md#mnt-03), [MNT-06](maintenance.md#mnt-06), [SAF-03](safety.md#saf-03), [SUP-01](support.md#sup-01), [VEH-02](#veh-02), [VEH-03](#veh-03), [VEH-04](#veh-04), [VEH-05](#veh-05)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-32 · PRD F-F2 · Decision VH-05 · Decision VH-07 · Decision VH-09  
**Old codes:** F-F2

<a id="veh-02"></a>

### VEH-02 Vehicle ownership

*Chủ sở hữu xe* · Must · P1.0 · Internal only

Each truck has exactly one owning organization; ownership can be transferred and every transfer is kept.

**Value:** Clear responsibility and data ownership, even when trucks are sold or leased.

**Users:** Operations, Sales

**Capabilities:**

- Record the owner at handover
- Transfer ownership with an effective date; keep the history

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [VEH-01](#veh-01), [ACC-01](identity.md#acc-01)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-32 · Decision VH-03

**Open questions:**

- After an ownership change, may the new owner see the truck's data from before the transfer? (open decision D3)

<a id="veh-03"></a>

### VEH-03 Vehicle model catalog

*Danh mục dòng xe* · Should · P1.0 · Internal only

Store each truck model's specifications (EVT-262/400/825): battery capacity, consumption curve by load, and the matching diesel baseline.

**Value:** Reports and forecasts use manufacturer-confirmed figures instead of defaults.

**Users:** Operations

**Capabilities:**

- Model specifications confirmed by the manufacturer
- Consumption reference curve by load, used by forecasts and empty-trip detection

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [VEH-01](#veh-01)  
**Needed by:** [CRB-04](carbon.md#crb-04), [MON-13](telemetry.md#mon-13), [MON-14](telemetry.md#mon-14), [RTE-01](routing.md#rte-01)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-32 · Data IN-34 · deferred.md 61 · Decision VH-08

<a id="veh-04"></a>

### VEH-04 Battery registry

*Hồ sơ pin* · Must · P1.0 · Internal only

Manage each truck battery as an asset: its model, owner and the trucks it has been fitted to.

**Value:** The battery is up to two thirds of a truck's price; its health, warranty and value follow it from truck to truck.

**Users:** Operations, Maintenance, Warranty

**Capabilities:**

- Battery model catalog: chemistry (LFP/CATL), design capacity, voltage, layout
- Each battery: serial number, model, owner (may differ from the truck's owner), status with a reason
- One battery per truck; history of which truck each battery was fitted to

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [VEH-01](#veh-01)  
**Needed by:** [MNT-06](maintenance.md#mnt-06), [MON-08](telemetry.md#mon-08)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-33 · Decision VH-08 · deferred.md 87 · deferred.md 88

<a id="veh-05"></a>

### VEH-05 Vehicle activation

*Kích hoạt xe* · Must · P1.0 · Internal only

Follow each truck from registration to device assignment to its first data received, and report the activation success rate.

**Value:** No truck leaves handover without working data; target ≥98% activation success.

**Users:** Operations, System (automatic)

**Capabilities:**

- Activation computed from the device fitted now and the data received, so a truck whose T-Box was removed shows it
- Activation success rate and the list of trucks still waiting

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [VEH-01](#veh-01), [DEV-02](telematics.md#dev-02)  
**Needed by:** [PLT-06](platform.md#plt-06)  
**Also touches:** `telematics`, `telemetry`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-F2 · Decision VH-06  
**Old codes:** F-F2

<a id="veh-06"></a>

### VEH-06 Fault code catalog

*Bảng mã lỗi* · Must · P1.0 · Internal only

The manufacturer's fault code (DTC) table: description, severity, whether the truck can keep driving, and what to do.

**Value:** Turns raw error codes into clear alerts and instructions for drivers and support.

**Users:** Operations, Customer care, Warranty

**Capabilities:**

- Import and version the manufacturer's DTC table
- Severity, drivability verdict and handling advice per code
- Standard motor states list

**Status:** Backend ⬜ · Portal ⬜

**Needed by:** [MNT-03](maintenance.md#mnt-03), [MON-06](telemetry.md#mon-06)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-16 · Data IN-34 · Prerequisite 1 · deferred.md 42

**Open questions:**

- The manufacturer must deliver the DTC table with severities in writing (prerequisite 1).
