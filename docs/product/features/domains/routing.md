<!-- GENERATED from features.yaml by the feature-catalog skill. Edit the source, then regenerate; never edit this file by hand. -->

# RTE — Range & route planning

*Quãng đường & lộ trình* · [← Feature catalog](../README.md)

How far and how long a truck can still go, whether it reaches its destination, where and when to charge, and roads it may not use. Backend domain: `telemetry`.

## Checklist

- [ ] **RTE-01** [Range and driving-time forecast](#rte-01) — Backend ⬜ · App ⬜
- [ ] **RTE-02** [Range-aware trip check](#rte-02) — Backend ⬜ · App ⬜
- [ ] **RTE-03** [Navigation to a station](#rte-03) — App ⬜
- [ ] **RTE-04** [Truck-ban road and time-window warning](#rte-04) — Backend ⬜ · App ⬜
- [ ] **RTE-05** [Cost-optimal charging suggestion](#rte-05) — Backend ⬜ · App ⬜
- [ ] **RTE-06** [Forecast quality report](#rte-06) — Backend ⬜ · Portal ⬜
- [ ] **RTE-07** [Map, terrain and weather data](#rte-07) — Backend ⬜

## Features

<a id="rte-01"></a>

### RTE-01 Range and driving-time forecast

*Dự báo quãng đường & thời gian còn lại* · Should · P1.0 · To be priced

Estimate the kilometres and minutes a truck can still drive from its battery, instantaneous consumption, load, terrain, temperature and driving style, with a confidence range.

**Value:** Replaces the naive battery-% estimate that misleads drivers on hills and with heavy loads.

**Users:** Driver, System (automatic)

**Capabilities:**

- Forecast model using load, elevation (DEM), weather and driving style
- Remaining km and minutes with a confidence range
- Retrained regularly on real route data (error ≤10% p50, ≤15% p90)

**Status:** Backend ⬜ · App ⬜

**Depends on:** [MON-01](telemetry.md#mon-01), [MON-13](telemetry.md#mon-13), [RTE-07](#rte-07), [VEH-03](vehicles.md#veh-03)  
**Needed by:** [MON-03](telemetry.md#mon-03), [RTE-02](#rte-02), [RTE-05](#rte-05), [RTE-06](#rte-06), [STN-08](charging_stations.md#stn-08)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-A7 · Data IN-2 · Data IN-3 · NF-20  
**Old codes:** F-A7

<a id="rte-02"></a>

### RTE-02 Range-aware trip check

*Kiểm tra đủ pin cho hành trình* · Should · P1.0 · To be priced

Tell the driver whether the current battery is enough to finish the chosen trip or reach the chosen station, and suggest stations within range when it is not.

**Value:** Drivers plan charging stops before it is too late.

**Users:** Driver

**Capabilities:**

- Check a destination or station against the forecast range
- Suggest reachable stations

**Status:** Backend ⬜ · App ⬜

**Depends on:** [RTE-01](#rte-01), [STN-06](charging_stations.md#stn-06)  
**Needed by:** [RTE-04](#rte-04)  
**Also touches:** `charging_stations`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-D3 · Data OUT-3  
**Old codes:** F-D3

<a id="rte-03"></a>

### RTE-03 Navigation to a station

*Chỉ đường tới trạm* · Must · P1.0 · Included in every plan

Turn-by-turn directions to the chosen station, in the app or handed over to Google Maps or VietMap.

**Value:** One tap from an alert to the road.

**Users:** Driver

**Capabilities:**

- Open directions from the station screen and from battery alerts
- Hand-off to an external map app

**Status:** App ⬜

**Depends on:** [STN-06](charging_stations.md#stn-06)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-D2 · Data OUT-5  
**Old codes:** F-D2

<a id="rte-04"></a>

### RTE-04 Truck-ban road and time-window warning

*Cảnh báo đường cấm tải & khung giờ cấm* · Should · P1.1 · To be priced

Warn the driver before entering a road closed to trucks or during a banned time window, and suggest another route with its effect on charging stops.

**Value:** Avoids fines and wasted trips.

**Users:** Driver, Dispatcher

**Capabilities:**

- Truck-ban roads and time windows from the map provider
- Warning ahead of the banned section
- Alternative route and its charging impact

**Status:** Backend ⬜ · App ⬜

**Depends on:** [RTE-07](#rte-07), [RTE-02](#rte-02)  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-7 · Data IN-42 · Prerequisite 4

**Open questions:**

- Truck-ban data for Vietnam must be a mandatory criterion when choosing the map provider (prerequisite 4).

<a id="rte-05"></a>

### RTE-05 Cost-optimal charging suggestion

*Gợi ý sạc tối ưu chi phí* · Should · P1.0 · To be priced

Suggest where and when to charge at the lowest cost while still arriving on time, with the estimated saving and delay risk; the driver can always dismiss it.

**Value:** Shifts charging to cheap and green hours, saving money for customers.

**Users:** Driver, Dispatcher

**Capabilities:**

- Combine tariffs, station load forecast, battery and route forecast
- Show saving and delay risk per suggestion

**Status:** Backend ⬜ · App ⬜

**Depends on:** [RTE-01](#rte-01), [STN-08](charging_stations.md#stn-08), [PAY-09](billing.md#pay-09)  
**Also touches:** `charging_stations`, `billing`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-D6 · Data OUT-9 · NF-20  
**Old codes:** F-D6

<a id="rte-06"></a>

### RTE-06 Forecast quality report

*Báo cáo chất lượng mô hình dự báo* · Should · P1.0 · Internal only

Measure forecast errors: range (p50/p90), station load and waiting time, weekly.

**Value:** Shows whether the forecasts can be trusted and when to retrain.

**Users:** Operations

**Capabilities:**

- Compare forecasts with what actually happened
- Weekly accuracy per model

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [RTE-01](#rte-01), [STN-07](charging_stations.md#stn-07), [STN-08](charging_stations.md#stn-08)  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-63 · NF-20

<a id="rte-07"></a>

### RTE-07 Map, terrain and weather data

*Dữ liệu bản đồ, địa hình & thời tiết* · Should · P1.0 · Internal only

Connect third-party map, routing, distance, speed-limit, traffic, elevation and weather data.

**Value:** Forecasts, warnings and safety scoring need the road context.

**Users:** System (automatic)

**Capabilities:**

- Routes, ETA, distance matrix, speed limits, traffic
- Elevation along a route
- Temperature and weather by area and route

**Status:** Backend ⬜

**Needed by:** [RTE-01](#rte-01), [RTE-04](#rte-04), [SAF-01](safety.md#saf-01)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-42 · Data IN-43 · Data IN-44 · Prerequisite 4
