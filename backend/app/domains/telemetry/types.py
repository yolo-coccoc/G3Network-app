"""Shared types for the telemetry domain.

Feature code: F-A2 (Tiered battery alerts), F-A4 (Anomaly detection),
F-A3 (Battery health (SOH) & cycle tracking), F-A6 (Operating performance
report), F-C6 (Per-customer energy usage)
"""

import enum
from dataclasses import dataclass, field
from datetime import datetime

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
    # this one generic type. See docs/01-requirements/future.md.
    DEVICE_FAULT = "DEVICE_FAULT"


# Engineering defaults, not vendor-confirmed against real battery/pack specs
# - see docs/01-requirements/future.md for the item to revisit these once
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
            in ``telemetry/service.py``.
    """

    anomaly_type: VehicleAnomalyType
    severity: NotificationSeverity
    evidence: dict[str, object] = field(default_factory=dict)


# Engineering default, not vendor-confirmed - see docs/01-requirements/future.md.
# A single threshold, not tiers like BATTERY_ALERT_THRESHOLDS: F-A3 only asks
# for "alert when SOH drops below a configured threshold," not multiple
# severity levels.
SOH_ALERT_THRESHOLD_PERCENT = 70.0


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


# Engineering default, not vendor-confirmed - see docs/01-requirements/future.md.
# Used only when a vehicle has no recorded battery_capacity_kwh (F-A6/F-C6),
# so an existing vehicle still produces a report instead of a 4xx.
# Deliberately NOT a DB column default: writing this into the vehicles
# table would make "nobody entered the spec" indistinguishable from "the
# spec really is this value." The report echoes is_default_battery_capacity
# so a consumer never mistakes the estimate for a recorded spec.
DEFAULT_BATTERY_CAPACITY_KWH = 75.0

# Engineering default, not a confirmed tariff - see
# docs/01-requirements/future.md. F-A6's own constraint says "cost formula
# must be configurable (electricity price varies)" - that is knowingly
# unmet this round. Flat rate only: no time-of-use, no per-station tariff,
# no tax or demand charges.
ENERGY_COST_PER_KWH_VND = 3000.0
