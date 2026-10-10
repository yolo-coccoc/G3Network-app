"""Smoke tests for the notifications service: mapping, per-person read state, lists (F-A2, NT-10)."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

import app.domains.notifications.repository as notification_repository
import app.domains.notifications.service as notifications_service
from app.domains.notifications.exceptions import (
    NotificationFilterError,
    NotificationNotFoundError,
    NotificationRecipientNotFoundError,
)
from app.domains.notifications.models import (
    NotificationModel,
    NotificationRecipientModel,
)
from app.domains.notifications.types import (
    NotificationListOrder,
    NotificationSeverity,
    NotificationType,
)
from tests.builders import fake_db_session
from tests.principals import build_internal_principal, build_principal

ORGANIZATION_ID = uuid4()


def _notification(notification_id: int = 42) -> NotificationModel:
    """Build a minimal notification ORM object."""
    return NotificationModel(
        notification_id=notification_id,
        organization_id=ORGANIZATION_ID,
        notification_type=NotificationType.BATTERY_ALERT,
        severity=NotificationSeverity.WARNING,
        vehicle_id=uuid4(),
        subject_type=None,
        subject_id=None,
        title="Battery at 18%",
        body="Vehicle battery dropped to 18.0%, crossing the 20% threshold.",
        payload={"threshold_percent": 20.0, "soc": 18.0},
        created_at=datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc),
    )


def _recipient(*, read_at: datetime | None) -> NotificationRecipientModel:
    """Build a minimal inbox row."""
    return NotificationRecipientModel(
        notification_recipient_id=1,
        notification_id=7,
        user_id=uuid4(),
        seen_at=read_at,
        read_at=read_at,
        created_at=datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc),
    )


def test_notification_service_builds_response_from_model() -> None:
    """to_notification_response() maps the ORM model, organization and subject included."""
    notification = _notification()

    response = notifications_service.to_notification_response(notification)

    assert response.notification_id == 42
    assert response.organization_id == ORGANIZATION_ID
    assert response.notification_type is NotificationType.BATTERY_ALERT
    assert response.payload["soc"] == 18.0
    assert (response.subject_type, response.subject_id) == (None, None)


@pytest.mark.asyncio
async def test_create_notification_needs_both_subject_fields_or_neither() -> None:
    """A subject type without an ID (or the reverse) is refused before any write."""
    with pytest.raises(NotificationFilterError):
        await notifications_service.create_notification(
            fake_db_session(),
            organization_id=ORGANIZATION_ID,
            notification_type=NotificationType.SOS_ALERT,
            severity=NotificationSeverity.CRITICAL,
            vehicle_id=None,
            title="SOS",
            body="SOS",
            payload={},
            subject_type="SUPPORT_CASE",
        )


@pytest.mark.asyncio
async def test_mark_notification_read_stamps_an_unread_inbox_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unread inbox row gets read_at = now, written through the repository."""
    recipient = _recipient(read_at=None)
    read_at = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)

    async def find_recipient(
        db: object, notification_id: int, user_id: object
    ) -> NotificationRecipientModel:
        return recipient

    async def set_recipient_read(
        db: object, record: NotificationRecipientModel, new_read_at: datetime
    ) -> None:
        record.read_at = record.seen_at = new_read_at

    monkeypatch.setattr(notification_repository, "find_recipient", find_recipient)
    monkeypatch.setattr(
        notification_repository, "set_recipient_read", set_recipient_read
    )
    monkeypatch.setattr(notifications_service, "utc_now", lambda: read_at)

    response = await notifications_service.mark_notification_read(
        fake_db_session(), 7, principal=build_principal(user_id=recipient.user_id)
    )

    assert (response.seen_at, response.read_at) == (read_at, read_at)


@pytest.mark.asyncio
async def test_mark_notification_read_keeps_the_first_read_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Marking an already-read alert again is idempotent: no write (NT-04)."""
    first_read_at = datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc)
    recipient = _recipient(read_at=first_read_at)

    async def find_recipient(
        db: object, notification_id: int, user_id: object
    ) -> NotificationRecipientModel:
        return recipient

    async def set_recipient_read(*args: object) -> None:
        raise AssertionError("an already-read alert must not be re-stamped")

    monkeypatch.setattr(notification_repository, "find_recipient", find_recipient)
    monkeypatch.setattr(
        notification_repository, "set_recipient_read", set_recipient_read
    )

    response = await notifications_service.mark_notification_read(
        fake_db_session(), 7, principal=build_principal(user_id=recipient.user_id)
    )

    assert response.read_at == first_read_at


@pytest.mark.asyncio
async def test_mark_notification_read_raises_when_the_alert_never_reached_the_person(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The endpoint's 404 comes from this domain error."""

    async def find_recipient(db: object, notification_id: int, user_id: object) -> None:
        return None

    monkeypatch.setattr(notification_repository, "find_recipient", find_recipient)

    with pytest.raises(NotificationRecipientNotFoundError):
        await notifications_service.mark_notification_read(
            fake_db_session(), 404, principal=build_principal()
        )


@pytest.mark.asyncio
async def test_list_notifications_desc_ignores_the_cursor_and_passes_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """order=desc lists newest first without the cursor; filters pass through."""
    vehicle_id, user_id = uuid4(), uuid4()
    newest, older = _notification(9), _notification(8)
    seen: dict[str, object] = {}
    stamped: dict[str, object] = {}

    async def list_newest(
        db: object, **kwargs: object
    ) -> list[notification_repository.NotificationRow]:
        seen.update(kwargs)
        return [(newest, None, None), (older, None, None)]

    async def set_recipients_seen(db: object, **kwargs: object) -> int:
        stamped.update(kwargs)
        return 2

    async def list_after_id(*args: object, **kwargs: object) -> None:
        raise AssertionError("order=desc must not use the poll cursor query")

    monkeypatch.setattr(notification_repository, "list_newest", list_newest)
    monkeypatch.setattr(notification_repository, "list_after_id", list_after_id)
    monkeypatch.setattr(
        notification_repository, "set_recipients_seen", set_recipients_seen
    )

    response = await notifications_service.list_notifications(
        fake_db_session(),
        principal=build_principal(user_id=user_id),
        after_id=100,
        limit=2,
        mine_only=True,
        unread_only=True,
        vehicle_id=vehicle_id,
        notification_type=NotificationType.SOS_ALERT,
        severity=NotificationSeverity.CRITICAL,
        order=NotificationListOrder.DESC,
    )

    assert [item.notification_id for item in response.notifications] == [9, 8]
    assert response.latest_notification_id == 9
    # Opening the notification centre marks the shown page seen (NT-10).
    assert stamped["user_id"] == user_id
    assert stamped["notification_ids"] == [9, 8]
    assert all(item.seen_at is not None for item in response.notifications)
    assert all(item.read_at is None for item in response.notifications)
    assert seen == {
        "limit": 2,
        "before_id": None,
        "organization_id": None,
        "user_id": user_id,
        "unread_only": True,
        "vehicle_id": vehicle_id,
        "notification_type": NotificationType.SOS_ALERT,
        "severity": NotificationSeverity.CRITICAL,
    }


@pytest.mark.asyncio
async def test_list_notifications_asc_keeps_the_cursor_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default order still polls after the cursor and echoes it when empty."""
    seen: dict[str, object] = {}

    async def list_after_id(
        db: object, **kwargs: object
    ) -> list[notification_repository.NotificationRow]:
        seen.update(kwargs)
        return []

    monkeypatch.setattr(notification_repository, "list_after_id", list_after_id)

    response = await notifications_service.list_notifications(
        fake_db_session(), after_id=42, limit=10, principal=build_internal_principal()
    )

    assert seen["after_id"] == 42
    assert response.count == 0
    assert response.latest_notification_id == 42


@pytest.mark.asyncio
async def test_list_notifications_unread_only_needs_a_person() -> None:
    """Read state is per person, so unread_only without a user_id is refused."""
    with pytest.raises(NotificationFilterError):
        await notifications_service.list_notifications(
            fake_db_session(),
            after_id=0,
            limit=10,
            unread_only=True,
            principal=build_internal_principal(),
        )


@pytest.mark.asyncio
async def test_get_notification_raises_not_found_for_an_unknown_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /notifications/{id} maps a missing row to this domain error."""

    async def get_by_id(db: object, notification_id: int) -> None:
        return None

    monkeypatch.setattr(notification_repository, "get_by_id", get_by_id)

    with pytest.raises(NotificationNotFoundError):
        await notifications_service.get_notification(
            fake_db_session(), 404, principal=build_internal_principal()
        )


@pytest.mark.asyncio
async def test_mark_all_notifications_read_returns_the_marked_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mark-all-read stamps one time on the person's unread rows and reports how many."""
    read_at = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
    user_id = uuid4()
    seen: dict[str, object] = {}

    async def set_all_recipient_read(db: object, **kwargs: object) -> int:
        seen.update(kwargs)
        return 3

    monkeypatch.setattr(
        notification_repository, "set_all_recipient_read", set_all_recipient_read
    )
    monkeypatch.setattr(notifications_service, "utc_now", lambda: read_at)

    response = await notifications_service.mark_all_notifications_read(
        fake_db_session(), principal=build_principal(user_id=user_id)
    )

    assert response.marked_count == 3
    assert seen == {"user_id": user_id, "read_at": read_at}
