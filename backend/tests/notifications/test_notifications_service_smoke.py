"""Smoke tests for the notifications service: response mapping and mark-read (F-A2)."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

import app.domains.notifications.repository as notification_repository
import app.domains.notifications.service as notifications_service
from app.domains.notifications.exceptions import NotificationNotFoundError
from app.domains.notifications.models import NotificationModel
from app.domains.notifications.types import NotificationSeverity, NotificationType
from tests.builders import fake_db_session


def test_notification_service_builds_response_from_model() -> None:
    """to_notification_response() maps the ORM model into the response schema."""
    now = datetime.now(timezone.utc)
    vehicle_id = uuid4()
    notification = NotificationModel(
        notification_id=42,
        notification_type=NotificationType.BATTERY_ALERT,
        severity=NotificationSeverity.WARNING,
        vehicle_id=vehicle_id,
        title="Battery at 18%",
        body="Vehicle battery dropped to 18.0%, crossing the 20% threshold.",
        payload={"threshold_percent": 20.0, "soc": 18.0},
        created_at=now,
        read_at=None,
    )

    response = notifications_service.to_notification_response(notification)

    assert response.notification_id == 42
    assert response.notification_type is NotificationType.BATTERY_ALERT
    assert response.severity is NotificationSeverity.WARNING
    assert response.vehicle_id == vehicle_id
    assert response.payload["soc"] == 18.0
    assert response.read_at is None


def _notification_record(*, read_at: datetime | None) -> NotificationModel:
    """Build a minimal notification ORM object for a mark-read test."""
    return NotificationModel(
        notification_id=7,
        notification_type=NotificationType.DEVICE_OFFLINE_ALERT,
        severity=NotificationSeverity.WARNING,
        vehicle_id=None,
        title="Device offline",
        body="No telemetry for 10 minutes.",
        payload={},
        created_at=datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc),
        read_at=read_at,
    )


@pytest.mark.asyncio
async def test_mark_notification_read_stamps_an_unread_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unread notification gets read_at = now, written through the repository."""
    notification_record = _notification_record(read_at=None)
    read_at = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
    stamped: list[datetime] = []

    async def get_by_id(db: object, notification_id: int) -> NotificationModel:
        return notification_record

    async def set_read_at(
        db: object, record: NotificationModel, new_read_at: datetime
    ) -> None:
        stamped.append(new_read_at)
        record.read_at = new_read_at

    monkeypatch.setattr(notification_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(notification_repository, "set_read_at", set_read_at)
    monkeypatch.setattr(notifications_service, "utc_now", lambda: read_at)

    response = await notifications_service.mark_notification_read(fake_db_session(), 7)

    assert stamped == [read_at]
    assert response.read_at == read_at


@pytest.mark.asyncio
async def test_mark_notification_read_keeps_the_first_read_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Marking an already-read notification again is idempotent: no write."""
    first_read_at = datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc)
    notification_record = _notification_record(read_at=first_read_at)

    async def get_by_id(db: object, notification_id: int) -> NotificationModel:
        return notification_record

    async def set_read_at(*args: object) -> None:
        raise AssertionError("an already-read notification must not be re-stamped")

    monkeypatch.setattr(notification_repository, "get_by_id", get_by_id)
    monkeypatch.setattr(notification_repository, "set_read_at", set_read_at)

    response = await notifications_service.mark_notification_read(fake_db_session(), 7)

    assert response.read_at == first_read_at


@pytest.mark.asyncio
async def test_mark_notification_read_raises_not_found_for_an_unknown_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The endpoint's 404 comes from this domain error."""

    async def get_by_id(db: object, notification_id: int) -> None:
        return None

    monkeypatch.setattr(notification_repository, "get_by_id", get_by_id)

    with pytest.raises(NotificationNotFoundError):
        await notifications_service.mark_notification_read(fake_db_session(), 404)
