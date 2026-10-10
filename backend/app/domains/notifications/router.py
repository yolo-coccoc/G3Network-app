"""HTTP router for listing, reading and acknowledging notifications.

This module only turns requests into service calls; it contains no database
queries or business logic. Domain exceptions are mapped to HTTP status codes
centrally in ``app/api/main.py``. The fixed-path routes (``/unread-count``,
``/mark-all-read``, ``/mark-seen``) are declared before
``/{notification_id}`` so they are never captured by it.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.notifications.service as notification_service
import app.domains.notifications.settings_service as notification_settings_service
from app.domains.identity.dependencies import require_roles
from app.domains.identity.types import Principal, roles_for
from app.domains.notifications.schemas import (
    NotificationListResponse,
    NotificationMarkAllReadResponse,
    NotificationMarkSeenResponse,
    NotificationReadResponse,
    NotificationResponse,
    NotificationSettingListResponse,
    NotificationSettingResponse,
    NotificationSettingUpdateRequest,
    NotificationUnreadCountResponse,
)
from app.domains.notifications.types import (
    NotificationListOrder,
    NotificationSeverity,
    NotificationType,
)
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["notifications"])
# NTF-05 lives under the organization (web-portal scenario, section 11);
# mounted by ``app/api/main.py`` with the ``/organizations`` prefix.
settings_router = APIRouter(tags=["notifications"])

# Who may call what (features.yaml `users`, via `roles_for`): NTF-01 the
# notification centre. What a caller sees inside it (own inbox, or the whole
# organization for staff roles) is decided by the service.
NOTIFICATION_USERS = require_roles(*roles_for("NTF-01"))
# NTF-05: the organization's channel settings.
NOTIFICATION_SETTINGS_USERS = require_roles(*roles_for("NTF-05"))


@router.get(
    "",
    response_model=NotificationListResponse,
    summary="List notifications: newer than a cursor, or newest first",
)
async def list_notifications_endpoint(
    after_id: int = Query(
        0, ge=0, description="Poll cursor (order=asc only): return IDs above it"
    ),
    before_id: int | None = Query(
        None,
        ge=1,
        description="Next page of the notification centre (order=desc only): "
        "return IDs below it",
    ),
    limit: int = Query(
        settings.API_DEFAULT_PAGE_SIZE, ge=1, le=settings.API_MAX_PAGE_SIZE
    ),
    organization_id: UUID | None = Query(None, description="Filter by organization"),
    mine_only: bool = Query(
        False,
        description=(
            "Only the alerts delivered to me (my inbox across organizations); "
            "always on for a caller who is only a driver"
        ),
    ),
    unread_only: bool = Query(
        False, description="With mine_only: only alerts I have not read"
    ),
    vehicle_id: UUID | None = Query(None, description="Filter by vehicle ID"),
    notification_type: NotificationType | None = Query(
        None, description="Filter by notification type"
    ),
    severity: NotificationSeverity | None = Query(
        None, description="Filter by severity"
    ),
    order: NotificationListOrder = Query(
        NotificationListOrder.ASC,
        description=(
            "asc (default): oldest first after after_id, the polling contract; "
            "desc: the newest notifications first, after_id ignored"
        ),
    ),
    principal: Principal = Depends(NOTIFICATION_USERS),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> NotificationListResponse:
    """Return notifications for a polling client or a notification centre.

    Args:
        after_id: With ``order=asc``, only return notifications with a
            larger ID than this cursor; defaults to ``0`` (from the
            beginning). Ignored with ``order=desc``.
        before_id: With ``order=desc``, only return notifications with a
            smaller ID than this (the next page).
        limit: Maximum number of records to return (1 to
            ``API_MAX_PAGE_SIZE``, like every other list endpoint).
        organization_id: Organization filter, if any.
        mine_only: Only the alerts delivered to the caller.
        unread_only: With ``mine_only``, only the alerts the caller has not read.
        vehicle_id: Vehicle filter, if any.
        notification_type: Type filter, if any.
        severity: Severity filter, if any.
        order: ``asc`` (polling) or ``desc`` (newest first).
        principal: The authenticated caller.
        db: Database session managed by the dependency.

    Returns:
        The notifications, plus the next poll cursor.
    """
    return await notification_service.list_notifications(
        db,
        after_id=after_id,
        limit=limit,
        before_id=before_id,
        organization_id=organization_id,
        mine_only=mine_only,
        unread_only=unread_only,
        vehicle_id=vehicle_id,
        notification_type=notification_type,
        severity=severity,
        order=order,
        principal=principal,
    )


@router.get(
    "/unread-count",
    response_model=NotificationUnreadCountResponse,
    summary="Count the alerts a person has not read or not seen",
)
async def count_unread_notifications_endpoint(
    principal: Principal = Depends(NOTIFICATION_USERS),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> NotificationUnreadCountResponse:
    """Return the number of alerts a person has not read, and not yet seen.

    Args:
        principal: The authenticated caller.
        db: Database session managed by the dependency.

    Returns:
        The unread count and the unseen count (the badge on the bell).
    """
    return await notification_service.count_unread_notifications(
        db, principal=principal
    )


@router.post(
    "/mark-all-read",
    response_model=NotificationMarkAllReadResponse,
    summary="Mark every unread alert of a person as read",
)
async def mark_all_notifications_read_endpoint(
    principal: Principal = Depends(NOTIFICATION_USERS),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> NotificationMarkAllReadResponse:
    """Mark every unread alert of a person read; read ones keep their ``read_at``.

    Args:
        principal: The authenticated caller.
        db: Database session managed by the dependency.

    Returns:
        How many alerts were marked read.
    """
    return await notification_service.mark_all_notifications_read(
        db, principal=principal
    )


@router.post(
    "/mark-seen",
    response_model=NotificationMarkSeenResponse,
    summary="Mark every unseen alert of a person as seen (clears the badge)",
)
async def mark_all_notifications_seen_endpoint(
    principal: Principal = Depends(NOTIFICATION_USERS),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> NotificationMarkSeenResponse:
    """Mark every unseen alert of a person seen; they stay unread.

    Args:
        principal: The authenticated caller.
        db: Database session managed by the dependency.

    Returns:
        How many alerts were marked seen.
    """
    return await notification_service.mark_all_notifications_seen(
        db, principal=principal
    )


@router.get(
    "/{notification_id}",
    response_model=NotificationResponse,
    summary="Get a notification",
)
async def get_notification_endpoint(
    notification_id: int,
    principal: Principal = Depends(NOTIFICATION_USERS),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> NotificationResponse:
    """Return one notification by ID.

    Args:
        notification_id: Internal ID of the notification.
        principal: The authenticated caller.
        db: Database session managed by the dependency.

    Returns:
        The notification.

    Raises:
        NotificationNotFoundError: The notification does not exist (HTTP 404).
    """
    return await notification_service.get_notification(
        db, notification_id, principal=principal
    )


@router.patch(
    "/{notification_id}/read",
    response_model=NotificationReadResponse,
    summary="Mark an alert as read for a person",
)
async def mark_notification_read_endpoint(
    notification_id: int,
    principal: Principal = Depends(NOTIFICATION_USERS),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> NotificationReadResponse:
    """Mark an alert read for a person; marking it again keeps the first ``read_at``.

    Args:
        notification_id: Internal ID of the alert to mark read.
        principal: The authenticated caller.
        db: Database session managed by the dependency.

    Returns:
        The person's inbox state for the alert.

    Raises:
        NotificationRecipientNotFoundError: The alert never reached the
            person (HTTP 404).
    """
    return await notification_service.mark_notification_read(
        db, notification_id, principal=principal
    )


@settings_router.get(
    "/{organization_id}/notification-settings",
    response_model=NotificationSettingListResponse,
    summary="An organization's push and e-mail switches per kind of alert",
)
async def list_notification_settings_endpoint(
    organization_id: UUID,
    principal: Principal = Depends(NOTIFICATION_SETTINGS_USERS),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> NotificationSettingListResponse:
    """Return every kind of alert with the organization's push / e-mail switches.

    Args:
        organization_id: The organization to read (the caller's own, or any
            for internal staff).
        principal: The authenticated caller (an ORG_ADMIN or our administrator).
        db: Database session managed by the dependency.

    Returns:
        One entry per kind of alert; defaults where nothing was saved.

    Raises:
        NotificationSettingOrganizationNotFoundError: Organization out of
            reach (HTTP 404).
    """
    return await notification_settings_service.list_organization_notification_settings(
        db, principal=principal, organization_id=organization_id
    )


@settings_router.put(
    "/{organization_id}/notification-settings/{notification_type}",
    response_model=NotificationSettingResponse,
    summary="Switch push and e-mail for one kind of alert",
)
async def update_notification_setting_endpoint(
    organization_id: UUID,
    notification_type: NotificationType,
    update_request: NotificationSettingUpdateRequest,
    principal: Principal = Depends(NOTIFICATION_SETTINGS_USERS),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> NotificationSettingResponse:
    """Save an organization's push / e-mail switches for one kind of alert.

    Args:
        organization_id: The organization to change (the caller's own, or any
            for internal staff).
        notification_type: The kind of alert.
        update_request: The new switches and an optional reason.
        principal: The authenticated caller (an ORG_ADMIN or our administrator).
        db: Database session managed by the dependency.

    Returns:
        The saved switches. The inbox itself is always on and not switchable.

    Raises:
        NotificationSettingOrganizationNotFoundError: Organization out of
            reach (HTTP 404).
    """
    return await notification_settings_service.update_organization_notification_setting(
        db,
        notification_type,
        update_request,
        principal=principal,
        organization_id=organization_id,
    )
