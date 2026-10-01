"""
Pydantic schemas for MQTT telemetry message validation and the HTTP query API.

Feature code: F-A1 (Real-time vehicle telemetry ingestion), F-A5 (Location,
trip history & geofencing - the history query only; geofencing itself is
deferred, see docs/01-requirements/future.md)

This schema validates messages from MQTT before they are placed on the queue.
The message is sent from the Telematics device and does not contain any
internal system ID.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.libs.common.geo import coordinates_to_location


class VehicleTelemetryLatestResponse(BaseModel):
    """Latest telemetry data returned for a vehicle.

    Attributes:
        vehicle_id: Internal ID of the vehicle.
        telematic_serial: Serial of the device that sent the message.
        recorded_at: Timestamp when the device recorded the data, in UTC.
        latitude: GPS latitude.
        longitude: GPS longitude.
        speed: Current speed, km/h.
        heading: Direction of travel, degrees.
        soc: Remaining battery percentage.
        battery_voltage: Battery voltage, V.
        battery_current: Battery current, A.
        battery_temperature: Battery temperature, °C.
        soh_percent: Battery State of Health, remaining capacity vs. new
            (F-A3), nullable.
        cycle_count: Charge/discharge cycle count (F-A3), nullable.
        motor_temperature: Motor temperature, °C.
        odometer: Total distance traveled, km.
        signal_strength: Signal strength, dBm.
        error_codes: Error codes from the device.
        schema_version: Version of the MQTT message schema the device used
            to send this record (F-A1).

    Note:
        No longer built via ``model_validate(orm_obj, from_attributes=True)``
        - the ORM model stores GPS as a single ``location`` geography point,
        which doesn't line up 1:1 with this schema's plain latitude/
        longitude fields. See
        ``telemetry.mappers.to_vehicle_telemetry_latest_response``.
    """

    vehicle_id: UUID
    telematic_serial: str
    recorded_at: datetime
    latitude: float
    longitude: float
    speed: float | None
    heading: float | None
    soc: float
    battery_voltage: float | None
    battery_current: float | None
    battery_temperature: float | None
    soh_percent: float | None
    cycle_count: int | None
    motor_temperature: float | None
    odometer: float | None
    signal_strength: int | None
    error_codes: dict[str, list[str]] | None
    schema_version: int


class VehicleTelemetryHistoryPoint(BaseModel):
    """One telemetry reading within a history query's time range (F-A5).

    Same field set as ``VehicleTelemetryLatestResponse`` minus
    ``vehicle_id``/``telematic_serial`` - both are redundant per point in a
    single-vehicle history and are carried once at the response's top level
    instead.

    Attributes:
        recorded_at: Timestamp when the device recorded the data, in UTC.
        latitude: GPS latitude.
        longitude: GPS longitude.
        speed: Current speed, km/h.
        heading: Direction of travel, degrees.
        soc: Remaining battery percentage.
        battery_voltage: Battery voltage, V.
        battery_current: Battery current, A.
        battery_temperature: Battery temperature, °C.
        soh_percent: Battery State of Health, remaining capacity vs. new
            (F-A3), nullable. Charting this field across a queried time
            range is how "estimated capacity fade over time" (F-A3) is
            served - no separate trend/regression endpoint exists.
        cycle_count: Charge/discharge cycle count (F-A3), nullable.
        motor_temperature: Motor temperature, °C.
        odometer: Total distance traveled, km.
        signal_strength: Signal strength, dBm.
        error_codes: Error codes from the device.
        schema_version: Version of the MQTT message schema the device used
            to send this record (F-A1).
    """

    recorded_at: datetime
    latitude: float
    longitude: float
    speed: float | None
    heading: float | None
    soc: float
    battery_voltage: float | None
    battery_current: float | None
    battery_temperature: float | None
    soh_percent: float | None
    cycle_count: int | None
    motor_temperature: float | None
    odometer: float | None
    signal_strength: int | None
    error_codes: dict[str, list[str]] | None
    schema_version: int


class VehicleTelemetryHistoryResponse(BaseModel):
    """Telemetry history for one vehicle within a queried time range (F-A5).

    Ordered chronologically for trip replay (the frontend draws the
    polyline); this backend does no trip-boundary/segmentation detection -
    see ``docs/01-requirements/future.md`` for that gap. No ``total``/
    ``page`` fields - a range with more points than the query's ``limit``
    is narrowed by the caller instead of paginated server-side.

    Attributes:
        vehicle_id: Internal ID of the vehicle queried.
        points: Telemetry readings ordered by ``recorded_at`` ascending,
            oldest first.
        count: Number of points in this response.
    """

    vehicle_id: UUID
    points: list[VehicleTelemetryHistoryPoint]
    count: int = Field(..., ge=0)


class VehicleOperatingReportResponse(BaseModel):
    """Per-vehicle operating performance over a queried window (F-A6).

    Energy is inferred from SOC drops in the vehicle's own telemetry, not
    from charging-session records (which carry no vehicle linkage) - see
    ``telemetry.service.get_vehicle_operating_report`` for the accuracy
    limits of that method (gross not net energy, SOC quantization,
    sparse-telemetry under-counting, nominal not SOH-adjusted capacity).
    Every rate field is ``None`` when it's undefined (no distance
    recorded, or fewer than two samples in the window); the raw sums are
    always numbers.

    Attributes:
        vehicle_id: Internal ID of the vehicle queried.
        start_time: Normalized (UTC) lower bound actually used.
        end_time: Normalized (UTC) upper bound actually used.
        sample_count: Telemetry rows inside the window.
        odometer_sample_count: Rows whose ``odometer`` was not NULL.
        first_recorded_at: Earliest telemetry timestamp in the window, or
            ``None`` if the window is empty.
        last_recorded_at: Latest telemetry timestamp in the window, or
            ``None`` if the window is empty.
        distance_km: Total distance traveled in the window.
        energy_consumed_kwh: Total energy inferred from SOC drops.
        energy_per_100km_kwh: Energy intensity, or ``None`` if
            ``distance_km`` is 0.
        distance_per_day_km: Average daily distance over the *requested*
            window (not the observed sample span), or ``None`` if fewer
            than two samples were recorded.
        energy_cost_vnd: ``energy_consumed_kwh`` priced at
            ``cost_per_kwh_vnd``.
        cost_per_km_vnd: Cost per kilometre, or ``None`` if ``distance_km``
            is 0.
        battery_capacity_kwh: Pack capacity used for the kWh conversion -
            the vehicle's recorded value, or the engineering default.
        is_default_battery_capacity: ``True`` if the vehicle has no
            recorded ``battery_capacity_kwh`` and the default was used.
        cost_per_kwh_vnd: Flat engineering-default tariff used for the
            cost figures - not vendor-confirmed, not configurable yet.
    """

    vehicle_id: UUID
    start_time: datetime
    end_time: datetime
    sample_count: int = Field(..., ge=0)
    odometer_sample_count: int = Field(..., ge=0)
    first_recorded_at: datetime | None = None
    last_recorded_at: datetime | None = None
    distance_km: float = Field(..., ge=0)
    energy_consumed_kwh: float = Field(..., ge=0)
    energy_per_100km_kwh: float | None = Field(None, ge=0)
    distance_per_day_km: float | None = Field(None, ge=0)
    energy_cost_vnd: float = Field(..., ge=0)
    cost_per_km_vnd: float | None = Field(None, ge=0)
    battery_capacity_kwh: float = Field(..., gt=0)
    is_default_battery_capacity: bool
    cost_per_kwh_vnd: float = Field(..., ge=0)


class VehicleEnergyUsageResponse(BaseModel):
    """Energy that entered one vehicle's battery over a queried window (F-C6).

    "Customer" is a vehicle in this MVP (one vehicle per customer); there
    is no customer entity in this backend. Measures energy *into the
    pack*, inferred from SOC rises in the vehicle's own telemetry - not
    kWh billed at a station meter, and not attributable to any station,
    connector, or session. It therefore cannot satisfy NF-10's 3-way
    reconciliation (<1% deviation): a station meter typically reads more
    than the pack receives (charger/conversion losses), and this also
    includes regenerative braking and any non-station charging. See
    ``docs/01-requirements/future.md`` for the station-metered method
    this is a stand-in for.

    Attributes:
        vehicle_id: Internal ID of the vehicle queried.
        start_time: Normalized (UTC) lower bound actually used.
        end_time: Normalized (UTC) upper bound actually used.
        sample_count: Telemetry rows inside the window.
        first_recorded_at: Earliest telemetry timestamp in the window, or
            ``None`` if the window is empty.
        last_recorded_at: Latest telemetry timestamp in the window, or
            ``None`` if the window is empty.
        energy_charged_kwh: Total energy inferred from SOC rises.
        battery_capacity_kwh: Pack capacity used for the kWh conversion -
            the vehicle's recorded value, or the engineering default.
        is_default_battery_capacity: ``True`` if the vehicle has no
            recorded ``battery_capacity_kwh`` and the default was used.
    """

    vehicle_id: UUID
    start_time: datetime
    end_time: datetime
    sample_count: int = Field(..., ge=0)
    first_recorded_at: datetime | None = None
    last_recorded_at: datetime | None = None
    energy_charged_kwh: float = Field(..., ge=0)
    battery_capacity_kwh: float = Field(..., gt=0)
    is_default_battery_capacity: bool


class TelemetryLocationPayload(BaseModel):
    """
    GPS location data from the telematic device.

    Attributes:
        latitude: Latitude (-90 to 90 degrees)
        longitude: Longitude (-180 to 180 degrees)
    """

    latitude: Annotated[float, Field(ge=-90, le=90, description="Latitude (degrees)")]
    longitude: Annotated[
        float, Field(ge=-180, le=180, description="Longitude (degrees)")
    ]

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"latitude": 21.0285, "longitude": 105.8542},
                {"latitude": 10.762622, "longitude": 106.660172},
            ]
        }
    }


class TelemetryVehicleStatePayload(BaseModel):
    """
    Vehicle state from the telematic device.

    Attributes:
        speed: Speed (0-200 km/h)
        heading: Direction of travel (0-360 degrees, nullable). 0°=North, 90°=East, 180°=South, 270°=West
        odometer: Total distance traveled (km)
    """

    speed: Annotated[
        float | None, Field(default=None, ge=0, le=200, description="Speed (km/h)")
    ]
    heading: Annotated[
        float | None,
        Field(
            default=None,
            ge=0,
            le=360,
            description="Direction of travel (degrees). 0°=North, 90°=East, 180°=South, 270°=West",
        ),
    ]
    odometer: Annotated[
        float | None,
        Field(default=None, ge=0, description="Total distance traveled (km)"),
    ]

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"speed": 45.2, "heading": 90.0, "odometer": 12345.6},
                {"speed": 0, "heading": None, "odometer": 5000.0},
            ]
        }
    }


class TelemetryBatteryPayload(BaseModel):
    """
    Battery data from the telematic device.

    Attributes:
        soc: State of Charge - remaining battery level (0-100%)
        voltage: Battery voltage (V)
        current: Current (A). Negative = discharging, Positive = charging
        temperature: Battery temperature (°C)
        soh_percent: State of Health - remaining capacity vs. new (0-100%),
            nullable (F-A3)
        cycle_count: Charge/discharge cycle count, nullable (F-A3)
    """

    soc: Annotated[
        float,
        Field(
            ge=0, le=100, description="State of Charge - remaining battery level (%)"
        ),
    ]
    voltage: Annotated[
        float | None, Field(default=None, ge=0, description="Battery voltage (V)")
    ]
    current: Annotated[
        float | None,
        Field(
            default=None,
            description="Current (A). Negative = discharging, Positive = charging",
        ),
    ]
    temperature: Annotated[
        float | None, Field(default=None, description="Battery temperature (°C)")
    ]
    soh_percent: Annotated[
        float | None,
        Field(
            default=None,
            ge=0,
            le=100,
            description="State of Health - remaining capacity vs. new (%)",
        ),
    ]
    cycle_count: Annotated[
        int | None,
        Field(default=None, ge=0, description="Charge/discharge cycle count"),
    ]

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "soc": 78.5,
                    "voltage": 400.2,
                    "current": -15.3,
                    "temperature": 35.2,
                    "soh_percent": 96.5,
                    "cycle_count": 142,
                },
                {
                    "soc": 50.0,
                    "voltage": None,
                    "current": None,
                    "temperature": None,
                    "soh_percent": None,
                    "cycle_count": None,
                },
            ]
        }
    }


class TelemetryMotorPayload(BaseModel):
    """
    Motor data from the telematic device.

    Attributes:
        temperature: Motor temperature (°C)
    """

    temperature: Annotated[
        float | None, Field(default=None, description="Motor temperature (°C)")
    ]

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"temperature": 42.1},
                {"temperature": None},
            ]
        }
    }


class TelemetrySignalPayload(BaseModel):
    """
    Network signal data from the telematic device.

    Attributes:
        strength: Signal strength (dBm). Negative value, the closer to 0 the stronger
    """

    strength: Annotated[
        int | None,
        Field(
            default=None,
            description="Signal strength (dBm). Negative value, the closer to 0 the stronger",
        ),
    ]

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"strength": -75},
                {"strength": -95},
            ]
        }
    }


class TelemetryMessage(BaseModel):
    """
    Telemetry message from a Telematics device via MQTT.

    This message is sent from the telematic device and does not contain any
    internal system ID. The backend will add: message_id, telematic_id,
    vehicle_id, received_at.

    Attributes:
        message_uuid: Unique ID for the message, generated by the telematic device
        telematic_serial: Physical serial code of the device (e.g. TBOX-VN-000123)
        recorded_at: Timestamp when the telematic device recorded the data (UTC)
        location: GPS location data
        vehicle_state: Vehicle state (nullable)
        battery: Battery data
        motor: Motor data (nullable)
        signal: Network signal data (nullable)
        errors: List of currently active error codes (nullable)
        schema_version: Version of this message contract the device is
            using. Defaults to ``1`` so devices that predate this field
            (and existing tests) keep validating without sending it;
            bump this when the payload shape changes in a
            backward-incompatible way.
    """

    message_uuid: Annotated[
        UUID,
        Field(
            description="Unique ID for the message, generated by the telematic device"
        ),
    ]
    telematic_serial: Annotated[
        str,
        Field(
            min_length=1,
            max_length=50,
            description="Physical serial code of the device",
        ),
    ]
    recorded_at: Annotated[
        datetime,
        Field(
            description="Timestamp when the telematic device recorded the data (UTC)"
        ),
    ]
    location: Annotated[
        TelemetryLocationPayload,
        Field(description="GPS location data"),
    ]
    vehicle_state: Annotated[
        TelemetryVehicleStatePayload | None,
        Field(default=None, description="Vehicle state"),
    ]
    battery: Annotated[
        TelemetryBatteryPayload,
        Field(description="Battery data"),
    ]
    motor: Annotated[
        TelemetryMotorPayload | None,
        Field(default=None, description="Motor data"),
    ]
    signal: Annotated[
        TelemetrySignalPayload | None,
        Field(default=None, description="Network signal data"),
    ]
    errors: Annotated[
        list[str] | None,
        Field(default=None, description="List of currently active error codes"),
    ]
    schema_version: Annotated[
        int,
        Field(
            default=1,
            ge=1,
            description="Version of this message contract the device is using",
        ),
    ]

    @field_validator("telematic_serial")
    @classmethod
    def validate_telematic_serial(cls, v: str) -> str:
        """Validate that telematic_serial is not empty and has no extra whitespace."""
        v = v.strip()
        if not v:
            raise ValueError("telematic_serial must not be empty")
        return v

    @field_validator("recorded_at")
    @classmethod
    def validate_recorded_at(cls, value: datetime) -> datetime:
        """Require a timezone and normalize the recorded timestamp to UTC."""
        if value.utcoffset() is None:
            raise ValueError("recorded_at must have a timezone")
        return value.astimezone(timezone.utc)

    def to_vehicle_telemetry_values(
        self,
        telematic_id: UUID,
        vehicle_id: UUID,
        received_at: datetime,
        raw_payload: dict[str, object],
    ) -> dict[str, object]:
        """
        Convert the message into a dict matching VehicleTelemetryModel.

        Args:
            telematic_id: UUID of the telematic device (looked up from telematic_serial)
            vehicle_id: UUID of the vehicle (looked up from telematic_id)
            received_at: Timestamp when the backend received the message
            raw_payload: Original JSON object before Pydantic normalization

        Returns:
            Dict with all fields needed to insert into the DB
        """
        # Build vehicle_state dict
        vehicle_state_dict = None
        if self.vehicle_state:
            vehicle_state_dict = {
                "speed": self.vehicle_state.speed,
                "heading": self.vehicle_state.heading,
                "odometer": self.vehicle_state.odometer,
            }

        # Build battery dict
        battery_dict = {
            "soc": self.battery.soc,
            "voltage": self.battery.voltage,
            "current": self.battery.current,
            "temperature": self.battery.temperature,
            "soh_percent": self.battery.soh_percent,
            "cycle_count": self.battery.cycle_count,
        }

        # Build motor dict
        motor_dict = None
        if self.motor:
            motor_dict = {
                "temperature": self.motor.temperature,
            }

        # Build signal dict
        signal_dict = None
        if self.signal:
            signal_dict = {
                "strength": self.signal.strength,
            }

        # Build error_codes as JSONB
        error_codes_dict = None
        if self.errors:
            error_codes_dict = {"codes": self.errors}

        return {
            "message_uuid": self.message_uuid,
            "telematic_id": telematic_id,
            "telematic_serial": self.telematic_serial,
            "vehicle_id": vehicle_id,
            "recorded_at": self.recorded_at,
            "received_at": received_at,
            "location": coordinates_to_location(
                self.location.latitude, self.location.longitude
            ),
            "speed": vehicle_state_dict.get("speed") if vehicle_state_dict else None,
            "heading": (
                vehicle_state_dict.get("heading") if vehicle_state_dict else None
            ),
            "soc": battery_dict["soc"],
            "battery_voltage": battery_dict.get("voltage"),
            "battery_current": battery_dict.get("current"),
            "battery_temperature": battery_dict.get("temperature"),
            "soh_percent": battery_dict.get("soh_percent"),
            "cycle_count": battery_dict.get("cycle_count"),
            "motor_temperature": motor_dict.get("temperature") if motor_dict else None,
            "odometer": (
                vehicle_state_dict.get("odometer") if vehicle_state_dict else None
            ),
            "signal_strength": signal_dict.get("strength") if signal_dict else None,
            "error_codes": error_codes_dict,
            "raw_payload": raw_payload,
            "schema_version": self.schema_version,
        }

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "message_uuid": "497f6eca-6276-4993-bfeb-53cbbbba6f08",
                    "telematic_serial": "TBOX-VN-000123",
                    "recorded_at": "2026-07-25T10:30:00Z",
                    "location": {"latitude": 21.0285, "longitude": 105.8542},
                    "vehicle_state": {
                        "speed": 45.2,
                        "heading": 90.0,
                        "odometer": 12345.6,
                    },
                    "battery": {
                        "soc": 78.5,
                        "voltage": 400.2,
                        "current": -15.3,
                        "temperature": 35.2,
                    },
                    "motor": {"temperature": 42.1},
                    "signal": {"strength": -75},
                    "errors": ["E001"],
                },
                {
                    "message_uuid": "497f6eca-6276-4993-bfeb-53cbbbba6f09",
                    "telematic_serial": "TBOX-VN-000124",
                    "recorded_at": "2026-07-25T10:30:00Z",
                    "location": {"latitude": 10.762622, "longitude": 106.660172},
                    "battery": {"soc": 50.0},
                },
            ]
        }
    }


@dataclass(slots=True)
class TelemetryEnvelope:
    """
    Validated telemetry message paired with its original JSON object.

    Attributes:
        message: Telemetry payload after Pydantic validation and normalization.
        raw_payload: Parsed JSON object before Pydantic modifies or drops fields.
    """

    message: TelemetryMessage
    raw_payload: dict[str, object]
