<!-- GENERATED from features.yaml by the feature-catalog skill. Edit the source, then regenerate; never edit this file by hand. -->

# CRB — Carbon & green transition

*Kiểm kê carbon & chuyển đổi xanh* · [← Feature catalog](../README.md)

Emissions avoided by electric trucks, green charging, fleet greenhouse gas inventory and carbon-credit evidence. Backend domain: `carbon`.

## Checklist

- [ ] **CRB-01** [Emission factor registry](#crb-01) — Backend ⬜ · Portal ⬜
- [ ] **CRB-02** [Generation mix per session](#crb-02) — Backend ⬜
- [ ] **CRB-03** [Charging loss accounting](#crb-03) — Backend ⬜
- [ ] **CRB-04** [Diesel baseline](#crb-04) — Backend ⬜ · Portal ⬜
- [ ] **CRB-05** [CO₂ saved per trip and month](#crb-05) — Backend ⬜ · App ⬜
- [ ] **CRB-06** [Green charging label](#crb-06) — Backend ⬜ · App ⬜
- [ ] **CRB-07** [Fleet Scope 1 & 2 inventory](#crb-07) — Backend ⬜ · Portal ⬜
- [ ] **CRB-08** [Emission intensity and EV vs diesel](#crb-08) — Backend ⬜ · Portal ⬜
- [ ] **CRB-09** [Enterprise greenhouse-gas dataset](#crb-09) — Backend ⬜ · Portal ⬜
- [ ] **CRB-10** [Renewable energy certificates](#crb-10) — Backend ⬜ · Portal ⬜
- [ ] **CRB-11** [Carbon credit MRV dossier](#crb-11) — Backend ⬜ · Portal ⬜

## Features

<a id="crb-01"></a>

### CRB-01 Emission factor registry

*Quản lý hệ số phát thải* · Should · P1.0 · Internal only

Keep emission factors with their version: Vietnam's grid factor (value, year, legal document, effective date) and life-cycle factors of solar, wind, biomass and storage; log which version was used for each period.

**Value:** Emissions can be recalculated when a factor is republished.

**Users:** Operations

**Capabilities:**

- Versioned factors with legal reference
- Log of factor versions applied per calculation period

**Status:** Backend ⬜ · Portal ⬜

**Needed by:** [CRB-05](#crb-05), [CRB-07](#crb-07)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-63 · Data IN-65 · Data OUT-69 · Prerequisite 8

<a id="crb-02"></a>

### CRB-02 Generation mix per session

*Cơ cấu nguồn điện của phiên sạc* · Should · P1.0 · Internal only

Share of each session's kWh coming from the grid, solar, wind, biomass and storage.

**Value:** Basis for green labels and accurate Scope 2 emissions.

**Users:** System (automatic)

**Capabilities:**

- Allocate each session's energy to sources by time slot

**Status:** Backend ⬜

**Depends on:** [CHG-02](charging_sessions.md#chg-02), [STN-13](charging_stations.md#stn-13)  
**Needed by:** [CRB-06](#crb-06), [CRB-07](#crb-07)  
**Also touches:** `charging_sessions`, `charging_stations`  
**Related tables:** — (after the database review)  
**Sources:** Data IN-64

<a id="crb-03"></a>

### CRB-03 Charging loss accounting

*Tổn thất sạc* · Should · P1.0 · Internal only

The difference between kWh measured at the charger and kWh that actually entered the battery.

**Value:** Avoids over- or under-counting emissions and reconciliation errors.

**Users:** System (automatic)

**Capabilities:**

- Loss per session from charger and vehicle data

**Status:** Backend ⬜

**Depends on:** [CHG-07](charging_sessions.md#chg-07), [MON-01](telemetry.md#mon-01)  
**Needed by:** [CHG-06](charging_sessions.md#chg-06)  
**Also touches:** `charging_sessions`, `telemetry`  
**Related tables:** — (after the database review)  
**Sources:** Data IN-67

<a id="crb-04"></a>

### CRB-04 Diesel baseline

*Đường cơ sở xe diesel* · Should · P1.0 · Internal only

Fuel consumption of an equivalent diesel truck by model and load, and the diesel emission factor.

**Value:** Makes "CO₂ avoided" measurable.

**Users:** Operations

**Capabilities:**

- Baseline per model and load

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [VEH-03](vehicles.md#veh-03)  
**Needed by:** [CRB-05](#crb-05)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-66

<a id="crb-05"></a>

### CRB-05 CO₂ saved per trip and month

*CO₂ giảm được theo chuyến & theo tháng* · Should · P1.0 · To be priced

The truck's actual emissions compared with an equivalent diesel truck on the same load and distance, per trip and per month.

**Value:** Drivers and owners see the environmental benefit of going electric.

**Users:** Driver, Fleet manager

**Capabilities:**

- Per trip and monthly totals at vehicle level

**Status:** Backend ⬜ · App ⬜

**Depends on:** [CRB-01](#crb-01), [CRB-04](#crb-04), [MON-11](telemetry.md#mon-11)  
**Needed by:** [CRB-08](#crb-08)  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-22

<a id="crb-06"></a>

### CRB-06 Green charging label

*Nhãn phiên sạc xanh* · Could · P1.0 · Included in every plan

Each session shows its share of renewable energy, the CO₂ avoided and the money saved.

**Value:** Rewards green charging and builds the brand.

**Users:** Driver

**Capabilities:**

- Label on the receipt and in history

**Status:** Backend ⬜ · App ⬜

**Depends on:** [CRB-02](#crb-02), [CHG-03](charging_sessions.md#chg-03)  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-23

<a id="crb-07"></a>

### CRB-07 Fleet Scope 1 & 2 inventory

*Kiểm kê phát thải Scope 1 & 2 của đội xe* · Should · P1.0 · To be priced

Tonnes of CO₂e per month, quarter and year, grid and renewable energy shown apart, within declared inventory boundaries and methodology.

**Value:** Transport companies meet their greenhouse-gas reporting duty.

**Users:** Fleet manager, Organization administrator

**Capabilities:**

- Inventory boundaries, period and method per organization
- Scope 1 & 2 totals by period

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [CRB-01](#crb-01), [CRB-02](#crb-02), [CHG-07](charging_sessions.md#chg-07)  
**Needed by:** [CRB-09](#crb-09), [CRB-11](#crb-11)  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-42 · Data IN-69

<a id="crb-08"></a>

### CRB-08 Emission intensity and EV vs diesel

*Cường độ phát thải & so sánh xe điện với diesel* · Should · P1.0 · To be priced

gCO₂e per km and per tonne-km by truck and route, and the CO₂ avoided compared with the diesel baseline.

**Value:** Hard numbers for sustainability reports and sales.

**Users:** Fleet manager

**Capabilities:**

- Intensity by truck and route
- Avoided CO₂ vs diesel

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [CRB-05](#crb-05), [MON-13](telemetry.md#mon-13)  
**Needed by:** [CRB-11](#crb-11), [TMS-04](tms.md#tms-04)  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-43

<a id="crb-09"></a>

### CRB-09 Enterprise greenhouse-gas dataset

*Bộ dữ liệu kiểm kê khí nhà kính cấp doanh nghiệp* · Should · P1.0 · To be priced

Total kWh, generation mix and tCO₂e per period, packaged for the official Scope 1 & 2 inventory submission.

**Value:** Ready-to-file data for us and our customers.

**Users:** Operations, Fleet manager

**Capabilities:**

- Export in the submission format

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [CRB-07](#crb-07)  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-68

<a id="crb-10"></a>

### CRB-10 Renewable energy certificates

*Chứng chỉ năng lượng tái tạo* · Could · P1.0 · Internal only

Record REC/I-REC certificates: code, matching volume, validity period and issuer.

**Value:** Backs green claims with certificates.

**Users:** Operations

**Capabilities:**

- Certificate registry linked to generation

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [STN-13](charging_stations.md#stn-13)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-68

<a id="crb-11"></a>

### CRB-11 Carbon credit MRV dossier

*Hồ sơ MRV tín chỉ carbon* · Could · P1.0 · Internal only

Measurement, reporting and verification data for an emission-reduction project based on the switch to electric trucks.

**Value:** Opens a carbon-credit revenue stream.

**Users:** Head administrator (internal), Operations

**Capabilities:**

- Assemble MRV data per project period

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [CRB-07](#crb-07), [CRB-08](#crb-08)  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-70
