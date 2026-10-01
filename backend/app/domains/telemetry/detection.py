"""Pure detectors for the per-message telemetry alerts.

Feature code: F-A2 (Tiered battery alerts), F-A3 (Battery health (SOH) &
cycle tracking), F-A4 (Anomaly detection).

Internal to the telemetry domain: other domains never import this module
(they go through ``telemetry/service.py``). Every function here is pure -
it compares the vehicle's previous reading with the current message and
says whether an alert condition was just entered. Raising the
notification is ``alerting.py``'s job.
"""

from collections.abc import Sequence

from app.domains.telemetry.models import VehicleTelemetryModel
from app.domains.telemetry.schemas import TelemetryMessage
from app.domains.telemetry.types import (
    BATTERY_ALERT_THRESHOLDS,
    HIGH_BATTERY_TEMPERATURE_THRESHOLD_CELSIUS,
    SOH_ALERT_THRESHOLD_PERCENT,
    VEHICLE_ANOMALY_SEVERITIES,
    VOLTAGE_DROP_THRESHOLD_VOLTS,
    BatteryAlertLevel,
    VehicleAnomaly,
    VehicleAnomalyType,
)


def detect_battery_alert_level(
    previous_soc: float | None, current_soc: float
) -> BatteryAlertLevel | None:
    """Detect whether SOC just crossed a tiered alert threshold (F-A2).

    A crossing is ``previous_soc > threshold >= current_soc`` - strict on
    the previous side, inclusive on the current side. The asymmetry is load-bearing: it makes a reading of exactly the
    threshold alert once and only once. If both sides were inclusive, a
    vehicle resting at exactly the threshold would alert on every message it
    sends while parked there.

    Args:
        previous_soc: The vehicle's previous SOC reading (%), or ``None`` if
            this is the first telemetry ever recorded for the vehicle (in
            which case no alert is ever raised, regardless of how low
            ``current_soc`` is).
        current_soc: The current message's SOC reading (%).

    Returns:
        The most severe level crossed by this single reading (a gap that
        skips multiple thresholds, e.g. 35% to 8%, still raises exactly one
        alert), or ``None`` if no threshold was crossed downward.
    """
    if previous_soc is None:
        return None
    crossed_level: BatteryAlertLevel | None = None
    for level, threshold in BATTERY_ALERT_THRESHOLDS.items():
        if previous_soc > threshold.threshold_percent >= current_soc:
            # Iterates least to most severe; keep the last match so a
            # multi-threshold drop resolves to the most severe one crossed.
            crossed_level = level
    return crossed_level


def detect_soh_alert(previous_soh: float | None, current_soh: float | None) -> bool:
    """Detect whether battery SOH just crossed the alert threshold (F-A3).

    Same strict-above/inclusive-below crossing shape as
    ``detect_battery_alert_level`` - fires once on entry, not on every
    message resting below the threshold. Unlike F-A4's fire-safety
    detectors, a missing previous reading means "no alert" here (matching
    F-A2): gradual SOH degradation isn't a condition where skipping the
    very first reading carries real risk.

    Args:
        previous_soh: The vehicle's previous SOH reading (%), or ``None``
            if this is the first reading or the device didn't report it.
        current_soh: The current message's SOH reading (%), or ``None`` if
            the device didn't report it.

    Returns:
        ``True`` if SOH just crossed below
        ``SOH_ALERT_THRESHOLD_PERCENT``, else ``False``.
    """
    if previous_soh is None or current_soh is None:
        return False
    return previous_soh > SOH_ALERT_THRESHOLD_PERCENT >= current_soh


def detect_high_battery_temperature(
    previous_celsius: float | None, current_celsius: float | None
) -> VehicleAnomaly | None:
    """Detect a battery temperature entering the high-temperature anomaly range (F-A4).

    This is a level condition, not a one-time crossing like F-A2's SOC
    thresholds - a vehicle resting above the
    threshold would alert on every message if this fired on "at or above the
    threshold" alone. It only fires on *entry*: unlike
    ``detect_battery_alert_level``, a missing previous reading is treated as
    "below threshold" rather than suppressing the alert - a fire-safety
    anomaly must not be silently skipped just because it is the vehicle's
    first message. It stays silent while the reading remains above the
    threshold, and re-arms once the reading recovers below it.

    Args:
        previous_celsius: The vehicle's previous battery temperature
            reading, or ``None`` if this is the first reading or the device
            didn't report it.
        current_celsius: The current message's battery temperature reading,
            or ``None`` if the device didn't report it (in which case
            nothing can be detected).

    Returns:
        A ``HIGH_BATTERY_TEMPERATURE`` anomaly on entry into the high range,
        or ``None``.
    """
    if current_celsius is None:
        return None
    was_below = (
        previous_celsius is None
        or previous_celsius < HIGH_BATTERY_TEMPERATURE_THRESHOLD_CELSIUS
    )
    if not (
        was_below and current_celsius >= HIGH_BATTERY_TEMPERATURE_THRESHOLD_CELSIUS
    ):
        return None
    return VehicleAnomaly(
        anomaly_type=VehicleAnomalyType.HIGH_BATTERY_TEMPERATURE,
        severity=VEHICLE_ANOMALY_SEVERITIES[
            VehicleAnomalyType.HIGH_BATTERY_TEMPERATURE
        ],
        evidence={
            "threshold_celsius": HIGH_BATTERY_TEMPERATURE_THRESHOLD_CELSIUS,
            "observed_celsius": current_celsius,
        },
    )


def detect_sudden_voltage_drop(
    previous_volts: float | None, current_volts: float | None
) -> VehicleAnomaly | None:
    """Detect a sudden absolute drop in battery voltage between readings (F-A4).

    Undefined without both readings, unlike the high-temperature
    detector - a drop is a comparison between two points,
    so a missing previous reading (first message, or device didn't report
    voltage) means nothing can be said, and this stays silent rather than
    guessing a baseline.

    Args:
        previous_volts: The vehicle's previous battery voltage reading, or
            ``None``.
        current_volts: The current message's battery voltage reading, or
            ``None``.

    Returns:
        A ``SUDDEN_VOLTAGE_DROP`` anomaly if the drop meets or exceeds
        ``VOLTAGE_DROP_THRESHOLD_VOLTS``, or ``None``.
    """
    if previous_volts is None or current_volts is None:
        return None
    drop_volts = previous_volts - current_volts
    if drop_volts < VOLTAGE_DROP_THRESHOLD_VOLTS:
        return None
    return VehicleAnomaly(
        anomaly_type=VehicleAnomalyType.SUDDEN_VOLTAGE_DROP,
        severity=VEHICLE_ANOMALY_SEVERITIES[VehicleAnomalyType.SUDDEN_VOLTAGE_DROP],
        evidence={
            "threshold_volts": VOLTAGE_DROP_THRESHOLD_VOLTS,
            "previous_volts": previous_volts,
            "current_volts": current_volts,
            "drop_volts": drop_volts,
        },
    )


def detect_new_error_codes(
    previous_codes: Sequence[str] | None, current_codes: Sequence[str] | None
) -> VehicleAnomaly | None:
    """Detect a device error code that wasn't present in the previous reading (F-A4).

    F-A4 names "cell/module fault" and "motor fault" as separate
    triggers, but the MQTT contract only carries opaque error
    code strings with no vendor catalog to map a code to one or the other -
    see ``VehicleAnomalyType.DEVICE_FAULT``'s docstring and
    ``docs/01-requirements/future.md``. Only *newly appearing* codes fire an
    anomaly; a code that was already active on the previous reading (still
    faulted, not a new fault) or one that cleared does not.

    Args:
        previous_codes: Error codes active on the vehicle's previous
            reading, or ``None`` if there were none.
        current_codes: Error codes active on the current reading, or
            ``None`` if there are none.

    Returns:
        A ``DEVICE_FAULT`` anomaly listing the newly appeared codes, or
        ``None`` if there are none.
    """
    if not current_codes:
        return None
    new_codes = sorted(set(current_codes) - set(previous_codes or []))
    if not new_codes:
        return None
    return VehicleAnomaly(
        anomaly_type=VehicleAnomalyType.DEVICE_FAULT,
        severity=VEHICLE_ANOMALY_SEVERITIES[VehicleAnomalyType.DEVICE_FAULT],
        evidence={
            "new_codes": new_codes,
            "active_codes": sorted(current_codes),
        },
    )


def detect_vehicle_anomalies(
    previous_telemetry: VehicleTelemetryModel | None,
    message: TelemetryMessage,
) -> list[VehicleAnomaly]:
    """Run every F-A4 detector against one telemetry reading.

    A single message may legitimately trip more than one
    detector (e.g. a battery fire event could show both high temperature
    and a new fault code), so every detector runs independently and all
    results are returned.

    Args:
        previous_telemetry: The vehicle's previous telemetry row, already
            queried by the caller, or ``None`` for the vehicle's first
            reading.
        message: The current message already validated by Pydantic.

    Returns:
        Every anomaly detected in this reading, in detector-declaration
        order (temperature, voltage, then fault codes); empty if none.
    """
    previous_temperature = (
        previous_telemetry.battery_temperature if previous_telemetry else None
    )
    previous_voltage = (
        previous_telemetry.battery_voltage if previous_telemetry else None
    )
    # error_codes is stored as {"codes": [...]} JSONB, or None.
    previous_error_codes = (
        previous_telemetry.error_codes.get("codes")
        if previous_telemetry and previous_telemetry.error_codes
        else None
    )

    anomalies: list[VehicleAnomaly] = []
    temperature_anomaly = detect_high_battery_temperature(
        previous_temperature,
        message.battery.temperature,
    )
    if temperature_anomaly is not None:
        anomalies.append(temperature_anomaly)
    voltage_anomaly = detect_sudden_voltage_drop(
        previous_voltage,
        message.battery.voltage,
    )
    if voltage_anomaly is not None:
        anomalies.append(voltage_anomaly)
    fault_anomaly = detect_new_error_codes(previous_error_codes, message.errors)
    if fault_anomaly is not None:
        anomalies.append(fault_anomaly)
    return anomalies
