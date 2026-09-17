"""Shared types for the telemetry domain.

Feature code: F-A2 (Tiered battery alerts)
"""

import enum
from dataclasses import dataclass

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
