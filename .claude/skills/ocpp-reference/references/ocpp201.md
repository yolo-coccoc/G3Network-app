# OCPP 2.0.1 for a CSMS, plus what 2.1 adds

> Part of the `ocpp-reference` skill. General protocol knowledge for designing
> and coding 2.0.1 support; what our gateway already does is in
> `ocpp16-willdigits.md` (2.0.1 today: BootNotification and Heartbeat only,
> deferred.md 78). Researched 2026-10-05 without the OCA specification PDFs.
> ✅ checked in the installed `ocpp` library's schemas/enums
> (`backend/.venv/lib/python3.12/site-packages/ocpp/v201/` and `v21/`) or an
> official OCA document · 🔎 secondary source, verify before relying on it.
> **When a message shape matters, read the schema file itself**:
> `ocpp/v201/schemas/<Message>Request.json` / `Response.json`.

2.0.1 (2020; edition 3 = IEC 63584:2024 🔎) has 64 messages (128 schema files ✅). One WebSocket = one version; subprotocol `ocpp2.0.1`.

## 1. Topology
- **Charging Station → EVSE → Connector.** An EVSE is one independently operated outlet: one EV and one transaction at a time. Messages address it as `EVSEType {id, connectorId?}` ✅.
- EVSE ids run 1..n with no gaps, and `evseId 0` is the whole station. Connector ids restart at 1 in each EVSE 🔎.
- `StatusNotification` requires both `evseId` and `connectorId` ✅. `TransactionEvent.evse` is optional but must appear at least once, usually in the first event 🔎.
- **1.6J mapping:** 1.6 `connectorId N` (N ≥ 1) is an independently operated outlet = `evseId N, connectorId 1`; 1.6 connector 0 = `evseId 0`. The dual-gun Willdigits charger = 2 EVSEs × 1 connector. This is what CS-03 already does. Only several sockets on one outlet, used one at a time, give more than one connector per EVSE.

## 2. Device model (replaces GetConfiguration / ChangeConfiguration)
- **Components** (name ≤50, optional `instance` and `evse`) hold **variables** (name ≤50, optional `instance`). Each variable has the attributes `Actual | Target | MinSet | MaxSet` ✅.
- **Reading and writing:**
  - `GetVariables` / `SetVariables` work in batches (set value ≤1000 chars).
  - Set results are `Accepted | Rejected | UnknownComponent | UnknownVariable | NotSupportedAttributeType | RebootRequired` ✅.
- **Inventory:**
  - `GetBaseReport(requestId, ConfigurationInventory | FullInventory | SummaryInventory)` → asynchronous `NotifyReport` parts with `seqNo` + `tbc`. **Reassemble the parts by `requestId`** ✅.
  - Each entry carries the value (≤2500), the mutability and `variableCharacteristics` (dataType, unit, min/max, `valuesList`).
  - `GetReport` filters the inventory; `NotifyEvent` + `Set/ClearVariableMonitoring` let the station push changes and alarms ✅.
- **Controllers** ✅: `OCPPCommCtrlr` (HeartbeatInterval, OfflineThreshold, MessageTimeout, QueueAllMessages), `TxCtrlr` (TxStartPoint, TxStopPoint, EVConnectionTimeOut, StopTxOnInvalidId), `SampledDataCtrlr`, `AlignedDataCtrlr`, `AuthCtrlr`, `AuthCacheCtrlr`, `LocalAuthListCtrlr`, `SecurityCtrlr` (SecurityProfile, BasicAuthPassword, Identity, OrganizationName), `SmartChargingCtrlr`, `ReservationCtrlr`, `TariffCostCtrlr`, `DisplayMessageCtrlr`, `ISO15118Ctrlr`, `DeviceDataCtrlr`, `ClockCtrlr`, `MonitoringCtrlr`.
  - Physical components: `ChargingStation`, `EVSE`, `Connector`, `ConnectedEV` ✅.
  - A full report is the only reliable way to learn what a charger supports 🔎.
- **Storage:** key configuration rows by (component, component instance, evseId, connectorId, variable, variable instance, attribute type). `charging_station_configuration_entries` keyed by a 1.6 `config_key` does not generalise.

## 3. Transactions
| | 1.6J | 2.0.1 |
|---|---|---|
| Messages | StartTransaction, StopTransaction, MeterValues | one `TransactionEvent`, `eventType` = Started / Updated / Ended ✅ |
| `transactionId` | integer from the CSMS (our sequence, CE-03) | **string ≤36 chosen by the station** (usually a UUID) ✅; unique per station, not globally |
| Ordering | none | `seqNo` required ✅, 0, 1, 2… per transaction 🔎 |

- **Required fields** ✅: `eventType`, `timestamp`, `triggerReason`, `seqNo`, `transactionInfo {transactionId, chargingState?, timeSpentCharging?, stoppedReason?, remoteStartId?}`.
- **Optional fields** ✅: `meterValue[]`, `offline`, `numberOfPhasesUsed`, `cableMaxCurrent`, `reservationId`, `evse`, `idToken`.
- **triggerReason** ✅: Authorized, CablePluggedIn, ChargingRateChanged, ChargingStateChanged, Deauthorized, EnergyLimitReached, EVCommunicationLost, EVConnectTimeout, MeterValueClock, MeterValuePeriodic, TimeLimitReached, Trigger, UnlockCommand, StopAuthorized, EVDeparted, EVDetected, RemoteStop, RemoteStart, AbnormalCondition, SignedDataReceived, ResetCommand.
- **stoppedReason** ✅: DeAuthorized, EmergencyStop, EnergyLimitReached, EVDisconnected, GroundFault, ImmediateReset, Local, LocalOutOfCredit, MasterPass, Other, OvercurrentFault, PowerLoss, PowerQuality, Reboot, Remote, SOCLimitReached, StoppedByEV, TimeLimitReached, Timeout. It's omitted when the reason is Local 🔎.
- **When a transaction starts and stops:** `TxCtrlr.TxStartPoint` / `TxStopPoint` ∈ `ParkingBayOccupancy | EVConnected | Authorized | DataSigned | PowerPathClosed | EnergyTransfer` ✅. The first listed point to happen starts it.
  - With `EVConnected`, **a transaction exists before anyone authorizes**, and the `idToken` arrives later in an `Updated` event with `triggerReason = Authorized` 🔎. Design sessions to accept "no token yet".
- **Offline:** the station queues events and replays them in order with `offline = true` ✅ (flag) / 🔎 (replay). `GetTransactionStatus` returns `messagesInQueue` and `ongoingIndicator` ✅.
- **Meter values:**
  - They travel inside `TransactionEvent.meterValue`, with the same `SampledValue` as 1.6 plus `signedMeterValue` ✅.
  - Which measurands are sent, and how often, is set by `SampledDataCtrlr.TxStartedMeasurands / TxUpdatedMeasurands / TxUpdatedInterval / TxEndedMeasurands / TxEndedInterval` ✅.
  - `Ended` may repeat a summary of readings 🔎.
  - Readings outside a transaction (clock-aligned) use `MeterValues(evseId, meterValue[])` ✅.
  - A missing unit on energy means Wh 🔎.
- **Response:** may return `idTokenInfo` (revalidation), `totalCost`, `chargingPriority` and `updatedPersonalMessage` ✅.

## 4. Status
- `StatusNotification.connectorStatus` has 5 values ✅: `Available | Occupied | Reserved | Unavailable | Faulted`. Charging detail moved to `TransactionEvent.transactionInfo.chargingState` ✅: `Charging | EVConnected | SuspendedEV | SuspendedEVSE | Idle`.
- 1.6 → 2.0.1 mapping 🔎:

| 1.6 status | 2.0.1 equivalent |
|---|---|
| Preparing | Occupied + EVConnected |
| Charging | Occupied + Charging |
| SuspendedEV / SuspendedEVSE | Occupied + SuspendedEV / SuspendedEVSE |
| Finishing | Occupied + Idle (or EVConnected) |

- There is **no errorCode** in 2.0.1 status: faults come through `NotifyEvent` and device-model variables 🔎.
- Operating status is set by `ChangeAvailability(operationalStatus = Operative | Inoperative, evse?)` ✅.
- **G3 note:** our `chargingconnectorstatus` enum mixes the 5 values of 2.0.1 with the 1.6 values (CS-04 / D4). `Occupied` alone does not mean charging; with 2.0.1, "charging" must come from `chargingState`.

## 5. Authorization
- **IdToken** ✅: `{idToken ≤36, type, additionalInfo[]?}`, where `type` is `Central | eMAID | ISO14443 | ISO15693 | KeyCode | Local | MacAddress | NoAuthorization`. An empty string is used for NoAuthorization 🔎.
- **Authorize** ✅: `Authorize(idToken, certificate?, iso15118CertificateHashData?)` → `idTokenInfo {status, cacheExpiryDateTime, chargingPriority, evseId[], groupIdToken, personalMessage}`.
  - Status values: `Accepted | Blocked | ConcurrentTx | Expired | Invalid | NoCredit | NotAllowedTypeEVSE | NotAtThisLocation | NotAtThisTime | Unknown`.
- **Offline authorization** ✅: the authorization cache (`ClearCache`), the local list (`SendLocalList` Full/Differential + `versionNumber`, `GetLocalListVersion`), and `AuthCtrlr.LocalAuthorizeOffline` / `OfflineTxForUnknownIdEnabled`.
- **Remote start** ✅: `RequestStartTransaction(idToken, remoteStartId, evseId?, groupIdToken?, chargingProfile?)`. **Both `idToken` and `remoteStartId` are required.**
  - The response is Accepted / Rejected, plus a `transactionId` when one already exists (cable plugged in under `TxStartPoint = EVConnected`).
  - The station echoes `transactionInfo.remoteStartId` in its TransactionEvent: **that is how the CSMS ties the app's start request to the transaction.**
  - `AuthCtrlr.AuthorizeRemoteStart` decides whether the station also sends Authorize.
- **Remote stop:** `RequestStopTransaction(transactionId)` ✅.
- **QR/app start (G3's launch flow, CO-13):** the app authenticates the user, then the CSMS sends `RequestStartTransaction` with a token it owns, typically `type = Central` with a per-start value 🔎. Version 2.1 adds proper ad-hoc payment flows.

## 6. Smart charging
- **Profile** ✅: `{id, stackLevel, chargingProfilePurpose, chargingProfileKind (Absolute | Recurring | Relative), recurrencyKind?, validFrom/To?, transactionId?, chargingSchedule[]}`.
- **Schedule** ✅: `{id, chargingRateUnit W | A, chargingSchedulePeriod[{startPeriod, limit, numberPhases?, phaseToUse?}], startSchedule?, duration?, minChargingRate?, salesTariff?}`. DC trucks normally use W.
- **Purposes** 🔎 (rules):
  - **ChargingStationMaxProfile** goes on evseId 0 and limits the whole charger.
  - **TxDefaultProfile** on evseId 0 applies to *each* EVSE.
  - **TxProfile** needs an active transaction and is not allowed on EVSE 0.
  - **ChargingStationExternalConstraints** comes from a local EMS or grid and is only reported to the CSMS, which never sets it.
  - Within a purpose the higher `stackLevel` wins; the composite schedule is the minimum across purposes.
- **Messages** ✅: `SetChargingProfile`, `GetChargingProfiles` → `ReportChargingProfiles`, `ClearChargingProfile`, `GetCompositeSchedule`, `NotifyChargingLimit`, `ClearedChargingLimit`, `NotifyEVChargingNeeds`, `NotifyEVChargingSchedule`. Remote start can carry a TxProfile.
- **Site or station power limits: OCPP has no site object.** The CSMS models the grid connection itself and pushes a `ChargingStationMaxProfile` per charger, or `TxProfile`s per session, re-sending them as sessions start and stop. Alternatively, an OCPP Local Controller (a proxy at the site) does it 🔎.
- Check per charger: `SmartChargingCtrlr.RateUnit`, `PeriodsPerSchedule`, `ProfileStackLevel`, `Entries` ✅.

## 7. Security profiles
| Profile | Transport | CSMS authenticated by | Charger authenticated by |
|---|---|---|---|
| 1 | `ws://`, **no TLS** | — | HTTP Basic auth |
| 2 | `wss://`, TLS ≥1.2 | server certificate | HTTP Basic auth |
| 3 | `wss://`, TLS ≥1.2 | server certificate | client certificate (mutual TLS) |

- **OCA Security Operations Guide (Jan 2026)** ✅:
  - Profile 1 is only for a VPN or private network.
  - "OCPP 2.x without security profile 2 is NOT a valid OCPP 2.x implementation."
  - TLS 1.3 is allowed.
  - The Basic auth username is the charger identity; `BasicAuthPassword` is ≥16 chars.
  - Downgrade only 3→2, and only when `AllowSecurityProfileDowngrade = true`; never back to profile 1 over OCPP.
  - **The CSMS must refuse unknown identities and handle a second WebSocket from the same identity.**
  - Client certificate: CN = charger identity, O = organization.
  - **Never log the Basic auth password or the Authorization header.**
- **Messages** ✅:
  - `SecurityEventNotification(type ≤50, timestamp, techInfo ≤255)`, with types such as FailedToAuthenticateAtCsms, InvalidCsmsCertificate, TamperDetectionActivated, InvalidFirmwareSignature, ResetOrReboot, SettingSystemTime.
  - Certificates: `SignCertificate`, `CertificateSigned`, `InstallCertificate`, `DeleteCertificate`, `GetInstalledCertificateIds`.
  - ISO 15118: `Get15118EVCertificate`, `GetCertificateStatus`.
  - `SetNetworkProfile` moves a charger to a new URL or profile.
- For G3's 1.6J charger, `wss://` and authentication are still unconfirmed (STN-11, deferred.md 73, open question 4).

## 8. Functional blocks: launch vs later
| Block | Messages ✅ | When |
|---|---|---|
| Provisioning | BootNotification (`Accepted / Pending / Rejected`), Heartbeat, Get/SetVariables, GetBaseReport, Reset (`Immediate / OnIdle`), TriggerMessage | launch |
| Transactions, status, authorization | above + `UnlockConnector`, `ChangeAvailability` | launch |
| Vendor extensions | `DataTransfer(vendorId ≤255, messageId?, data?)` | launch |
| Firmware | signed `UpdateFirmware`, `FirmwareStatusNotification` | soon after |
| Diagnostics | `GetLog`, `LogStatusNotification`, monitoring, `CustomerInformation` | soon after |
| Reservation | `ReserveNow` (evseId optional; `connectorType` instead), `CancelReservation`, `ReservationStatusUpdate` | later (STN-09, P1.5) |
| Display, tariff & cost | `Set/Get/ClearDisplayMessage`, `CostUpdated`, `TransactionEventResponse.totalCost`, `TariffCostCtrlr` | later |
| ISO 15118 / Plug & Charge | eMAID tokens, contract certificates, OCSP | later; needs a V2G PKI and an eMSP |

- **Boot while `Pending`:** the CSMS may provision the charger (Get/SetVariables, reports, TriggerMessage) before accepting it. The charger sends nothing else unless triggered 🔎.
- **Plug & Charge vs Autocharge (relevant to VIN Autocharge, deferred.md 90):**
  - Plug & Charge (ISO 15118-2 contract certificates, eMAID) is heavy for a closed fleet 🔎.
  - **Autocharge** has the charger report the vehicle's EVCCID (the MAC address of its communication controller) as `IdToken type = MacAddress` ✅. In 1.6 that's the vendor convention `idTag = "VID:<mac>"` 🔎. Store a MAC → truck link per vehicle.
  - The VIN itself is not in ISO 15118-2 or DIN 70121 🔎. `ConnectedEV.VehicleId` exists ✅ but is rarely filled.

## 9. OCPP 2.1 (January 2025, IEC 63584-210)
- **Compatibility:** 181 schema files locally, 27 new messages for 91 in total ✅. The 2.0.1 messages are kept 🔎. Subprotocol `ocpp2.1`; the RPC layer adds `SEND` and `CALLRESULTERROR` 🔎.
- **New areas** ✅:
  - V2X / bidirectional (`NotifyAllowedEnergyTransfer`, `AFRRSignal`, `DC_BPT` / `AC_BPT`)
  - DER control (`Set/Get/Clear/ReportDERControl`, `NotifyDERAlarm`, `NotifyDERStartStop`)
  - local tariffs and cost (`SetDefaultTariff`, `GetTariffs`, `ClearTariffs`, `ChangeTransactionTariff`, `TransactionEvent.costDetails`)
  - payment (`NotifySettlement`, `NotifyWebPaymentStarted`, `VatNumberValidation`)
  - battery swap (`BatterySwap`, `RequestBatterySwap`)
  - priority charging and dynamic schedules (`UsePriorityCharging`, `UpdateDynamicSchedule`, `PullDynamicScheduleUpdate`)
  - periodic event streams
  - `GetCertificateChainStatus`
- **Changed types** ✅:
  - `IdToken.idToken` grows to ≤255, and `type` becomes a free string (≤20) instead of an enum.
  - New triggerReasons: CostLimitReached, LimitSet, OperationModeChanged, RunningCost, SoCLimitReached, TariffChanged, TariffNotAccepted, TxResumed.
  - New stoppedReason: ReqEnergyTransferRejected.
  - `TransactionType` gains `operationMode`, `tariffId` and `transactionLimit`.
  - About 30 new measurands.
- **Design tip:** size token columns for 2.1 now: `id_token` 255 chars and the token type a free string, not an enum.

## 10. Implementer pitfalls
1. **`seqNo`:** store it per transaction and detect gaps. A late, offline or duplicate event must never reopen a transaction that has `Ended`. CitrineOS had this bug, plus defaulting a missing connectorId to 1 🔎.
2. **Partial transactions:** accept `Updated` / `Ended` for a transaction never seen, because `Started` may be lost or replayed late. Upsert by (charger, `transactionId`), and don't assume an `idToken` on `Started` 🔎.
3. **`transactionId`** is a string ≤36, never an integer, and is unique per charger only. Our `ocpp_transaction_id varchar(255)` + unique (station, id) already fits.
4. **Duplicate meter readings:** values arrive both in `TransactionEvent` and in `MeterValues`, and `Ended` may repeat samples. Deduplicate by timestamp, measurand, phase and context 🔎.
5. **Multi-part messages:** `NotifyReport` / `NotifyEvent` come in parts (`tbc`, `seqNo`). Answer each part at once and assemble afterwards.
6. **Library:** the mobilityhouse library converts camelCase ↔ snake_case with special cases (`ocppCSMSURL`, `SoC`, `V2X`, `SOCLimitReached`). Handlers get snake_case kwargs. Use one ChargePoint class per version (`ocpp.v201`, `ocpp.v21`). The default response timeout is 30 s ✅.
7. **Status ≠ charging state** (§4).
8. **Provisioning:** use the `Pending` boot to set variables and pull a `FullInventory` before accepting the charger 🔎.

## Sources
- Local `ocpp` library 2.1.0: `v201/schemas/*.json` (TransactionEvent, StatusNotification, Request{Start,Stop}Transaction, Authorize, SetChargingProfile, GetCompositeSchedule, Get/SetVariables, GetBaseReport, NotifyReport, SecurityEventNotification, BootNotification, SetNetworkProfile, certificate messages, ReserveNow, MeterValues, NotifyEvent, TriggerMessage, Reset, ChangeAvailability, GetLog, UpdateFirmware, DataTransfer, display/cost, SendLocalList, GetTransactionStatus, NotifyChargingLimit, NotifyEVChargingNeeds), `v201/enums.py`, `v201/call.py`, `charge_point.py`, `v21/schemas/*.json`
- https://openchargealliance.org/wp-content/uploads/2026/01/ocpp_security_operations_guide-2.pdf
- https://openchargealliance.org/wp-content/uploads/2024/01/new_in_ocpp_201-v10.pdf
- https://openchargealliance.org/ocpp-2-1-is-now-available/ · https://openchargealliance.org/ocpp-2-1-accepted-by-iec-as-iec-63584-210/
- https://www.protocol-c.com/articles/ocpp-201-transactions/ (secondary)
- https://github.com/citrineos/citrineos-core/pull/1078 (seqNo / offline handling)
- Not reachable: the OCPP 2.0.1 Part 2 PDF (HTTP 403); claims that depend on it are marked 🔎.
