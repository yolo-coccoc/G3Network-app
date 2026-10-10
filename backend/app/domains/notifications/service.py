"""Business service for the notifications domain.

Holds the notification rules (a person's mark-read and mark-all-read keep the
first read time, NT-04; opening the list marks what it shows seen, NT-10) and
the public cross-domain entry points ``create_notification``,
``add_notification_recipients``, ``register_vehicle_audience_hooks`` and
``resolve_last_notified_at``.

``create_notification`` is the whole producer flow (NT-15): store the alert,
find its recipients from the routing table (NTF-06), put it in their inboxes,
and send the push and e-mail the organization has switched on (NTF-02,
NTF-04, NTF-05). Routing and delivery each run in their own savepoint and
never fail the producer: a failure is logged and the stored alert (and, for a
delivery failure, the inboxes) stays.

The transaction is owned by whichever entry boundary called in - the HTTP
``get_db`` dependency for the list/read/count/mark-read endpoints, or a
producer's own transaction (the telemetry ingestion worker, the telematics
device-health monitor, the support SOS intake request). This module never
commits or rolls back on its own.

Access (ACC-15): the inbox is per person across their organizations (NT-09),
so "my notifications" are those delivered to the caller whatever organization
they are acting for. A caller with a staff role (fleet manager, operations,
customer care, our administrators) may also read the notifications of their
organization (internal staff: all); a caller who is only a DRIVER reads just
their own inbox. Opening one notification needs either.
"""

import logging
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.notifications.delivery as notification_delivery
import app.domains.notifications.recipient_service as recipient_service
import app.domains.notifications.repository as notification_repository
from app.domains.identity.types import Principal, UserRole, roles_for
from app.domains.notifications.exceptions import (
    NotificationFilterError,
    NotificationNotFoundError,
    NotificationRecipientNotFoundError,
)
from app.domains.notifications.models import NotificationModel
from app.domains.notifications.schemas import (
    NotificationListResponse,
    NotificationMarkAllReadResponse,
    NotificationMarkSeenResponse,
    NotificationReadResponse,
    NotificationResponse,
    NotificationUnreadCountResponse,
)
from app.domains.notifications.types import (
    NotificationContext,
    NotificationListOrder,
    NotificationReference,
    NotificationSeverity,
    NotificationType,
    VehicleAudienceHooks,
)
from app.libs.common.clock import utc_now

logger = logging.getLogger(__name__)

# Roles that read the notifications of a whole organization (NTF-01); a
# DRIVER-only caller reads only what was delivered to them.
NOTIFICATION_STAFF_ROLES = roles_for("NTF-01") - {UserRole.DRIVER}


def to_notification_response(
    notification_record: NotificationModel,
    *,
    seen_at: datetime | None = None,
    read_at: datetime | None = None,
) -> NotificationResponse:
    """Build a notification response from the ORM model.

    Args:
        notification_record: Notification ORM object queried or created by
            the repository.
        seen_at: In an inbox list, when the caller first saw it.
        read_at: In an inbox list, when the caller first opened it.

    Returns:
        Response schema corresponding to the notification.
    """
    notification_response = NotificationResponse.model_validate(notification_record)
    notification_response.seen_at = seen_at
    notification_response.read_at = read_at
    return notification_response


def register_vehicle_audience_hooks(hooks: VehicleAudienceHooks) -> None:
    """Register how routing asks the drivers and fleet domains about a truck.

    Public start-up entry point (``app/api/notification_hooks.py``); see
    ``recipient_service``.

    Args:
        hooks: The checked-in driver lookup and the fleet-visibility filter.

    Side Effects:
        Sets the process-wide hooks; calling it again replaces them.
    """
    recipient_service.register_vehicle_audience_hooks(hooks)


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
    recipient_user_ids: Sequence[UUID] = (),
) -> NotificationReference:
    """Raise an alert: store it, route it, deliver it. Public producer entry point.

    Rule:
        After the row is stored, the recipients come from the routing table of
        the alert's kind (``routing.py``) plus ``recipient_user_ids``; each
        gets an inbox row, then the push and e-mail the organization has on
        are sent. Routing and delivery run in separate savepoints: if either
        raises, the error is logged and the alert (and, after a delivery
        failure, the inboxes) is kept - a notification fault never fails the
        producer's own work.

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
        recipient_user_ids: People the producer addresses explicitly, on top
            of the routing table (the driver of a trip assigned to them).

    Returns:
        A minimal reference DTO - never the ORM model - so a calling domain
        never depends on this domain's persistence details.

    Raises:
        NotificationFilterError: If only one of ``subject_type`` and
            ``subject_id`` is given.

    Side Effects:
        Writes the alert and its recipient rows into the caller's transaction,
        asks identity for the people, push tokens and addresses, and calls the
        push and e-mail providers (the logging fakes today).
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
    context = NotificationContext(
        notification_id=notification_record.notification_id,
        organization_id=organization_id,
        notification_type=notification_type,
        severity=severity,
        vehicle_id=vehicle_id,
        subject_type=subject_type,
        subject_id=subject_id,
        title=title,
        body=body,
    )
    await _route_and_deliver(db, context, recipient_user_ids)
    return NotificationReference(notification_id=notification_record.notification_id)


async def _route_and_deliver(
    db: AsyncSession,
    context: NotificationContext,
    extra_user_ids: Sequence[UUID],
) -> None:
    """Put a new alert in its recipients' inboxes and send the extra channels.

    Two savepoints, so a delivery fault keeps the inboxes and a routing fault
    keeps the alert; neither propagates (the producer's work comes first).

    Args:
        db: The producer's session.
        context: The stored alert.
        extra_user_ids: People the producer named.

    Side Effects:
        Inbox rows, provider calls and log lines, as described above.
    """
    recipient_user_ids: list[UUID] = []
    try:
        async with db.begin_nested():
            recipient_user_ids = await recipient_service.resolve_recipient_user_ids(
                db, context, extra_user_ids=extra_user_ids
            )
            await notification_repository.insert_recipients(
                db, context.notification_id, recipient_user_ids
            )
    # Routing is a boundary of the producer's transaction: the savepoint
    # isolates it, the alert stays and the failure is logged.
    except Exception:
        recipient_user_ids = []
        logger.exception(
            "Notification routing failed",
            extra={"notification_id": context.notification_id},
        )
        return
    try:
        async with db.begin_nested():
            await notification_delivery.deliver_notification(
                db, context, recipient_user_ids
            )
    # Same boundary as above, for push and e-mail.
    except Exception:
        logger.exception(
            "Notification delivery failed",
            extra={"notification_id": context.notification_id},
        )


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
    principal: Principal,
    after_id: int,
    limit: int,
    before_id: int | None = None,
    organization_id: UUID | None = None,
    vehicle_id: UUID | None = None,
    notification_type: NotificationType | None = None,
    severity: NotificationSeverity | None = None,
    mine_only: bool = False,
    unread_only: bool = False,
    order: NotificationListOrder = NotificationListOrder.ASC,
) -> NotificationListResponse:
    """List notifications for a polling client or a notification centre.

    Args:
        db: Async session owned by the HTTP boundary.
        principal: The caller. Staff roles list their organization's
            notifications (internal staff: all) or, with ``mine_only``, their
            own inbox; a DRIVER-only caller always gets their own inbox.
        after_id: With ``order=ASC``, only return notifications with a
            larger ID than this cursor (``0`` returns from the beginning);
            ignored with ``order=DESC``.
        limit: Maximum number of records to return.
        before_id: With ``order=DESC``, only notifications with a smaller ID
            than this (the next page of the notification centre); ignored
            with ``order=ASC``.
        organization_id: Only notifications of this organization, if given;
            for a caller restricted to their organization any other value
            gives an empty list.
        vehicle_id: Only notifications about this vehicle, if given.
        notification_type: Only notifications of this type, if given.
        severity: Only notifications of this severity, if given.
        mine_only: Only the notifications delivered to the caller (their
            inbox across organizations).
        unread_only: Only the caller's inbox entries they have not read.
        order: ``ASC`` (default, the polling contract): oldest first after
            the cursor. ``DESC``: the newest ``limit`` notifications, newest
            first.

    Returns:
        The notifications, plus ``latest_notification_id``: the highest ID
        returned, or ``after_id`` when nothing was returned. In an inbox each
        entry carries the caller's ``seen_at`` and ``read_at``.

    Raises:
        NotificationFilterError: If ``unread_only`` is set without
            ``mine_only`` (read state is per person, NT-10).

    Side Effects:
        Opening the notification centre (an inbox read with ``order=DESC``)
        marks the entries it returns as seen, which clears the badge (NT-10);
        the background poll (``order=ASC``) does not.
    """
    is_inbox = mine_only or not principal.has_any_role(*NOTIFICATION_STAFF_ROLES)
    if unread_only and not is_inbox:
        raise NotificationFilterError(
            "unread_only needs mine_only: read state is per person"
        )
    user_id = principal.user_id if is_inbox else None
    if not is_inbox:
        scope = principal.data_scope
        if scope is not None and organization_id not in (None, scope):
            return NotificationListResponse(
                notifications=[], count=0, latest_notification_id=after_id
            )
        organization_id = scope if scope is not None else organization_id
    if order is NotificationListOrder.DESC:
        rows = await notification_repository.list_newest(
            db,
            limit=limit,
            before_id=before_id,
            organization_id=organization_id,
            vehicle_id=vehicle_id,
            notification_type=notification_type,
            severity=severity,
            user_id=user_id,
            unread_only=unread_only,
        )
    else:
        rows = await notification_repository.list_after_id(
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
    shown_at = utc_now()
    if is_inbox and order is NotificationListOrder.DESC:
        await notification_repository.set_recipients_seen(
            db,
            user_id=principal.user_id,
            seen_at=shown_at,
            notification_ids=[row[0].notification_id for row in rows],
        )
    latest_notification_id = max(
        (row[0].notification_id for row in rows), default=after_id
    )
    return NotificationListResponse(
        notifications=[
            to_notification_response(
                notification,
                seen_at=(
                    shown_at
                    if is_inbox
                    and order is NotificationListOrder.DESC
                    and seen_at is None
                    else seen_at
                ),
                read_at=read_at,
            )
            for notification, seen_at, read_at in rows
        ],
        count=len(rows),
        latest_notification_id=latest_notification_id,
    )


async def mark_notification_read(
    db: AsyncSession, notification_id: int, *, principal: Principal
) -> NotificationReadResponse:
    """Mark an alert read for one person, keeping the first read time.

    Rule:
        Idempotent - an already-read alert keeps its original ``read_at``
        instead of being stamped again, so the value always means "first
        opened at" (NT-04). Opening also counts as seeing it.

    Args:
        db: Async session owned by the HTTP boundary.
        notification_id: Internal ID of the alert to mark read.
        principal: The caller, the person who opened it.

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
        db, notification_id, principal.user_id
    )
    if recipient_record is None:
        raise NotificationRecipientNotFoundError(
            f"Notification '{notification_id}' not found in your inbox"
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
    db: AsyncSession, notification_id: int, *, principal: Principal
) -> NotificationResponse:
    """Get one notification by ID if it is in the caller's reach.

    Args:
        db: Async session owned by the HTTP boundary.
        notification_id: Internal ID of the notification.
        principal: The caller. Allowed when the notification was delivered to
            them, or when they hold a staff role and the notification belongs
            to their organization (internal staff: any).

    Returns:
        The notification response.

    Raises:
        NotificationNotFoundError: If the notification does not exist or is
            out of the caller's reach.
    """
    notification_record = await notification_repository.get_by_id(db, notification_id)
    if notification_record is None:
        raise NotificationNotFoundError(f"Notification '{notification_id}' not found")
    in_organization_reach = principal.has_any_role(*NOTIFICATION_STAFF_ROLES) and (
        principal.is_internal
        or (
            notification_record.organization_id is not None
            and principal.can_access_organization(notification_record.organization_id)
        )
    )
    if not in_organization_reach and (
        await notification_repository.find_recipient(
            db, notification_id, principal.user_id
        )
        is None
    ):
        raise NotificationNotFoundError(f"Notification '{notification_id}' not found")
    return to_notification_response(notification_record)


async def count_unread_notifications(
    db: AsyncSession, *, principal: Principal
) -> NotificationUnreadCountResponse:
    """Count the alerts the caller has not read, and those not yet seen.

    Args:
        db: Async session owned by the HTTP boundary.
        principal: The caller, the person whose counts are read.

    Returns:
        The unread count and the unseen count (the badge on the bell).
    """
    unread_count = await notification_repository.count_unread(db, principal.user_id)
    unseen_count = await notification_repository.count_unseen(db, principal.user_id)
    return NotificationUnreadCountResponse(
        unread_count=unread_count, unseen_count=unseen_count
    )


async def mark_all_notifications_seen(
    db: AsyncSession, *, principal: Principal
) -> NotificationMarkSeenResponse:
    """Mark every unseen alert of a person seen (the badge goes to zero).

    Rule:
        Only unseen rows are stamped, so the first seen time is kept and doing
        it again changes nothing. Seen is not read: the alerts stay unread
        until the person opens them (NT-10).

    Args:
        db: Async session owned by the HTTP boundary.
        principal: The caller, the person whose badge is cleared.

    Returns:
        How many alerts were marked seen.

    Side Effects:
        One ``UPDATE`` of the unseen rows; does not commit.
    """
    marked_count = await notification_repository.set_recipients_seen(
        db, user_id=principal.user_id, seen_at=utc_now()
    )
    return NotificationMarkSeenResponse(marked_count=marked_count)


async def mark_all_notifications_read(
    db: AsyncSession, *, principal: Principal
) -> NotificationMarkAllReadResponse:
    """Mark every unread alert of a person seen and read (NT-04).

    Rule:
        Same as ``mark_notification_read``: an already-read alert keeps its
        original ``read_at``; only unread ones are stamped, all with the same
        time. Doing it again changes nothing.

    Args:
        db: Async session owned by the HTTP boundary.
        principal: The caller, the person whose inbox is marked.

    Returns:
        How many alerts were marked read.

    Side Effects:
        One ``UPDATE`` of the unread rows; does not commit.
    """
    marked_count = await notification_repository.set_all_recipient_read(
        db, user_id=principal.user_id, read_at=utc_now()
    )
    return NotificationMarkAllReadResponse(marked_count=marked_count)
