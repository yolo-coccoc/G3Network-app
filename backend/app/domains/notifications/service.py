"""Business service for the notifications domain.

Holds the notification rules (a person's mark-read and mark-all-read keep the
first read time, NT-04) and the public cross-domain entry points
``create_notification``, ``add_notification_recipients`` and
``resolve_last_notified_at``. Who should receive which alert (roles and data
scope, NTF-06) and the push / e-mail delivery come with the notifications work
package; until then callers add recipients explicitly. The transaction is owned
by whichever entry boundary called in - the HTTP ``get_db`` dependency for the
list/read/count/mark-read endpoints, or a producer's own transaction (the
telemetry ingestion worker, the telematics device-health monitor, the
support SOS intake request). This module never commits or rolls back on its
own.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.notifications.repository as notification_repository
from app.domains.notifications.exceptions import (
    NotificationFilterError,
    NotificationNotFoundError,
    NotificationRecipientNotFoundError,
)
from app.domains.notifications.models import NotificationModel
from app.domains.notifications.schemas import (
    NotificationListResponse,
    NotificationMarkAllReadResponse,
    NotificationReadResponse,
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
    organization_id: UUID,
    notification_type: NotificationType,
    severity: NotificationSeverity,
    vehicle_id: UUID | None,
    title: str,
    body: str,
    payload: dict[str, object],
    subject_type: str | None = None,
    subject_id: UUID | None = None,
) -> NotificationReference:
    """Create a notification. Public cross-domain entry point for producers.

    Args:
        db: Async session owned by the caller's entry boundary (e.g. the
            telemetry ingestion worker's transaction).
        organization_id: The organization the alert belongs to: the truck's
            owner at that moment (DM-24 case C).
        notification_type: Kind of event that raised the notification.
        severity: Severity independent of type.
        vehicle_id: Vehicle the notification is about, nullable.
        title: Short human-readable summary.
        body: Longer human-readable description.
        payload: Type-specific structured data.
        subject_type: What the alert is about (which screen the app opens),
            if anything; given together with ``subject_id``.
        subject_id: ID of that object; given together with ``subject_type``.

    Returns:
        A minimal reference DTO - never the ORM model - so a calling domain
        never depends on this domain's persistence details.

    Raises:
        NotificationFilterError: If only one of ``subject_type`` and
            ``subject_id`` is given.
    """
    if (subject_type is None) != (subject_id is None):
        raise NotificationFilterError(
            "subject_type and subject_id must be given together"
        )
    notification_record = await notification_repository.insert(
        db,
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
    return NotificationReference(notification_id=notification_record.notification_id)


async def add_notification_recipients(
    db: AsyncSession, notification_id: int, user_ids: Sequence[UUID]
) -> int:
    """Deliver an alert to people's inboxes. Public entry point for producers.

    Idempotent: a person who already has the alert is skipped.

    Args:
        db: Async session owned by the caller's entry boundary.
        notification_id: Internal ID of the alert.
        user_ids: The people who receive it.

    Returns:
        How many people were newly added.
    """
    return await notification_repository.insert_recipients(
        db, notification_id, list(user_ids)
    )


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
    organization_id: UUID | None = None,
    vehicle_id: UUID | None = None,
    notification_type: NotificationType | None = None,
    severity: NotificationSeverity | None = None,
    user_id: UUID | None = None,
    unread_only: bool = False,
    order: NotificationListOrder = NotificationListOrder.ASC,
) -> NotificationListResponse:
    """List notifications for a polling client or a notification centre.

    Args:
        db: Async session owned by the HTTP boundary.
        after_id: With ``order=ASC``, only return notifications with a
            larger ID than this cursor (``0`` returns from the beginning);
            ignored with ``order=DESC``.
        limit: Maximum number of records to return.
        organization_id: Only notifications of this organization, if given.
        vehicle_id: Only notifications about this vehicle, if given.
        notification_type: Only notifications of this type, if given.
        severity: Only notifications of this severity, if given.
        user_id: Only notifications delivered to this person, if given.
        unread_only: Only those the person (``user_id``) has not read.
        order: ``ASC`` (default, the polling contract): oldest first after
            the cursor. ``DESC``: the newest ``limit`` notifications, newest
            first.

    Returns:
        The notifications, plus ``latest_notification_id``: the highest ID
        returned, or ``after_id`` when nothing was returned.

    Raises:
        NotificationFilterError: If ``unread_only`` is set without ``user_id``
            (read state is per person, NT-10).
    """
    if unread_only and user_id is None:
        raise NotificationFilterError(
            "unread_only needs a user_id: read state is per person"
        )
    if order is NotificationListOrder.DESC:
        notifications = await notification_repository.list_newest(
            db,
            limit=limit,
            organization_id=organization_id,
            vehicle_id=vehicle_id,
            notification_type=notification_type,
            severity=severity,
            user_id=user_id,
            unread_only=unread_only,
        )
    else:
        notifications = await notification_repository.list_after_id(
            db,
            after_id=after_id,
            limit=limit,
            organization_id=organization_id,
            vehicle_id=vehicle_id,
            notification_type=notification_type,
            severity=severity,
            user_id=user_id,
            unread_only=unread_only,
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
    db: AsyncSession, notification_id: int, user_id: UUID
) -> NotificationReadResponse:
    """Mark an alert read for one person, keeping the first read time.

    Rule:
        Idempotent - an already-read alert keeps its original ``read_at``
        instead of being stamped again, so the value always means "first
        opened at" (NT-04). Opening also counts as seeing it.

    Args:
        db: Async session owned by the HTTP boundary.
        notification_id: Internal ID of the alert to mark read.
        user_id: The person who opened it.

    Returns:
        The person's inbox state for the alert.

    Raises:
        NotificationRecipientNotFoundError: If the alert never reached the
            person.

    Side Effects:
        Sets ``read_at`` and flushes when the alert was unread; does not
        commit.
    """
    recipient_record = await notification_repository.find_recipient(
        db, notification_id, user_id
    )
    if recipient_record is None:
        raise NotificationRecipientNotFoundError(
            f"Notification '{notification_id}' never reached user '{user_id}'"
        )
    if recipient_record.read_at is None:
        await notification_repository.set_recipient_read(
            db, recipient_record, utc_now()
        )
    return NotificationReadResponse(
        notification_id=recipient_record.notification_id,
        user_id=recipient_record.user_id,
        seen_at=recipient_record.seen_at,
        read_at=recipient_record.read_at,
    )


async def get_notification(
    db: AsyncSession, notification_id: int
) -> NotificationResponse:
    """Get one notification by ID.

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
    db: AsyncSession, user_id: UUID
) -> NotificationUnreadCountResponse:
    """Count the alerts a person has not read (their badge count).

    Args:
        db: Async session owned by the HTTP boundary.
        user_id: The person.

    Returns:
        The unread count.
    """
    unread_count = await notification_repository.count_unread(db, user_id)
    return NotificationUnreadCountResponse(unread_count=unread_count)


async def mark_all_notifications_read(
    db: AsyncSession, user_id: UUID
) -> NotificationMarkAllReadResponse:
    """Mark every unread alert of a person seen and read (NT-04).

    Rule:
        Same as ``mark_notification_read``: an already-read alert keeps its
        original ``read_at``; only unread ones are stamped, all with the same
        time. Doing it again changes nothing.

    Args:
        db: Async session owned by the HTTP boundary.
        user_id: The person.

    Returns:
        How many alerts were marked read.

    Side Effects:
        One ``UPDATE`` of the unread rows; does not commit.
    """
    marked_count = await notification_repository.set_all_recipient_read(
        db, user_id=user_id, read_at=utc_now()
    )
    return NotificationMarkAllReadResponse(marked_count=marked_count)
