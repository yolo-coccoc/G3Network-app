"""Shared types for the telemetry domain.

Feature code: F-A2 (Tiered battery alerts), F-A4 (Anomaly detection)
"""

import enum
from dataclasses import dataclass, field

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
