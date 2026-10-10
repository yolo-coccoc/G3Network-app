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
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.notifications.models import (
    NotificationModel,
    NotificationRecipientModel,
)
from app.domains.notifications.types import NotificationSeverity, NotificationType
from app.libs.common.clock import utc_now


async def insert(
    db: AsyncSession,
    *,
    organization_id: UUID,
    notification_type: NotificationType,
    severity: NotificationSeverity,
    vehicle_id: UUID | None,
    subject_type: str | None,
    subject_id: UUID | None,
    title: str,
    body: str,
    payload: dict[str, object],
) -> NotificationModel:
    """Add a notification and flush to obtain its generated ID.

    No refresh is needed: the flush's ``INSERT ... RETURNING`` fills
    ``notification_id`` and ``created_at`` is a client-side default that the
    ORM writes back onto the object, so every column is loaded and reading one
    never triggers a lazy load (which an ``AsyncSession`` cannot do
    implicitly).

    Args:
        db: Async session owned by the entry boundary.
        organization_id: The organization the alert belongs to.
        notification_type: Kind of event that raised the notification.
        severity: Severity independent of type.
        vehicle_id: Vehicle the notification is about, nullable.
        subject_type: What the alert is about, nullable (with ``subject_id``).
        subject_id: ID of that object, nullable (with ``subject_type``).
        title: Short human-readable summary.
        body: Longer human-readable description.
        payload: Type-specific structured data.

    Returns:
        The notification that was just flushed, fully populated.

    Side Effects:
        Adds the row and flushes; does not commit.
    """
    notification_record = NotificationModel(
        organization_id=organization_id,
        notification_type=notification_type,
        severity=severity,
        vehicle_id=vehicle_id,
        subject_type=subject_type,
        subject_id=subject_id,
        title=title,
        body=body,
        payload=payload,
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
    organization_id: UUID | None,
    vehicle_id: UUID | None,
    notification_type: NotificationType | None,
    severity: NotificationSeverity | None,
    user_id: UUID | None,
    unread_only: bool,
) -> list[ColumnElement[bool]]:
    """Build the filter conditions shared by the list queries.

    Args:
        organization_id: Only notifications of this organization, if given.
        vehicle_id: Only notifications about this vehicle, if given.
        notification_type: Only notifications of this type, if given.
        severity: Only notifications of this severity, if given.
        user_id: Only notifications delivered to this person, if given.
        unread_only: With ``user_id``, only those the person has not read.

    Returns:
        Conditions to AND together (possibly none).
    """
    conditions: list[ColumnElement[bool]] = []
    if organization_id is not None:
        conditions.append(NotificationModel.organization_id == organization_id)
    if vehicle_id is not None:
        conditions.append(NotificationModel.vehicle_id == vehicle_id)
    if notification_type is not None:
        conditions.append(NotificationModel.notification_type == notification_type)
    if severity is not None:
        conditions.append(NotificationModel.severity == severity)
    if user_id is not None:
        recipient_conditions = [
            NotificationRecipientModel.notification_id
            == NotificationModel.notification_id,
            NotificationRecipientModel.user_id == user_id,
        ]
        if unread_only:
            recipient_conditions.append(NotificationRecipientModel.read_at.is_(None))
        conditions.append(select(1).where(*recipient_conditions).exists())
    return conditions


async def list_after_id(
    db: AsyncSession,
    *,
    after_id: int,
    limit: int,
    organization_id: UUID | None = None,
    vehicle_id: UUID | None = None,
    notification_type: NotificationType | None = None,
    severity: NotificationSeverity | None = None,
    user_id: UUID | None = None,
    unread_only: bool = False,
) -> list[NotificationModel]:
    """List notifications newer than a cursor, oldest first.

    Args:
        db: Current async session.
        after_id: Only return notifications with a larger ID than this
            cursor; ``0`` returns from the beginning.
        limit: Maximum number of records to return.
        organization_id: Only notifications of this organization, if given.
        vehicle_id: Only notifications about this vehicle, if given.
        notification_type: Only notifications of this type, if given.
        severity: Only notifications of this severity, if given.
        user_id: Only notifications delivered to this person, if given.
        unread_only: With ``user_id``, only those the person has not read.

    Returns:
        Notifications ordered by ``notification_id`` ascending, so the
        caller's next cursor is the last item's ID.
    """
    conditions = [
        NotificationModel.notification_id > after_id,
        *_list_conditions(
            organization_id=organization_id,
            vehicle_id=vehicle_id,
            notification_type=notification_type,
            severity=severity,
            user_id=user_id,
            unread_only=unread_only,
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
    organization_id: UUID | None = None,
    vehicle_id: UUID | None = None,
    notification_type: NotificationType | None = None,
    severity: NotificationSeverity | None = None,
    user_id: UUID | None = None,
    unread_only: bool = False,
) -> list[NotificationModel]:
    """List the newest notifications, newest first.

    Args:
        db: Current async session.
        limit: Maximum number of records to return.
        organization_id: Only notifications of this organization, if given.
        vehicle_id: Only notifications about this vehicle, if given.
        notification_type: Only notifications of this type, if given.
        severity: Only notifications of this severity, if given.
        user_id: Only notifications delivered to this person, if given.
        unread_only: With ``user_id``, only those the person has not read.

    Returns:
        Notifications ordered by ``notification_id`` descending (the
        domain's monotonic order, so ties on ``created_at`` cannot reorder).
    """
    conditions = _list_conditions(
        organization_id=organization_id,
        vehicle_id=vehicle_id,
        notification_type=notification_type,
        severity=severity,
        user_id=user_id,
        unread_only=unread_only,
    )
    query_result = await db.execute(
        select(NotificationModel)
        .where(*conditions)
        .order_by(NotificationModel.notification_id.desc())
        .limit(limit)
    )
    return list(query_result.scalars().all())


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


async def insert_recipients(
    db: AsyncSession, notification_id: int, user_ids: list[UUID]
) -> int:
    """Add the people who receive an alert, ignoring those already added.

    Args:
        db: Current async session.
        notification_id: Internal ID of the alert.
        user_ids: The recipients.

    Returns:
        How many recipient rows were added.

    Side Effects:
        One ``INSERT ... ON CONFLICT DO NOTHING``; does not commit.
    """
    if not user_ids:
        return 0
    created_at = utc_now()
    insert_result = await db.execute(
        pg_insert(NotificationRecipientModel)
        .values(
            [
                {
                    "notification_id": notification_id,
                    "user_id": user_id,
                    "created_at": created_at,
                }
                for user_id in user_ids
            ]
        )
        .on_conflict_do_nothing(index_elements=["notification_id", "user_id"])
    )
    return cast(CursorResult[Any], insert_result).rowcount


async def find_recipient(
    db: AsyncSession, notification_id: int, user_id: UUID
) -> NotificationRecipientModel | None:
    """Find one person's inbox row for an alert.

    Args:
        db: Current async session.
        notification_id: Internal ID of the alert.
        user_id: The person.

    Returns:
        The recipient row, or ``None`` if the alert never reached them.
    """
    query_result = await db.execute(
        select(NotificationRecipientModel).where(
            NotificationRecipientModel.notification_id == notification_id,
            NotificationRecipientModel.user_id == user_id,
        )
    )
    return query_result.scalar_one_or_none()


async def set_recipient_read(
    db: AsyncSession, recipient_record: NotificationRecipientModel, read_at: datetime
) -> None:
    """Store when a person opened an alert (it also counts as seen).

    Unconditional: whether an already-read alert may be stamped again is the
    service's decision, not this function's.

    Args:
        db: Current async session.
        recipient_record: The inbox row, loaded in ``db``.
        read_at: Timezone-aware UTC time to store.

    Side Effects:
        Sets ``read_at`` (and ``seen_at`` if still empty) and flushes; does not
        commit.
    """
    recipient_record.read_at = read_at
    if recipient_record.seen_at is None:
        recipient_record.seen_at = read_at
    await db.flush()


async def count_unread(db: AsyncSession, user_id: UUID) -> int:
    """Count the alerts a person has not read.

    Args:
        db: Current async session.
        user_id: The person.

    Returns:
        Number of inbox rows with ``read_at`` empty.
    """
    query_result = await db.execute(
        select(func.count(NotificationRecipientModel.notification_recipient_id)).where(
            NotificationRecipientModel.user_id == user_id,
            NotificationRecipientModel.read_at.is_(None),
        )
    )
    return query_result.scalar() or 0


async def set_all_recipient_read(
    db: AsyncSession, *, user_id: UUID, read_at: datetime
) -> int:
    """Mark every unread alert of a person seen and read, in one ``UPDATE``.

    Only rows whose ``read_at`` is still ``NULL`` are touched, which keeps the
    first read time of the others (idempotent, NT-04). One statement is simply
    how "mark all" is expressed in SQL, not a batched variant of a per-row loop.

    Args:
        db: Current async session.
        user_id: The person.
        read_at: Timezone-aware UTC time to store.

    Returns:
        Number of rows that were marked read.

    Side Effects:
        Executes the ``UPDATE``; does not commit.
    """
    update_result = await db.execute(
        update(NotificationRecipientModel)
        .where(
            NotificationRecipientModel.user_id == user_id,
            NotificationRecipientModel.read_at.is_(None),
        )
        .values(
            read_at=read_at,
            seen_at=func.coalesce(NotificationRecipientModel.seen_at, read_at),
        )
    )
    # An UPDATE's result is a CursorResult (the session's execute() is only
    # typed as Result); its rowcount is the number of rows matched.
    return cast(CursorResult[Any], update_result).rowcount
