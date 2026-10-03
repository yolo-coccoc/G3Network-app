<!-- GENERATED from features.yaml by the feature-catalog skill. Edit the source, then regenerate; never edit this file by hand. -->

# PLT — Platform, data & compliance

*Nền tảng, dữ liệu & tuân thủ* · [← Feature catalog](../README.md)

The data pipeline and warehouse, system health, data retention, data sovereignty and company-wide dashboards. No backend domain yet.

## Checklist

- [ ] **PLT-01** [Data pipeline and warehouse](#plt-01) — Backend ⬜
- [ ] **PLT-02** [Data quality monitoring](#plt-02) — Backend ⬜ · Portal ⬜
- [ ] **PLT-03** [System health dashboard](#plt-03) — Backend ⬜ · Portal ⬜
- [ ] **PLT-04** [Data retention and archiving](#plt-04) — Backend ⬜
- [ ] **PLT-05** [Data sovereignty monitoring](#plt-05) — Backend ⬜ · Portal ⬜
- [ ] **PLT-06** [Executive KPI dashboard](#plt-06) — Backend ⬜ · Portal ⬜
- [ ] **PLT-07** [Third-party aggregated data](#plt-07) — Backend ⬜

## Features

<a id="plt-01"></a>

### PLT-01 Data pipeline and warehouse

*Đường ống & kho dữ liệu chuẩn hóa* · Should · P1.0 · Internal only

Collect, normalize, tag and store data per truck, customer and route in a warehouse for AI and BI, with schema versions and reserved trip/shipment/customer keys for Phase 2.

**Value:** Analytics and future AI features run on clean, comparable data.

**Users:** System (automatic), Operations

**Capabilities:**

- Normalized, tagged datasets with schema versions
- Reserved optional keys trip_id, shipment_id, customer_id

**Status:** Backend ⬜

**Depends on:** [MON-01](telemetry.md#mon-01), [CHG-02](charging_sessions.md#chg-02)  
**Needed by:** [PLT-02](#plt-02), [TMS-01](tms.md#tms-01)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-G3 · Data IN-40 · Data OUT-64 · Prerequisite 8 · NF-16  
**Old codes:** F-G3

<a id="plt-02"></a>

### PLT-02 Data quality monitoring

*Giám sát chất lượng dữ liệu* · Should · P1.0 · Internal only

Detect dirty, missing or impossible data and alert operations.

**Value:** Reports and bills are not built on bad data.

**Users:** Operations, System (automatic)

**Capabilities:**

- Quality rules per data source
- Quality score and alerts

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [PLT-01](#plt-01)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-G3 · Data OUT-61  
**Old codes:** F-G3

<a id="plt-03"></a>

### PLT-03 System health dashboard

*Dashboard sức khỏe hệ thống* · Must · P1.0 · Internal only

Uptime, ingestion delay, data-flow interruptions and data quality, with alerts to the operations team.

**Value:** Problems are seen before customers report them.

**Users:** Operations

**Capabilities:**

- Health of every process (API, ingestion, charger gateway, monitors)
- Alert on ingestion stop
- Centralized logs and metrics

**Status:** Backend ⬜ · Portal ⬜

**Related tables:** — (after the database review)  
**Sources:** Data OUT-61 · NF-14 · future.md 17 · future.md 19

<a id="plt-04"></a>

### PLT-04 Data retention and archiving

*Lưu trữ & lưu trữ lạnh dữ liệu* · Should · P1.0 · Internal only

Keep time-series data hot for 12 months and cold for 5 years (the warranty period), with per-type retention rules.

**Value:** Meets legal and warranty needs while controlling storage cost.

**Users:** System (automatic), Operations

**Capabilities:**

- Retention rules per data type
- Automatic move to cold storage and deletion

**Status:** Backend ⬜

**Needed by:** [SAF-09](safety.md#saf-09)  
**Related tables:** — (after the database review)  
**Sources:** NF-16 · future.md 48

<a id="plt-05"></a>

### PLT-05 Data sovereignty monitoring

*Giám sát chủ quyền dữ liệu* · Must · P1.0 · Internal only

Map which data leaves the truck, to which servers, in which country and to which third parties; detect undeclared foreign connections and compare them with the manufacturer's written commitments.

**Value:** Protects national and customer data; verified, not taken on trust.

**Users:** Operations, Head administrator (internal)

**Capabilities:**

- Declared data-flow map per device type
- Traffic monitoring during the pilot
- Report of undeclared foreign connections

**Status:** Backend ⬜ · Portal ⬜

**Related tables:** — (after the database review)  
**Sources:** Data IN-24 · Data OUT-60 · Prerequisite 2

<a id="plt-06"></a>

### PLT-06 Executive KPI dashboard

*Bộ KPI cho Ban lãnh đạo* · Should · P1.0 · Internal only

North Star (weekly active vehicles), charging compliance, app activation, CSAT/NPS, revenue and number of trucks.

**Value:** Leadership steers the business on a few clear numbers.

**Users:** Head administrator (internal)

**Capabilities:**

- KPI set with targets and trend

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [PAY-13](billing.md#pay-13), [POL-04](policy.md#pol-04), [VEH-05](vehicles.md#veh-05)  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-37

**Open questions:**

- Is this for G3's leadership or for each transport company's leadership (the data sheet lists it under transport companies)?
- Exact definition of WHAV and how CSAT/NPS are collected.

<a id="plt-07"></a>

### PLT-07 Third-party aggregated data

*Dữ liệu tổng hợp từ đối tác ngoài* · Could · P1.5 · Internal only

Import aggregated, anonymous mobility and demand data by area from outside partners.

**Value:** Better station planning and forecasts.

**Users:** Operations

**Capabilities:**

- Import and store aggregated datasets

**Status:** Backend ⬜

**Related tables:** — (after the database review)  
**Sources:** Data IN-48
