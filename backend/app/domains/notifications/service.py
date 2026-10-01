"""Business service for the notifications domain.

Holds the notification rules (mark-read and mark-all-read keep the first
``read_at``) and the public cross-domain entry points ``create_notification``
and ``resolve_last_notified_at``. The transaction is owned by whichever
entry boundary called in - the HTTP ``get_db`` dependency for the
list/read/count/mark-read endpoints, or a producer's own transaction (the
telemetry ingestion worker, the telematics device-health monitor, the
support SOS intake request). This module never commits or rolls back on its
own.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.notifications.repository as notification_repository
from app.domains.notifications.exceptions import NotificationNotFoundError
from app.domains.notifications.models import NotificationModel
from app.domains.notifications.schemas import (
    NotificationListResponse,
    NotificationMarkAllReadResponse,
    NotificationResponse,
    NotificationUnreadCountResponse,
)
from app.domains.notifications.types import (
    NotificationListOrder,
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
    vehicle_id: UUID | None = None,
    notification_type: NotificationType | None = None,
    severity: NotificationSeverity | None = None,
    order: NotificationListOrder = NotificationListOrder.ASC,
) -> NotificationListResponse:
    """List notifications for a polling client or a notification centre.

    Args:
        db: Async session owned by the HTTP boundary.
        after_id: With ``order=ASC``, only return notifications with a
            larger ID than this cursor (``0`` returns from the beginning);
            ignored with ``order=DESC``.
        limit: Maximum number of records to return.
        unread_only: Whether to exclude notifications already marked read.
        vehicle_id: Only notifications about this vehicle, if given.
        notification_type: Only notifications of this type, if given.
        severity: Only notifications of this severity, if given.
        order: ``ASC`` (default, the polling contract): oldest first after
            the cursor. ``DESC``: the newest ``limit`` notifications, newest
            first.

    Returns:
        The notifications, plus ``latest_notification_id``: the highest ID
        returned, or ``after_id`` when nothing was returned.
    """
    if order is NotificationListOrder.DESC:
        notifications = await notification_repository.list_newest(
            db,
            limit=limit,
            unread_only=unread_only,
            vehicle_id=vehicle_id,
            notification_type=notification_type,
            severity=severity,
        )
    else:
        notifications = await notification_repository.list_after_id(
            db,
            after_id=after_id,
            limit=limit,
            unread_only=unread_only,
            vehicle_id=vehicle_id,
            notification_type=notification_type,
            severity=severity,
        )
    latest_notification_id = max(
        (notification.notification_id for notification in notifications),
        default=after_id,
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


async def get_notification(
    db: AsyncSession, notification_id: int
) -> NotificationResponse:
    """Get one notification by ID, read or unread.

    Args:
        db: Async session owned by the HTTP boundary.
        notification_id: Internal ID of the notification.

    Returns:
        The notification response.

    Raises:
        NotificationNotFoundError: If the notification does not exist.
    """
    notification_record = await notification_repository.get_by_id(db, notification_id)
    if notification_record is None:
        raise NotificationNotFoundError(f"Notification '{notification_id}' not found")
    return to_notification_response(notification_record)


async def count_unread_notifications(
    db: AsyncSession, vehicle_id: UUID | None
) -> NotificationUnreadCountResponse:
    """Count the notifications not yet marked read (a badge count).

    Args:
        db: Async session owned by the HTTP boundary.
        vehicle_id: Only count notifications about this vehicle, or every
            notification when ``None``.

    Returns:
        The unread count.
    """
    unread_count = await notification_repository.count_unread(db, vehicle_id)
    return NotificationUnreadCountResponse(unread_count=unread_count)


async def mark_all_notifications_read(
    db: AsyncSession, vehicle_id: UUID | None
) -> NotificationMarkAllReadResponse:
    """Mark every unread notification read, keeping each first read time.

    Rule:
        Same as ``mark_notification_read``: an already-read notification
        keeps its original ``read_at``; only unread ones are stamped, all
        with the same time.

    Args:
        db: Async session owned by the HTTP boundary.
        vehicle_id: Only notifications about this vehicle, or every
            notification when ``None``.

    Returns:
        How many notifications were marked read.

    Side Effects:
        One ``UPDATE`` of the unread rows; does not commit.
    """
    marked_count = await notification_repository.set_read_at_on_unread(
        db, read_at=utc_now(), vehicle_id=vehicle_id
    )
    return NotificationMarkAllReadResponse(marked_count=marked_count)
