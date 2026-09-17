"""Data access repository for the notifications domain.

The repository only queries and flushes data; it does not commit or roll
back the transaction. The entry boundary owns the transaction.
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.notifications.models import NotificationModel
from app.domains.notifications.types import NotificationSeverity, NotificationType


def utc_now() -> datetime:
    """Get the UTC timestamp used for ``read_at``.

    Returns:
        The current time with UTC timezone.
    """
    return datetime.now(timezone.utc)


async def create_notification(
    db: AsyncSession,
    *,
    notification_type: NotificationType,
    severity: NotificationSeverity,
    vehicle_id: UUID | None,
    title: str,
    body: str,
    payload: dict[str, object],
) -> NotificationModel:
    """Create a notification and flush to obtain its generated ID.

    Args:
        db: Async session owned by the entry boundary.
        notification_type: Kind of event that raised the notification.
        severity: Severity independent of type.
        vehicle_id: Vehicle the notification is about, nullable.
        title: Short human-readable summary.
        body: Longer human-readable description.
        payload: Type-specific structured data.

    Returns:
        The notification that was just persisted.
    """
    notification = NotificationModel(
        notification_type=notification_type,
        severity=severity,
        vehicle_id=vehicle_id,
        title=title,
        body=body,
        payload=payload,
    )
    db.add(notification)
    await db.flush()
    await db.refresh(notification)
    return notification


async def get_notification_by_id(
    db: AsyncSession, notification_id: int
) -> NotificationModel | None:
    """Find a notification by internal ID.

    Args:
        db: Current async session.
        notification_id: Internal ID of the notification.

    Returns:
        The matching notification, or ``None``.
    """
    result = await db.execute(
        select(NotificationModel).where(
            NotificationModel.notification_id == notification_id
        )
    )
    return result.scalar_one_or_none()


async def list_notifications(
    db: AsyncSession,
    *,
    after_id: int,
    limit: int,
    unread_only: bool,
) -> list[NotificationModel]:
    """List notifications newer than a cursor, oldest first.

    Args:
        db: Current async session.
        after_id: Only return notifications with a larger ID than this
            cursor; ``0`` returns from the beginning.
        limit: Maximum number of records to return.
        unread_only: Whether to exclude notifications already marked read.

    Returns:
        Notifications ordered by ``notification_id`` ascending, so the
        caller's next cursor is the last item's ID.
    """
    conditions: list[ColumnElement[bool]] = [
        NotificationModel.notification_id > after_id
    ]
    if unread_only:
        conditions.append(NotificationModel.read_at.is_(None))
    result = await db.execute(
        select(NotificationModel)
        .where(*conditions)
        .order_by(NotificationModel.notification_id.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def mark_notification_read(
    db: AsyncSession, notification_id: int
) -> NotificationModel | None:
    """Mark a notification read if it exists and isn't already.

    Args:
        db: Current async session.
        notification_id: Internal ID of the notification to mark read.

    Returns:
        The updated notification, or ``None`` if it does not exist.

    Side Effects:
        Sets ``read_at`` and flushes; does not commit. Idempotent - marking
        an already-read notification read again keeps the original
        ``read_at`` instead of overwriting it.
    """
    notification = await get_notification_by_id(db, notification_id)
    if notification is None:
        return None
    if notification.read_at is None:
        notification.read_at = utc_now()
        await db.flush()
        await db.refresh(notification)
    return notification
