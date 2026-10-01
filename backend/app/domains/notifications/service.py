"""Business service for the notifications domain.

Holds the notification rules (mark-read keeps the first ``read_at``) and the
public cross-domain entry points ``create_notification`` and
``resolve_last_notified_at``. The transaction is owned by whichever entry
boundary called in - the HTTP ``get_db`` dependency for the poll/mark-read
endpoints, or a producer's own transaction (the telemetry ingestion worker,
the telematics device-health monitor). This module never commits or rolls
back on its own.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.notifications.repository as notification_repository
from app.domains.notifications.exceptions import NotificationNotFoundError
from app.domains.notifications.models import NotificationModel
from app.domains.notifications.schemas import (
    NotificationListResponse,
    NotificationResponse,
)
from app.domains.notifications.types import (
    NotificationReference,
    NotificationSeverity,
    NotificationType,
)
from app.libs.common.clock import utc_now


def to_notification_response(
    notification_record: NotificationModel,
) -> NotificationResponse:
    """Build a notification response from the ORM model.

    Args:
        notification_record: Notification ORM object queried or created by
            the repository.

    Returns:
        Response schema corresponding to the notification.
    """
    return NotificationResponse.model_validate(notification_record)


async def create_notification(
    db: AsyncSession,
    *,
    notification_type: NotificationType,
    severity: NotificationSeverity,
    vehicle_id: UUID | None,
    title: str,
    body: str,
    payload: dict[str, object],
) -> NotificationReference:
    """Create a notification. Public cross-domain entry point for producers.

    Args:
        db: Async session owned by the caller's entry boundary (e.g. the
            telemetry ingestion worker's transaction).
        notification_type: Kind of event that raised the notification.
        severity: Severity independent of type.
        vehicle_id: Vehicle the notification is about, nullable.
        title: Short human-readable summary.
        body: Longer human-readable description.
        payload: Type-specific structured data.

    Returns:
        A minimal reference DTO - never the ORM model - so a calling domain
        never depends on this domain's persistence details.
    """
    notification_record = await notification_repository.insert(
        db,
        notification_type=notification_type,
        severity=severity,
        vehicle_id=vehicle_id,
        title=title,
        body=body,
        payload=payload,
    )
    return NotificationReference(notification_id=notification_record.notification_id)


async def resolve_last_notified_at(
    db: AsyncSession, *, vehicle_id: UUID, notification_type: NotificationType
) -> datetime | None:
    """Get when a vehicle was last notified of a given type. Public entry point for F-J1/F-J3.

    Args:
        db: Async session owned by the caller's entry boundary (the
            telematics device-health monitor's transaction).
        vehicle_id: Vehicle to look up.
        notification_type: Notification type to filter by.

    Returns:
        The `created_at` of the most recent matching notification, or
        `None` if none exist. A primitive return type, not the ORM model -
        the correct shape for a cross-domain boundary.
    """
    notification_record = await notification_repository.find_latest_by_vehicle_and_type(
        db, vehicle_id, notification_type
    )
    return notification_record.created_at if notification_record is not None else None


async def list_notifications(
    db: AsyncSession,
    *,
    after_id: int,
    limit: int,
    unread_only: bool,
) -> NotificationListResponse:
    """List notifications newer than a cursor, for a polling client.

    Args:
        db: Async session owned by the HTTP boundary.
        after_id: Only return notifications with a larger ID than this
            cursor; ``0`` returns from the beginning.
        limit: Maximum number of records to return.
        unread_only: Whether to exclude notifications already marked read.

    Returns:
        Notifications newer than ``after_id``, plus the cursor to pass on
        the next poll.
    """
    notifications = await notification_repository.list_after_id(
        db, after_id=after_id, limit=limit, unread_only=unread_only
    )
    latest_notification_id = (
        notifications[-1].notification_id if notifications else after_id
    )
    return NotificationListResponse(
        notifications=[
            to_notification_response(notification) for notification in notifications
        ],
        count=len(notifications),
        latest_notification_id=latest_notification_id,
    )


async def mark_notification_read(
    db: AsyncSession, notification_id: int
) -> NotificationResponse:
    """Mark a notification read, keeping the first read time.

    Rule:
        Idempotent - an already-read notification keeps its original
        ``read_at`` instead of being stamped again, so the value always
        means "first acknowledged at".

    Args:
        db: Async session owned by the HTTP boundary.
        notification_id: Internal ID of the notification to mark read.

    Returns:
        The notification response, with ``read_at`` set.

    Raises:
        NotificationNotFoundError: If the notification does not exist.

    Side Effects:
        Sets ``read_at`` and flushes when the notification was unread; does
        not commit.
    """
    notification_record = await notification_repository.get_by_id(db, notification_id)
    if notification_record is None:
        raise NotificationNotFoundError(f"Notification '{notification_id}' not found")
    if notification_record.read_at is None:
        await notification_repository.set_read_at(db, notification_record, utc_now())
    return to_notification_response(notification_record)
