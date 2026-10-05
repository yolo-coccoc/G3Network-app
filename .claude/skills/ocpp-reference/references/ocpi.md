# OCPI: the roaming vocabulary (2.2.1, with 2.3.0 changes)

> Part of the `ocpp-reference` skill. G3 does no roaming today; this file is
> here so tables, enums and IDs stay mappable to OCPI. Researched 2026-10-05
> from the official asciidoc sources (`github.com/ocpi/ocpi`, branches
> `release-2.2.1-bugfixes`, `release-2.3.0-bugfixes`). ✅ read in the spec ·
> 🔎 secondary or industry practice.

## Versions
- **2.2.1** (2021): what most of the market runs 🔎. Edition 2.2.1-d2 mostly rewrote the tariff text.
- **2.3.0** (2025-02-21; ed2 plus the separate Payments and Bookings modules, June 2026) ✅:
  - AFIR parking data and vehicle types; connector types `MCS` and `SAE_J3400`; Plug & Charge capabilities; token type `EMAID`.
  - Taxes: `Price` → `before_taxes` + `taxes[]`; `Tariff.tax_included`.
  - `Location.help_phone`; open enums (accept unknown values); `HUB` dropped from the Role enum.
- **3.0**: draft only, target mid-2027, to become a CENELEC standard 🔎.

## Roles ✅
| Role | Meaning | Typical direction |
|---|---|---|
| CPO | Operates charge points (G3) | Sends Locations, Tariffs, Sessions, CDRs; receives Tokens, Commands, ChargingProfiles |
| eMSP | Gives drivers access (app, cards) | Mirror of the CPO |
| Hub | Connects many CPOs and eMSPs | Both |
| NSP / NAP | Navigation service / national access point | Read Locations and Tariffs |
| SCSP | Smart-charging service provider | Sends ChargingProfiles |

- A **platform** can hold several roles: G3 with its own driver app is CPO + eMSP.
- Parties are identified by `country_code` (2) + `party_id` (3): for G3, `VN` + e.g. `G3N` 🔎. No Vietnamese ID office is known.
- Only the Versions and Credentials modules are mandatory.

## Locations: Location → EVSE → Connector ✅

**There is no charger (OCPP charging station) level, and 2.3.0 still has none.** The spec's "Group of Charge Points" section prefers **one Location for a group of chargers at the same place**, because drivers care how many EVSEs are free.
- Split one place into several Locations when its EVSEs are hard to find from one point.
- Split it into two when only some EVSEs may be published.

**For OCPP 1.6, each connector becomes a virtual EVSE**, and in the Commands module the EVSE ID maps to the connector ID. G3's "gun n = EVSE n" (CS-03) already does this.

**G3 mapping 🔎:** station → Location. Charger and site stay internal. `operator` / `suboperator` / `owner` (BusinessDetails) carry company information.

**Location** ✅ ((1) = required)
| Group | Fields |
|---|---|
| Identity | `country_code`, `party_id`, `id` (CiString(36), never changes) |
| Visibility | `publish` (1); `publish_allowed_to[]` (token uid/type/visual_number/issuer/group_id), only when `publish=false`. Private sites that need no remote control are not sent at all. |
| Address | `name`, `address` (1, ≤255), `city` (1), `postal_code`, `state`, `country` (1, **ISO alpha-3, `VNM`**) |
| Place | `coordinates` (1; lat/long as **strings**, 5–7 decimals), `related_locations`, `parking_type` (ALONG_MOTORWAY, PARKING_GARAGE, PARKING_LOT, ON_DRIVEWAY, ON_STREET, UNDERGROUND_GARAGE), `directions`, `facilities[]`, `images`, `energy_mix` |
| Time | `time_zone` (1, IANA: `Asia/Ho_Chi_Minh`); `opening_times` (see below); `charging_when_closed` (default true) |
| Sync | `last_updated` (1) |

`opening_times` (Hours) has `twentyfourseven`, `regular_hours[]` (weekday 1 = Mon … 7 = Sun, local "HH:MM" begin/end) and `exceptional_openings[]` / `exceptional_closings[]` (UTC periods).

**EVSE** ✅
- **Keys:**
  - `uid`: CiString(36), the technical key, never changes. **Never a hardware ID**, because hardware gets redeployed while EVSEs are never deleted.
  - `evse_id`: CiString(48), optional, the human-readable eMI3 ID `<country>*<operator>*E<id>`, e.g. `BE*BEC*E041503001`.
- **Status:** `status` (1) is one of AVAILABLE, BLOCKED, CHARGING, INOPERATIVE, OUTOFORDER, PLANNED, REMOVED, RESERVED, UNKNOWN (also used for offline). `status_schedule[]` holds planned changes.
- **Capabilities:** `capabilities[]` includes REMOTE_START_STOP_CAPABLE, RFID_READER, RESERVABLE, UNLOCK_CAPABLE, CHARGING_PROFILE_CAPABLE, START_SESSION_CONNECTOR_REQUIRED, TOKEN_GROUP_CAPABLE, and payment-terminal values.
- **Placement:** `floor_level`, `coordinates`, `physical_reference` (≤16, the label on the unit), `parking_restrictions[]` (EV_ONLY, PLUGGED, DISABLED, CUSTOMERS, MOTORCYCLES).
- **No deletion:** a removed EVSE gets `status = REMOVED`.
- **2.3.0 additions:** a Parking object with `vehicle_types` (**SEMI_TRACTOR, RIGID, TRUCK_WITH_TRAILER**, VAN, BUS…), max weight/height/length/width, `dangerous_goods_allowed`, `refrigeration_outlet`, `reservation_required` and `time_limit`. This is the industry vocabulary for truck bays.

**Connector** ✅
- `id`: CiString(36), unique within its EVSE.
- `standard`: DC values are **`IEC_62196_T2_COMBO` (CCS2)**, `IEC_62196_T1_COMBO`, `CHADEMO`, **`GBT_DC`**, `CHAOJI`, `PANTOGRAPH_TOP_DOWN` and `_BOTTOM_UP`. **`MCS` only from 2.3.0.**
- `format`: SOCKET or CABLE (a DC gun is CABLE).
- `power_type`: AC_1_PHASE, AC_2_PHASE, AC_2_PHASE_SPLIT, AC_3_PHASE, DC.
- Ratings: `max_voltage` V (1), `max_amperage` A (1), and `max_electric_power` **W**, set it when it's lower than V×A.
- `tariff_ids[]`: at most one active tariff per tariff type.
- 2.3.0 adds `capabilities` (ISO_15118_2 / ISO_15118_20 Plug & Charge).

**Interfaces:** a paginated GET on the CPO, filtered by `last_updated`; the CPO PUTs/PATCHes to the eMSP at `/{country}/{party}/{location}[/{evse_uid}[/{connector}]]`, and status changes are small PATCHes.

## Tariffs ✅
- **Tariff:** `id`, `currency` (ISO 4217), `type`, `tariff_alt_text[]`, `min_price` / `max_price`, `elements` (≥1), `start_date_time` / `end_date_time` (UTC), `energy_mix`, `last_updated`.
- **TariffElement:** `price_components` (≥1) + optional `restrictions`.
  - Each dimension is evaluated on its own: the **first** element that has that dimension and whose restrictions match wins. Always add an unrestricted fallback element.
  - A free tariff is one element holding a `FLAT` component at price 0.
- **PriceComponent:** `type` + `price` per unit **excluding VAT** + `vat` %. Leaving `vat` out means no VAT, which is not the same as 0. `step_size` rounds up to the next block; it is gone in 3.0, so use 1.

| Dimension | Unit / step |
|---|---|
| ENERGY | kWh / 1 Wh |
| FLAT | one-off fee |
| TIME | hours charging / 1 s |
| PARKING_TIME | hours not charging (idle fee) / 1 s |

- **Restrictions** (all must hold; they apply to stretches of a session):
  - `start_time` / `end_time` (local HH:MM; may cross midnight)
  - `start_date` / `end_date` (end date exclusive)
  - `min_kwh` / `max_kwh`, `min_current` / `max_current`, `min_power` / `max_power` (kW), `min_duration` / `max_duration` (s)
  - `day_of_week[]`
  - `reservation`
- **TariffType:** AD_HOC_PAYMENT, PROFILE_CHEAP / FAST / GREEN, REGULAR; omitted means it applies to every session.
- **Changes during a session:** allowed, but keeping the tariff in force at session start is common and often a legal requirement.
- **Per-customer and private prices:** the Tariff object has **no customer or token field**. CPOs send different tariffs to each eMSP connection, and driver-specific prices stay inside the operator's own billing. The CDR's `tariffs[]` records what was applied.

## Sessions and CDRs ✅
- **Session** (live):
  - `id`, start/end, `kwh`, `cdr_token`, `auth_method`, `authorization_reference`
  - `location_id`, `evse_uid`, `connector_id`, `meter_id`, `currency`
  - `charging_periods[]`, `total_cost`, `last_updated`
  - `status`: PENDING / RESERVATION / ACTIVE / COMPLETED / INVALID
- **CDR**, "the only billing-relevant object":
  - **Immutable**: a correction is a **credit CDR** (`credit = true`, `credit_reference_id`, negative totals, new id).
  - **Fields**: a `cdr_location` snapshot (address, coordinates, EVSE uid/id, connector standard/format/power type), `tariffs[]`, `charging_periods` (≥1, each with a `tariff_id` and dimensions ENERGY, TIME, PARKING_TIME, POWER, STATE_OF_CHARGE…), and `signed_data` (e.g. OCMF, German calibration law).
  - **Totals**: `total_cost`, `total_energy` kWh, `total_time` h, plus energy/time/parking/fixed/reservation cost parts.
- **AuthMethod:** AUTH_REQUEST, COMMAND, WHITELIST.

## Tokens and Commands ✅
- **Token:**
  - Fields: `uid` + `type` (unique together), `contract_id` (eMA-ID), `visual_number`, `issuer`, `group_id`, `valid`, `whitelist`, `default_profile_type`.
  - **TokenType:** RFID; APP_USER (the same token every session); AD_HOC_USER (single use, generated by the app or server); OTHER; EMAID (2.3.0).
  - **Whitelist:** ALWAYS, ALLOWED, ALLOWED_OFFLINE or NEVER (always a live `authorize`). The `authorize` call answers ALLOWED / BLOCKED / EXPIRED / NO_CREDIT / NOT_ALLOWED.
- **Commands (asynchronous):**
  - The CPO answers at once with a `CommandResponse` (ACCEPTED / REJECTED / NOT_SUPPORTED / UNKNOWN_SESSION + `timeout`).
  - It then POSTs a `CommandResult` (ACCEPTED, EVSE_OCCUPIED, EVSE_INOPERATIVE, FAILED, TIMEOUT…) to `response_url`.
  - The commands are START_SESSION (token, location, EVSE, connector, `authorization_reference`), STOP_SESSION, RESERVE_NOW, CANCEL_RESERVATION and UNLOCK_CONNECTOR.
- **App/QR start in OCPI terms 🔎:**
  1. The QR code carries the `evse_id` (or a URL holding it).
  2. The app resolves it to location + EVSE.
  3. START_SESSION is sent with an APP_USER or AD_HOC_USER token.
  4. The CPO sends OCPP `RemoteStartTransaction` (1.6, `idTag` = token uid) or `RequestStartTransaction` (2.0.1).
  5. The session gets `auth_method = COMMAND`.

## Charging profiles and hub info ✅
- **ChargingProfiles** work per session (PUT sets, DELETE clears, GET returns the active profile) through asynchronous callbacks, using the OCPP profile structures. A profile is a maximum limit, not a target.
- **Charging preferences:** `profile_type`, `departure_time`, `energy_need`, `discharge_allowed`.
- **HubClientInfo** is irrelevant until G3 joins a hub.

## What to store natively so an OCPI export stays easy
1. **Separate the technical key from the readable ID.** Each object gets a never-reused internal UUID (Location ≤36 chars) plus a nullable eMI3 `evse_id` (`VN*<op>*E…`). Never key on a charger serial.
2. **Station = Location.** Store `time_zone`, the address split into `address` / `city` / `postal_code` / `state` / `country`, coordinates, `parking_type` and `facilities`.
3. **Opening hours follow the Hours shape**, plus `charging_when_closed`.
4. **Give the station a `publish` flag** plus an allow-list, so private or depot sites stay off public maps and out of OCPI.
5. **Connectors** use the OCPI `standard` values verbatim (`IEC_62196_T2_COMBO`, `GBT_DC`, `MCS` later), plus `format`, `power_type`, `max_voltage`, `max_amperage` and `max_electric_power` in W.
6. **Never hard-delete EVSEs or connectors.** Use a removed or retired status; sessions and CDRs reference them.
7. **Keep an EVSE status that maps to OCPI's 9 values**, derived from the OCPP status and online state 🔎: Faulted → OUTOFORDER, Unavailable → INOPERATIVE, offline → UNKNOWN, Preparing/Charging/Suspended*/Finishing → CHARGING (or BLOCKED).
8. **Tariffs** use ordered elements with restrictions and per-dimension components; prices excl. VAT plus a VAT %, a validity period, and a link to the connectors.
9. **The billing record is an immutable snapshot**: location/EVSE/connector, tariffs applied, charging periods, energy. Corrections are credit records only.
10. **Record the token type and auth method on every session.**
11. **Keep `last_updated` on everything exportable.**
12. **For trucks, use 2.3.0's Parking vocabulary**: vehicle types, max weight and length.

## Sources
- github.com/ocpi/ocpi, branch `release-2.2.1-bugfixes`: `mod_locations`, `mod_tariffs`, `mod_sessions`, `mod_cdrs`, `mod_tokens`, `mod_commands`, `mod_charging_profiles`, `mod_hub_client_info`, `types`, `terminology` (.asciidoc)
- github.com/ocpi/ocpi, branch `release-2.3.0-bugfixes`: `changelog`, `mod_locations`, `mod_tariffs`, `mod_payments`, `types`
- https://github.com/ocpi/ocpi/releases · https://evroaming.org/ocpi/ · https://evroaming.org/ocpi-downloads/ · https://evroaming.org/contract-evse-ids/
