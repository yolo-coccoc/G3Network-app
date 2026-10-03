# Planner: Telematic Simulator (F-A1, F-F2)

> Feature code: F-A1 (Real-time vehicle telemetry ingestion), F-F2 (Device provisioning)
> Status: ✅ Done (MVP scope; closed 2026-10-04 when moved to `done/`).
> Everything left over is deferred in `docs/decisions/deferred.md`; unticked
> acceptance items below were never re-verified. Status before closing:
>
> 🚧 Source implemented at `simulator/`; automated regression tests
> are still tracked in [`backend-automated-tests.md`](./backend-automated-tests.md)
> Created: 2026-07-28

## 1. Goal

Create a minimal simulator to test the end-to-end data flow:

```text
POST vehicles + POST telematics
              ↓
        Telematic Simulator
              ↓ MQTT
             EMQX
              ↓
     telemetry-ingestion → PostgreSQL
```

In this planner:

- **Telematic** is the simulated physical device, identified by
  `telematic_serial`.
- **Telemetry** is the data message the device sends continuously over MQTT.
- The simulator does not create data directly in the database and does not bypass the API/MQTT.

## 2. Scope

Create exactly two scripts:

```text
simulator/seed_simulator_devices.py  # run once to create vehicles and telematics
simulator/telematic_simulator.py     # run continuously to publish telemetry
```

Does not include a UI, a separate Docker image, OCPP simulation, backend command
simulation, or advanced network failure simulation.

## 3. Required reference documents and source contracts

The simulator must follow these sources exactly and must not define its own schema:

- Vehicle request: `backend/app/domains/vehicles/schemas.py`, class
  `VehicleCreate`.
- Telematic request: `backend/app/domains/telematics/schemas.py`, class
  `TelematicCreate`.
- Telematic status: `backend/app/domains/telematics/types.py`, enum
  `TelematicStatus`.
- Telemetry payload: `backend/app/domains/telemetry/schemas.py`, classes
  `TelemetryMessage`, `LocationData`, `VehicleState`, `BatteryData`,
  `MotorData`, `SignalData`.
- Database mapping: `backend/app/domains/telematics/models.py`, class
  `Telematic`, and `backend/app/domains/vehicles/models.py`, class `Vehicle`.
- MQTT topic/payload/QoS: `docs/design/specifications/mqtt-spec.md`.
- MQTT runtime settings: `backend/app/libs/common/config.py`, variables
  `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_QOS`.

If the source schema changes, the simulator must be updated to match that source before
changing the sample payload in the script.

## 4. Script 1 — seed vehicles and telematics

### 4.1. Responsibility

`seed_simulator_devices.py` runs once and performs, in sequence:

1. Create one or more vehicles via `POST /api/v1/vehicles/`.
2. Read `vin` and `vehicle_id` from the response.
3. Create a corresponding Telematic via `POST /api/v1/telematics/`, sending
   `vehicle_vin` so the backend resolves it to `vehicle_id`.
4. Print a mapping table of `telematic_serial → vehicle_id → VIN` for use when running
   the simulator.

### 4.2. Vehicle data

Each request must include all fields per `VehicleCreate`/`VehicleBase`:

```json
{
  "license_plate": "51D-123.45",
  "vin": "SIMULATORVIN00001",
  "make": "G3Network",
  "model": "E-Truck Simulator",
  "year": 2026,
  "status": "ACTIVE",
  "fleet_id": null
}
```

The VIN must be exactly 17 characters per `VehicleCreate`. The script must generate VINs and license plates
that do not collide across runs, or support a configurable prefix/index.

### 4.3. Telematic data

Each request must follow `TelematicCreate`:

```json
{
  "telematic_serial": "TBOX-SIM-000001",
  "vehicle_vin": "SIMULATORVIN00001",
  "status": "ACTIVE",
  "firmware_version": "simulator-1.0.0"
}
```

`vehicle_vin` must match the VIN just created. Do not use `vehicle_id` in the request, because the
Telematic CRUD API accepts a VIN and resolves the FK itself.

### 4.4. Minimal idempotency

- By default, the script fails fast if the API returns `409`, to avoid silently creating an incorrect mapping.
- It has a `--prefix` or `--start-index` parameter to create a new set of serials/VINs on a re-run.
- It does not delete old data and does not call DELETE automatically.
- To make it easy for developers to run, the MVP configuration is hard-coded centrally in a
  `CONFIG` block at the top of the script, with Vietnamese-language comments for each value; passing
  command-line arguments is not required.
- Values that need to be edited directly include `API_BASE_URL`, the number of devices,
  `TELEMATIC_SERIAL_PREFIX`, `VIN_PREFIX`, firmware, and the request timeout. The default
  is `http://localhost:8000`.

## 5. Script 2 — continuously publish telemetry

### 5.1. Responsibility

`telematic_simulator.py` reads the list of seeded serials and opens an MQTT client to
publish continuously. The MVP may use a single process and one async task per device.

Each device's topic:

```text
g3network/telematics/{telematic_serial}/telemetry
```

Set QoS `0`, retain `false`, exactly as in `docs/design/specifications/mqtt-spec.md`.

### 5.2. Payload must match TelemetryMessage

Each message has the structure:

```json
{
  "message_uuid": "497f6eca-6276-4993-bfeb-53cbbbba6f08",
  "telematic_serial": "TBOX-SIM-000001",
  "recorded_at": "2026-07-28T10:30:00Z",
  "location": {
    "latitude": 10.762622,
    "longitude": 106.660172
  },
  "vehicle_state": {
    "speed": 42.5,
    "heading": 90.0,
    "odometer": 12500.5
  },
  "battery": {
    "soc": 78.0,
    "voltage": 650.0,
    "current": -120.0,
    "temperature": 32.0
  },
  "motor": {
    "temperature": 45.0
  },
  "signal": {
    "strength": -70
  },
  "errors": []
}
```

Data generation rules:

- `message_uuid`: a new UUID for each message.
- `telematic_serial`: keeps the task's own serial.
- `recorded_at`: UTC timezone-aware, advancing in real time.
- latitude/longitude: small fluctuations around the configured coordinates, always within the schema range.
- speed: fluctuates within `0..200` km/h.
- heading: fluctuates within `0..360` or `null`.
- odometer: does not decrease between messages from the same device.
- battery.soc: fluctuates within `0..100`.
- `errors`: defaults to `[]`; the MVP does not simulate technical errors.

The script should validate the payload with `TelemetryMessage.model_validate()` before
serializing to JSON, so the simulator detects contract errors early.

### 5.3. Runtime parameters

Proposed configuration centralized at the top of the script:

```python
CONFIG = {
    "mqtt_host": "localhost",
    "mqtt_port": 1883,
    "mqtt_topic_prefix": "g3network/telematics",
    "device_count": 3,
    "publish_interval_seconds": 5,
}
```

The developer only needs to edit the `CONFIG` block, then run the script with no parameters:

```bash
uv run python scripts/telematic_simulator.py
```

It may optionally support reading serials from a JSON file exported by the seed script, but this is not required
for the MVP. `Ctrl+C` must stop the task, disconnect MQTT, and exit cleanly.

## 6. Run order and preconditions

1. Start PostgreSQL, EMQX, and the API.
2. Run the latest migration.
3. Start telemetry ingestion.
4. Run `seed_simulator_devices.py`.
5. Run `telematic_simulator.py` with the seeded serials.
6. Check the ingestion logs and the row count in `vehicle_telemetry`.

If a Telematic has not been assigned a vehicle, ingestion will skip the message per the current business rule;
therefore the vehicle must be seeded first and the correct VIN sent when creating the Telematic.

## 7. Acceptance criteria

- [x] The seed script creates a vehicle and Telematic via the API using `vehicle_vin`.
- [x] The seed script prints the serial/VIN/vehicle_id mapping.
- [x] The simulator publishes to the correct topic with QoS 0.
- [x] The payload validates against `TelemetryMessage`.
- [x] Each device emits messages at the configured interval.
- [x] Ingestion can receive, enrich, and store telemetry via the active MVP path.
- [x] Ctrl+C cancels the simulator's MQTT task.
- [x] No fields are added outside the telemetry contract.
- [ ] Automated regression tests are tracked in `backend-automated-tests.md`.
