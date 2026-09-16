# Feature List — Electric Truck Driver Support System

Status: ✅ Done | 🚧 In progress | 📋 Planned | 💡 Proposed addition (needs your confirmation on whether it's needed)

## Comparison with the current backend baseline

The list below describes the product scope by actor; not every `✅` item
already has a backend API in the repo. As of the 2026-09-15 baseline, the backend already has:

- CRUD for vehicles and telematic devices, including device–vehicle mapping.
- Receiving/storing telemetry via MQTT and an API to read a vehicle's latest telemetry.
- CRUD for the Station → EVSE → Connector topology.
- OCPP 2.0.1, charging session happy path, events, and meter values.

Features without active backend source yet include the entire telemetry history,
vehicle/station map, aggregated connector status, battery/anomaly alerts, threshold
pushes, geofencing, device health, user/RBAC, policy, payment, notification, and frontend.
For details on the deferred items, see [`future.md`](./future.md) and the corresponding planners.

---

## 1. In-vehicle display app (In-vehicle display)

### 1.1 Real-time vehicle status display
- Actor: Driver
- Trigger: Continuously while the vehicle is on, whenever new telemetry arrives
- Input: telemetry stream (battery %, speed, remaining range)
- Output: Visual display on the vehicle screen
- Status: 🚧

### 1.2 Low battery alert
- Actor: Driver
- Trigger: Battery % drops below the configured threshold
- Input: current battery %, vehicle GPS location
- Output: Alert popup + suggestion of the nearest charging station
- Constraints:
  - The alert threshold should be configurable, not hardcoded — it may differ by vehicle type/route
  - The charging station suggestion must also account for station status (whether a slot is free), not just distance
- Status: 📋

### 1.3 Confirm shift start (synced with mobile clock-in)
- Actor: Driver
- Trigger: Vehicle startup
- Input: the driver's clock-in status (retrieved from the mobile app)
- Output: Allow or block vehicle startup
- Business decisions to finalize:
  - Is clock-in **mandatory** before starting the vehicle, or should it just show a warning?
  - If mandatory: how should it handle the mobile app losing connection during clock-in (to avoid the driver getting stuck unable to start the vehicle)?
- Status: 📋

### 1.4 Vehicle technical fault alert
- Actor: Driver
- Trigger: Telemetry reports an error code
- Input: error_code from the vehicle
- Output: Alert categorized by severity (minor/critical)
- Constraints: Critical-level errors may block continued driving (safety); a list of error codes and their corresponding severity levels is needed
- Status: 💡

### 1.5 Directions to a charging station
- Actor: Driver
- Trigger: Driver selects a station from the suggested list
- Input: station_id, the vehicle's current location
- Output: Display map/directions on the vehicle screen
- Constraints: Requires integration with a map provider (Google Maps/Mapbox, etc.)
- Status: 💡

### 1.6 Display charging session status while plugged in
- Actor: Driver
- Trigger: Charging session starts (session is created)
- Input: session_id, real-time power data
- Output: current battery %, estimated time to full
- Status: 💡

### 1.7 Alert for exceeding regulated driving hours
- Actor: Driver
- Trigger: Shift exceeds the allowed time threshold
- Input: shift_id, current shift duration
- Output: Alert + rest reminder
- Constraints: Related to driving-hour regulations/labor safety; the threshold must be configurable
- Status: 💡

---

## 2. Mobile app for drivers

### 2.1 Log in / clock in (clock-in)
- Actor: Driver
- Trigger: Driver opens the app, selects a vehicle when starting a shift
- Input: driver_id, truck_id, authentication method (PIN/account/NFC)
- Output: Create a new "driving shift" record, linked to driver_id + truck_id + start time
- Constraints:
  - A driver cannot open 2 shifts at the same time
  - A vehicle cannot have 2 active drivers at the same time
- Status: 🚧

### 2.2 Clock out at end of shift (clock-out)
- Actor: Driver
- Trigger: Driver taps "end shift"
- Input: shift_id
- Output: Shift transitions to `ended` status
- Constraints: Do not allow ending a shift while a charging session is in progress
- Status: 🚧

### 2.3 View charging session & payment history
- Actor: Driver
- Trigger: Opens the history tab in the app
- Input: driver_id
- Output: List of completed charging sessions with amounts and payment status
- Status: 📋

### 2.4 Payment after charging
- Actor: Driver
- Trigger: Charging session transitions to `completed` status
- Input: session_id, a saved payment method or a newly selected one
- Output: Create a payment transaction; the session's `payment_status` transitions to `paid`;
  the session's operational status `completed` is not changed
- Constraints: Payment is not allowed while the session is not yet `completed`; the amount is calculated per the formula in `docs/business-rules-billing.md`
- Edge cases:
  - Payment fails → `payment_status = failed`, the session is not cancelled and the
    driver can retry
  - More than 24h unpaid → `payment_status = overdue`, the system creates a debt record,
    and locks the ability to start a new charging session (requires admin approval to unlock — related to section 3.6)
- Status: 📋

### 2.5 Push notifications (push notification)
- Actor: Driver
- Trigger: Low battery, charging station fault, payment reminder, shift time about to run out
- Input: —
- Output: Send notification via FCM/APNs
- Status: 📋

### 2.6 View driving shift history
- Actor: Driver
- Trigger: Opens the shift history tab
- Input: driver_id
- Output: List of shifts driven, clock-in/out times, vehicles used
- Status: 💡

### 2.7 Manage payment methods
- Actor: Driver
- Trigger: Goes to settings
- Input: card/e-wallet information
- Output: Add/remove/set default payment method
- Constraints: Do not store raw card information in the system; process it through the payment gateway (tokenization)
- Status: 💡

### 2.8 Find the nearest charging station
- Actor: Driver
- Trigger: Driver actively searches (not triggered by a low battery alert)
- Input: current location
- Output: List of charging stations with available/busy status
- Constraints: Requires real-time station status data
- Status: 💡

### 2.9 Reserve a charging station (reservation)
- Actor: Driver
- Trigger: Selects a station and a desired time slot
- Input: station_id, reservation time
- Output: Holds the station for the selected time window
- Constraints: Must handle automatic reservation cancellation if the driver doesn't arrive on time
- Status: 💡

### 2.10 Report an incident (incident report)
- Actor: Driver
- Trigger: Encounters an issue with the vehicle or a charging station
- Input: description, attached photo, location
- Output: Create a ticket sent to the admin (related to section 3.11)
- Status: 💡

### 2.11 View driving performance score
- Actor: Driver
- Trigger: Opens the personal statistics tab
- Input: driver_id
- Output: A score based on driving behavior (speed, hard braking, sudden acceleration, etc.)
- Constraints: The scoring formula must be clearly defined before implementation (see section 4.10)
- Status: 💡

---

## 3. Admin website

### 3.1 Real-time monitoring dashboard
- Actor: Admin
- Trigger: Opens the dashboard
- Input: telemetry stream from all vehicles/stations
- Output: Vehicle location map, status of each charging station, list of ongoing charging sessions
- Constraints:
  - Need to decide the mechanism for pushing data to the UI: direct WebSocket push or periodic polling (depending on the number of vehicles/stations)
  - Do not display raw telemetry (every few seconds) directly — a separate aggregation/throttling layer is needed before it reaches the UI
- Status: 🚧

### 3.2 Driver management (CRUD)
- Actor: Admin
- Status: 📋
- Note: There is no `drivers` domain, API, or UI in the current repo yet.

### 3.3 Vehicle management (CRUD, device assignment)
- Actor: Admin
- Status: ✅

### 3.4 Charging station management (CRUD, device assignment)
- Actor: Admin
- Status: ✅

### 3.5 Reports & statistics
- Actor: Admin
- Trigger: Selects filters (vehicle/driver/station, time range)
- Output: Charts of driving hours, battery consumption, revenue by charging station
- Status: 📋

### 3.6 Debt management / payment reconciliation
- Actor: Accounting admin
- Trigger: Opens the debt screen
- Input: driver_id or a time range
- Output: List of debts, payment transaction reconciliation
- Constraints: Directly related to the edge case in section 2.4 (overdue payment)
- Status: 📋

### 3.7 System alert management
- Actor: Admin
- Trigger: Alert generated by the system (section 4.5)
- Output: List of alerts, handling status
- Constraints:
  - Minimum alert types: vehicle disconnected for more than X minutes, charging station fault, battery below the danger threshold, charging session stopped abnormally
  - Needs a mechanism to mark "handled/dismissed" to prevent admins from being spammed with repeated alerts
- Status: 📋

### 3.8 Driving shift management (view/adjust)
- Actor: Admin
- Trigger: Viewing shift history, or discovering an error that needs correcting
- Input: driver_id/truck_id
- Output: List of driving shifts, allows manual adjustment
- Constraints: Every manual adjustment must be recorded in the audit log (section 4.8) since it affects payroll/debt
- Status: 💡

### 3.9 Admin authorization (role-based access)
- Actor: Senior admin
- Trigger: Managing other admin accounts
- Output: Restrict functionality by role
- Constraints:
  - At least 2 roles: operations admin (devices, drivers) and accounting admin (debt, payments)
  - Need to clearly define which role is allowed to adjust driving shifts (section 3.8), since this is a sensitive operation
- Status: 💡

### 3.10 Vehicle/station maintenance management (maintenance)
- Actor: Operations admin
- Trigger: Scheduling maintenance or recording that maintenance was performed
- Input: truck_id/station_id, maintenance date
- Output: Maintenance history, periodic maintenance reminders
- Constraints: May be based on distance driven or operating hours, not just calendar date
- Status: 💡

### 3.11 Incident management from drivers
- Actor: Admin
- Trigger: A new ticket is created from section 2.10
- Output: View, assign a handler, close the ticket
- Status: 💡

### 3.12 Export reports (export)
- Actor: Admin
- Trigger: Clicks the export button on the report screen
- Output: CSV/Excel/PDF file
- Status: 💡

### 3.13 Depot/yard management
- Actor: Admin
- Output: Assign vehicles/stations/drivers to each operating location
- Constraints: Only needs to be implemented if the system operates multiple locations — needs confirmation
- Status: 💡

### 3.14 Configure system alert thresholds
- Actor: Admin
- Trigger: Goes to system settings
- Output: Update the low-battery threshold, disconnection threshold, shift time threshold, etc.
- Status: 💡

---

## 4. Background system (no dedicated UI, but core functionality)

### 4.1 Receive & store truck telemetry (streaming)
- Trigger: Vehicle sends data periodically (every few seconds)
- Input: truck_id, battery %, speed, GPS, error code (if any)
- Output: Write to the time-series DB, update the latest status in the cache
- See details: `docs/data-flow-telemetry.md`
- Status: 🚧

### 4.2 Receive & store charging station telemetry (streaming)
- Trigger: Station sends data periodically
- Input: station_id, status, power currently being delivered, temperature
- Output: Write to the time-series DB
- See details: `docs/data-flow-telemetry.md`
- Status: 🚧

### 4.3 Charging session lifecycle management
- Trigger: Vehicle plugs into a charging station
- Input: truck_id, station_id, power data over time
- Output: Create/update/finalize the charging session
- Edge cases:
  - The station may send the `session_end` event **before** the vehicle sends its final telemetry — the system must wait for enough data from both sources before finalizing the session
  - If the connection drops midway: bill based on the last data received, and the charging session MUST NOT be rolled back
- See the full state machine details: `docs/domain-glossary.md`
- Status: 🚧

### 4.4 Calculate charging session cost
- Trigger: Charging session transitions to `completed` status
- Input: the session's power consumption data
- Output: Amount due
- Formula: see `docs/business-rules-billing.md`
- Status: 📋

### 4.5 System alerting (alerting)
- Trigger: Telemetry data/device status exceeds an abnormal threshold
- Output: Create an alert, send a notification to the relevant admin/driver
- See alert type details: section 3.7
- Status: 📋

### 4.6 Device authentication & authorization
- Trigger: Vehicle/station connects to the system
- Input: device certificate or a per-device API key
- Output: Allow or deny the connection
- Constraints: The device authentication mechanism is completely different from user authentication (driver/admin)
- Status: 💡

### 4.7 Data sync on reconnection (offline sync)
- Trigger: The vehicle's/station's network connection is restored after an interruption
- Input: data buffered locally during the disconnection period
- Output: Push the backlog of data to the server
- Business decisions to finalize:
  - Does the vehicle/station buffer data locally when offline, or is data loss during that period accepted?
  - If buffering exists: late-arriving (out-of-order) data must be processed according to its actual occurrence time, not the time it was received by the server
- Status: 💡

### 4.8 Write audit log
- Trigger: Every important operation (shift adjustment, payment, configuration change, etc.)
- Output: An immutable log record, including actor, action, and time
- Constraints: Mandatory for section 3.8 (shift adjustment) and every payment transaction
- Status: 💡

### 4.9 Old telemetry data cleanup/compaction job
- Trigger: Runs periodically (cron job)
- Input: telemetry data older than X days
- Output: Downsample or archive old data to reduce storage size
- Retention policy: see `docs/data-schema.md`
- Status: 💡

### 4.10 Calculate driving performance score
- Trigger: After each shift ends
- Input: telemetry during the shift (speed, hard braking, sudden acceleration, etc.)
- Output: Update the score displayed in section 2.11
- Constraints: The scoring formula must be clearly defined before coding
- Status: 💡

### 4.11 Webhook/payment gateway integration
- Trigger: Payment gateway returns the transaction result (callback)
- Input: payload from the payment gateway
- Output: Update the corresponding transaction status
- Status: 💡

---

## Items marked 💡 that need confirmation before implementation

Before assigning Claude Code to write these features, you should answer (or discuss with the team) the following business questions yourself — since these are subjective decisions that Claude cannot guess correctly on its own:

1. Is clock-in mandatory before starting the vehicle? (section 1.3)
2. How should overdue payments be handled? Should the account be locked? (sections 2.4, 3.6)
3. Is a charging station reservation feature needed, or is showing status enough? (section 2.9)
4. Is driving performance scoring needed — if so, based on what criteria? (sections 2.11, 4.10)
5. Does the system have multiple depots/yards or just one location? (section 3.13)
6. Do devices (vehicles/stations) need to buffer data when the network is lost? (section 4.7)
