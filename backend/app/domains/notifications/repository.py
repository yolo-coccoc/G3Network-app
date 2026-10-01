"""Data access repository for the notifications domain.

The repository only queries, adds and flushes rows; it holds no business
policy (e.g. whether an already-read notification keeps its ``read_at`` is
decided by the service) and never commits or rolls back - the entry
boundary owns the transaction.
"""

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.notifications.models import NotificationModel
from app.domains.notifications.types import NotificationSeverity, NotificationType


async def insert(
    db: AsyncSession,
    *,
    notification_type: NotificationType,
    severity: NotificationSeverity,
    vehicle_id: UUID | None,
    title: str,
    body: str,
    payload: dict[str, object],
) -> NotificationModel:
    """Add a notification and flush to obtain its generated ID.

    No refresh is needed: the flush's ``INSERT ... RETURNING`` fills
    ``notification_id``, ``created_at`` is a client-side default that the
    ORM writes back onto the object, and ``read_at`` is set explicitly, so
    every column is loaded and reading one never triggers a lazy load
    (which an ``AsyncSession`` cannot do implicitly).

    Args:
        db: Async session owned by the entry boundary.
        notification_type: Kind of event that raised the notification.
        severity: Severity independent of type.
        vehicle_id: Vehicle the notification is about, nullable.
        title: Short human-readable summary.
        body: Longer human-readable description.
        payload: Type-specific structured data.

    Returns:
        The notification that was just flushed, fully populated.

    Side Effects:
        Adds the row and flushes; does not commit.
    """
    notification_record = NotificationModel(
        notification_type=notification_type,
        severity=severity,
        vehicle_id=vehicle_id,
        title=title,
        body=body,
        payload=payload,
        # A new notification is unread. Set explicitly rather than left unset:
        # an attribute never assigned before the INSERT stays unloaded on the
        # object afterwards.
        read_at=None,
    )
    db.add(notification_record)
    await db.flush()
    return notification_record


async def get_by_id(db: AsyncSession, notification_id: int) -> NotificationModel | None:
    """Find a notification by internal ID.

    Args:
        db: Current async session.
        notification_id: Internal ID of the notification.

    Returns:
        The matching notification, or ``None``.
    """
    query_result = await db.execute(
        select(NotificationModel).where(
            NotificationModel.notification_id == notification_id
        )
    )
    return query_result.scalar_one_or_none()


def _list_conditions(
    *,
    unread_only: bool,
    vehicle_id: UUID | None,
    notification_type: NotificationType | None,
    severity: NotificationSeverity | None,
) -> list[ColumnElement[bool]]:
    """Build the filter conditions shared by the list queries.

    Args:
        unread_only: Whether to exclude notifications already marked read.
        vehicle_id: Only notifications about this vehicle, if given.
        notification_type: Only notifications of this type, if given.
        severity: Only notifications of this severity, if given.

    Returns:
        Conditions to AND together (possibly none).
    """
    conditions: list[ColumnElement[bool]] = []
    if unread_only:
        conditions.append(NotificationModel.read_at.is_(None))
    if vehicle_id is not None:
        conditions.append(NotificationModel.vehicle_id == vehicle_id)
    if notification_type is not None:
        conditions.append(NotificationModel.notification_type == notification_type)
    if severity is not None:
        conditions.append(NotificationModel.severity == severity)
    return conditions


async def list_after_id(
    db: AsyncSession,
    *,
    after_id: int,
    limit: int,
    unread_only: bool,
    vehicle_id: UUID | None = None,
    notification_type: NotificationType | None = None,
    severity: NotificationSeverity | None = None,
) -> list[NotificationModel]:
    """List notifications newer than a cursor, oldest first.

    Args:
        db: Current async session.
        after_id: Only return notifications with a larger ID than this
            cursor; ``0`` returns from the beginning.
        limit: Maximum number of records to return.
        unread_only: Whether to exclude notifications already marked read.
        vehicle_id: Only notifications about this vehicle, if given.
        notification_type: Only notifications of this type, if given.
        severity: Only notifications of this severity, if given.

    Returns:
        Notifications ordered by ``notification_id`` ascending, so the
        caller's next cursor is the last item's ID.
    """
    conditions = [
        NotificationModel.notification_id > after_id,
        *_list_conditions(
            unread_only=unread_only,
            vehicle_id=vehicle_id,
            notification_type=notification_type,
            severity=severity,
        ),
    ]
    query_result = await db.execute(
        select(NotificationModel)
        .where(*conditions)
        .order_by(NotificationModel.notification_id.asc())
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def list_newest(
    db: AsyncSession,
    *,
    limit: int,
    unread_only: bool,
    vehicle_id: UUID | None = None,
    notification_type: NotificationType | None = None,
    severity: NotificationSeverity | None = None,
) -> list[NotificationModel]:
    """List the newest notifications, newest first.

    Args:
        db: Current async session.
        limit: Maximum number of records to return.
        unread_only: Whether to exclude notifications already marked read.
        vehicle_id: Only notifications about this vehicle, if given.
        notification_type: Only notifications of this type, if given.
        severity: Only notifications of this severity, if given.

    Returns:
        Notifications ordered by ``notification_id`` descending (the
        domain's monotonic order, so ties on ``created_at`` cannot reorder).
    """
    conditions = _list_conditions(
        unread_only=unread_only,
        vehicle_id=vehicle_id,
        notification_type=notification_type,
        severity=severity,
    )
    query_result = await db.execute(
        select(NotificationModel)
        .where(*conditions)
        .order_by(NotificationModel.notification_id.desc())
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count_unread(db: AsyncSession, vehicle_id: UUID | None) -> int:
    """Count notifications not yet marked read.

    Args:
        db: Current async session.
        vehicle_id: Only count notifications about this vehicle, or every
            notification when ``None``.

    Returns:
        Number of unread notifications.
    """
    conditions = _list_conditions(
        unread_only=True, vehicle_id=vehicle_id, notification_type=None, severity=None
    )
    query_result = await db.execute(
        select(func.count(NotificationModel.notification_id)).where(*conditions)
    )
    return query_result.scalar() or 0


async def set_read_at_on_unread(
    db: AsyncSession, *, read_at: datetime, vehicle_id: UUID | None
) -> int:
    """Stamp ``read_at`` on every unread notification, in one ``UPDATE``.

    Only rows whose ``read_at`` is still ``NULL`` are touched, which keeps
    the service's "the first read time is kept" rule for rows already read.
    One statement is simply how "mark all" is expressed in SQL, not a
    batched variant of a per-row loop: loading every unread row just to
    stamp each one would be an unbounded read for the same result.

    Args:
        db: Current async session.
        read_at: Timezone-aware UTC time to store.
        vehicle_id: Only notifications about this vehicle, or every
            notification when ``None``.

    Returns:
        Number of notifications that were marked read.

    Side Effects:
        Executes the ``UPDATE``; does not commit. Objects of the affected
        rows already loaded in ``db`` are updated too (SQLAlchemy's default
        session synchronization).
    """
    conditions = _list_conditions(
        unread_only=True, vehicle_id=vehicle_id, notification_type=None, severity=None
    )
    update_result = await db.execute(
        update(NotificationModel).where(*conditions).values(read_at=read_at)
    )
    # An UPDATE's result is a CursorResult (the session's execute() is only
    # typed as Result); its rowcount is the number of rows matched.
    return cast(CursorResult[Any], update_result).rowcount


async def find_latest_by_vehicle_and_type(
    db: AsyncSession, vehicle_id: UUID, notification_type: NotificationType
) -> NotificationModel | None:
    """Find the most recent notification of a type for a vehicle (F-J1/F-J3).

    Composite business-identity lookup - both parts positional, per this
    backend's naming convention (the function name already names both).

    Args:
        db: Current async session.
        vehicle_id: Vehicle to look up.
        notification_type: Notification type to filter by.

    Returns:
        The matching notification with the highest ``notification_id``
        (the domain's own monotonic ordering, not ``created_at`` - avoids a
        tie if two notifications land within the same tick), or ``None`` if
        none exist.
    """
    query_result = await db.execute(
        select(NotificationModel)
        .where(
            NotificationModel.vehicle_id == vehicle_id,
            NotificationModel.notification_type == notification_type,
        )
        .order_by(NotificationModel.notification_id.desc())
        .limit(1)
    )
    return query_result.scalar_one_or_none()


async def set_read_at(
    db: AsyncSession, notification_record: NotificationModel, read_at: datetime
) -> None:
    """Store the time a notification was read.

    Unconditional: whether an already-read notification may be stamped
    again is the service's decision, not this function's.

    Args:
        db: Current async session.
        notification_record: The notification to update, loaded in ``db``.
        read_at: Timezone-aware UTC time to store.

    Side Effects:
        Sets ``read_at`` on the object and flushes the ``UPDATE``; does not
        commit. The object keeps the value, so no refresh is needed.
    """
    notification_record.read_at = read_at
    await db.flush()
