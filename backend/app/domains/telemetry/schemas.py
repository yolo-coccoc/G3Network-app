"""
Pydantic schemas for MQTT telemetry message validation and the HTTP query API.

Feature code: F-A1 (Real-time vehicle telemetry ingestion), F-A3 (Battery
health trend), F-A5 (Location, trip history & geofencing - the history
query; geofence alerts are notifications, no schema here), F-A6
(Operating performance report, its per-period breakdown and the fleet
rollup), F-C6 (Per-customer energy usage), F-E1 (fleet live positions)

Two separate contracts live here and never share a class: the HTTP
responses (``Vehicle*Response``/``Fleet*Response``/
``VehicleTelemetryHistoryPoint``) and the
MQTT message (``TelemetryMessage`` and its ``Telemetry*Payload`` parts),
which the consumer validates before putting it on the queue. The MQTT
message comes from the telematic device and carries no internal system ID.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.domains.telemetry.types import ReportGranularity, VehicleActivationStatus
from app.domains.vehicles.types import VehicleStatus
from app.libs.common.config import settings
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
        received_at: When the backend received this record, in UTC.
        is_online: ``True`` while the vehicle's newest telemetry arrived
            within ``settings.TELEMETRY_ONLINE_THRESHOLD_SECONDS`` of now;
            computed at read time, never stored (planner D2).

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
    received_at: datetime
    is_online: bool


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
            (F-A3), nullable. The daily trend with an estimated capacity
            is served by ``VehicleBatteryHealthResponse``; no regression
            or forecast is computed.
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
    see ``docs/decisions/deferred.md`` for that gap. No ``total``/
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


class VehicleOperatingReportPeriod(BaseModel):
    """One calendar period of a broken-down F-A6 operating report.

    Same metrics and ``None`` rules as ``VehicleOperatingReportResponse``,
    over the period instead of the whole window. A reading's delta from
    the previous reading counts in the reading's own period, even when the
    previous reading lies in the period before.

    Attributes:
        period_start: Start of the period (local midnight of
            ``settings.APP_REPORT_TIMEZONE`` as UTC), or the window start
            for a clipped first period.
        period_end: Start of the next period, or the window end for a
            clipped last period.
        sample_count: Telemetry rows inside the period.
        odometer_sample_count: Rows whose ``odometer`` was not NULL.
        first_recorded_at: Earliest reading in the period, or ``None``.
        last_recorded_at: Latest reading in the period, or ``None``.
        distance_km: Distance traveled in the period.
        energy_consumed_kwh: Energy inferred from SOC drops.
        energy_per_100km_kwh: Energy intensity, or ``None`` if undefined.
        distance_per_day_km: Average daily distance over the period's
            span, or ``None`` if fewer than two samples.
        energy_cost_vnd: ``energy_consumed_kwh`` priced at the report's
            ``cost_per_kwh_vnd``.
        cost_per_km_vnd: Cost per kilometre, or ``None`` if undefined.
    """

    period_start: datetime
    period_end: datetime
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
        cost_per_kwh_vnd: Flat tariff used for the cost figures
            (``settings.TELEMETRY_ENERGY_COST_PER_KWH_VND``).
        granularity: Period breakdown requested, or ``None`` when only the
            whole-window aggregate was asked for.
        periods: The same metrics per calendar period of
            ``settings.APP_REPORT_TIMEZONE`` (first/last clipped to the
            window), or ``None`` without ``granularity``. The periods' sums
            add up to the whole-window sums.
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
    granularity: ReportGranularity | None = None
    periods: list[VehicleOperatingReportPeriod] | None = None


class VehicleBatteryHealthPoint(BaseModel):
    """A vehicle's battery health on one day (F-A3).

    Attributes:
        day_start: Local midnight of the day in
            ``settings.APP_REPORT_TIMEZONE``, as a UTC timestamp.
        soh_percent: SOH (%) of the day's last reading that reported it,
            or ``None``.
        cycle_count: Cycle count of the day's last reading that reported
            it, or ``None``.
        estimated_capacity_kwh: ``soh_percent`` / 100 x the vehicle's
            recorded battery capacity, or ``None`` when either is unknown
            (the engineering default capacity is deliberately not used).
    """

    day_start: datetime
    soh_percent: float | None = None
    cycle_count: int | None = None
    estimated_capacity_kwh: float | None = None


class VehicleBatteryHealthResponse(BaseModel):
    """Daily battery-health trend of one vehicle over a window (F-A3).

    Days without any SOH or cycle-count reading are omitted, so ``points``
    can have gaps; nothing is interpolated.

    Attributes:
        vehicle_id: Internal ID of the vehicle queried.
        start_time: Normalized (UTC) lower bound actually used.
        end_time: Normalized (UTC) upper bound actually used.
        battery_capacity_kwh: The vehicle's recorded nominal pack capacity,
            or ``None`` if not recorded.
        points: One point per day with data, oldest first.
        count: Number of points.
    """

    vehicle_id: UUID
    start_time: datetime
    end_time: datetime
    battery_capacity_kwh: float | None = None
    points: list[VehicleBatteryHealthPoint]
    count: int = Field(..., ge=0)


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
    ``docs/decisions/deferred.md`` for the station-metered method
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


class FleetVehicleLiveStatusResponse(BaseModel):
    """One member vehicle of a fleet with its newest position (F-E1 fleet map).

    Attributes:
        vehicle_id: Internal ID of the vehicle.
        vin: VIN of the vehicle; ``None`` when the vehicle was soft-deleted
            after joining (its membership is still open and still counted
            in ``total``).
        license_plate: License plate; ``None`` in the same case.
        vehicle_status: Vehicle lifecycle status; ``None`` in the same case.
        latitude: GPS latitude of the newest reading, or ``None`` if the
            vehicle has never reported.
        longitude: GPS longitude of the newest reading, or ``None``.
        recorded_at: Device timestamp of the newest reading (UTC), or
            ``None``.
        received_at: Backend receive time of that reading (UTC), or
            ``None``.
        is_online: ``True`` while the vehicle's newest telemetry arrived
            within ``settings.TELEMETRY_ONLINE_THRESHOLD_SECONDS`` of now
            (planner D2); ``False`` for a vehicle that never reported.
        signal_strength_dbm: Signal strength of the newest reading in dBm,
            or ``None``.
    """

    vehicle_id: UUID
    vin: str | None = None
    license_plate: str | None = None
    vehicle_status: VehicleStatus | None = None
    latitude: float | None = None
    longitude: float | None = None
    recorded_at: datetime | None = None
    received_at: datetime | None = None
    is_online: bool
    signal_strength_dbm: int | None = None


class FleetVehicleLiveStatusListResponse(BaseModel):
    """A fleet's member vehicles with their newest positions, paginated (F-E1).

    Members are ordered oldest member first.
    """

    items: list[FleetVehicleLiveStatusResponse] = Field(
        ..., description="Member vehicles with their newest position"
    )
    total: int = Field(..., ge=0, description="Total number of member vehicles")
    page: int = Field(..., ge=1, description="Current page")
    page_size: int = Field(
        ..., ge=1, le=settings.API_MAX_PAGE_SIZE, description="Number of items per page"
    )


class FleetVehicleOperatingReportRow(BaseModel):
    """One vehicle's figures inside a fleet operating report (F-A6).

    Same method and ``None`` rules as ``VehicleOperatingReportResponse``:
    a rate is ``None`` with fewer than two samples or zero distance.

    Attributes:
        vehicle_id: Internal ID of the vehicle.
        vin: VIN of the vehicle.
        sample_count: Telemetry rows inside the window.
        distance_km: Distance traveled in the window.
        energy_consumed_kwh: Energy inferred from SOC drops.
        energy_per_100km_kwh: Energy intensity, or ``None`` if undefined.
        energy_cost_vnd: ``energy_consumed_kwh`` priced at the report's
            ``cost_per_kwh_vnd``.
        cost_per_km_vnd: Cost per kilometre, or ``None`` if undefined.
        battery_capacity_kwh: Pack capacity used for the kWh conversion.
        is_default_battery_capacity: ``True`` if the vehicle has no
            recorded capacity and the engineering default was used.
    """

    vehicle_id: UUID
    vin: str
    sample_count: int = Field(..., ge=0)
    distance_km: float = Field(..., ge=0)
    energy_consumed_kwh: float = Field(..., ge=0)
    energy_per_100km_kwh: float | None = Field(None, ge=0)
    energy_cost_vnd: float = Field(..., ge=0)
    cost_per_km_vnd: float | None = Field(None, ge=0)
    battery_capacity_kwh: float = Field(..., gt=0)
    is_default_battery_capacity: bool


class FleetOperatingReportTotals(BaseModel):
    """Fleet-wide totals of a fleet operating report (F-A6).

    Sums are added across vehicles; every rate is recomputed from the
    summed numerator and denominator, never averaged across vehicles (an
    average would weight a parked vehicle like a busy one).

    Attributes:
        vehicle_count: Vehicles included in the report.
        sample_count: Telemetry rows across every vehicle.
        distance_km: Sum of the vehicles' distances.
        energy_consumed_kwh: Sum of the vehicles' consumed energy.
        energy_per_100km_kwh: ``energy_consumed_kwh`` per 100 km of
            ``distance_km``, or ``None`` when the fleet traveled no
            distance.
        energy_cost_vnd: ``energy_consumed_kwh`` priced at the report's
            ``cost_per_kwh_vnd``.
        cost_per_km_vnd: ``energy_cost_vnd`` per km of ``distance_km``, or
            ``None`` when the fleet traveled no distance.
    """

    vehicle_count: int = Field(..., ge=0)
    sample_count: int = Field(..., ge=0)
    distance_km: float = Field(..., ge=0)
    energy_consumed_kwh: float = Field(..., ge=0)
    energy_per_100km_kwh: float | None = Field(None, ge=0)
    energy_cost_vnd: float = Field(..., ge=0)
    cost_per_km_vnd: float | None = Field(None, ge=0)


class FleetOperatingReportResponse(BaseModel):
    """Operating performance of a fleet's current members over a window (F-A6).

    Covers the vehicles with an open membership when the report is run
    (not the members at the time of each reading); a member vehicle
    soft-deleted since joining is left out. Same accuracy limits as the
    per-vehicle report (``VehicleOperatingReportResponse``).

    Attributes:
        fleet_id: Internal ID of the fleet.
        start_time: Normalized (UTC) lower bound actually used.
        end_time: Normalized (UTC) upper bound actually used.
        cost_per_kwh_vnd: Flat tariff used for the cost figures
            (``settings.TELEMETRY_ENERGY_COST_PER_KWH_VND``).
        vehicles: One row per included vehicle, oldest member first.
        totals: Fleet-wide sums and the rates recomputed from them.
    """

    fleet_id: UUID
    start_time: datetime
    end_time: datetime
    cost_per_kwh_vnd: float = Field(..., ge=0)
    vehicles: list[FleetVehicleOperatingReportRow]
    totals: FleetOperatingReportTotals


class VehicleActivationResponse(BaseModel):
    """Activation of one truck, computed at read time (VEH-05, VH-06).

    Attributes:
        vehicle_id: Internal ID of the vehicle.
        vin: VIN of the vehicle.
        license_plate: License plate of the vehicle.
        activation_status: ``NO_DEVICE``, ``AWAITING_DATA`` or ``ACTIVATED``.
        handover_at: When the truck first went to an owner (start of its
            first ownership period), if known.
        telematic_id: The device mounted now, if any.
        telematic_serial: Serial of that device.
        device_mounted_at: When it was mounted.
        first_data_at: When the mounted device first delivered data at or
            after the later of its mounting and the handover.
        activation_hours: Hours from the handover to ``first_data_at``; `None`
            when either is unknown or the data came before the handover.
    """

    vehicle_id: UUID
    vin: str
    license_plate: str
    activation_status: VehicleActivationStatus
    handover_at: datetime | None
    telematic_id: UUID | None
    telematic_serial: str | None
    device_mounted_at: datetime | None
    first_data_at: datetime | None
    activation_hours: float | None


class VehicleActivationSummaryResponse(BaseModel):
    """Activation success over a scope of trucks (VEH-05).

    Attributes:
        total_count: Live trucks in the scope.
        no_device_count: Trucks with no device mounted.
        awaiting_data_count: Trucks with a device that sent nothing yet.
        activated_count: Trucks whose device delivered data.
        activation_rate_percent: ``activated_count`` as a share of the trucks
            with a device (awaiting plus activated), one decimal; `None` when
            none has a device. The target is at least 98.
        average_activation_hours: Mean ``activation_hours`` of the activated
            trucks that have one; `None` when there is none.
    """

    total_count: int = Field(..., ge=0)
    no_device_count: int = Field(..., ge=0)
    awaiting_data_count: int = Field(..., ge=0)
    activated_count: int = Field(..., ge=0)
    activation_rate_percent: float | None
    average_activation_hours: float | None


class VehicleActivationListResponse(BaseModel):
    """The activation summary and a page of trucks (VEH-05).

    Attributes:
        summary: The counts over the whole filtered scope (not the page).
        items: Trucks on this page, in the order of the vehicle list.
        total: Trucks matching the status filter across all pages.
        page: Normalized page number.
        page_size: Normalized page size.
    """

    summary: VehicleActivationSummaryResponse
    items: list[VehicleActivationResponse]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=settings.API_MAX_PAGE_SIZE)


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
    def validate_telematic_serial(cls, value: str) -> str:
        """Strip surrounding whitespace and reject a blank serial.

        Args:
            value: ``telematic_serial`` as sent by the device (already
                length-checked by the field constraints).

        Returns:
            The serial without leading/trailing whitespace.

        Raises:
            ValueError: If the serial is empty after stripping; Pydantic
                reports it as a validation error.
        """
        stripped_serial = value.strip()
        if not stripped_serial:
            raise ValueError("telematic_serial must not be empty")
        return stripped_serial

    @field_validator("recorded_at")
    @classmethod
    def validate_recorded_at(cls, value: datetime) -> datetime:
        """Require a timezone and normalize the recorded timestamp to UTC.

        Args:
            value: ``recorded_at`` as parsed from the device's payload.

        Returns:
            The same instant in UTC.

        Raises:
            ValueError: If the timestamp carries no timezone; Pydantic
                reports it as a validation error.
        """
        if value.utcoffset() is None:
            raise ValueError("recorded_at must have a timezone")
        return value.astimezone(timezone.utc)

    def to_vehicle_telemetry_values(
        self,
        telematic_id: UUID,
        vehicle_id: UUID,
        organization_id: UUID,
        received_at: datetime,
        raw_payload: dict[str, object],
    ) -> dict[str, object]:
        """
        Convert the message into a dict matching TelemetryModel.

        Args:
            telematic_id: UUID of the telematic device (looked up from telematic_serial)
            vehicle_id: UUID of the vehicle (looked up from telematic_id)
            organization_id: Organization that owns the vehicle now, which is
                the owner at ``recorded_at`` for a live message (DM-24 C)
            received_at: Timestamp when the backend received the message
            raw_payload: Original JSON object before Pydantic normalization

        Returns:
            Dict with all fields needed to insert into the DB. A field of an
            optional section the device omitted (``vehicle_state``,
            ``motor``, ``signal``) is ``None``; ``error_codes`` is stored as
            ``{"codes": [...]}`` JSONB, or ``None`` when no code is active.
        """
        vehicle_state = self.vehicle_state
        return {
            "organization_id": organization_id,
            "device_message_id": self.message_uuid,
            "telematic_id": telematic_id,
            "vehicle_id": vehicle_id,
            "recorded_at": self.recorded_at,
            "received_at": received_at,
            "location": coordinates_to_location(
                self.location.latitude, self.location.longitude
            ),
            "speed_kmh": vehicle_state.speed if vehicle_state else None,
            "heading_degrees": vehicle_state.heading if vehicle_state else None,
            "soc_percent": self.battery.soc,
            "battery_voltage_v": self.battery.voltage,
            "battery_current_a": self.battery.current,
            "battery_temperature_celsius": self.battery.temperature,
            "soh_percent": self.battery.soh_percent,
            "cycle_count": self.battery.cycle_count,
            "motor_temperature_celsius": (
                self.motor.temperature if self.motor else None
            ),
            "odometer_km": vehicle_state.odometer if vehicle_state else None,
            "signal_dbm": self.signal.strength if self.signal else None,
            "error_codes": {"codes": self.errors} if self.errors else None,
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
