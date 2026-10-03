"""Enums, thresholds and small DTOs shared across the telemetry domain's layers.

Feature code: F-A1 (live status), F-A2 (Tiered battery alerts), F-A4
(Anomaly detection), F-A3 (Battery health (SOH) & cycle tracking), F-A5
(geofence transitions), F-A6 (Operating performance report), F-C6
(Per-customer energy usage)

Scope: the alert levels/anomaly types and their thresholds and severities
(read by ``detection.py`` and ``alerting.py``), the geofence transition
kind (``geofencing.py``), the report granularity and output format, the
folded-window/per-period/per-day DTOs the repository
hands to ``reports.py`` and the service, the public cross-domain DTOs
returned by ``service.py`` (``VehicleLiveStatusReference``,
``VehicleOperatingSummary``), and the default battery capacity. Every
numeric threshold and default here is an engineering value, not
vendor-confirmed. The SOH alert threshold and the energy tariff are not
here: they are settings (``TELEMETRY_SOH_ALERT_THRESHOLD_PERCENT``,
``TELEMETRY_ENERGY_COST_PER_KWH_VND``), read at call time. No
FastAPI/Pydantic/SQLAlchemy imports.
"""

import enum
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.domains.notifications.types import NotificationSeverity


class BatteryAlertLevel(str, enum.Enum):
    """Tiered battery SOC alert level, from least to most severe."""

    EARLY = "EARLY"  # crosses 30%
    MAIN = "MAIN"  # crosses 20%
    CRITICAL = "CRITICAL"  # crosses 10%


@dataclass(frozen=True)
class BatteryAlertThreshold:
    """The SOC threshold and notification severity for one alert level.

    Attributes:
        threshold_percent: SOC value (%) this level fires at.
        severity: Notification severity to raise for this level.
    """

    threshold_percent: float
    severity: NotificationSeverity


# Ordered from least to most severe so a multi-threshold drop in one message
# can be resolved to "the most severe level crossed" by taking the last
# match in this sequence.
BATTERY_ALERT_THRESHOLDS: dict[BatteryAlertLevel, BatteryAlertThreshold] = {
    BatteryAlertLevel.EARLY: BatteryAlertThreshold(30.0, NotificationSeverity.INFO),
    BatteryAlertLevel.MAIN: BatteryAlertThreshold(20.0, NotificationSeverity.WARNING),
    BatteryAlertLevel.CRITICAL: BatteryAlertThreshold(
        10.0, NotificationSeverity.CRITICAL
    ),
}


class VehicleAnomalyType(str, enum.Enum):
    """Kind of anomaly detected in a single telemetry reading (F-A4)."""

    HIGH_BATTERY_TEMPERATURE = "HIGH_BATTERY_TEMPERATURE"
    SUDDEN_VOLTAGE_DROP = "SUDDEN_VOLTAGE_DROP"
    # F-A4 names "cell/module fault" and "motor fault" as separate triggers,
    # but the MQTT contract (mqtt-spec.md) only carries opaque error code
    # strings with no vendor catalog to tell them apart - both collapse into
    # this one generic type. See docs/decisions/deferred.md.
    DEVICE_FAULT = "DEVICE_FAULT"


# Engineering defaults, not vendor-confirmed against real battery/pack specs
# - see docs/decisions/deferred.md for the item to revisit these once
# real thresholds are available.
HIGH_BATTERY_TEMPERATURE_THRESHOLD_CELSIUS = 60.0
VOLTAGE_DROP_THRESHOLD_VOLTS = 50.0

# Severity is fixed per anomaly type (unlike BATTERY_ALERT_THRESHOLDS, which
# ties severity to a tier) since each type in this MVP has only one
# detection condition, not tiers of its own.
VEHICLE_ANOMALY_SEVERITIES: dict[VehicleAnomalyType, NotificationSeverity] = {
    VehicleAnomalyType.HIGH_BATTERY_TEMPERATURE: NotificationSeverity.CRITICAL,
    VehicleAnomalyType.SUDDEN_VOLTAGE_DROP: NotificationSeverity.WARNING,
    VehicleAnomalyType.DEVICE_FAULT: NotificationSeverity.WARNING,
}


@dataclass(frozen=True)
class VehicleAnomaly:
    """One anomaly detected in a single telemetry reading (F-A4).

    Attributes:
        anomaly_type: Kind of anomaly detected.
        severity: Notification severity to raise for this anomaly.
        evidence: Type-specific structured data supporting the detection
            (e.g. threshold/observed values); shape documented per detector
            in ``telemetry/detection.py``.
    """

    anomaly_type: VehicleAnomalyType
    severity: NotificationSeverity
    evidence: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class VehicleTelemetryWindowSummary:
    """Folded telemetry deltas for one vehicle over one time window (F-A6/F-C6).

    Produced by a single SQL pass over ``vehicle_telemetry`` (see
    ``telemetry/repository.py::get_vehicle_window_summary``). Every field
    is already clamped and coalesced by the query, so an empty window
    yields zeros (never ``None``) for the sums.

    Attributes:
        soc_discharge_percent: Sum of positive SOC drops between
            consecutive samples (%). Gross discharge - SOC rises are not
            netted out. F-A6's consumed-energy input.
        soc_charge_percent: Sum of positive SOC rises between consecutive
            samples (%). F-C6's charged-energy input.
        distance_km: Sum of positive odometer deltas between consecutive
            samples (km).
        sample_count: Telemetry rows inside the window. A fold needs two
            adjacent rows to produce one delta, so a count below 2 means
            no interval was measurable.
        odometer_sample_count: Rows inside the window whose ``odometer``
            was not NULL - lets a caller tell "vehicle didn't move" apart
            from "device never reports odometer".
        first_recorded_at: Earliest ``recorded_at`` in the window, or
            ``None`` when the window is empty.
        last_recorded_at: Latest ``recorded_at`` in the window, or
            ``None`` when the window is empty.
    """

    soc_discharge_percent: float
    soc_charge_percent: float
    distance_km: float
    sample_count: int
    odometer_sample_count: int
    first_recorded_at: datetime | None
    last_recorded_at: datetime | None


# Engineering default, not vendor-confirmed - see docs/decisions/deferred.md.
# Used only when a vehicle has no recorded battery_capacity_kwh (F-A6/F-C6),
# so an existing vehicle still produces a report instead of a 4xx.
# Deliberately NOT a DB column default: writing this into the vehicles
# table would make "nobody entered the spec" indistinguishable from "the
# spec really is this value." The report echoes is_default_battery_capacity
# so a consumer never mistakes the estimate for a recorded spec.
DEFAULT_BATTERY_CAPACITY_KWH = 75.0


class GeofenceTransition(str, enum.Enum):
    """Direction a vehicle crossed a fleet geofence's boundary in (F-A5).

    Carried as ``transition`` in a ``GEOFENCE_ALERT`` notification payload.
    """

    ENTER = "ENTER"  # previous reading outside, current reading inside
    EXIT = "EXIT"  # previous reading inside, current reading outside


class ReportGranularity(str, enum.Enum):
    """Calendar period an F-A6 operating report is broken down by.

    Periods are cut in ``settings.APP_REPORT_TIMEZONE`` (planner D4); a
    week is an ISO week starting on Monday. Each value is also the
    PostgreSQL ``date_trunc`` field name the repository groups by.
    """

    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class ReportFormat(str, enum.Enum):
    """Output format of a report endpoint (F-A6)."""

    JSON = "json"
    CSV = "csv"


@dataclass(frozen=True)
class TelemetryReportPeriod:
    """One calendar period of a broken-down report window (F-A6).

    Attributes:
        bucket_start: Unclipped start of the calendar period (local
            midnight in the report time zone, as a UTC timestamp) - the
            key the repository's ``date_trunc`` grouping produces.
        period_start: Start of the period clipped to the requested window
            (UTC).
        period_end: End of the period clipped to the requested window
            (UTC); the next period's ``bucket_start`` when not clipped.
    """

    bucket_start: datetime
    period_start: datetime
    period_end: datetime


@dataclass(frozen=True)
class VehicleBatteryHealthDay:
    """A vehicle's battery health on one report-time-zone day (F-A3).

    Attributes:
        day_start: Local midnight of the day in
            ``settings.APP_REPORT_TIMEZONE``, as a UTC timestamp.
        soh_percent: SOH (%) of the day's last reading that reported one,
            or ``None`` if no reading that day did.
        cycle_count: Cycle count of the day's last reading that reported
            one, or ``None`` if no reading that day did.
    """

    day_start: datetime
    soh_percent: float | None
    cycle_count: int | None


@dataclass(frozen=True)
class VehicleLiveStatusReference:
    """A vehicle's newest position and connectivity, for other domains (F-A1).

    Returned by ``telemetry.service.resolve_vehicle_live_status``.

    Attributes:
        vehicle_id: Internal ID of the vehicle.
        latitude: GPS latitude of the newest reading (by ``recorded_at``).
        longitude: GPS longitude of the newest reading.
        recorded_at: Device timestamp of the newest reading (UTC).
        received_at: Backend receive time of that same reading (UTC).
        is_online: ``True`` while the vehicle's newest *received* telemetry
            arrived within ``settings.TELEMETRY_ONLINE_THRESHOLD_SECONDS``
            (planner D2); computed at read time, never stored.
        signal_strength_dbm: Signal strength of the newest reading in dBm,
            or ``None`` if the device didn't report it.
    """

    vehicle_id: UUID
    latitude: float
    longitude: float
    recorded_at: datetime
    received_at: datetime
    is_online: bool
    signal_strength_dbm: int | None


@dataclass(frozen=True)
class VehicleOperatingSummary:
    """Additive operating figures of one vehicle over one window (F-A6, F-E1).

    Returned by ``telemetry.service.resolve_vehicle_operating_summary`` so a
    fleet rollup can sum numerators and denominators across vehicles
    instead of averaging per-vehicle rates.

    Attributes:
        vehicle_id: Internal ID of the vehicle.
        start_time: Normalized (UTC) lower bound actually used.
        end_time: Normalized (UTC) upper bound actually used.
        distance_km: Distance traveled in the window (sum of positive
            odometer deltas).
        energy_consumed_kwh: Energy inferred from summed SOC drops,
            converted with ``battery_capacity_kwh``.
        sample_count: Telemetry rows inside the window.
        odometer_sample_count: Rows whose ``odometer`` was not NULL.
        first_recorded_at: Earliest reading in the window, or ``None``.
        last_recorded_at: Latest reading in the window, or ``None``.
        battery_capacity_kwh: Pack capacity used for the kWh conversion.
        is_default_battery_capacity: ``True`` if the vehicle has no
            recorded capacity and ``DEFAULT_BATTERY_CAPACITY_KWH`` was used.
    """

    vehicle_id: UUID
    start_time: datetime
    end_time: datetime
    distance_km: float
    energy_consumed_kwh: float
    sample_count: int
    odometer_sample_count: int
    first_recorded_at: datetime | None
    last_recorded_at: datetime | None
    battery_capacity_kwh: float
    is_default_battery_capacity: bool
