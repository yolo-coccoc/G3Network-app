# How other CSMSs model charging: open source and commercial

> Part of the `ocpp-reference` skill. A survey of how widely used systems name
> their levels and where they put access rules, tariffs, credentials and power
> limits, so G3's model can follow industry practice. Researched 2026-10-05
> from each product's own docs, API specs or source code. ✅ product's own
> docs/API/source · 🔎 secondary or inference. Products change: re-check a
> source before copying a detail.

## Standards everyone maps to
- **OCPP 2.0.1:** Charging Station (= one OCPP connection, our *charger*) → EVSE → Connector. In OCPP 1.6 it's Charge Point → Connector, with connector 0 = the whole charger ✅. `IdTokenInfo.groupIdToken` lets several tokens count as one, for example a company's cards ✅. Details in `ocpp201.md`.
- **OCPI:** Location → EVSE → Connector, **with no charger level** ✅.
  - Private visibility uses `publish` + `publish_allowed_to`.
  - Business parties are `operator` / `suboperator` / `owner`.
  - `tariff_ids` sit on the Connector.
  - 2.3.0 adds truck parking fields.
  - Details in `ocpi.md`.
- **EU AFIR / eMI3:** recharging **pool** (one place) → recharging **station** (a physical installation, our *charger*) → recharging **point** (one vehicle at a time) ✅.

**In OCPP, AFIR, CitrineOS and Kempower, "charging station" means the charger, not the place.**

## Open source
| System | Hierarchy | Access / tenancy | Tariffs | Identification |
|---|---|---|---|---|
| **SteVe** (OCPP 1.2–1.6) ✅ | `charge_box` → `connector`; optional address, **no place level** | single operator | none | `ocpp_tag` (`id_tag`, `parent_id_tag`, expiry, max active transactions), optional user |
| **CitrineOS** (LF Energy; 2.0.1 + 1.6) ✅ | Tenant → **Location** ("charging pool") → ChargingStation (`ocppConnectionName`, unique per tenant) → Evse (`evseTypeId` = OCPP number, `evseId` = eMI3 ID, `physicalReference`) → Connector (keeps both the 1.6 connectorId and the 2.0.1 per-EVSE number) | `tenantId` on every row; TenantPartner = roaming party | `Tariff` (kWh/min/session, idle, tax, `authorizationAmount`) **FK on Connector**, optionally on an Authorization | `Authorization`: idToken + type, `groupAuthorizationId`, `chargingPriority`, `isPrepaid` + `prepaidBalance`, `allowedConnectorTypes` |
| **MaEVe** (Thoughtworks) ✅ | OCPI-shaped Location → Evse → Connector, kept apart from `ChargeStation` (auth, settings, certificates) | single operator | none | OCPI Token; built for ISO 15118 Plug & Charge |
| **Open e-Mobility** (SAP Labs, **archived Jan 2025**) ✅ | Tenant → Company → **Site → SiteArea** (nestable) → ChargingStation → Connector | `public` on Site and on ChargingStation; users linked per site (admin, owner); `accessControl` on SiteArea | **PricingDefinition on Tenant, Company, Site, SiteArea or ChargingStation; the most specific wins.** Dimensions: flat, energy, charging time, parking time | `Tag` (RFID) → user; OCPI token |
| **EVerest** 🔎 | runs **on the charger** (OCPP client, ISO 15118, local energy manager); not a CSMS | – | – | – |

Open e-Mobility 🔎: SiteArea carries `maximumPower` and `smartCharging`, and either Site or SiteArea can be published as the OCPI Location.

## Commercial
**AMPECO** ✅ (public OpenAPI)
- **Hierarchy:** **Location → Charging Zone (optional) → Charge Point → EVSE → Connector.** The Location is the point of interest, with working hours that can be limited to user groups.
- **Charge Point:**
  - `type` public | private | personal; `operatorId`.
  - A partner with `accessType` (`private_view_private_use | private_view_public_use | public_view_private_use`) and `corporateBillingAsDefault`.
  - `autoStartWithoutAuthorization` is a free-vend mode, not ISO 15118 Plug & Charge.
- **EVSE:** `networkId` (OCPP number), `physicalReference`, **`tariffGroupId`**, `maxPower`.
- **Tariffs:**
  - Types: free, flat, duration+energy, energy time-of-use, peak-power levels, dynamic. Fees: `idleFeePerMinute` + `idleFeeGracePeriodMinutes`, `connectionFee`.
  - **Who a tariff applies to:** `applyToUserGroupIds`, `applyToUsersOfChargePointPartner`, `applyToUsersWithSubscriptions`, `applyToAdHocUsers`, `applyToAuthorizationMethods` (id_tag | mac_address | user_device | plug_and_charge).
  - A **Tariff Group** bundles tariffs and attaches to an EVSE.
  - Sessions snapshot their tariff.
- **Other objects:** Partners (site hosts, fleets, sub-operators) and Partner Contracts; User Groups; pre- and post-paid Subscriptions.
- **ID Tags:** "RFID card or MAC address".
- **Load management:** **Circuits**, groups of charge points for load balancing, in multiple parent/child levels.

**Monta** ✅ (Partner API)
- **Hierarchy:** Operator → **Team** (nestable) → **Site** (`maxKw`, `visibility` public/private) → **Charge Point** (`siteId`, `visibility`, `maxKw`, **one `evseId` each**, so a Monta charge point ≈ an EVSE) → connectors.
- **Access:** a private charge point is used only by members of the owning team.
- **Price groups** per team/site:
  - Types: Public, Member, Sponsored, Roaming, Cost.
  - A charge point carries `priceGroupId`, `roamingPriceGroupId`, `sponsoredPriceGroupId`, `costGroupId`; member prices are set per team member.
- **Auth tokens:** `type` rfid | vehicleId (Autocharge style), with team, user, vehicle, `activeUntil`, `blockedAt` and network flags.
- **Other objects:** wallets, plans, vehicles.

**Kempower ChargEye** ✅ (truck and bus depots)
- **Hierarchy:** **Location** ("a physical area, like a parking lot, where multiple Charging Stations are installed") → **Charging Station** (one OCPP connection; for satellite systems, power units + satellites together) → connectors (`maxPowerKw`, `type`).
- **Load balancing:** a **Power Group tree** separate from the Location (`powerGroupParentPath`, `actualSiteFuseSize`, `dynamicMaxCurrentA`, `defaultMinPowerReservationKw`, `defaultOfflinePowerKw`).
- **Vehicles API:** charging-port MAC addresses (Autocharge), consumption, departure schedules, per-transaction scheduling priority, and a telematics feed (SoC, position).

**ChargePoint** (API guide behind a login)
- **Hierarchy:** Organization → Station Group → Station → Port 🔎.
- **Access:** **Driver Groups** get **Access Policies** and **Pricing Policies** ✅.
- **Pricing:** time-of-use windows with per-hour, per-kWh and per-session prices, plus min/max price 🔎.
- **Load management:** load shedding per port, station or group; "circuit sharing" 🔎.

**Too little public documentation** 🔎:
- **Driivz:** locations and groups, fleet billing for depot/public/home, price plans in kW, kWh, minutes, TOU and parking.
- **Virta:** docs under NDA; station groups with group prices, private/public.
- **Etrel Ocean:** Sub-CPO portal, Fleet portal, and a tariff configurator with "15 criteria".
- **Everon (EVBox):** shut down on 1 Dec 2025.

**Vietnam** 🔎:
- **V-GREEN:** trạm sạc (station) → trụ sạc (charger) → cổng sạc (port). **The QR code is on the charger**, and the app then asks which port.
- **EBOOST:** **one QR code per outlet**, plus an "Auto Charging" start right after the scan.

## Hierarchy terms compared
| G3 word | OCPI | AFIR/eMI3 | OCPP | CitrineOS | AMPECO | Open e-Mobility | Monta | Kempower | ChargePoint |
|---|---|---|---|---|---|---|---|---|---|
| organization | operator/owner (details) | – | – | Tenant | Operator / Partner | Tenant / Company | Operator / Team | tenant | Organization |
| site (khu sạc) | – | – | – | – | (Location) | Site | Team / Site | Power Group (electrical only) | Station Group |
| station (trạm, the place) | **Location** | recharging **pool** | – | Location | Location (+ Charging Zone) | SiteArea | Site | Location | – |
| charger (trụ, one OCPP connection) | – | recharging **station** | **Charging Station** / Charge Point | ChargingStation | Charge Point | ChargingStation | – | Charging Station | Station |
| EVSE | EVSE | recharging point | EVSE | Evse | EVSE | (connector) | Charge Point | – | Port |
| connector (súng) | Connector | connector | Connector | Connector | Connector | Connector | connector | connector | – |

## Common patterns
1. **Place → OCPP device → EVSE → connector.** Everyone except SteVe has the place level.
2. **A level above the place is common**, but it's an ownership, contract or electrical grouping, not a map pin. Open e-Mobility (Site → SiteArea), AMPECO (Location → Charging Zone) and Monta (Team → Site) all have **two levels above the charger**.
3. **Load management is its own tree** (AMPECO Circuits, Kempower Power Groups, ChargePoint groups and circuits), separate from the place tree. Open e-Mobility, which puts `maximumPower` on SiteArea, is the exception.
4. **Public/private is set on the place or the device; who may charge is decided by groups** of users or tokens: AMPECO partner access + user groups, Monta team members, ChargePoint driver groups, OCPI `publish_allowed_to` + `group_id`, OCPP `groupIdToken`.
5. **Tariffs attach to the EVSE or connector** (OCPI, CitrineOS, AMPECO) **or are inherited down the tree, most specific wins** (Open e-Mobility). **The customer's group chooses the tariff** (AMPECO user groups, Monta Member vs Public, ChargePoint pricing policies). Everyone has energy, time, flat and **idle/parking** components with time-of-use restrictions.
6. **One credential table with a type column** covers app, RFID, MAC Autocharge and eMAID, with a group link, expiry or blocked fields, and an owner (user, team or vehicle). A prepaid balance belongs to the account's wallet, not the token; CitrineOS's `prepaidBalance` on a token is a shortcut to avoid 🔎.
7. **Sessions snapshot or reference the tariff they used** (AMPECO snapshot, CitrineOS `Transaction.tariffId`).

## Lessons for G3
1. **Naming:** "charging station" means the *charger* in OCPP, AFIR and several products, but the *place* in Vietnamese usage ("trạm"), in the G3 feature catalog and for V-GREEN. Whatever G3 picks, write the mapping into the table notes. 🔎 One option is the CitrineOS/OCPI naming (`charging_locations` for the place, `charging_stations` for the charger); the other follows the product language (`charging_stations` for the place, `chargers` for the charger). G3's decision is in the decision log (CS area).
2. **The place drivers go to = one OCPI Location.** It carries the map pin, address, opening hours, time zone, publish flag and truck-parking attributes 🔎.
3. **Site power limits** belong in a separate, possibly multi-level, load-group entity that chargers point to, not a single `max_power_kw` on the site. A site may have several transformers, and a station may share a feeder 🔎.
4. **Access:** a public/private flag on the place, optionally overridden per charger; allowed organizations in a separate table (place or site × organization), optionally with time windows 🔎.
5. **Tariffs:**
   - Attach them at the EVSE, with a default inherited from the station or site.
   - Choose them by customer organization or group.
   - Use OCPI-style components with idle fees and grace minutes, and TOU restrictions.
   - Snapshot the tariff onto the session.
6. **The QR code** should point at a stable public EVSE code (eMI3-style `VN*<op>*E…` or a `physical_reference`), never an internal UUID, so a replaced charger keeps its sticker. One code per EVSE makes a one-step start; one per charger forces the driver to pick a port 🔎.
7. **Credentials:** one table with a type (Central/app, ISO14443 RFID, MacAddress Autocharge, eMAID), a group, an owner (user or vehicle), expiry and blocked fields 🔎. For G3 this is deferred (deferred.md 90, CO-13: launch is QR only).
8. **Trucks:**
   - OCPI 2.3 parking fields (SEMI_TRACTOR, TRUCK_WITH_TRAILER, max weight and length, drive-through).
   - Long dwell: idle fee, grace period, possibly booking.
   - Depot scheduling: departure time, priority (Kempower).
   - **MCS (megawatt charging)** uses ISO 15118-20 with OCPP 2.1, so keep the protocol per charger.
   - One OCPP "station" can be a power cabinet with several dispensers (Kempower), so a charger is not necessarily a single post. Keep `physical_reference` on the EVSE.

## Sources
- OCPI: https://github.com/ocpi/ocpi (mod_locations, mod_tokens) · https://evroaming.org/wp-content/uploads/2025/02/OCPI-2.3.0.pdf
- AFIR Regulation (EU) 2023/1804: https://eur-lex.europa.eu/eli/reg/2023/1804/oj/eng
- SteVe: https://github.com/steve-community/steve (db/migration `B1_0_5__stevedb.sql`)
- CitrineOS: https://github.com/citrineos/citrineos-core/tree/main/packages/dal/src/models · https://lfenergy.org/projects/citrineos/
- MaEVe: https://github.com/thoughtworks/maeve-csms/tree/main/manager/store
- Open e-Mobility: https://github.com/sap-labs-france/ev-server/tree/master/src/types
- AMPECO: https://developers.ampeco.com/docs/main-players · /docs/models-relationship · /reference/chargepointcreate · /reference/evsecreate · /reference/tariffcreate · https://www.ampeco.com/blog/ampecos-multi-level-dlm-addressing-grid-complexity-with-precision/
- Monta: https://developer.monta.com/docs/price-groups · https://monta-partner-api.readme.io/reference/get-sites-1.md · /get-charge-points-1.md · /get-charge-auth-tokens-1.md
- Kempower ChargEye: https://docs.kempower.io/
- ChargePoint: https://volttron.readthedocs.io/en/releases-8.x/driver-framework/chargepoint/chargepoint-specification.html · https://www.chargepoint.com/trainingvideos/summary/ChargePoint_Connections_2.pdf
- Driivz, Virta, Etrel: product pages (marketing only) · Everon shutdown: https://electrek.co/2025/07/11/blink-charging-just-threw-a-lifeline-to-evbox-everon-customers/
- MCS: https://www.charin.global/technology/mcs/
- Vietnam: https://vinfastauto.com/vn_vi/cach-sac-pin-xe-may-dien-vinfast · https://eboost.vn/en/user-guide/
