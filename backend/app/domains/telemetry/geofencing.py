"""Detect fleet geofence entry/exit in one telemetry reading and raise its alerts.

Feature code: F-A5 (Location, trip history & geofencing - the geofencing
part).

Internal to the telemetry domain: other domains never import this module
(they go through ``telemetry/service.py``). ``raise_geofence_alerts_for_reading``
is the one entry point, called by ``service.process_message`` after the
reading is inserted, next to ``alerting.raise_alerts_for_reading``.

Rules (planner D6/D7 of ``backend-happy-path-completion.md``):

- Geofences belong to a fleet and apply to the fleet's *current* member
  vehicles; a vehicle in no fleet is never checked.
- A transition is the difference between the geofences covering the
  previous reading's position and those covering the current one, both
  looked up in the vehicle's current fleet. A boundary point counts as
  inside (the fleet domain uses ``ST_Covers``).
- A vehicle's first-ever reading has nothing to compare against and
  raises nothing, so a newly reporting vehicle that already sits inside a
  geofence does not produce a burst of ENTER alerts.
- The previous reading is the newest by ``recorded_at`` before the insert
  (the same row the F-A2/F-A3/F-A4 detectors compare against); readings
  arriving out of order are not re-sequenced (happy path).

It never commits - the ingestion worker owns the transaction, so a failed
notification write rolls back the telemetry row with it.

Cross-domain edges owned by this module: ``fleet`` (the vehicle's current
fleet and geofence containment) and ``notifications`` (storage of every
``GEOFENCE_ALERT``).
"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.fleet.service as fleet_service
import app.domains.notifications.service as notifications_service
import app.domains.telemetry.mappers as telemetry_mappers
from app.domains.fleet.types import GeofenceReference
from app.domains.notifications.types import NotificationSeverity, NotificationType
from app.domains.telemetry.models import TelemetryModel
from app.domains.telemetry.schemas import TelemetryMessage
from app.domains.telemetry.types import GeofenceTransition

logger = logging.getLogger(__name__)


def detect_geofence_transitions(
    previous_geofences: list[GeofenceReference],
    current_geofences: list[GeofenceReference],
) -> list[tuple[GeofenceReference, GeofenceTransition]]:
    """Compare the geofences around two consecutive readings.

    Pure function, no I/O. Geofences are matched by ``geofence_id``.

    Args:
        previous_geofences: Geofences covering the previous reading.
        current_geofences: Geofences covering the current reading.

    Returns:
        One ``(geofence, transition)`` pair per geofence left (``EXIT``,
        in ``previous_geofences`` order) followed by one per geofence
        entered (``ENTER``, in ``current_geofences`` order) - exits first,
        so moving from one area straight into another reads in order. A
        geofence covering both readings yields nothing.
    """
    previous_ids = {geofence.geofence_id for geofence in previous_geofences}
    current_ids = {geofence.geofence_id for geofence in current_geofences}
    transitions: list[tuple[GeofenceReference, GeofenceTransition]] = [
        (geofence, GeofenceTransition.EXIT)
        for geofence in previous_geofences
        if geofence.geofence_id not in current_ids
    ]
    transitions.extend(
        (geofence, GeofenceTransition.ENTER)
        for geofence in current_geofences
        if geofence.geofence_id not in previous_ids
    )
    return transitions


def _format_utc(timestamp: datetime) -> str:
    """Render a timestamp as a UTC ISO 8601 string for a JSONB payload.

    Args:
        timestamp: Timezone-aware timestamp.

    Returns:
        The timestamp converted to UTC, in ISO 8601.
    """
    return timestamp.astimezone(timezone.utc).isoformat()


async def _raise_geofence_alert(
    db: AsyncSession,
    *,
    organization_id: UUID,
    vehicle_id: UUID,
    fleet_id: UUID,
    geofence: GeofenceReference,
    transition: GeofenceTransition,
    message: TelemetryMessage,
    previous_recorded_at: datetime,
) -> None:
    """Raise one ``GEOFENCE_ALERT`` notification (F-A5).

    Args:
        db: Session whose transaction is owned by the ingestion worker.
        organization_id: The vehicle's owner at the time of the reading
            (written on the alert once, DM-24 case C).
        vehicle_id: Vehicle that crossed the boundary.
        fleet_id: The vehicle's current fleet, which owns the geofence.
        geofence: The geofence entered or left.
        transition: ``ENTER`` or ``EXIT``.
        message: The current reading (its position and ``recorded_at``).
        previous_recorded_at: ``recorded_at`` of the previous reading -
            the crossing happened between the two timestamps.

    Side Effects:
        Writes one notification row (severity ``WARNING``) into the
        session and logs one structured line; does not commit.
    """
    latitude = message.location.latitude
    longitude = message.location.longitude
    payload: dict[str, object] = {
        "geofence_id": str(geofence.geofence_id),
        "geofence_name": geofence.name,
        "fleet_id": str(fleet_id),
        "transition": transition.value,
        "latitude": latitude,
        "longitude": longitude,
        "recorded_at": _format_utc(message.recorded_at),
        "previous_recorded_at": _format_utc(previous_recorded_at),
    }
    action = "entered" if transition is GeofenceTransition.ENTER else "left"
    await notifications_service.create_notification(
        db,
        organization_id=organization_id,
        notification_type=NotificationType.GEOFENCE_ALERT,
        severity=NotificationSeverity.WARNING,
        vehicle_id=vehicle_id,
        title=f"Geofence {transition.value}: {geofence.name}",
        body=(
            f"Vehicle {action} geofence '{geofence.name}' ({transition.value}) "
            f"at {latitude:.6f}, {longitude:.6f}."
        ),
        payload=payload,
    )
    logger.info(
        "geofence alert raised",
        extra={
            "vehicle_id": str(vehicle_id),
            "fleet_id": str(fleet_id),
            "geofence_id": str(geofence.geofence_id),
            "transition": transition.value,
        },
    )


async def raise_geofence_alerts_for_reading(
    db: AsyncSession,
    *,
    organization_id: UUID,
    vehicle_id: UUID,
    previous_telemetry: TelemetryModel | None,
    message: TelemetryMessage,
) -> None:
    """Detect geofence entry/exit for one reading and raise one alert per crossing.

    Does nothing without a previous reading or when the vehicle is in no
    fleet; otherwise looks up the fleet's geofences covering the previous
    and the current position (two queries) and raises one
    ``GEOFENCE_ALERT`` per geofence entered or left.

    Args:
        db: Session whose transaction is owned by the ingestion worker.
        organization_id: The vehicle's owner at the time of the reading
            (written on the alert once, DM-24 case C).
        vehicle_id: Vehicle the reading belongs to.
        previous_telemetry: The vehicle's reading before this one, read by
            the caller *before* inserting the current row, or ``None`` for
            the vehicle's first-ever message.
        message: The current message, already validated by Pydantic.

    Side Effects:
        Writes zero or more notification rows into the session; does not
        commit. A database error propagates so the worker rolls back the
        whole message.
    """
    if previous_telemetry is None:
        return
    fleet_id = await fleet_service.find_current_fleet_id_by_vehicle(db, vehicle_id)
    if fleet_id is None:
        return

    previous_latitude, previous_longitude = telemetry_mappers.to_coordinates(
        previous_telemetry
    )
    previous_geofences = await fleet_service.list_geofences_containing(
        db, fleet_id, latitude=previous_latitude, longitude=previous_longitude
    )
    current_geofences = await fleet_service.list_geofences_containing(
        db,
        fleet_id,
        latitude=message.location.latitude,
        longitude=message.location.longitude,
    )
    for geofence, transition in detect_geofence_transitions(
        previous_geofences, current_geofences
    ):
        await _raise_geofence_alert(
            db,
            organization_id=organization_id,
            vehicle_id=vehicle_id,
            fleet_id=fleet_id,
            geofence=geofence,
            transition=transition,
            message=message,
            previous_recorded_at=previous_telemetry.recorded_at,
        )
