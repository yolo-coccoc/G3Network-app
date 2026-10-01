"""Raise the notifications for the alerts detected in one telemetry reading.

Feature code: F-A2 (Tiered battery alerts), F-A3 (Battery health (SOH) &
cycle tracking), F-A4 (Anomaly detection).

Internal to the telemetry domain: other domains never import this module
(they go through ``telemetry/service.py``). ``raise_alerts_for_reading`` is
the one entry point, called by ``service.process_message`` after the
reading is inserted: it runs the pure detectors in ``detection.py`` and
writes one notification per alert into the caller's session. It never
commits - the ingestion worker owns the transaction, so a failed
notification write rolls back the telemetry row with it.

Cross-domain edges owned by this module: ``notifications`` (storage of
every alert) and ``charging_stations`` (nearest operational station for
the F-A2 battery-alert payload).
"""

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_stations.service as charging_stations_service
import app.domains.notifications.service as notifications_service
import app.domains.telemetry.detection as telemetry_detection
import app.domains.telemetry.mappers as telemetry_mappers
from app.domains.notifications.types import NotificationSeverity, NotificationType
from app.domains.telemetry.models import VehicleTelemetryModel
from app.domains.telemetry.schemas import TelemetryMessage
from app.domains.telemetry.types import (
    BATTERY_ALERT_THRESHOLDS,
    BatteryAlertLevel,
    VehicleAnomaly,
    VehicleAnomalyType,
)
from app.libs.common.config import settings

logger = logging.getLogger(__name__)

# Notification title per F-A4 anomaly type. Every VehicleAnomalyType member
# must have an entry: a missing one raises KeyError mid-ingestion.
_ANOMALY_TITLES: dict[VehicleAnomalyType, str] = {
    VehicleAnomalyType.HIGH_BATTERY_TEMPERATURE: "High battery temperature detected",
    VehicleAnomalyType.SUDDEN_VOLTAGE_DROP: "Sudden battery voltage drop detected",
    VehicleAnomalyType.DEVICE_FAULT: "Device fault code reported",
}


async def _raise_battery_alert(
    db: AsyncSession,
    *,
    vehicle_id: UUID,
    alert_level: BatteryAlertLevel,
    current_soc: float,
    latitude: float,
    longitude: float,
) -> None:
    """Resolve the nearest operational station and raise a battery alert.

    Args:
        db: Session whose transaction is owned by the worker.
        vehicle_id: Vehicle the alert is about.
        alert_level: Level returned by ``detect_battery_alert_level``.
        current_soc: SOC (%) that triggered the alert.
        latitude: Vehicle's GPS latitude at the triggering message.
        longitude: Vehicle's GPS longitude at the triggering message.

    Side Effects:
        Writes one notification row into the session; does not commit. The
        nearest-station lookup is a snapshot taken now, from the vehicle's
        GPS at this exact message - it is not recomputed later, so it
        describes where the vehicle was when it crossed the threshold, not
        where it currently is. The payload carries the station's id, name,
        coordinates (``station_latitude``/``station_longitude``, so a client
        can route there without a second lookup) and distance - all
        ``None`` when no operational station exists.
    """
    threshold = BATTERY_ALERT_THRESHOLDS[alert_level]
    nearest_station = await charging_stations_service.find_nearest_operational_station(
        db, latitude=latitude, longitude=longitude
    )
    payload: dict[str, object] = {
        "threshold_percent": threshold.threshold_percent,
        "soc": current_soc,
        "station_id": (
            str(nearest_station.station_id) if nearest_station is not None else None
        ),
        "station_name": (
            nearest_station.display_name if nearest_station is not None else None
        ),
        "station_latitude": (
            nearest_station.latitude if nearest_station is not None else None
        ),
        "station_longitude": (
            nearest_station.longitude if nearest_station is not None else None
        ),
        "distance_km": (
            nearest_station.distance_km if nearest_station is not None else None
        ),
    }
    await notifications_service.create_notification(
        db,
        notification_type=NotificationType.BATTERY_ALERT,
        severity=threshold.severity,
        vehicle_id=vehicle_id,
        title=f"Battery at {current_soc:.0f}% ({alert_level.value.title()})",
        body=(
            f"Vehicle battery dropped to {current_soc:.1f}%, crossing the "
            f"{threshold.threshold_percent:.0f}% threshold."
        ),
        payload=payload,
    )
    logger.info(
        "battery alert raised",
        extra={
            "vehicle_id": str(vehicle_id),
            "alert_level": alert_level.value,
            "soc": current_soc,
        },
    )


async def _raise_soh_alert(
    db: AsyncSession,
    *,
    vehicle_id: UUID,
    current_soh: float,
    cycle_count: int | None,
) -> None:
    """Raise a battery-health notification (F-A3).

    Args:
        db: Session whose transaction is owned by the worker.
        vehicle_id: Vehicle the alert is about.
        current_soh: SOH (%) that triggered the alert.
        cycle_count: The vehicle's current charge/discharge cycle count,
            nullable, included in the payload for context.

    Side Effects:
        Writes one notification row into the session; does not commit.
        Reads ``settings.TELEMETRY_SOH_ALERT_THRESHOLD_PERCENT`` for the
        payload and body.
    """
    threshold_percent = settings.TELEMETRY_SOH_ALERT_THRESHOLD_PERCENT
    payload: dict[str, object] = {
        "threshold_percent": threshold_percent,
        "soh_percent": current_soh,
        "cycle_count": cycle_count,
    }
    await notifications_service.create_notification(
        db,
        notification_type=NotificationType.SOH_ALERT,
        severity=NotificationSeverity.WARNING,
        vehicle_id=vehicle_id,
        title=f"Battery health at {current_soh:.0f}%",
        body=(
            f"Vehicle battery SOH dropped to {current_soh:.1f}%, crossing "
            f"the {threshold_percent:.0f}% threshold."
        ),
        payload=payload,
    )
    logger.info(
        "SOH alert raised",
        extra={"vehicle_id": str(vehicle_id), "soh_percent": current_soh},
    )


async def _raise_vehicle_anomaly_alert(
    db: AsyncSession,
    *,
    vehicle_id: UUID,
    anomaly: VehicleAnomaly,
    message: TelemetryMessage,
) -> None:
    """Raise an F-A4 anomaly notification carrying evidence and a data snapshot.

    Args:
        db: Session whose transaction is owned by the worker.
        vehicle_id: Vehicle the anomaly was detected on.
        anomaly: Anomaly already detected by ``detect_vehicle_anomalies``.
        message: The telemetry message the anomaly was detected in, used to
            build the stored snapshot.

    Side Effects:
        Writes one notification row into the session; does not commit.
    """
    payload: dict[str, object] = {
        "anomaly_type": anomaly.anomaly_type.value,
        "evidence": anomaly.evidence,
        "snapshot": telemetry_mappers.to_telemetry_snapshot(message),
    }
    await notifications_service.create_notification(
        db,
        notification_type=NotificationType.ANOMALY_ALERT,
        severity=anomaly.severity,
        vehicle_id=vehicle_id,
        title=_ANOMALY_TITLES[anomaly.anomaly_type],
        body=(
            f"Vehicle anomaly '{anomaly.anomaly_type.value}' detected with "
            f"evidence {anomaly.evidence}."
        ),
        payload=payload,
    )
    logger.info(
        "vehicle anomaly alert raised",
        extra={
            "vehicle_id": str(vehicle_id),
            "anomaly_type": anomaly.anomaly_type.value,
        },
    )


async def raise_alerts_for_reading(
    db: AsyncSession,
    *,
    vehicle_id: UUID,
    previous_telemetry: VehicleTelemetryModel | None,
    message: TelemetryMessage,
) -> None:
    """Detect every per-message alert in one reading and raise its notification.

    Runs, in this order, F-A2's battery-threshold detection against the
    previous SOC (a crossing raises exactly one notification), F-A3's SOH
    threshold detection against the previous SOH, and F-A4's anomaly
    detectors against the previous reading (a message may trip zero, one,
    or more of them, each raising its own notification).

    Args:
        db: Session whose transaction is owned by the ingestion worker.
        vehicle_id: Vehicle the reading belongs to.
        previous_telemetry: The vehicle's reading before this one, read by
            the caller *before* inserting the current row, or ``None`` for
            the vehicle's first-ever message.
        message: The current message, already validated by Pydantic.

    Side Effects:
        Writes zero or more notification rows into the session and logs one
        structured line per alert; does not commit. A database error
        propagates so the worker rolls back the whole message.
    """
    previous_soc = previous_telemetry.soc if previous_telemetry is not None else None
    alert_level = telemetry_detection.detect_battery_alert_level(
        previous_soc, message.battery.soc
    )
    if alert_level is not None:
        await _raise_battery_alert(
            db,
            vehicle_id=vehicle_id,
            alert_level=alert_level,
            current_soc=message.battery.soc,
            latitude=message.location.latitude,
            longitude=message.location.longitude,
        )

    previous_soh = previous_telemetry.soh_percent if previous_telemetry else None
    if telemetry_detection.detect_soh_alert(
        previous_soh=previous_soh,
        current_soh=message.battery.soh_percent,
        threshold_percent=settings.TELEMETRY_SOH_ALERT_THRESHOLD_PERCENT,
    ):
        assert message.battery.soh_percent is not None, (
            "detect_soh_alert() only returns True when current_soh is not None"
        )
        await _raise_soh_alert(
            db,
            vehicle_id=vehicle_id,
            current_soh=message.battery.soh_percent,
            cycle_count=message.battery.cycle_count,
        )

    for anomaly in telemetry_detection.detect_vehicle_anomalies(
        previous_telemetry, message
    ):
        await _raise_vehicle_anomaly_alert(
            db,
            vehicle_id=vehicle_id,
            anomaly=anomaly,
            message=message,
        )
