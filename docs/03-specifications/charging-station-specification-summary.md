# Willdigits DC Fast Charging Station (240–480 kW) — Device Summary

> **What this is.** A single English reference that explains the charging station itself, distilled
> from two source documents, organised around four questions:
>
> 1. **What is this device?** → Part 1
> 2. **How is it used?** → Part 2
> 3. **How do we connect it?** → Part 3
> 4. **How is it operated (remotely, over OCPP)?** → Part 4
>
> Part 5 is the plan for verifying the device; Part 6 lists what is still unknown or must be requested
> from the vendor; Part 7 holds other facts worth remembering. Comparing the device with the current
> backend is deliberately **out of scope** here and will be done separately.

**Evidence tags**

| Tag | Meaning |
|---|---|
| ✅ | Stated as fact in a source document |
| ⚠️ | Stated in the source as *unconfirmed / needs verification* ("CẦN KIỂM CHỨNG") |
| 🔎 | **Not in any text** — read from a screenshot in the manual, or a reviewer inference. A lead to verify with real logs, not a fact |

**Sources** (both in this folder)

| # | File | Language | Nature |
|---|---|---|---|
| A | `G3_Backend_Tich_hop_Tru_sac_OCPP_1.6J.docx` | Vietnamese | G3 Network's internal technical handover for the Backend team: how to evaluate, integrate and collect real data from the charger over OCPP 1.6J. v1.0, 2026-09-16, "working draft — updated after each test phase". Built on source B plus the vendor's 240 kW spec sheet (that sheet is not in this folder). |
| B | `HDSD DC Fast Charging Station 240-480kW.docx` | English | Vendor user manual (Willdigits, series 240–480 kW, V1.0, released 2025-01-11): safety, specs, installation, operation, HMI, fault codes, maintenance, warranty. ~63 images (HMI screens, vendor-platform screenshots, installation photos). |

Doc A explicitly warns: *it is not a final specification*. The vendor has only confirmed "has OCPP 1.6J" — not which profiles, measurands, or whether TLS is supported. Everything marked ⚠️ must be proven with real charger logs, not paper confirmation.

---

# Part 1 — What is this device?

## 1.1 Identity
| Item | Value |
|---|---|
| Manufacturer | Willdigits (www.willdigits.com) |
| Product | DC Fast Charging Station, series **240–480 kW** (models 240 / 320 / 360 / 400 / 480 kW) |
| Model under evaluation | **240 kW, Dual Gun, CCS2 + CCS2** (European market) |
| Protocol | **OCPP 1.6J** (JSON over WebSocket) — ✅ confirmed verbally by the vendor; 🔎 the HMI has a protocol selector showing `OCPP1.6` |
| Intended use | Electric buses, logistics vehicles, passenger cars; public stations, bus depots, highway service areas, fleet parking lots |
| Manual | V1.0, released 2025-01-11 (predates the firmware seen in screenshots — see §7.5) |

## 1.2 What it does
A high-power **DC** fast charger. Its output automatically follows the vehicle battery-management-system (BMS) request. Headline features:
- Efficiency ≥ 95 %, standby < 50 W.
- **Dual gun**: charges two vehicles simultaneously (or alternately), or one vehicle at higher power depending on configuration.
- Multiple activation methods: RFID card, mobile APP/mini-program, QR code, Autocharge by VIN/MAC, password.
- Communications: Ethernet and 4G as standard; the manual (§2.2) calls the **OCPP protocol "optional"** — make sure it is actually included in the order.
- **Modular power**: power modules are hot-swappable.
- Protections: over-voltage, under-voltage, over-current, short-circuit, leakage, lightning, over-temperature.
- **Offline mode**: local whitelist + local VIN Autocharge; records stored locally and uploaded when the network returns.
- Has its **own DC energy meter** ✅ (evidenced by the `PowerMeterFailure` code and a "Meter calibration" menu).

## 1.3 Physical and functional parts
| # | Part | Function |
|---|---|---|
| 1 | Touchscreen display (HMI) | Information, parameter settings, charging operation |
| 2 | Status indicator lights | Standby / Connected / Charging / Fault |
| 3 | RFID reader | Card-swipe area (if fitted) |
| 4 | **Emergency-stop button** | Red mushroom head; cuts output immediately |
| 5 / 10 | Cooling fan inlet / outlet | Forced-air cooling — **never block** |
| 6 | Door handle | Front/maintenance door |
| 7 | Nameplate | Model, power, voltage, current, serial number, certifications |
| 8 | Lifting / mounting rings | On top, for hoisting during installation |
| 9 | Router antenna | External 4G / Wi-Fi antenna |
| 11 | Gun holster | Holds the gun when not in use |
| 12 | Charging gun A / B | DC gun; connector type selectable per market (CCS2 / CCS1 / CHAdeMO / GB/T / NACS) |

🔎 **Internal control architecture (from the HMI settings screens).** The station is built from separate controller boards, each with its own firmware version on the sample unit:
- **OCU** — the OCPP/communication controller (version string on the home screen: `OCPP_L4.05_260227`).
- **CCU** — charge control unit (per gun; version `CCU_GBT_3.12 20251002`): max/min voltage and current, plug-temperature limit, insulation mode, meter protocol/address, digital inputs (gun-lock feedback, contactor feedback, **E-stop**, **door**, **water-ingress**, fuse, backup).
- **PCU** — power control unit (version `PCU_3.11 20251229`): module counts and grouping, power-allocation type, module voltage/current limits, module protocol.
- The HMI itself has a hardware type (`FE6070WE` on the sample).

## 1.4 Specifications
**Common to the series**
| Parameter | Value |
|---|---|
| Input | 400 V AC ±15 %, 3-phase 5-wire (L1, L2, L3, N, PE), 50/60 Hz, power factor ≥ 0.99 |
| Output voltage | 150–1000 V DC |
| Dual-gun allocation | Equal or Dynamic (see §2.3) |
| Efficiency / standby | ≥ 95 % / < 50 W |
| Enclosure | Painted sheet metal (stainless optional); IP54 (IP55 optional) |
| Cooling / noise | Forced air / < 65 dB @ 1 m |
| Humidity | 5–95 % non-condensing |
| Operating temperature | −30 to +75 °C in the spec table (−30 to +70 °C elsewhere — see §7.5) |
| Storage temperature | −40 to +75 °C (−40 to +70 °C in §4.1 of the manual) |
| Dimensions (H×W×D) | 1900 × 900 × 700 mm (packaged 2150 × 1200 × 850 mm) |
| Charging cable | 5 m standard, 4–7 m optional |
| Standards | CE (UKCA / UL optional); safety IEC 61851-1, -23; EMC IEC 61851-21-2; connector IEC 62196-3 |

**By model**
| Parameter | 240 kW | 320 kW | 360 kW | 400 kW | 480 kW |
|---|---|---|---|---|---|
| Max input current | ~480 A | ~640 A | ~720 A | ~800 A | ~960 A |
| Max output current | 300 / 600 A* | 400 / 800 A* | 400 / 800 A* | 400 / 800 A* | 400 / 800 A* |
| Max power per gun | 240 kW | 320 kW | 360 kW | 400 kW | 480 kW |
| Net / gross weight | 400 / 425 kg | 450 / 475 kg | 475 / 500 kg | 500 / 525 kg | 550 / 575 kg |
| Upstream breaker | ≥ 500 A | ≥ 630 A | ≥ 630 A | ≥ 800 A | ≥ 2×500 A |
| Input cable (Cu, ≥ 90 °C) | 2×(3×95 + 2×50 mm²) | 2×(3×120 + 2×70) | 2×(3×150 + 2×70) | 2×(3×185 + 2×95) | 2×(3×185 + 2×95) |

\* Depends on output voltage and configuration; the nameplate is authoritative. Cable sizes are reference only — recompute for voltage drop over the real distance.

## 1.5 Connector types
The series can be ordered with any combination: **CCS2** (IEC 62196-3, Europe), **CCS1** (North America), **CHAdeMO** (Japan), **GB/T DC** (China), **NACS**. The manual says to read the markings on the unit — so the manual alone does not prove this unit is CCS2; that comes from the vendor spec sheet (✅ per doc A). ⚠️ Vehicle-side compatibility: a CCS2 charger cannot charge a GB/T vehicle (see §7.4).

---

# Part 2 — How is it used?

## 2.1 Before charging
1. Confirm the vehicle supports DC fast charging and the **connector type matches** the station.
2. Inspect the gun head for contamination, foreign objects, moisture or damage; inspect the cable for damage or twisting. Do not use it if damaged.
3. Park, and turn off the ignition or switch the vehicle to charging state (per the vehicle manual).
4. Hold the gun by its body, never by the cable; hands dry.

## 2.2 Starting a charge
Sequence: park → take the gun from the holster → insert into the vehicle inlet until it **clicks/locks** → authenticate → charging starts.

Authentication options (depending on station settings):
| Method | How it works |
|---|---|
| **Autocharge** | For pre-registered vehicles; charging starts automatically after plug-in |
| **RFID card** | Swipe an authorised card near the reader; a beep confirms |
| **Password** | Enter a pre-registered password on the charger keypad |
| **Mobile APP / mini-program** | Scan the QR code on the screen and choose "start charging" |
| **VIN/MAC recognition** | Station recognises the vehicle's VIN/MAC and authorises it — **requires backend support** |

After authentication the station communicates with the vehicle BMS/EVCC. Once parameters are agreed, the **contactor closes and power output begins**; the screen shows voltage, current, energy, time.

## 2.3 Charging modes
| Mode | Behaviour |
|---|---|
| **Automatic (standard)** | After plug-in + authentication the station follows BMS demand (voltage/current) until full or manually stopped. The most common mode |
| **Scheduled** | The user sets a start time via the APP; after plug-in + authentication the station waits until then. For off-peak tariffs |
| **Simultaneous (dual gun)** | Both guns charging: total power split **equally** (240 kW station → 120 kW each) |
| **Smart / dynamic allocation** | Power follows each vehicle's real demand; **first-connected vehicle has priority**, the later one gets the remainder; power freed as the first vehicle tapers is passed to the second. Maximises utilisation |
| **Offline** | On network loss the station keeps working: offline card whitelist or VIN-based local Autocharge; records stored locally and uploaded automatically when the network returns |

🔎 Local switches seen on the HMI "Charging Setting" screen of a test unit: double-gun single charge (both guns on one vehicle), verification required on stop, QR code in standby, show gun-unlock button, high-precision electric meter, V2G, stored amount in card, swipe card to stop, allow user charging plan, screensaver, PnC (Plug & Charge). `PnC` and `V2G` were off.

## 2.4 During charging
- Monitor on the screen or the APP: output voltage (V), current (A), energy (kWh), time (min), power (kW), **battery SOC (%)**, charging stage (constant current / constant voltage / fully charged), BMS communication status.
- If an abnormality occurs the equipment stops automatically and shows a fault message.
- Slow charging is often normal: BMS demand limit (cold battery, high SOC), **power sharing while the other gun is active**, low grid voltage, thermal derating.

## 2.5 Stopping
- **Normal:** APP "Stop charging"; swipe the RFID card again (if supported); touchscreen "Stop"; from inside the vehicle (some vehicles). The station negotiates with the BMS, ramps current down, cuts output, and unlocks the electronic lock.
- **Emergency:** press the red E-stop — all output is cut immediately. To reset: turn the button **clockwise** so it pops up, then re-energise/reset the unit before normal use.
- Charging can also stop by itself: BMS stop (battery full — normal), grid fluctuation, over-temperature, output over-voltage/over-current.

## 2.6 After charging
Confirm the screen shows "Finished"; hold the gun handle and remove it; return it to the holster **gun head facing down** (prevents water ingress), fully seated; close the vehicle inlet cover.

## 2.7 Status indicator lights
| Light | Equipment state |
|---|---|
| Blue, steady | Standby / idle — ready for charging |
| Green, flashing | Connected / communicating with vehicle, or waiting for user authentication |
| Green, steady / breathing | Charging in progress |
| Red, steady | Fault / alarm (cannot charge) **or** emergency stop pressed — same colour for both |

Colours may vary slightly by model.

## 2.8 HMI (touchscreen)
| Screen | Content |
|---|---|
| Main / standby | Welcome, time/date, equipment status (Idle/Ready), gun status (Available / Occupied / Fault), entry points: scan to charge, swipe card, inquiry, settings |
| Charging monitor | Real-time data listed in §2.4, stage + BMS status, **Stop** button |
| Fault alarm | Current fault codes with short description; historical fault records (for service staff) |
| System settings (password-protected, professionals only) | Communication parameters (IP, server address, OCPP parameters), system time, brightness/language, **meter calibration (authorised only)**, system info (software/hardware version, serial number). "For the charging station, you only need to set the **URL and charger ID**." |

🔎 On the sample unit the settings menu has buttons: System Config, OCPP, Charging Setting, Plug, Price, CCU, PCU, Info Service, Card (details in §3.5 and §4.9).

---

# Part 3 — How do we connect it?

## 3.1 Site selection and installation
**Site requirements**
| Item | Requirement |
|---|---|
| Space | Front ≥ 1000 mm, both sides ≥ 1000 mm, rear ≥ 200 mm; top not blocked (≈ 2.9 m width per unit in practice) |
| Foundation | Solid, level concrete pad; footprint = base + 100 mm; depth ≥ 500 mm (or below frost line); 4× M12×110 anchor bolts (or wall-mount with expansion bolts) |
| Cable conduit | Diameter ≥ 100 mm, emerging at the foundation centre, protruding ≥ 50 mm above the slab (water ingress) |
| Environment | Avoid flood-prone/low spots, strong direct sun, heat sources, corrosive gas, explosive atmospheres; prefer sheltered. **Verify 4G/Wi-Fi signal at the site** |
| Weight handling | 400–550 kg — forklift or crane (lifting rings on top) |
| Electrical supply | Transformer/line capacity ≥ maximum demand; verify 400 V AC ±15 % (3-phase, 5-wire) with a multimeter |
| Upstream protection | Correctly rated breaker (see §1.4) plus RCD/leakage protection |
| Cabling | Copper, ≥ 90 °C |
| Grounding | Resistance **≤ 4 Ω**; PE reliably connected; re-measure periodically |

**Installation procedure** (certified electrician only; upstream power isolated and locked out)
1. Prepare foundation and conduit; mark and drill anchor holes.
2. Place the unit (crane/forklift, keep balanced); level it; tighten anchor nuts; apply anti-rust coating.
3. Open the wiring compartment; route the input cable; strip; connect L1/L2/L3 (typ. brown/black/gray), N (blue), PE (green/yellow) to the marked terminals; tighten with a torque wrench (M10 ≈ 20–30 Nm); pull-test; tidy wiring away from fans/sharp edges; connect communication lines (Ethernet or 4G antenna); close the cover.
4. **Pre-power checks:** insulation resistance between L1/L2/L3/N and PE with a 500/1000 V megger **> 5 MΩ**; input voltage and **phase sequence** (a phase-sequence protection will report an error if wrong); E-stop button released.
5. **First power-on:** clear the area; close the upstream breaker; the display should light and status LEDs read standby; fans run smoothly; no fault alarm.
6. **Functional test:** connect to a test vehicle or **dedicated load bank**; start via card/APP/screen; confirm gun lock and BMS communication; output begins; displayed V/A match the demand; no shock feeling from the enclosure (grounding); test stop; test E-stop.
7. **Post-installation checklist (13 items):** secure & level; connections tight; ground reliable; cable entry sealed; upstream breaker/RCD rated; insulation passed; voltage & phase sequence OK; display/indicators OK; charging start/stop/output OK; E-stop OK; communication OK; fans OK; basic operation explained to the user.

Packing list: main unit, manual, certificate of quality, warranty card, 2 keys, installation kit (expansion bolts, screws…), optional pedestal/pole, spare fuses.

## 3.2 Network connection
- Standard connectivity: **Ethernet** and **4G** (external router antenna; Wi-Fi mentioned for signal). No extra module needed.
- 🔎 Local network settings sit on the OCPP screen: DHCP or static IP, IP address, gateway.
- The site must have adequate 4G/Wi-Fi signal (check before installing) — transport routes cross weak-signal areas and the charger's offline behaviour then matters (§4.9).

## 3.3 How the OCPP link works
- OCPP connects a **charging station (Charge Point, CP)** to a management system (**CSMS**). The **charger always opens the WebSocket outward**; the CSMS never connects into the charger. Consequences: a charger behind NAT works, but **the CSMS must have a public, stable address**.
- Endpoint form: `ws://host:port/path/{chargePointId}` (plain) or `wss://…` (TLS).
- **`chargePointId` is the "Charger ID"** typed on the charger screen. It is the identity key for the charger everywhere — **decide the naming convention before the first unit is installed** (renaming later is costly).
- The charger is not locked to the vendor's cloud: it can be pointed at any CSMS by setting the URL and charger ID (manual §7.2.4) ✅.

Logical layers (doc A):
| Layer | Component | Role |
|---|---|---|
| 1 | Willdigits charger (Charge Point) | Opens the WebSocket; sends status, meter values, session records |
| 2 | **CSMS** | The OCPP end point: receives/answers messages, stores raw records, validates `idTag`, issues control commands |
| 3 | Business system | Billing, reconciliation, tariffs, station map, queue, alerts |
| 4 | Driver app / manager portal | Consumers of layer 3 |

## 3.4 Setting up the connection on the charger
1. Open the HMI **System Settings** (password-protected — see §3.6, change the default first).
2. On the **OCPP** screen: 🔎 set `Protocol` = `OCPP1.6`; set the **server URL**; set the **Charger ID**; network mode (DHCP/static) + IP + gateway; press **Save**.
3. 🔎 The sample unit's URL field held a SteVe-style address (`…:8080/steve/websocket/CentralSystemService/`) with the Charger ID separate — the charger very likely **appends the Charger ID** to the URL (standard OCPP-J behaviour). Confirm the exact resulting URL from the first connection log.
4. 🔎 A **"Create QR code"** button generates two QR codes on this screen (probably one per gun) — their content format is unknown and should be captured (relevant to scan-to-charge).

## 3.5 Other HMI configuration screens (🔎 seen on a test unit)
| Screen | What it holds |
|---|---|
| System Config | Language, card key (`123456`), card-reader enable and type, **admin password (shown in plain text, default `77777777`)**, timezone selector (`UTC`), AC-meter addresses/rates, program-update selector |
| OCPP | Protocol, server URL, Charger ID, QR codes, network mode/IP/gateway (see §3.4) |
| Charging Setting | The switches listed in §2.3 |
| Plug | Plug number (2), connector type per gun (`GBT`), PCU mapping, "Limit Power" with total-power value (100 kW on the sample), Exclusive / Liquid-cooled options per gun |
| Price | Local tariff: 48 half-hour slots, currency CNY (default), user-defined unit; all rates `1.00000` on the sample |
| CCU / PCU | Controller parameters (see §1.3) |
| Info Service | Local **Charging Record** (No., card no., start/end time, cost time, kWh, **begin-SOC, end-SOC**), Alarm Record, Charging Password; export and **clear** buttons |
| Card | Register/read card ID (16 digits), balance read/recharge/deduct, unlock/clean card, set/verify 6-digit key |

> ⚠️ These screenshots come from a **GB/T, 2-module (≈100 kW cap) test unit**, not the CCS2 240 kW sample doc A describes. Do not read their values as the sample's.

## 3.6 Security of the link and of the device
| Topic | Detail |
|---|---|
| **Default HMI password** | The manual §7.2.1 prints **`77777777`** publicly. It opens System Settings — the OCPP server address and meter calibration. Anyone at the charger could redirect session data to another server or tamper with the metering used for billing. **Mandatory:** set a unique password per charger at acceptance and record it in the handover procedure; ask the vendor whether multi-level permissions exist (operator vs meter-calibration rights). |
| Other default secret | 🔎 Card key `123456`. |
| Transport | OCPP 1.6J defaults to unencrypted `ws://`. ⚠️ `wss://` support and authentication (Basic Auth or a client certificate) are unconfirmed — **high risk**, because session data and control commands cross public 4G. Fallback: site-to-site VPN or a private APN for the charger SIMs (adds operating cost). |
| Public endpoint | The CSMS needs a stable public address and a TLS certificate ready to test both `ws://` and `wss://`. |
| Development | Plain `ws://` without authentication is acceptable only in an isolated test environment. |
| Local tamper points | 🔎 The HMI can **clear** its local charging records and has its own **local price table** — local data must never be the source of truth; the CSMS-side raw log is the evidence. |

## 3.7 First-connection procedure (Phase 1 of the test plan)
Charger powered and networked, **no charging yet** (3–5 days):
1. Enter the CSMS URL and Charger ID on the screen; **change the default password immediately**.
2. Capture `BootNotification` — record vendor, model, firmware, serial. This is the baseline for detecting a later firmware swap.
3. Send **`GetConfiguration` with no key**; save the verbatim result. Read `SupportedFeatureProfiles` — the authoritative answer to which profiles the charger supports (more reliable than any email confirmation). Store it in the repository: it is the charger's real specification.
4. Try `ChangeConfiguration` to lower `MeterValueSampleInterval` to 30 s, then 10 s; note the **smallest value accepted**.
5. Try `TriggerMessage` for `StatusNotification` and `MeterValues`; if they work, Remote Trigger is supported.
6. Plug and unplug repeatedly; log the `StatusNotification` sequence and the delay from the physical action to the CSMS.
7. Try `wss://`. If the charger cannot connect, that is a key finding — escalate to the project manager at once.

---

# Part 4 — How is it operated (over OCPP)?

## 4.1 Message set
**Core Profile — mandatory, almost certainly present**
| Message | Direction | Purpose |
|---|---|---|
| BootNotification | CP → CSMS | Vendor, model, firmware, serial at startup. CSMS replies with `currentTime` (clock sync) and `interval` |
| Heartbeat | CP → CSMS | Keep-alive and clock sync |
| StatusNotification | CP → CSMS | Per-connector status and error code |
| Authorize | CP → CSMS | Ask permission for an `idTag` before charging |
| StartTransaction | CP → CSMS | Open a session; CSMS returns `transactionId`; carries `meterStart` |
| StopTransaction | CP → CSMS | Close a session; `meterStop`, stop reason, `transactionData` |
| MeterValues | CP → CSMS | Periodic readings during a session |
| RemoteStartTransaction | CSMS → CP | Start charging from the platform (basis of the **QR-scan flow**) |
| RemoteStopTransaction | CSMS → CP | Stop charging from the platform |
| GetConfiguration | CSMS → CP | Read all charger parameters — the **first check** |
| ChangeConfiguration | CSMS → CP | Change parameters (e.g. MeterValues interval) |
| ChangeAvailability | CSMS → CP | Put a gun into maintenance |
| Reset | CSMS → CP | Remote reboot (Soft / Hard) |
| UnlockConnector | CSMS → CP | Release a stuck gun (removes a class of support tickets) |
| ClearCache | CSMS → CP | Clear the local authorisation cache |
| DataTransfer | both | Vendor-specific extension channel — ask whether Willdigits uses it |

**Optional profiles — ⚠️ NOT confirmed; ask about each one.** Most Chinese chargers implement only Core.
| Profile | Key messages | If absent |
|---|---|---|
| **Smart Charging** (highest priority) | SetChargingProfile, ClearChargingProfile, GetCompositeSchedule | Power cannot be throttled from the backend (station load balancing and time-of-day power limits impossible). 🔎 The HMI's local "Limit Power" setting and the vendor platform's "power adjustment" are possible alternatives |
| Remote Trigger | TriggerMessage | Cannot actively re-query state; must wait for the charger to report — hard to recover after desynchronisation |
| Firmware Management | UpdateFirmware, GetDiagnostics, FirmwareStatusNotification | Every patch needs a site visit; no remote logs during incidents. 🔎 The vendor platform offers Firmware Upgrade / Diagnostic Log actions, but whether via OCPP is unknown |
| Local Auth List | SendLocalList, GetLocalListVersion | Offline whitelist cannot be pushed over OCPP; offline mode would depend on a vendor mechanism |
| Reservation | ReserveNow, CancelReservation | The gun cannot really be locked; booking would be only "soft" at app level |

## 4.2 Configuration keys to read/set (with G3's target value)
| Key | Meaning | Target |
|---|---|---|
| SupportedFeatureProfiles | Profiles the charger supports | **Read first — do not change** |
| NumberOfConnectors | Number of guns | Must be **2** |
| MeterValueSampleInterval | MeterValues period (s) | **≤ 30** |
| MeterValuesSampledData | Measurands sent | See §4.6 |
| ClockAlignedDataInterval | Clock-aligned reading period | **900 s (15 min)** — for time-of-use tariffs |
| MeterValuesAlignedData | Measurands in clock-aligned records | At least `Energy.Active.Import.Register` |
| HeartbeatInterval | Heartbeat period (s) | 60–300 |
| WebSocketPingInterval | WebSocket keep-alive ping | Enabled, ≤ 60 |
| ConnectionTimeOut | Wait for plug-in after authorisation | Per app experience |
| AuthorizeRemoteTxRequests | Whether `Authorize` is needed before a RemoteStart | Determine — drives the remote-start flow design |
| LocalAuthorizeOffline | Local authorisation when offline | true |
| StopTransactionOnEVSideDisconnect | Auto-close session when the gun is unplugged | true |
| StopTransactionOnInvalidId | Stop when `idTag` is invalid | true |
| TransactionMessageAttempts | Retries for session records on network error | ≥ 3 |
| TransactionMessageRetryInterval | Retry spacing (s) | ≥ 60 |
| UnlockConnectorOnEVSideDisconnect | Auto-unlock when the vehicle disconnects | true |

Not every key is writable; fixed ones answer `readOnly`. **Record which keys are read-only** — that is a design constraint, not an error.

## 4.3 Identifiers and connectorId
- `connectorId = 0` → the **whole charger** (general status, device-level faults); `1` → gun A; `2` → gun B.
- On a dual-gun unit, `StatusNotification` with connector **0 `Faulted`** = the **entire charger is down**; connector **1 `Faulted`** = only that gun. Availability counting must distinguish the two, otherwise the number of available chargers is misreported.
- 🔎 The vendor platform lists status for 0, 1 and 2 per charger; its "Charge Box ID (O&M)" is an operator-assigned alias alongside the serial number.

## 4.4 A complete session — the reference sequence
Compare every real log with this and record every deviation.

| # | Event | Message | Note |
|---|---|---|---|
| 1 | Charger boots | `BootNotification` → Accepted | CSMS returns `currentTime` and `interval` |
| 2 | Ready | `StatusNotification: Available` for 0, 1, 2 | Three separate messages: charger, gun A, gun B |
| 3 | Driver plugs in | `StatusNotification: Preparing` | Signal to show a confirmation screen |
| 4 | Driver scans QR; app calls CSMS | `RemoteStartTransaction` → Accepted | Accepted = command **received**, not charging |
| 5 | Charger opens session | `StartTransaction` → `transactionId` | Capture `meterStart` (Wh); `transactionId` becomes the session key |
| 6 | Power starts | `StatusNotification: Charging` | Steps 4→6 = latency the user perceives |
| 7 | While charging | `MeterValues` every N s | Carries `transactionId` — groups samples into the right session |
| 8 | Stop | `RemoteStopTransaction` → Accepted | Or stop at the charger / the vehicle stops itself |
| 9 | Close | `StopTransaction` | Capture `meterStop`, `reason`, `transactionData` |
| 10 | End | `StatusNotification: Finishing` → `Available` | Only `Available` means the gun is free |

**Three easy mistakes**
1. Steps 4→6: `Accepted` ≠ charging. Report "charging" only after `StatusNotification: Charging`.
2. Step 10: `Finishing` is still **busy** (gun not yet removed).
3. `SuspendedEV` / `SuspendedEVSE` are **normal** (charging paused: battery nearly full / vehicle request / charger load-sharing) — not faults, not free; no driver alert.

## 4.5 Connector status meanings (OCPP 1.6)
| Status | Meaning | Gun free? |
|---|---|---|
| Available | Ready, no vehicle | **YES** |
| Preparing | Plugged in or authorised, not yet delivering | NO |
| Charging | Delivering power | NO |
| SuspendedEV | Connected; **vehicle** paused (battery full / BMS asks to stop) | NO |
| SuspendedEVSE | Connected; **charger** paused (load reduction / power sharing) | NO |
| Finishing | Session ended, gun not yet removed | NO |
| Reserved | Booked | NO |
| Unavailable | In maintenance via `ChangeAvailability` | NO |
| Faulted | Fault, cannot charge | NO — **raise an alert** |

Misreading this group is the most common cause of apps showing the wrong number of free guns.

## 4.6 Measurements and metering
**Measurands wanted in `MeterValues`**
| Measurand | Unit | Use | Level |
|---|---|---|---|
| `Energy.Active.Import.Register` | Wh | Cumulative energy — basis for billing/invoice | **MANDATORY** |
| `Power.Active.Import` | W | Instantaneous power — ETA forecasting, monitoring | **MANDATORY** |
| `SoC` | % | Vehicle battery level — three-way reconciliation, driver display | **MANDATORY** |
| `Voltage` | V | Diagnostics | Should have |
| `Current.Import` | A | Diagnostics | Should have |
| `Temperature` | °C | Over-temperature alerts (gun/charger) | Should have |
| `Power.Offered` | W | Power the charger allots to this gun — checks dual-gun load sharing | Should have |

`SoC` is the most important and the most often missing. Without it, reconciliation degrades from three-way to two-way (charger ↔ payment) and loses the vehicle-data check. The charger *displays* SOC, so it very probably reads it from the BMS — but "readable for display" and "sent over OCPP" are different things.

🔎 **A real sample** from the vendor platform's transaction-detail screen (≈ 30 s sampling, last row 5 s later): columns `Voltage.Demand (V)`, `Current.Demand (A)`, `Voltage.Import (V)`, `Current.Import (A)`, `Power.Active.Import (W)`, `Energy.Active.Import.Register (Wh)`, `SOC (%)`. Observations:
- SOC is present in the vendor's own data (encouraging; still to be seen over OCPP).
- `Voltage.Demand` / `Current.Demand` (BMS demand) are **not standard OCPP 1.6 measurand names** → tolerate unknown/vendor names instead of rejecting them.
- The first row has **SOC = 0 before the BMS handshake** (demand 0 V/0 A) → treat SoC 0 at session start as *unknown*.
- The energy register moved in **10 Wh steps** (19420 → 19430) — coarse resolution, matters for short sessions and for a <1 % tolerance.
- The sample came from a bench unit (SOC jumped 0 → 80 in 30 s); not representative.

**The decisive metering question.** Are `meterStart` / `meterStop` read **directly from the DC energy meter**, or **computed by integrating current over time**? They give different results and only the first can support a legally valid kWh invoice. Doc A notes Vietnamese Circular 03/2024/TT-BKHCN classifies EV-charging energy-measuring devices as group-2 measuring instruments requiring verification under the Law on Measurement.

Check without waiting for the vendor: compare `(meterStop − meterStart)` with (a) the kWh on the charger screen and (b) the sum/integral of the `MeterValues` series. If all three agree within a small error they probably share a source; if not, investigate. Acceptance threshold in doc A: **< 1 % deviation**.

🔎 Extra evidence to chase: on the sample unit's **CCU** screen `meter_protocol = none` (with a `meter_address` filled in), and the *Charging Setting* screen has an unticked **"High precision electric meter"**. That may mean the DC meter is not read over a meter protocol on that unit. Ask the vendor explicitly.

**Meter-related codes = "suspect session".** Standard `PowerMeterFailure` → stop billing that gun and mark the session suspect. Vendor codes touching the meter: error 23 / 24 *Power Meter Failure*; stop reasons 43 *energy meter communication timeout*, 63 *Meter metering jump*, 64 *"meter exceeds 5 degrees"* (🔎 "degrees" is very likely a machine translation of Chinese 度 = kWh), 65 *Meter metering stop*.

## 4.7 Faults and alarms
**Standard OCPP `ChargePointErrorCode` values listed by the manual (13)** — their presence is evidence that OCPP was really implemented, not just claimed.

| errorCode | Manual's remedy | Recommended reaction |
|---|---|---|
| ConnectorLockFailure | Re-plug gun, check for damage | Suggest the driver re-plug; try `UnlockConnector` |
| EVCommunicationError | Restart device, capture packets, analyse | Log fully; alert if repeated on the same vehicle |
| GroundFailure | Check the ground wire | **Block the gun immediately**; safety alert to operations |
| HighTemperature | Stop charging 30 min, restart, check modules | Set the gun `Unavailable`; operations alert |
| InternalError | On-site inspection | Auto-create a technical ticket |
| OverCurrentFailure | Check meter and shunt match/damage | High alert — measurement reliability |
| OverVoltage | Check input voltage | Grid-quality alert for the site |
| PowerMeterFailure | Check the energy meter | **Stop billing this gun; flag the session suspect** |
| PowerSwitchFailure | Check the AC contactor | Technical ticket |
| ReaderFailure | Check the card reader | Low — RFID authentication only |
| ResetFailure | Re-send restart | Bounded retry, then alert operations |
| UnderVoltage | Check input voltage | Grid-quality alert for the site |
| WeakSignal | Check the 4G module signal | Log; if repeated flag the site as weak-signal |

*General OCPP 1.6 knowledge, not from the docs:* the standard enum also has `NoError`, `OtherError` and `LocalListConflict`; `NoError` accompanies ordinary non-fault status updates, so accept all values, not just these 13.

**Vendor-specific codes.** Beyond the 13, the manual lists **80 vendor entries**: **error codes 1–28** and **stop reasons 29–80** (full list in Appendix A). Per doc A they normally travel in `vendorErrorCode`; ⚠️ how the stop reasons surface (`StopTransaction.reason`, `vendorErrorCode`, `info`…) is unknown and must be captured.

Problems in the vendor table (block automatic alerting): #23 and #24 share the name "Power Meter Failure", many remedy cells are blank or "none", all stop reasons have no remedy, and the English is machine-translated. **Needed:** a complete CSV/JSON mapping of all 80 internal codes to `vendorErrorCode` with (Vietnamese) descriptions. Until then: log the raw `vendorErrorCode` and build the mapping from real data.

Not every "stop reason" is a fault: #66 *BMS – required SOC target reached*, #67 *total-voltage set-point reached*, #68 *monomer-voltage set value reached* are **normal completions initiated by the BMS**; #69 *BMS charger abort (CST frame)* is a BMS-initiated abort.

## 4.8 Remote operation and monitoring
- **Remote commands over OCPP:** start/stop a session, reset (soft/hard), change availability, unlock a stuck gun, read/change configuration, clear cache; optionally trigger messages, set charging profiles, update firmware, fetch diagnostics, reserve a gun (see §4.1 — profiles are unconfirmed).
- 🔎 **Vendor operations platform** (Chinese title 充电桩管理系统; marketing scope per manual §7.3: real-time monitoring with map view, remote start/stop, device restart, firmware upgrade, power adjustment, automatic fault detection with alarm-ticket generation and closed-loop handling records). Screenshots show:
  - **Dashboard:** total / online / charging / fault station counts; 7- and 30-day trend (sessions + kWh); status distribution (Online, Charging, Fault, Offline).
  - **Charger list:** serial number, Charge-Box ID (O&M), online/offline, **last heartbeat**, per-connector status for 0, 1, 2 with connector model (`GBT`), customer remark, usage type (`Production`); row actions: Restart, Firmware Upgrade, Log History, Diagnostic Log, Live Order, Order History, Edit Remark.
  - **Order history:** transaction ID, connector ID and model, start/end time, start/stop value; per-transaction detail (§4.6 sample) with *Export Excel*.
  - **Quirks worth designing against:** an **offline** charger still shows its last connector statuses as `Faulted` for 0, 1 and 2 → last-known status is **stale when a charger is offline**; some orders have **no end time and no stop value** (open/orphaned sessions); **transaction IDs have mixed shapes** (short integers like `5424`/`5425`, long ~20-digit strings, and empty IDs) — OCPP 1.6 `transactionId` is a CSMS-assigned integer, so long/empty IDs point to offline-generated or vendor-mode records.

## 4.9 Time, offline behaviour and local records
| Topic | Detail |
|---|---|
| Clock | Synced via `BootNotification.currentTime` and `Heartbeat`. Test with a deliberately wrong charger clock: are timestamps of *old* records shifted or timezone-skewed? 🔎 The HMI has a timezone selector (`UTC` on the sample) |
| Offline | The manual (§8.4) states records are buffered and pushed on reconnection but **does not say how much can be buffered** → measure it (Phase 3) |
| Local records | 🔎 Info Service screen keeps charging records (with begin/end SOC), alarm records and charging passwords; they can be exported or **cleared** on the device |
| Local tariff | 🔎 48-slot (30-min) price table in CNY on the HMI; decide how the charger's local price relates to the platform's price (displayed price must equal billed price) |
| Config drift | Save every `GetConfiguration` result — a changed configuration may indicate someone altered the charger |

**Recommended data capture** (minimum, from doc A; extend later, but don't wait for a perfect design before logging):
| Table | Content | Notes |
|---|---|---|
| `ocpp_raw_log` | **Verbatim** message in/out, `chargePointId`, direction, **received** timestamp | **Append-only, never edited.** Root evidence |
| `charge_point` | `chargePointId`, vendor, model, firmware, serial, last boot, online state | From BootNotification + Heartbeat |
| `connector` | `chargePointId`, `connectorId`, current status, `errorCode`, `vendorErrorCode`, changed-at | Source for free-gun counts |
| `charge_session` | `transactionId`, `chargePointId`, `connectorId`, `idTag`, VIN, start/end, `meterStart`, `meterStop`, reason | Append-only |
| `meter_value` | `transactionId`, timestamp, measurand, value, unit, **context**, location, phase | Largest table; consider a time-series DB |
| `charge_point_config` | `chargePointId`, key, value, readOnly, read-at | History of every `GetConfiguration` |
| `tariff_version` | Tariff version applied per session | Keep ≥ 5 years, never hard-code |

Why keep raw messages: only they can arbitrate a dispute between G3, the charger vendor and the customer; Chinese-vendor stacks commonly deviate slightly from the standard and only raw logs show it; session records and evidence must be immutable. Keep the `context` field on meter values (`Transaction.Begin`, `Sample.Periodic`, `Transaction.End`, `Sample.Clock`) — it separates periodic samples from the closing reading.

**Design principle from doc A:** build a **per-vendor adapter layer** from day one; all Willdigits deviations from OCPP then live in one layer, so a second charger brand means a new adapter, not a rewritten core.

## 4.10 Maintenance and troubleshooting (operator view)
- **Before any maintenance:** disconnect upstream power, **wait ≥ 5 min** for capacitors to discharge, and confirm DC-bus voltage **< 60 V** with a multimeter.
- **Daily/weekly (operator):** visual check of enclosure and door locks; display and indicators; gun head and cable sheath; E-stop in the released position; fan noise; wipe screen/exterior with a dry soft cloth (no liquid sprayed).
- **Quarterly/semi-annual (qualified technician):** internal dust cleaning (vacuum or low-pressure dry air); re-torque terminals; inspect contactors/relays/boards for discoloration or leakage; fans; door and cable-entry seals; grounding ≤ 4 Ω; test E-stop and RCD.
- **Long inactivity:** disconnect power, clean and dry, holster the gun with the protective cap, brief monthly power-up.
- **Wear parts:** fuses (identical spec only); gun connector (original/certified); fans (identical spec).
- Quick troubleshooting — see Appendix B. Contact after-sales for smoke/odour, an immediately tripping breaker, a live-feeling enclosure, an uncleared critical fault, a gun that cannot be removed, or anything unresolved; have model + serial (nameplate), symptom + fault code, and site + contact ready.

---

# Part 5 — Verifying the device (test plan and acceptance)

## 5.1 Four phases (goal: real data as early as possible)
| Phase | Duration / needs | Work | Exit |
|---|---|---|---|
| **0 — Preparation** | 1 week; no charger needed — **start now** | Stand up a test CSMS (SteVe — open-source Java — or a self-written one on an existing OCPP library for Python/Node/Java); build an OCPP charge-point **simulator**; write the raw logger and schema (§4.9); prepare a public endpoint with stable address and TLS certificates (test `ws://` and `wss://`); fix the `chargePointId` naming convention; pre-write automatic check scripts for the §5.3 checklist | A working CSMS that captures and decodes the whole Core Profile against the simulator |
| **1 — Connect, no charging** | 3–5 days; powered charger + network | Procedure in §3.7 | No "to verify" rows left in §5.4 (OCPP side) |
| **2 — Real sessions** | 1 week; a real vehicle or DC dummy load | First session started at the charger (capture Preparing → Available); check `SoC` in MeterValues (if missing, add `SoC` to `MeterValuesSampledData` and re-read); reconcile the **three figures** (§4.6); if the vehicle supports it, compare start/end SOC from vehicle telematics with charger SoC; session via `RemoteStartTransaction` and measure command → `Charging` latency; stop via `RemoteStopTransaction` and inspect `reason`; charge both guns at once and read each `Power.Offered` to check load sharing and first-plugged priority | A complete session record, matching kWh, measured latency of every step |
| **3 — Edge cases** | 1 week | Table below | Know how the device behaves in real-world failures |

## 5.2 Phase 3 edge cases
| Scenario | How to create | Observe |
|---|---|---|
| Unplug mid-session | Pull the gun while charging | `StopTransaction` sent? reason? auto-unlock? |
| Network loss mid-session | Remove the 4G antenna / block network for 5 min | Does charging continue? On reconnect, are records and MeterValues back-filled? |
| Long outage | Block network 2–4 h across several sessions | How many sessions are buffered? Any data lost? |
| Emergency stop | Press E-stop while charging | Error code, status, recovery procedure |
| Charger reboot mid-session | Soft `Reset` while charging | Is the session closed properly or orphaned? |
| CSMS down | Stop the CSMS for 10 min | Retry period? Does the charger give up? |
| Invalid `idTag` | Send an unknown idTag | Behaviour; does `StopTransactionOnInvalidId` work? |
| Wrong charger clock | Set a skewed time, let Heartbeat sync | Are old records' timestamps shifted/timezone-skewed? |
| Two guns at once | Charge in parallel, then drop one | Does power move to the other gun? How long does it take? |

Outage handling matters especially because the transport routes cross weak-signal areas; the manual claims offline storage without giving capacity, so it must be **measured**.

## 5.3 Acceptance checklist
Items marked **[BLOCK]** must pass, or the charger is not signed off. Use for the sample and every later installation.

| # | Item | Pass criterion | Blocker |
|---|---|---|---|
| 1 | WebSocket connection | Charger connects to the CSMS by itself and stays stable **≥ 24 h** | ✔ |
| 2 | BootNotification | Receives vendor, model, firmware, serial | ✔ |
| 3 | GetConfiguration | Full response incl. `SupportedFeatureProfiles` | ✔ |
| 4 | StatusNotification | Correct for connectorId 0, 1, 2; latency **≤ 30 s** | ✔ |
| 5 | Periodic MeterValues | Adjustable to **≤ 30 s** and stable | ✔ |
| 6 | Energy measurand | `Energy.Active.Import.Register` present, clear unit | ✔ |
| 7 | SoC measurand | `SoC` present in MeterValues while charging | ✔ |
| 8 | RemoteStart | CSMS command starts a real session | ✔ |
| 9 | RemoteStop | CSMS command stops a running session | ✔ |
| 10 | kWh agreement | Three sources within **< 1 %** | ✔ |
| 11 | HMI password changed | `77777777` no longer used; unique per charger | ✔ |
| 12 | WSS / TLS | Connects over `wss://` | |
| 13 | Smart Charging | `SetChargingProfile` really limits power | |
| 14 | Remote Trigger | `TriggerMessage` works | |
| 15 | Firmware Management | `GetDiagnostics` retrieves logs remotely | |
| 16 | Reservation | `ReserveNow` locks the gun | |
| 17 | Offline back-fill | After a 30-min outage, reconnect pushes all records | |
| 18 | Unplug mid-session | Session closed correctly, clear reason | |
| 19 | Dual-gun load sharing | Observable via `Power.Offered` | |
| 20 | Error-code table | Every `vendorErrorCode` maps to a (Vietnamese) description | |

## 5.4 Confirmation status (what can start now vs what must wait)
| Item | Status | Source |
|---|---|---|
| CCS2 connectors | ✅ Confirmed | Vendor spec sheet + manual §2.4 |
| OCPP 1.6J | ✅ Confirmed (verbal only) | Vendor conversation; 🔎 HMI shows `OCPP1.6` |
| Can point to a third-party CSMS | ✅ Confirmed | Manual §7.2.4 |
| Own DC meter | ✅ Confirmed | `PowerMeterFailure` code, "Meter calibration" menu |
| Reads vehicle SOC | Very likely | Manual §7.2.2 (screen shows SOC); 🔎 vendor-platform sample has SOC |
| Smart Charging profile | ⚠️ To verify | Not in the docs |
| Reservation profile | ⚠️ To verify | Not in the docs |
| Firmware Management profile | ⚠️ To verify | Vendor platform has OTA; unclear over OCPP |
| `SoC` measurand in MeterValues | ⚠️ To verify | Not in the docs |
| MeterValues interval ≤ 30 s | ⚠️ To verify | Not in the docs |
| WSS / TLS | ⚠️ To verify — **HIGH RISK** | OCPP 1.6J defaults to `ws://` |
| `meterStart/Stop` from the meter | ⚠️ To verify — **HIGH RISK** | Determines invoice validity |

---

# Part 6 — What is still unknown / must be requested from the vendor

## 6.1 Impact if the unknowns turn out negative (doc A's fallbacks)
| Risk | Level | Fallback |
|---|---|---|
| No Smart Charging | HIGH | Backend cannot force power reduction; fall back to soft steering (recommend the driver switch chargers). Record it as a product limitation |
| No `SoC` measurand | HIGH | Reconciliation becomes two-way (charger ↔ payment), supplemented by vehicle-telematics SOC at plug-in/out. Less accurate, still usable |
| Only unencrypted `ws://` | HIGH | Site-to-site VPN or carrier private APN for the charger SIMs; adds operating cost |
| `meterStart/Stop` not from a verified meter | **VERY HIGH** | No kWh invoices. Install a verified sub-meter, or change the revenue model from selling electricity to a **service fee**. An executive-level decision |
| Deviations from standard OCPP | MEDIUM | Per-vendor adapter layer |
| No Firmware Management | MEDIUM | Accept on-site updates; include in operating cost and vendor SLA |
| Incomplete/duplicated error-code table | MEDIUM | Ask the vendor for a full CSV/JSON; meanwhile log raw `vendorErrorCode` |

## 6.2 Requests to the vendor (via procurement)
| # | Request | Why | Level |
|---|---|---|---|
| 1 | Willdigits **OCPP Implementation Guide / Interface Document** | Lists every message, configuration key and each deviation from the standard (not a datasheet) | **BLOCK** |
| 2 | Mapping of the 80 internal error codes → `vendorErrorCode` as CSV/JSON | The manual table has a duplicate (23/24) and blanks; automatic alerts cannot be built | **BLOCK** |
| 3 | DC meter: brand, model, accuracy class, tamper seal | Decides whether a legal kWh invoice is possible | **BLOCK** |
| 4 | Confirmation of `wss://` and the authentication mechanism (Basic Auth or client certificate) | Session data and commands cross public 4G | **BLOCK** |
| 5 | List of supported OCPP profiles, **in writing** | Sets the scope of load balancing, reservation, remote configuration | HIGH |
| 6 | Multi-level permission mechanism on the HMI | Separate operating rights from meter-calibration rights | HIGH |
| 7 | Does VIN Autocharge run over OCPP or a proprietary mechanism? | If OCPP, the remote-start flow shortens significantly | MEDIUM |
| 8 | Offline buffer capacity (sessions / days) | Reconciliation design for weak-signal areas | MEDIUM |
| 9 | Does the vendor use `DataTransfer` for custom features? | May hold useful non-standard features | LOW |

---

# Part 7 — Other facts worth remembering

## 7.1 Safety essentials (manual §1)
- **High voltage — danger of shock, burns, death.** Install/maintain only by trained, certified electricians; no unauthorised disassembly/modification; read the manual before operating.
- Reliable grounding to a PE system; correctly rated upstream breaker; copper input cable sized for maximum current and voltage drop.
- Not for explosive atmospheres; keep away from standing water/frequent spray despite IP54/IP55; ensure ventilation clearances; keep ambient temperature within range.
- Inspect gun and cable before every charge; hold the gun body; dry hands; supervise children; **emergency = press E-stop and cut upstream power**.
- EMC: complies with IEC 61851-21-2; people with pacemakers/implanted devices should consult their physician and keep a safe distance.
- Before opening: isolate power, wait ≥ 5 min, verify DC bus < 60 V. For faults involving internal electrical parts, or smoke/odour/sparking: press E-stop, disconnect upstream power, call qualified service — do not self-repair.

## 7.2 Warranty (manual §11)
- Term written as **`[1]` year** from purchase (the template's square brackets remain). Free repair/replacement for quality issues under normal use.
- **Excluded:** failure to install/use/maintain per the manual; force majeure (lightning, flood, earthquake, fire, abnormal voltage); unauthorised disassembly/modification/repair; non-original accessories (e.g. guns); damage from external grid abnormalities or vehicle faults; normal wear (cosmetic parts, cable sheath, fans); no proof of purchase.
- The after-sales contact section (company, hotline, email, website, address) and the product-information record (model, SN, manufacture/purchase date, dealer, address) are **blank templates** — fill the record per unit.

## 7.3 Certifications and glossary
- Appendix of the manual lists **CE (Europe), CB (Europe), CCC (China)**; the spec table lists CE (UKCA/UL optional) — they do not match (see §7.5).
- Glossary highlights: AC/DC, kW vs kWh, SOC, BMS, OBC, CCS, CHAdeMO, GB/T, NACS, Type 1/2, RCD/GFCI, IC-CPD, OCPP, ISO 15118 / Plug & Charge, V2G, IP rating, PE, L/N, CE, UL, FCC, UKCA, IEC 61851, UL 2202/2594.

## 7.4 Three blockers outside the software scope (doc A §10.1)
1. **Vehicle inlet standard.** Whether the target trucks (Tri-Ring EVT-262/400/825) use CCS2 or GB/T is unconfirmed. The charger is confirmed CCS2; if the trucks are GB/T, both meet their own specs but cannot charge together (the manual's troubleshooting table lists vehicle/station incompatibility). 🔎 The vendor-platform test units in the manual show `GBT` connectors — the vendor sells both.
2. **DC meter verification in Vietnam.** If the meter cannot be verified, kWh-based billing and invoicing lose their legal basis.
3. **Warranty and technical support in Vietnam.** Manual §11.3 (contact) is entirely blank; the warranty is `[1]` year with the template brackets.

## 7.5 Inconsistencies and quality problems in the source documents
| Where | Problem | Consequence |
|---|---|---|
| Manual §2.1 | Output range "60 kW to 240 kW" although the manual covers 240–480 kW | Likely copied from an older model; verify on the nameplate |
| Manual, temperatures | Operating −30…+75 °C (spec table) vs −30…+70 °C (§1.3); storage −40…+75 °C (table) vs −40…+70 °C (§4.1) | Ask the vendor for the correct figures |
| Manual §2.3 | Editorial instruction still present ("Insert product front view…") | Unfinished document |
| Certifications | Spec table: CE (UKCA/UL optional); appendix: CE, CB, CCC | Do not rely on certification claims without certificates |
| Manual §2.2 | OCPP described as **optional** | Make sure the OCPP option is ordered/enabled |
| Manual fault table | Duplicate #23/#24, blank remedies, garbled translation ("matchor", "examine4GIs", "Replug and replug") | Cannot automate alerts from it |
| Manual breaker table | 320 and 360 kW both ≥ 630 A; 480 kW ≥ 2×500 A (doc A's summary omits 480 kW) | Use the manual's full table |
| Manual vs screenshots | Manual dated 2025-01-11; screenshots show firmware from 2025-10 to 2026-02-27 | The manual may not describe the delivered firmware |
| Screenshots vs sample | Test unit in screenshots is GB/T, 2-module, ≈ 100 kW cap; CCU/PCU show module minimum 200 V while the series claims 150 V minimum output | Not the CCS2 240 kW sample; do not copy values |
| Doc A conclusion | Source has internal contradictions → **verify every important parameter against the real nameplate**, not the paper | Standing rule |

Doc A is a living document: after each test phase, update the confirmation-status table and the acceptance checklist, bump the version, and record every deviation from the OCPP standard in its own section as input for the adapter layer.

---

## Appendix A — Vendor-specific codes (manual §10.2)

Numbering is the manual's own (continuous 1–13 standard, then vendor 1–28 and stop reasons 29–80). Remedy text is the manual's; blank = blank in the source.

### A.1 Vendor error codes (1–28)
| # | Description | Manual remedy |
|---|---|---|
| 1 | CCU communication failure | Check whether the communication is loose |
| 2 | Card-reader communication failure | Check whether the card-reader connection is loose |
| 3 | Gun connection failure | Check whether the gun head is damaged |
| 4 | Module hardware failure | Check whether the module is damaged |
| 5 | Module communication failure | Check whether module communication is damaged |
| 6 | Parallel switch fault | Check whether the AC-contactor coil is damaged |
| 7 | Discharge circuit fault | Check whether the module is damaged |
| 8 | Discharge switch adhesion failure | Check whether the module is damaged |
| 9 | Charge switch adhesion failure | Check whether the DC contactor closes normally |
| 10 | AC switch adhesion failure | Check whether the AC circuit breaker closes normally |
| 11 | Arrester failure | Check whether the lightning-arrester light is on |
| 12 | Fuse failure | Check whether the fuse is blown |
| 13 | HMI communication timeout error | Check the display communication line |
| 14 | Door opened | Check whether the access switch is loose |
| 15 | Emergency stop fault | Check whether the E-stop is pressed |
| 16 | Communication timeout between CCUs | Check whether the controller communication line is loose |
| 17 | Charge error | Capture charging messages on site |
| 18 | Insulation warning | Restart |
| 19 | Input overvoltage | Check whether the AC line voltage is normal |
| 20 | Input undervoltage | Check whether the AC line voltage is normal |
| 21 | Jlcl-2 communication timeout | none |
| 22 | Gun temperature inspection timeout | Check whether the gun head is damaged |
| 23 | Power Meter Failure | Check whether the DC meter works properly |
| 24 | Power Meter Failure *(duplicate name)* | none |
| 25 | CCU communication between pile cabinets timeout | none |
| 26 | Transfer switch pulled-in fault | none |
| 27 | AC power loss | Restart the power supply |
| 28 | Change-over switch off fault | none |

### A.2 Stop reasons (29–80) — no remedies given in the manual
| Group (this summary's grouping; the manual gives a flat list) | Codes and names |
|---|---|
| Charger hardware / infrastructure | 29 gun connection failure · 30 module hardware failure · 31 module communication failure · 32 parallel switch fault · 33 discharge circuit failure · 34 discharge switch adhesion · 35 charge switch adhesion · 36 AC switch adhesion · 37 arrester failure · 38 fuse failure · 39 HMI communication timeout · 40 door open · 41 emergency stop fault · 42 power-distribution timeout |
| Meter (⇒ treat the session as suspect) | 43 energy-meter communication timeout · 63 meter metering jump · 64 "meter exceeds 5 degrees" (likely 5 kWh) · 65 meter metering stop |
| Battery / BMS-side errors | 44 battery reverse connection · 45 battery matching error · 46 BMS communication error · 47 BMS charge-preparation timeout · 48 BMS prohibit-charge timeout · 49 BMS parameter not suitable for this charger · 50 BMS monomer voltage abnormality · 51 battery current too high · 52 battery temperature too high · 53 BMS insulation error · 54 BMS connection error · 55 BMS charging voltage too high · 61 battery SOC abnormal |
| Charger output / lock | 56 insulation failure error · 57 output voltage cannot reach expected · 58 E-lock not locked · 59 charging switch not closed · 60 charging gun temperature too high · 62 parallel synchronisation error |
| **Normal / BMS-initiated completion** | 66 BMS – required SOC target reached · 67 BMS – total-voltage set-point reached · 68 BMS – monomer-voltage set value reached · 69 BMS charger abort (CST frame received) |
| BMS faults | 70 BMS insulation fault · 71 BMS output-connector over-temperature · 72 BMS element and output-connector over-temperature · 73 BMS charging-connector failure · 74 BMS battery-pack over-temperature · 75 BMS high-voltage relay fault · 76 BMS detection-point-2 voltage detection fault · 77 BMS other faults · 78 BMS excessive current · 79 BMS abnormal voltage · 80 BMS unknown reason |

## Appendix B — Manual troubleshooting quick reference (§10.1)

| Symptom | Possible causes → remedy |
|---|---|
| Unit dead, screen off | Upstream breaker tripped/off → reset; input phase loss/abnormal voltage → measure; internal fuse blown → qualified personnel |
| Charging won't start | E-stop pressed → rotate clockwise; gun not fully inserted → re-insert until "click"; vehicle/station incompatibility → check connector type and protocol; BMS communication failure → re-plug/restart the vehicle; card/APP authentication failed → check card permissions/account |
| Stops mid-session | BMS stop (battery full — normal); grid fluctuation → check upstream supply; charger over-temperature protection → check fans/ambient/air intakes; output over-voltage/over-current protection → possible abnormal BMS request or equipment fault, contact service |
| Current too low / slow | BMS demand limit (low temperature, high SOC); power sharing (both guns in use); low grid voltage; charger derating (high internal temperature) |
| Fault code on display | Look the code up in Appendix A / manual §10.2 |
| RCD/GFCI trips | Internal or cable leakage → disconnect, check internal wiring and gun insulation, contact service; RCD itself faulty → replace with an identical rating and test |
| Touchscreen unresponsive | Dirty/wet screen → clean; frozen system → power-cycle via the upstream breaker |
