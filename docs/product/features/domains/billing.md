<!-- GENERATED from features.yaml by the feature-catalog skill. Edit the source, then regenerate; never edit this file by hand. -->

# PAY — Plans, payments & invoices

*Gói dịch vụ, thanh toán & hóa đơn* · [← Feature catalog](../README.md)

What customers buy (features, plans, add-ons), how they pay for charging and subscriptions, tariffs and e-invoices. Backend domain: `billing`.

## Checklist

- [ ] **PAY-01** [Feature catalog and list prices](#pay-01) — Backend ⬜ · Portal ⬜
- [ ] **PAY-02** [Service plans](#pay-02) — Backend ⬜ · Portal ⬜
- [ ] **PAY-03** [Customer subscriptions](#pay-03) — Backend ⬜ · Portal ⬜
- [ ] **PAY-04** [Add-ons](#pay-04) — Backend ⬜ · Portal ⬜
- [ ] **PAY-05** [Self-service plan purchase](#pay-05) — Backend ⬜ · App ⬜
- [ ] **PAY-06** [In-app charging payment](#pay-06) — Backend ⬜ · App ⬜
- [ ] **PAY-07** [Prepaid wallet](#pay-07) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **PAY-08** [Fleet centralized billing](#pay-08) — Backend ⬜ · Portal ⬜
- [ ] **PAY-09** [Dynamic tariffs](#pay-09) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **PAY-10** [Tariff log per session](#pay-10) — Backend ⬜
- [ ] **PAY-11** [E-invoicing](#pay-11) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **PAY-12** [Promotion campaigns](#pay-12) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **PAY-13** [Platform revenue report](#pay-13) — Backend ⬜ · Portal ⬜

## Features

<a id="pay-01"></a>

### PAY-01 Feature catalog and list prices

*Danh mục tính năng & giá niêm yết* · Must · P1.0 · Internal only

Keep the sellable features with their list price and pricing unit (per truck per month, per organization, per user, per message).

**Value:** Sales change prices and offers without developers.

**Users:** Sales, Head administrator (internal)

**Capabilities:**

- List price and unit per feature
- Price changes with an effective date and history

**Status:** Backend ⬜ · Portal ⬜

**Needed by:** [PAY-02](#pay-02)  
**Related tables:** — (after the database review)  
**Sources:** Decision BL-01

<a id="pay-02"></a>

### PAY-02 Service plans

*Gói dịch vụ* · Must · P1.0 · Internal only

Bundle features into plans: public plans for most customers, private plans for one customer, and a free default plan for individuals.

**Value:** New offers are data, not code; fits both small and large customers.

**Users:** Sales, Head administrator (internal)

**Capabilities:**

- Create plans from catalog features
- Public, private and default plans
- Basic plan (compliant driver camera + vehicle, battery and charging management) and Advanced plan (in-cab ADAS)

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [PAY-01](#pay-01)  
**Needed by:** [ACC-07](identity.md#acc-07), [ACC-14](identity.md#acc-14), [PAY-03](#pay-03)  
**Related tables:** — (after the database review)  
**Sources:** Decision BL-02 · Decision BL-03 · Data IN-36 · Prerequisite 7

<a id="pay-03"></a>

### PAY-03 Customer subscriptions

*Thuê bao của khách hàng* · Should · P1.0 · Internal only

Give an organization a plan with its agreed price, billing cycle and period; remind before expiry, invoice each cycle and lock features when overdue.

**Value:** Recurring SaaS revenue collected on time.

**Users:** Sales, Accountant, Organization administrator

**Capabilities:**

- Agreed price copied onto the subscription
- Per-truck-per-month billing cycles
- Expiry reminders, feature lock on overdue
- Customers see their plan and usage in the portal

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [PAY-02](#pay-02), [ACC-01](identity.md#acc-01)  
**Needed by:** [ACC-08](identity.md#acc-08), [PAY-04](#pay-04), [PAY-05](#pay-05), [PAY-13](#pay-13)  
**Also touches:** `identity`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-H4 · Data IN-36 · Decision BL-01  
**Old codes:** F-H4

**Open questions:**

- Plan prices and annual-plan terms are not decided.

<a id="pay-04"></a>

### PAY-04 Add-ons

*Tiện ích mua thêm* · Should · P1.0 · Internal only

Sell single features on top of a plan, such as the in-cab ADAS package or an SMS quota, each with its agreed price.

**Value:** Customers pay only for extras they need.

**Users:** Sales, Organization administrator

**Capabilities:**

- Add or remove an add-on on a subscription
- Usage quotas (e.g. SMS per month)

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [PAY-03](#pay-03)  
**Related tables:** — (after the database review)  
**Sources:** Decision BL-01 · Decision BL-05 · Prerequisite 7

<a id="pay-05"></a>

### PAY-05 Self-service plan purchase

*Tự mua gói trên app* · Could · P1.1 · Included in every plan

Individual customers buy or upgrade a plan in the app and pay online.

**Value:** Retail customers convert without talking to sales.

**Users:** Driver, Organization administrator

**Capabilities:**

- Compare plans and buy in the app
- Online payment and automatic activation

**Status:** Backend ⬜ · App ⬜

**Depends on:** [PAY-03](#pay-03), [PAY-06](#pay-06)  
**Related tables:** — (after the database review)  
**Sources:** Decision BL-04

<a id="pay-06"></a>

### PAY-06 In-app charging payment

*Thanh toán sạc trên app* · Must · P1.0 · Included in every plan

Pay for charging in the app with VNPay, Momo or the wallet, without storing card data.

**Value:** Scan, charge, pay in three steps; no cash at stations.

**Users:** Driver

**Capabilities:**

- Payment gateways with tokenized cards
- Payment status and gateway reconciliation codes
- Refunds

**Status:** Backend ⬜ · App ⬜

**Depends on:** [CHG-01](charging_sessions.md#chg-01), [CHG-03](charging_sessions.md#chg-03)  
**Needed by:** [CHG-06](charging_sessions.md#chg-06), [PAY-05](#pay-05), [PAY-07](#pay-07), [PAY-13](#pay-13), [STN-09](charging_stations.md#stn-09)  
**Also touches:** `charging_sessions`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-H1 · Data IN-45 · NF-05  
**Old codes:** F-H1

<a id="pay-07"></a>

### PAY-07 Prepaid wallet

*Ví trả trước* · Should · P1.0 · Included in every plan

A prepaid wallet for a driver or a fleet: top up, withdraw, pay for charging, with the transaction history.

**Value:** Faster payment and prepaid revenue.

**Users:** Driver, Fleet manager, Accountant

**Capabilities:**

- Top-up and withdrawal within legal limits
- Balance always reconciles with sessions
- Deposit handling (e.g. 300k deposit)

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [PAY-06](#pay-06)  
**Needed by:** [CHG-08](charging_sessions.md#chg-08)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-H2 · Data IN-45 · Data OUT-12  
**Old codes:** F-H2

**Open questions:**

- Deposit rule: 300k deposit, and what happens on withdrawal? (data sheet note)

<a id="pay-08"></a>

### PAY-08 Fleet centralized billing

*Thanh toán tập trung cho đội xe* · Must · P1.0 · To be priced

The company pays for its drivers' charging and receives one monthly invoice with kWh and cost broken down per truck.

**Value:** Drivers never pay out of pocket; companies get one clean invoice.

**Users:** Accountant, Fleet manager

**Capabilities:**

- Charge the company for sessions started by its drivers
- Monthly consolidated invoice per truck

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [CHG-07](charging_sessions.md#chg-07), [PAY-11](#pay-11)  
**Also touches:** `charging_sessions`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-H2 · PRD F-H3 · Data OUT-35  
**Old codes:** F-H2, F-H3

**Open questions:**

- Who pays when a fleet driver charges: the driver or the company? (open decision D5)

<a id="pay-09"></a>

### PAY-09 Dynamic tariffs

*Biểu giá điện động* · Should · P1.0 · Included in every plan

Prices by time of day and generation source (grid, solar, wind, biomass, storage), versioned with effective dates; the app shows the current price and the cheapest window of the day per station.

**Value:** Moves charging to cheap and green hours.

**Users:** Operations, Driver

**Capabilities:**

- Tariff versions per station and time slot
- Current price and cheapest window in the app
- Price shown at session start = price billed (100%)

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [STN-01](charging_stations.md#stn-01), [STN-13](charging_stations.md#stn-13)  
**Needed by:** [DRV-07](drivers.md#drv-07), [MON-14](telemetry.md#mon-14), [PAY-10](#pay-10), [RTE-05](routing.md#rte-05), [STN-12](charging_stations.md#stn-12)  
**Also touches:** `charging_stations`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-C8 · Data IN-30 · Data OUT-8 · NF-19  
**Old codes:** F-C8

**Open questions:**

- Who owns the tariff (G3 Energy or G3 Network), and how large should the peak/off-peak gap be?

<a id="pay-10"></a>

### PAY-10 Tariff log per session

*Log biểu giá áp dụng cho từng phiên* · Must · P1.0 · Internal only

Record the tariff version and unit price applied to each session, kept at least 5 years.

**Value:** Every amount billed can be explained years later.

**Users:** Accountant, System (automatic)

**Capabilities:**

- Tariff version and price stored on the session
- Kept ≥5 years

**Status:** Backend ⬜

**Depends on:** [PAY-09](#pay-09), [CHG-02](charging_sessions.md#chg-02)  
**Needed by:** [CHG-03](charging_sessions.md#chg-03)  
**Also touches:** `charging_sessions`  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-54 · NF-19

<a id="pay-11"></a>

### PAY-11 E-invoicing

*Hóa đơn điện tử* · Must · P1.0 · Included in every plan

Issue legal Vietnamese e-invoices through a licensed provider: per retail session and monthly per company; track invoice status and lookup code.

**Value:** Legal compliance and customers can claim VAT.

**Users:** Accountant, Driver, System (automatic)

**Capabilities:**

- Integration with a licensed e-invoice provider
- Retail and monthly consolidated invoices
- Invoice number, status and lookup code
- Reconciles with energy per customer

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [CHG-05](charging_sessions.md#chg-05), [ACC-01](identity.md#acc-01)  
**Needed by:** [PAY-08](#pay-08)  
**Also touches:** `charging_sessions`, `identity`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-H3 · Data IN-46 · Data OUT-11 · NF-10  
**Old codes:** F-H3

<a id="pay-12"></a>

### PAY-12 Promotion campaigns

*Chiến dịch ưu đãi* · Should · P1.1 · Internal only

Create promotions (discounts, offers, maintenance deals) and send them to customers who agreed to receive marketing.

**Value:** Drives usage and loyalty.

**Users:** Sales, Driver

**Capabilities:**

- Campaign with audience, period and offer
- Sent only with marketing consent (opt-in/opt-out designed with this feature)
- Apply discounts to charging or services

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [ACC-17](identity.md#acc-17), [NTF-05](notifications.md#ntf-05)  
**Also touches:** `notifications`, `identity`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-F4 · Data OUT-15  
**Old codes:** F-F4

**Open questions:**

- Details of promotions to be specified (Hùng Võ).

<a id="pay-13"></a>

### PAY-13 Platform revenue report

*Báo cáo doanh thu nền tảng* · Should · P1.0 · Internal only

Revenue from charging through the app and from subscriptions per truck per month, split by plan, for G3 Energy and G3 Network.

**Value:** Management sees how the business grows.

**Users:** Head administrator (internal), Accountant

**Capabilities:**

- Energy revenue and subscription revenue by period
- Split by plan and by company

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [PAY-03](#pay-03), [PAY-06](#pay-06)  
**Needed by:** [PLT-06](platform.md#plt-06)  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-62 · PRD F-H4
