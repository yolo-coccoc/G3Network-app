# MQTT Specification - Telematic Device Protocol

> Version: 1.2.0  
> Created: 2026-07-25  
> Feature code: F-A1 (Real-time vehicle telemetry ingestion), F-J2 (Remote
> device configuration - OTA, partial)

---

## 1. Overview

This document defines the communication protocol between the Telematics device (mounted on the electric truck) and the Backend via the MQTT broker (EMQX).

**Design principles:**
- Telematic is the source of truth for time (`recorded_at`)
- Backend adds metadata (`message_id`, `telematic_id`, `vehicle_id`, `received_at`)
- The payload does not contain internal system IDs
- QoS 0 (fire-and-forget) for the MVP

---

## 2. MQTT Topics

### 2.1. Telemetry Data (Published by the Telematic)

**Topic pattern:**
```
g3network/telematics/{telematic_serial}/telemetry
```

**Example:**
```
g3network/telematics/TBOX-VN-000123/telemetry
```

**Explanation:**
- `g3network`: Root topic prefix for the whole system
- `telematics`: Device type
- `{telematic_serial}`: The device's physical serial number (e.g. `TBOX-VN-000123`)
- `telemetry`: Data type (real-time data)

**QoS Level:** 0 (fire-and-forget)  
**Retain:** false

---

### 2.2. Telematic Status (Published by the Telematic)

**Topic pattern:**
```
g3network/telematics/{telematic_serial}/status
```

**Payload:**
```json
{
  "status": "online",
  "firmware_version": "1.2.3",
  "timestamp": "2026-07-25T10:30:00Z"
}
```

**Explanation:**
- Used to track the device's connection status
- MQTT Last Will can be used to automatically publish `offline` when the connection is lost

**Device health fields — PROVISIONAL (v1.2.0).** The design (`telematic_status_reports`,
decisions TX-09 and TX-10 in `docs/decisions/decision-log.md`) expects the device to add
the fields below to its status message, about once a day. The names, units and
values are our proposal; **the device vendor has not confirmed them**, and
nothing ingests this topic yet. Update this section once the vendor's real
format is known.

```json
{
  "status": "online",
  "firmware_version": "1.2.3",
  "telemetry_interval_seconds": 10,
  "sim": {"iccid": "8984049000001234567", "is_esim": false, "data_status": "ACTIVE"},
  "supply_voltage_v": 24.3,
  "signal_dbm": -78,
  "storage_used_percent": 41.5,
  "gnss_status": "FIX",
  "timestamp": "2026-07-25T10:30:00Z"
}
```

| Field | Type | Unit / values | Meaning |
|---|---|---|---|
| `telemetry_interval_seconds` | integer | s | Publish interval the device actually uses (confirms a pushed interval) |
| `sim.iccid` | string | up to 22 digits | ICCID of the SIM in the device |
| `sim.is_esim` | boolean | | The SIM is an eSIM |
| `sim.data_status` | string | `ACTIVE` \| `NO_DATA` \| `SUSPENDED` \| `NO_SIM` | Mobile data status |
| `supply_voltage_v` | number | V | Power supply voltage at the device |
| `signal_dbm` | integer | dBm | Mobile signal strength |
| `storage_used_percent` | number | 0-100 | Device storage in use |
| `gnss_status` | string | `FIX` \| `NO_FIX` \| `ANTENNA_FAULT` | Satellite positioning status |

---

### 2.3. Backend Command (Published by the Backend, subscribed by the Telematic)

**Topic pattern:**
```
g3network/telematics/{telematic_serial}/command
```

**Direction:** backend → device. QoS 1 (not the telemetry default of 0) - a
config command is a one-shot instruction, so a silently dropped message
would leave the device on its old configuration with nothing to notice
the loss. Not retained by default. Published from a short-lived MQTT
client scoped to one publish (`telematics/commands/mqtt_publisher.py`);
its client id always differs from the telemetry consumer's `MQTT_CLIENT_ID`
so publishing a command never evicts the ingestion consumer's session.

**Implemented command - `set_telemetry_interval` (F-J2, partial):**
```json
{
  "command": "set_telemetry_interval",
  "telemetry_interval_seconds": 60,
  "timestamp": "2026-09-17T10:30:31.064251+00:00"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `command` | string | yes | One of the implemented commands below. |
| `telemetry_interval_seconds` | integer | yes, for `set_telemetry_interval` | Desired publish interval, seconds. Bounded server-side by `TELEMATICS_MIN_TELEMETRY_INTERVAL_SECONDS`/`TELEMATICS_MAX_TELEMETRY_INTERVAL_SECONDS`. |
| `timestamp` | string (ISO 8601, UTC offset) | yes | Backend-issued; matches the `config_pushed_at` value recorded on the `telematics` row for this same push. |

**Defined but not implemented** - `restart`:
```json
{ "command": "restart", "timestamp": "2026-07-25T10:30:00Z" }
```

**Important limitation:** no acknowledgement/confirmation topic exists.
The backend cannot know whether a device received or applied a command -
a successful publish means only that the broker accepted the message
(QoS 1 PUBACK). See `docs/decisions/deferred.md` for the deferred
ack/confirmation topic this would need.

---

## 3. Payload Schema - Telemetry Message

### 3.1. JSON structure

```json
{
  "message_uuid": "497f6eca-6276-4993-bfeb-53cbbbba6f08",
  "telematic_serial": "TBOX-VN-000123",
  "recorded_at": "2026-07-25T10:30:00Z",
  "location": {
    "latitude": 21.0285,
    "longitude": 105.8542
  },
  "vehicle_state": {
    "speed": 45.2,
    "heading": 90.0,
    "odometer": 12345.6
  },
  "battery": {
    "soc": 78.5,
    "voltage": 400.2,
    "current": -15.3,
    "temperature": 35.2
  },
  "motor": {
    "temperature": 42.1
  },
  "signal": {
    "strength": -75
  },
  "errors": ["E001", "E005"]
}
```

### 3.2. Field details

#### Root fields (Root level)

| Field | Type | Required | Description |
|--------|------|-----------|-------|
| `message_uuid` | UUID (string) | ✓ | Unique ID for the message, generated by the telematic. Used for tracing and duplicate avoidance |
| `telematic_serial` | string | ✓ | The device's physical serial number (e.g. `TBOX-VN-000123`) |
| `recorded_at` | ISO 8601 datetime | ✓ | The time the telematic recorded the data (UTC). Format: `YYYY-MM-DDTHH:MM:SSZ` |

#### Location (GPS position)

| Field | Type | Required | Range | Unit | Description |
|--------|------|-----------|-------|--------|-------|
| `latitude` | float | ✓ | -90 to 90 | degrees (°) | Latitude |
| `longitude` | float | ✓ | -180 to 180 | degrees (°) | Longitude |

#### Vehicle State (Vehicle state)

| Field | Type | Required | Range | Unit | Description |
|--------|------|-----------|-------|--------|-------|
| `speed` | float | ✗ | 0-200 | km/h | Current speed |
| `heading` | float | ✗ | 0-360 | degrees (°) | Direction of travel. 0°=North, 90°=East, 180°=South, 270°=West. Nullable because not every telematic has a compass |
| `odometer` | float | ✗ | ≥0 | km | Total distance traveled |

#### Battery (Battery)

| Field | Type | Required | Range | Unit | Description |
|--------|------|-----------|-------|--------|-------|
| `soc` | float | ✓ | 0-100 | % | State of Charge - remaining battery level |
| `voltage` | float | ✗ | ≥0 | V (Volt) | Battery voltage |
| `current` | float | ✗ | any | A (Ampere) | Current. Negative = discharging, Positive = charging |
| `temperature` | float | ✗ | any | °C | Battery temperature |

#### Motor (Motor)

| Field | Type | Required | Range | Unit | Description |
|--------|------|-----------|-------|--------|-------|
| `temperature` | float | ✗ | any | °C | Motor temperature |

#### Signal (Network signal)

| Field | Type | Required | Range | Unit | Description |
|--------|------|-----------|-------|--------|-------|
| `strength` | int | ✗ | any | dBm | Signal strength. Negative value (e.g. -75); the closer to 0, the stronger |

#### Errors (Error codes)

| Field | Type | Required | Description |
|--------|------|-----------|-------|
| `errors` | array[string] | ✗ | List of currently active error codes. E.g.: `["E001", "E005"]`. Null or empty if there are no errors |

---

## 4. Illustrative examples

### 4.1. Full message

```json
{
  "message_uuid": "497f6eca-6276-4993-bfeb-53cbbbba6f08",
  "telematic_serial": "TBOX-VN-000123",
  "recorded_at": "2026-07-25T10:30:00Z",
  "location": {
    "latitude": 21.0285,
    "longitude": 105.8542
  },
  "vehicle_state": {
    "speed": 45.2,
    "heading": 90.0,
    "odometer": 12345.6
  },
  "battery": {
    "soc": 78.5,
    "voltage": 400.2,
    "current": -15.3,
    "temperature": 35.2
  },
  "motor": {
    "temperature": 42.1
  },
  "signal": {
    "strength": -75
  },
  "errors": ["E001"]
}
```

### 4.2. Minimal message (required fields only)

```json
{
  "message_uuid": "497f6eca-6276-4993-bfeb-53cbbbba6f08",
  "telematic_serial": "TBOX-VN-000123",
  "recorded_at": "2026-07-25T10:30:00Z",
  "location": {
    "latitude": 21.0285,
    "longitude": 105.8542
  },
  "battery": {
    "soc": 78.5
  }
}
```

---

## 5. QoS and Retain

| Setting | Value | Reason |
|-----------|---------|-------|
| QoS Level | 0 | Fire-and-forget. The telematic sends continuously (5-10s/message); losing a few messages does not affect the business. The MVP does not need high reliability |
| Retain | false | No need to retain old messages. The Backend only cares about the most recent message |
| Clean Session | true | The telematic does not need to receive old messages on reconnect |

---

## 6. Fields NOT in the payload

The following fields are added by the Backend and are **not** sent by the Telematic:

| Field | Source | Description |
|--------|-------|-------|
| `message_id` | Backend | Auto-increment BIGINT, PK in the DB |
| `telematic_id` | Backend | UUID, looked up from `telematic_serial` |
| `vehicle_id` | Backend | UUID, looked up from `telematic_id` |
| `received_at` | Backend | The time the backend received the message |

The payload's `message_uuid` is stored in the target database design as
`telemetry.device_message_id` (decision TM-18 in `docs/decisions/decision-log.md`);
the wire field keeps its name.

**Reason:**
- The telematic does not know `telematic_id` or `vehicle_id` (internal UUIDs)
- `message_id` is a technical ID with no business meaning for the telematic
- `received_at` helps measure latency and debug

---

## 7. ACL Rules (EMQX)

### 7.1. Telematic Device

```
pattern = ${clientid}
allow publish g3network/telematics/${clientid}/telemetry
allow publish g3network/telematics/${clientid}/status
allow subscribe g3network/telematics/${clientid}/command
```

**Explanation:**
- `${clientid}`: Client ID (telematic serial)
- The telematic may only publish to its own topic
- The telematic may subscribe to receive commands from the backend

### 7.2. Backend Service

```
user = g3network-backend
allow subscribe g3network/telematics/+/telemetry
allow subscribe g3network/telematics/+/status
allow publish g3network/telematics/+/command
```

**Explanation:**
- The Backend can subscribe to all telematics
- The Backend can send commands to any telematic

---

## 8. Testing

### 8.1. Subscribe (Backend)

```bash
mosquitto_sub -h localhost -p 1883 -u g3network-backend \
  -i g3network-backend -t "g3network/telematics/+/telemetry" -v
```

### 8.2. Publish (Telematic Simulator)

```bash
mosquitto_pub -h localhost -p 1883 -q 0 -i TBOX-VN-000123 \
  -t "g3network/telematics/TBOX-VN-000123/telemetry" \
  -m '{"message_uuid":"497f6eca-6276-4993-bfeb-53cbbbba6f08","telematic_serial":"TBOX-VN-000123","recorded_at":"2026-07-25T10:00:00Z","location":{"latitude":10.76,"longitude":106.66},"battery":{"soc":50.0}}'
```

### 8.3. Subscribe to a backend command (verifying F-J2)

```bash
mosquitto_sub -h localhost -p 1883 -t "g3network/telematics/+/command" -v
```

Then trigger a push via `POST /api/v1/telematics/{telematic_id}/config`
with `{"telemetry_interval_seconds": 60}` and confirm the message appears
on `g3network/telematics/{serial}/command` with the exact payload shape
in section 2.3.

---

## 9. Version History

| Version | Date | Change |
|-----------|------|----------|
| 1.0.0 | 2026-07-25 | Initial version |
| 1.1.0 | 2026-09-17 | F-J2 (partial): implemented `set_telemetry_interval` on the backend command topic; documented its exact payload, QoS, and the missing-ack limitation. |
| 1.2.0 | 2026-10-04 | Provisional device health fields on the status topic (§2.2), from the database design (TX-09); not confirmed by the vendor, not ingested. |
