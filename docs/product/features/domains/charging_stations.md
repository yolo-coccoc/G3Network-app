<!-- GENERATED from features.yaml by the feature-catalog skill. Edit the source, then regenerate; never edit this file by hand. -->

# STN — Charging network

*Mạng lưới trạm sạc* · [← Feature catalog](../README.md)

Stations and chargers, their live status over OCPP, faults, the station map, queues, reservations and load. Backend domain: `charging_stations`.

## Checklist

- [ ] **STN-01** [Station directory](#stn-01) — Backend ⬜ · Portal ⬜
- [ ] **STN-02** [Charger topology](#stn-02) — Backend ⬜ · Portal ⬜
- [ ] **STN-03** [Charger connection (OCPP)](#stn-03) — Backend ⬜
- [ ] **STN-04** [Live connector status](#stn-04) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **STN-05** [Charger fault alerts](#stn-05) — Backend ⬜ · Portal ⬜
- [ ] **STN-06** [Station map and search](#stn-06) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **STN-07** [Queue and waiting time](#stn-07) — Backend ⬜ · App ⬜
- [ ] **STN-08** [Station load forecast and balancing](#stn-08) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **STN-09** [Charger reservation](#stn-09) — Backend ⬜ · App ⬜
- [ ] **STN-10** [Remote charger commands](#stn-10) — Backend ⬜ · Portal ⬜
- [ ] **STN-11** [Secure charger connections](#stn-11) — Backend ⬜ · Portal ⬜
- [ ] **STN-12** [Private stations](#stn-12) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **STN-13** [Green generation and storage data](#stn-13) — Backend ⬜ · Portal ⬜
- [ ] **STN-14** [Station energy output](#stn-14) — Backend ⬜ · Portal ⬜
- [ ] **STN-15** [Charger message log viewer](#stn-15) — Backend ⬜ · Portal ⬜

## Features

<a id="stn-01"></a>

### STN-01 Station directory

*Danh mục trạm sạc* · Must · P1.0 · Internal only

Keep every station: location, region, power, number of chargers, connector standard, opening hours, maintenance and actual operating condition, managing unit and incident contact.

**Value:** The base of the station map, alerts and energy reports.

**Users:** Operations

**Capabilities:**

- Create, edit, search and retire stations
- Managing unit and incident contact per station
- Map view of all stations

**Status:** Backend ⬜ · Portal ⬜

**Needed by:** [PAY-09](billing.md#pay-09), [STN-02](#stn-02), [STN-12](#stn-12), [STN-13](#stn-13)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-C1 · Data IN-25  
**Old codes:** F-C1

<a id="stn-02"></a>

### STN-02 Charger topology

*Cấu trúc trụ & súng sạc* · Must · P1.0 · Internal only

Describe each station's chargers and guns: identities used by the charger, power and connector standard per gun.

**Value:** Every status and session from a charger lands on the right gun.

**Users:** Operations

**Capabilities:**

- Provision chargers and guns before they connect
- Power and connector standard per gun

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [STN-01](#stn-01)  
**Needed by:** [STN-03](#stn-03)  
**Related tables:** — (after the database review)  
**Sources:** Decision CS-02 · Decision CS-03 · deferred.md 80

<a id="stn-03"></a>

### STN-03 Charger connection (OCPP)

*Kết nối trụ sạc (OCPP)* · Must · P1.0 · Internal only

Connect chargers over OCPP 1.6J and 2.0.1: boot, heartbeat, status, transactions and meter values, keeping every message.

**Value:** Works with any standard charger brand, starting with Willdigits.

**Users:** System (automatic)

**Capabilities:**

- Both protocol versions on one gateway
- Charger info, firmware and configuration captured after each boot
- Verbatim, append-only message log
- Online flag from the last message

**Status:** Backend ⬜

**Depends on:** [STN-02](#stn-02)  
**Needed by:** [CHG-02](charging_sessions.md#chg-02), [STN-04](#stn-04), [STN-10](#stn-10), [STN-11](#stn-11), [STN-15](#stn-15)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-G2 · Decision CO-01 · Decision CO-02 · Decision CO-05  
**Old codes:** F-G2

**Open questions:**

- Vendor documents still missing: implementation guide, error-code mapping, meter details, TLS/authentication (open question 4).
- Require OCPP 2.0.1 for future purchases, or 1.6J only?

<a id="stn-04"></a>

### STN-04 Live connector status

*Trạng thái súng sạc realtime* · Must · P1.0 · Included in every plan

Available, charging or faulted status and available power of every gun across the network, updated within 30 seconds.

**Value:** Drivers go only to stations that can serve them.

**Users:** Operations, Driver, System (automatic)

**Capabilities:**

- Status as reported by the charger, with error details
- Whole-charger and per-gun status in one view
- Mark status unknown when the charger goes offline

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [STN-03](#stn-03)  
**Needed by:** [STN-05](#stn-05), [STN-06](#stn-06), [STN-07](#stn-07)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-C2 · Data IN-26 · Data OUT-48 · NF-02 · deferred.md 76  
**Old codes:** F-C2

<a id="stn-05"></a>

### STN-05 Charger fault alerts

*Cảnh báo trụ sạc lỗi* · Must · P1.0 · Internal only

Alert on faulted or disconnected chargers and abnormal power, naming the station's managing unit and incident contact.

**Value:** Faults are fixed fast and the right person is called.

**Users:** Operations, System (automatic)

**Capabilities:**

- Alert on fault, disconnection and abnormal power
- Vendor error codes translated to readable causes
- Route the alert to the station's contact

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [STN-04](#stn-04), [NTF-01](notifications.md#ntf-01)  
**Also touches:** `notifications`  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-49 · Decision CS-08 · deferred.md 75

<a id="stn-06"></a>

### STN-06 Station map and search

*Bản đồ & tìm trạm sạc* · Must · P1.0 · Included in every plan

Find nearby stations on a map, filtered by availability, power and connector standard.

**Value:** Drivers find a free charger quickly.

**Users:** Driver, Dispatcher

**Capabilities:**

- Nearest stations within a radius
- Filters: available now, minimum power, connector standard
- Available guns count and current price per station

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [STN-04](#stn-04)  
**Needed by:** [DRV-05](drivers.md#drv-05), [MON-03](telemetry.md#mon-03), [MON-04](telemetry.md#mon-04), [MON-18](telemetry.md#mon-18), [RTE-02](routing.md#rte-02), [RTE-03](routing.md#rte-03)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-D1 · Data OUT-5  
**Old codes:** F-D1

<a id="stn-07"></a>

### STN-07 Queue and waiting time

*Hàng đợi & thời gian chờ tại trạm* · Should · P1.0 · To be priced

Show how many trucks are waiting, how many are expected within 60 minutes and the estimated wait until a gun frees up; warn when the wait is too long.

**Value:** Drivers avoid queues and re-plan in time.

**Users:** Driver, System (automatic)

**Capabilities:**

- Waiting and arriving trucks per station
- Estimated wait from session durations (median/p90 per gun, power and hour)
- Warning above a threshold, with alternative stations

**Status:** Backend ⬜ · App ⬜

**Depends on:** [STN-04](#stn-04), [MON-02](telemetry.md#mon-02), [CHG-02](charging_sessions.md#chg-02)  
**Needed by:** [RTE-06](routing.md#rte-06), [STN-08](#stn-08), [STN-09](#stn-09)  
**Also touches:** `telemetry`, `charging_sessions`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-C3 · Data IN-28 · Data IN-29 · Data OUT-6  
**Old codes:** F-C3

<a id="stn-08"></a>

### STN-08 Station load forecast and balancing

*Dự báo tải trạm & phân tải* · Must · P1.0 · To be priced

Forecast how many trucks will arrive at each station against its free guns, warn drivers heading to an overloaded station within 60 minutes and suggest at least one alternative in range.

**Value:** Spreads demand across the network; shorter waits, better charger use.

**Users:** Driver, Operations, System (automatic)

**Capabilities:**

- Arrival forecast from position, battery, heading and history
- Fill rate per charger; recommendations to operations
- Driver warning with an alternative station

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [STN-07](#stn-07), [RTE-01](routing.md#rte-01)  
**Needed by:** [RTE-05](routing.md#rte-05), [RTE-06](routing.md#rte-06)  
**Also touches:** `telemetry`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-C7 · Data IN-28 · Data OUT-53 · NF-20  
**Old codes:** F-C7

<a id="stn-09"></a>

### STN-09 Charger reservation

*Đặt chỗ sạc* · Could · P1.5 · To be priced

Reserve a gun for a time slot and cancel it; a no-show penalty applies.

**Value:** Guaranteed charging for time-critical trips.

**Users:** Driver, Dispatcher

**Capabilities:**

- Reserve and cancel based on the expected wait
- Hold the gun on the charger during the slot
- Configurable no-show penalty

**Status:** Backend ⬜ · App ⬜

**Depends on:** [STN-07](#stn-07), [STN-10](#stn-10), [PAY-06](billing.md#pay-06)  
**Also touches:** `billing`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-C4 · Data IN-54  
**Old codes:** F-C4

**Open questions:**

- What is the no-show penalty?

<a id="stn-10"></a>

### STN-10 Remote charger commands

*Điều khiển trụ sạc từ xa* · Must · P1.0 · Internal only

Send commands to chargers: start and stop a charge, unlock a connector, reset, change configuration, fetch diagnostics.

**Value:** Needed to start charging from the app and to fix chargers without a site visit.

**Users:** Operations, System (automatic)

**Capabilities:**

- Remote start and stop
- Unlock, reset, change configuration, diagnostics
- Every command and its answer logged

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [STN-03](#stn-03)  
**Needed by:** [CHG-01](charging_sessions.md#chg-01), [STN-09](#stn-09)  
**Related tables:** — (after the database review)  
**Sources:** Decision CO-12 · deferred.md 74

<a id="stn-11"></a>

### STN-11 Secure charger connections

*Kết nối trụ sạc an toàn* · Must · P1.0 · Internal only

Encrypt charger connections and authenticate each charger before real deployment.

**Value:** No fake charger can report sessions or receive commands.

**Users:** Operations

**Capabilities:**

- Encrypted connection (wss://)
- Per-charger password or client certificate

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [STN-03](#stn-03)  
**Related tables:** — (after the database review)  
**Sources:** NF-05 · Decision IS-05 · deferred.md 73

<a id="stn-12"></a>

### STN-12 Private stations

*Trạm sạc riêng* · Should · P1.0 · To be priced

Stations that are not public (for example at a mine or a customer's depot) are visible and usable only by the organizations allowed, with their own pricing.

**Value:** Serve customers who own or host chargers without opening them to everyone.

**Users:** Operations, Sales

**Capabilities:**

- Public or private station; allowed organizations
- Separate tariff for a private station

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [STN-01](#stn-01), [PAY-09](billing.md#pay-09)  
**Also touches:** `billing`  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-10

**Open questions:**

- How are private (mine, non-public) chargers billed? The data sheet says 'separate scheme'.

<a id="stn-13"></a>

### STN-13 Green generation and storage data

*Dữ liệu nguồn phát xanh & BESS* · Should · P1.0 · Internal only

Record each station's power sources (grid, solar, wind, biomass, battery storage), their hourly output, storage charge level and forecasts.

**Value:** Basis for green pricing, green charging labels and carbon reports.

**Users:** Operations, System (automatic)

**Capabilities:**

- Generation sources per station
- Hourly output, storage charge level, output forecast

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [STN-01](#stn-01)  
**Needed by:** [CRB-02](carbon.md#crb-02), [CRB-10](carbon.md#crb-10), [PAY-09](billing.md#pay-09)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-31

<a id="stn-14"></a>

### STN-14 Station energy output

*Sản lượng điện theo trạm* · Should · P1.0 · Internal only

kWh sold per station and per time slot, for time-of-use pricing.

**Value:** Shows which stations and hours earn money, to set prices.

**Users:** Operations, Head administrator (internal)

**Capabilities:**

- Total kWh and sessions per station over a period
- Hourly and daily series
- Ranking of all stations

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [CHG-02](charging_sessions.md#chg-02)  
**Also touches:** `charging_sessions`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-C5 · Data OUT-50 · deferred.md 77  
**Old codes:** F-C5

<a id="stn-15"></a>

### STN-15 Charger message log viewer

*Xem nhật ký bản tin trụ sạc* · Could · P1.1 · Internal only

Let operations read a charger's raw messages for troubleshooting, with card IDs masked, and delete old messages after a retention period.

**Value:** Charger problems are diagnosed without database access.

**Users:** Operations

**Capabilities:**

- Read messages by charger and time
- Mask card IDs and other personal data
- Retention period for raw messages

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [STN-03](#stn-03)  
**Related tables:** — (after the database review)  
**Sources:** Decision IS-07 · deferred.md 79
