<!-- GENERATED from features.yaml by the feature-catalog skill. Edit the source, then regenerate; never edit this file by hand. -->

# CHG — Charging sessions

*Phiên sạc* · [← Feature catalog](../README.md)

Starting a charge, the session record, receipts, history, energy per customer and reconciliation. Backend domain: `charging_sessions`.

## Checklist

- [ ] **CHG-01** [Start charging by QR code](#chg-01) — Backend ⬜ · App ⬜
- [ ] **CHG-02** [Charging session record](#chg-02) — Backend ⬜ · Portal ⬜
- [ ] **CHG-03** [Charging receipt](#chg-03) — Backend ⬜ · App ⬜
- [ ] **CHG-04** [Charging history](#chg-04) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **CHG-05** [Energy per customer and session](#chg-05) — Backend ⬜ · Portal ⬜
- [ ] **CHG-06** [Three-way reconciliation](#chg-06) — Backend ⬜ · Portal ⬜
- [ ] **CHG-07** [Session attribution](#chg-07) — Backend ⬜
- [ ] **CHG-08** [Charging cards and VIN Autocharge](#chg-08) — Backend ⬜ · Portal ⬜

## Features

<a id="chg-01"></a>

### CHG-01 Start charging by QR code

*Bắt đầu sạc bằng mã QR* · Must · P1.0 · Included in every plan

The driver scans the QR code on the charger's screen; the app authorizes them and checks the wallet, the driver starts and stops on the charger, and the app shows the progress.

**Value:** Scan, charge, pay in three steps for companies and individual drivers alike.

**Users:** Driver

**Capabilities:**

- Scan the QR code on the charger's screen; the app authorizes the driver and checks the wallet
- The driver chooses the gun and starts on the charger's screen
- Record who started the charge and which organization pays
- Live progress: kWh, battery %, power, cost so far
- Stop on the charger's screen (or by the truck); the app sends no command
- Keep the session and bill later if the signal is weak

**Status:** Backend ⬜ · App ⬜

**Depends on:** [STN-10](charging_stations.md#stn-10), [CHG-02](#chg-02), [ACC-04](identity.md#acc-04)  
**Needed by:** [CHG-07](#chg-07), [PAY-06](billing.md#pay-06)  
**Also touches:** `charging_stations`, `identity`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-H1 · Decision CO-12 · Decision CO-13 · Decision CO-14 · Decision CE-10 · Decision CE-11  
**Old codes:** F-H1

**Open questions:**

- Vendor to confirm: QR content, choosing the gun on the screen after a remote authorization, AuthorizeRemoteTxRequests (open question 4).

<a id="chg-02"></a>

### CHG-02 Charging session record

*Bản ghi phiên sạc* · Must · P1.0 · Internal only

Keep an unchangeable record of every session: time, station, gun, power, kWh, start and end battery %, duration and cost, plus all meter readings.

**Value:** The single source for billing, warranty and disputes; 100% of sessions on our network are logged.

**Users:** System (automatic), Operations, Customer care

**Capabilities:**

- Session lifecycle from the charger's messages
- All measurements (energy, power, voltage, current, battery %, temperature)
- Append-only once finished
- Retry, duplicate and out-of-order handling

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [STN-03](charging_stations.md#stn-03)  
**Needed by:** [CHG-01](#chg-01), [CHG-03](#chg-03), [CHG-04](#chg-04), [CRB-02](carbon.md#crb-02), [PAY-10](billing.md#pay-10), [PLT-01](platform.md#plt-01), [STN-07](charging_stations.md#stn-07), [STN-14](charging_stations.md#stn-14)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-B2 · Data IN-27 · NF-11 · deferred.md 27 · Decision CE-10 · Decision CE-12  
**Old codes:** F-B2

<a id="chg-03"></a>

### CHG-03 Charging receipt

*Biên nhận phiên sạc* · Must · P1.0 · Included in every plan

A receipt for every session: time, station and gun, kWh, unit price and amount.

**Value:** Transparent charging: the price shown is the price paid.

**Users:** Driver, Accountant

**Capabilities:**

- Receipt in the app right after charging
- Unit price from the tariff applied to the session

**Status:** Backend ⬜ · App ⬜

**Depends on:** [CHG-02](#chg-02), [PAY-10](billing.md#pay-10)  
**Needed by:** [CRB-06](carbon.md#crb-06), [PAY-06](billing.md#pay-06)  
**Also touches:** `billing`  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-10 · NF-19

<a id="chg-04"></a>

### CHG-04 Charging history

*Lịch sử phiên sạc* · Must · P1.0 · Included in every plan

List past sessions for a driver, a truck or a whole organization, with filters.

**Value:** Drivers and accountants check what was charged and paid.

**Users:** Driver, Fleet manager, Accountant

**Capabilities:**

- Filters by period, station, truck and driver
- Session detail with duration, battery % and peak power

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [CHG-02](#chg-02), [CHG-07](#chg-07)  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-12 · PRD F-H2  
**Old codes:** F-H2

<a id="chg-05"></a>

### CHG-05 Energy per customer and session

*Điện năng theo khách hàng & phiên* · Must · P1.0 · Internal only

kWh consumed per transport company and per session, for invoicing and reconciliation.

**Value:** Accurate monthly billing of each customer.

**Users:** Accountant, Operations

**Capabilities:**

- kWh per organization, truck and session over a period
- Station-metered figures, not estimates

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [CHG-07](#chg-07)  
**Needed by:** [CHG-06](#chg-06), [PAY-11](billing.md#pay-11)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-C6 · Data OUT-51 · deferred.md 62  
**Old codes:** F-C6

<a id="chg-06"></a>

### CHG-06 Three-way reconciliation

*Đối soát 3 chiều* · Must · P1.0 · Internal only

Match kWh between the charger, the truck's own data and the payment; alert when they differ by 1% or more.

**Value:** Catches meter errors, fraud and billing mistakes.

**Users:** Accountant, Operations, System (automatic)

**Capabilities:**

- Per-session comparison of the three sources
- Charging-loss allowance between charger and battery
- Mismatch alerts and a reconciliation report

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [CHG-05](#chg-05), [PAY-06](billing.md#pay-06), [MON-01](telemetry.md#mon-01), [CRB-03](carbon.md#crb-03)  
**Also touches:** `telemetry`, `billing`  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-52 · NF-10

<a id="chg-07"></a>

### CHG-07 Session attribution

*Xác định xe & người của phiên sạc* · Must · P1.0 · Included in every plan

Link each session to the truck, the driver and the paying organization, and cross-check it with the truck's data.

**Value:** Unlocks per-driver, per-truck and per-customer charging reports.

**Users:** System (automatic)

**Capabilities:**

- From the QR start: user and organization
- From the driver's assignment: the truck
- Confirm with the truck's battery and position during the session

**Status:** Backend ⬜

**Depends on:** [CHG-01](#chg-01), [DRV-02](drivers.md#drv-02)  
**Needed by:** [CHG-04](#chg-04), [CHG-05](#chg-05), [CHG-08](#chg-08), [CRB-03](carbon.md#crb-03), [CRB-07](carbon.md#crb-07), [DRV-07](drivers.md#drv-07), [FLT-08](fleet.md#flt-08), [PAY-08](billing.md#pay-08), [POL-02](policy.md#pol-02)  
**Also touches:** `drivers`, `telemetry`, `identity`  
**Related tables:** — (after the database review)  
**Sources:** deferred.md 62 · Decision CO-12 · Data IN-27 · Decision CE-13

**Open questions:**

- A guest truck (not registered with us) has no vehicle record: does the app ask for its plate when the QR charge starts?

<a id="chg-08"></a>

### CHG-08 Charging cards and VIN Autocharge

*Thẻ sạc & tự động nhận xe (VIN Autocharge)* · Could · P1.5 · To be priced

Start charging with an RFID card or automatically by the truck's VIN, for prepaid enterprise customers.

**Value:** Fastest charging for large fleets, no phone needed.

**Users:** Driver, Fleet manager

**Capabilities:**

- Issue, block and assign cards to drivers or trucks
- Authorize each card or VIN at the charger

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [CHG-07](#chg-07), [PAY-07](billing.md#pay-07)  
**Also touches:** `drivers`  
**Related tables:** — (after the database review)  
**Sources:** Decision CO-12 · Decision CE-11 · Decision CO-13 · deferred.md 90

**Open questions:**

- Does the Willdigits charger support VIN Autocharge over OCPP?
