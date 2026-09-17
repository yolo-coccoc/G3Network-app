"""Business service for the notifications domain.

The transaction is owned by whichever entry boundary called in - the HTTP
``get_db`` dependency for the read/mark-read endpoints, or the telemetry
ingestion worker's transaction when ``create_notification`` is called as a
cross-domain producer. This module never commits/rollbacks on its own.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.notifications import repository
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


def to_notification_response(notification: NotificationModel) -> NotificationResponse:
    """Build a notification response from the ORM model.

    Args:
        notification: Notification ORM object queried or created by the
            repository.

    Returns:
        Response schema corresponding to the notification.
    """
    return NotificationResponse.model_validate(notification)


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
    notification = await repository.create_notification(
        db,
        notification_type=notification_type,
        severity=severity,
        vehicle_id=vehicle_id,
        title=title,
        body=body,
        payload=payload,
    )
    return NotificationReference(notification_id=notification.notification_id)


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
    notifications = await repository.list_notifications(
        db, after_id=after_id, limit=limit, unread_only=unread_only
    )
    latest_notification_id = (
        notifications[-1].notification_id if notifications else after_id
    )
    return NotificationListResponse(
        notifications=[to_notification_response(n) for n in notifications],
        count=len(notifications),
        latest_notification_id=latest_notification_id,
    )


async def mark_notification_read(
    db: AsyncSession, notification_id: int
) -> NotificationResponse:
    """Mark a notification read.

    Args:
        db: Async session owned by the HTTP boundary.
        notification_id: Internal ID of the notification to mark read.

    Returns:
        The updated notification response.

    Raises:
        NotificationNotFoundError: If the notification does not exist.
    """
    notification = await repository.mark_notification_read(db, notification_id)
    if notification is None:
        raise NotificationNotFoundError(f"Notification '{notification_id}' not found")
    return to_notification_response(notification)
