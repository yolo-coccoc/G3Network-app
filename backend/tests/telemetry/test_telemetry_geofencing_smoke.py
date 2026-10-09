"""Smoke tests for geofence entry/exit alerts during ingestion (F-A5).

The fleet and notifications domains are monkeypatched at their public
services; ``process_message`` runs with a fake session.
"""

from datetime import datetime, timezone
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.fleet.service as fleet_service
import app.domains.notifications.service as notifications_service
import app.domains.telematics.service as telematics_public_service
import app.domains.telemetry.geofencing as telemetry_geofencing
import app.domains.telemetry.repository as telemetry_repository
import app.domains.telemetry.service as telemetry_service
from app.domains.fleet.types import GeofenceReference
from app.domains.notifications.types import NotificationSeverity, NotificationType
from app.domains.telematics.types import TelematicVehicleMapping
from app.domains.telemetry.models import VehicleTelemetryModel
from app.domains.telemetry.types import GeofenceTransition
from tests.builders import (
    build_telemetry_envelope,
    build_telemetry_record,
    fake_db_session,
)

# build_telemetry_record() stores this position; build_telemetry_envelope()
# reports (10.8, 106.7).
_PREVIOUS_LATITUDE = 10.762622
_CURRENT_LATITUDE = 10.8
_PREVIOUS_RECORDED_AT = datetime(2026, 8, 26, 9, 59, tzinfo=timezone.utc)

_DEPOT = GeofenceReference(geofence_id=uuid4(), name="Depot")
_YARD = GeofenceReference(geofence_id=uuid4(), name="Yard")


def test_detect_geofence_transitions_lists_exits_then_entries() -> None:
    """Left geofences come first, then entered ones; a shared geofence yields nothing."""
    shared = GeofenceReference(geofence_id=uuid4(), name="City")

    transitions = telemetry_geofencing.detect_geofence_transitions(
        [_DEPOT, shared], [shared, _YARD]
    )

    assert transitions == [
        (_DEPOT, GeofenceTransition.EXIT),
        (_YARD, GeofenceTransition.ENTER),
    ]


def test_detect_geofence_transitions_is_empty_without_change() -> None:
    """Staying inside (or outside) every geofence raises nothing."""
    assert telemetry_geofencing.detect_geofence_transitions([_DEPOT], [_DEPOT]) == []
    assert telemetry_geofencing.detect_geofence_transitions([], []) == []


def _patch_ingestion(
    monkeypatch: pytest.MonkeyPatch,
    *,
    vehicle_id: UUID,
    previous_telemetry: VehicleTelemetryModel | None,
    fleet_id: UUID | None,
    geofences_by_latitude: dict[float, list[GeofenceReference]],
) -> list[dict[str, object]]:
    """Wire process_message to fakes and capture every notification created.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        vehicle_id: Vehicle the device maps to.
        previous_telemetry: The vehicle's reading before the message.
        fleet_id: The vehicle's current fleet, or ``None``.
        geofences_by_latitude: Geofences covering a point, keyed by the
            point's latitude (the two test positions differ by latitude).

    Returns:
        The list that collects each ``create_notification`` call's kwargs.
    """
    created_notifications: list[dict[str, object]] = []
    mapping = TelematicVehicleMapping(telematic_id=uuid4(), vehicle_id=vehicle_id)

    async def resolve_mapping(db: AsyncSession, serial: str) -> TelematicVehicleMapping:
        return mapping

    async def insert_telemetry(db: AsyncSession, values: dict[str, object]) -> int:
        return 1

    async def previous_reading(
        db: AsyncSession, vehicle_id: UUID
    ) -> VehicleTelemetryModel | None:
        return previous_telemetry

    async def current_fleet(db: AsyncSession, vehicle_id: UUID) -> UUID | None:
        return fleet_id

    async def geofences_containing(
        db: AsyncSession, fleet_id: UUID, latitude: float, longitude: float
    ) -> list[GeofenceReference]:
        return geofences_by_latitude.get(round(latitude, 6), [])

    async def record_notification(db: AsyncSession, **kwargs: object) -> None:
        created_notifications.append(kwargs)

    monkeypatch.setattr(
        telematics_public_service, "resolve_mapping_by_serial", resolve_mapping
    )
    monkeypatch.setattr(telemetry_repository, "insert_telemetry", insert_telemetry)
    monkeypatch.setattr(
        telemetry_repository, "get_latest_vehicle_telemetry", previous_reading
    )
    monkeypatch.setattr(
        fleet_service, "find_current_fleet_id_by_vehicle", current_fleet
    )
    monkeypatch.setattr(
        fleet_service, "list_geofences_containing", geofences_containing
    )
    monkeypatch.setattr(
        notifications_service, "create_notification", record_notification
    )
    return created_notifications


def _previous_reading(vehicle_id: UUID) -> VehicleTelemetryModel:
    """Build the vehicle's previous reading (SOC 80, same as the message).

    Args:
        vehicle_id: Vehicle the reading belongs to.

    Returns:
        A telemetry row at ``_PREVIOUS_LATITUDE`` that trips no other alert.
    """
    return build_telemetry_record(
        vehicle_id=vehicle_id, recorded_at=_PREVIOUS_RECORDED_AT
    )


@pytest.mark.asyncio
async def test_process_message_raises_geofence_enter_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moving into a fleet geofence raises one GEOFENCE_ALERT with the ENTER payload."""
    vehicle_id, fleet_id = uuid4(), uuid4()
    created_notifications = _patch_ingestion(
        monkeypatch,
        vehicle_id=vehicle_id,
        previous_telemetry=_previous_reading(vehicle_id),
        fleet_id=fleet_id,
        geofences_by_latitude={_CURRENT_LATITUDE: [_DEPOT]},
    )

    result = await telemetry_service.process_message(
        fake_db_session(), build_telemetry_envelope()
    )

    assert result == {"processed": 1, "skipped": 0, "errors": 0}
    assert len(created_notifications) == 1
    call = created_notifications[0]
    assert call["notification_type"] is NotificationType.GEOFENCE_ALERT
    assert call["severity"] is NotificationSeverity.WARNING
    assert call["vehicle_id"] == vehicle_id
    assert "Depot" in str(call["title"]) and "ENTER" in str(call["title"])
    assert "Depot" in str(call["body"])
    payload = cast(dict[str, object], call["payload"])
    assert payload == {
        "geofence_id": str(_DEPOT.geofence_id),
        "geofence_name": "Depot",
        "fleet_id": str(fleet_id),
        "transition": "ENTER",
        "latitude": 10.8,
        "longitude": 106.7,
        "recorded_at": "2026-08-26T10:00:00+00:00",
        "previous_recorded_at": _PREVIOUS_RECORDED_AT.isoformat(),
    }


@pytest.mark.asyncio
async def test_process_message_raises_geofence_exit_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moving out of a fleet geofence raises one EXIT alert."""
    vehicle_id = uuid4()
    created_notifications = _patch_ingestion(
        monkeypatch,
        vehicle_id=vehicle_id,
        previous_telemetry=_previous_reading(vehicle_id),
        fleet_id=uuid4(),
        geofences_by_latitude={_PREVIOUS_LATITUDE: [_YARD]},
    )

    await telemetry_service.process_message(
        fake_db_session(), build_telemetry_envelope()
    )

    assert len(created_notifications) == 1
    payload = cast(dict[str, object], created_notifications[0]["payload"])
    assert payload["transition"] == "EXIT"
    assert payload["geofence_name"] == "Yard"


@pytest.mark.asyncio
async def test_process_message_raises_nothing_while_staying_inside(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two readings inside the same geofence raise no alert."""
    vehicle_id = uuid4()
    created_notifications = _patch_ingestion(
        monkeypatch,
        vehicle_id=vehicle_id,
        previous_telemetry=_previous_reading(vehicle_id),
        fleet_id=uuid4(),
        geofences_by_latitude={
            _PREVIOUS_LATITUDE: [_DEPOT],
            _CURRENT_LATITUDE: [_DEPOT],
        },
    )

    await telemetry_service.process_message(
        fake_db_session(), build_telemetry_envelope()
    )

    assert created_notifications == []


@pytest.mark.asyncio
async def test_process_message_skips_geofences_on_first_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A vehicle's first reading inside a geofence raises nothing (no burst)."""
    vehicle_id = uuid4()
    created_notifications = _patch_ingestion(
        monkeypatch,
        vehicle_id=vehicle_id,
        previous_telemetry=None,
        fleet_id=uuid4(),
        geofences_by_latitude={_CURRENT_LATITUDE: [_DEPOT]},
    )

    await telemetry_service.process_message(
        fake_db_session(), build_telemetry_envelope()
    )

    assert created_notifications == []


@pytest.mark.asyncio
async def test_process_message_skips_geofences_without_a_fleet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A vehicle in no fleet is never checked against any geofence."""
    vehicle_id = uuid4()
    created_notifications = _patch_ingestion(
        monkeypatch,
        vehicle_id=vehicle_id,
        previous_telemetry=_previous_reading(vehicle_id),
        fleet_id=None,
        geofences_by_latitude={_CURRENT_LATITUDE: [_DEPOT]},
    )

    await telemetry_service.process_message(
        fake_db_session(), build_telemetry_envelope()
    )

    assert created_notifications == []
