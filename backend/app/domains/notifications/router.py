"""HTTP router for listing, reading and acknowledging notifications.

This module only turns requests into service calls; it contains no database
queries or business logic. Domain exceptions are mapped to HTTP status codes
centrally in ``app/api/main.py``. The fixed-path routes (``/unread-count``,
``/mark-all-read``) are declared before ``/{notification_id}`` so they are
never captured by it.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.notifications.service as notification_service
from app.domains.notifications.schemas import (
    NotificationListResponse,
    NotificationMarkAllReadResponse,
    NotificationResponse,
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


@router.get(
    "",
    response_model=NotificationListResponse,
    summary="List notifications: newer than a cursor, or newest first",
)
async def list_notifications_endpoint(
    after_id: int = Query(
        0, ge=0, description="Poll cursor (order=asc only): return IDs above it"
    ),
    limit: int = Query(
        settings.API_DEFAULT_PAGE_SIZE, ge=1, le=settings.API_MAX_PAGE_SIZE
    ),
    unread_only: bool = False,
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
    db: AsyncSession = Depends(get_db),
) -> NotificationListResponse:
    """Return notifications for a polling client or a notification centre.

    Args:
        after_id: With ``order=asc``, only return notifications with a
            larger ID than this cursor; defaults to ``0`` (from the
            beginning). Ignored with ``order=desc``.
        limit: Maximum number of records to return (1 to
            ``API_MAX_PAGE_SIZE``, like every other list endpoint).
        unread_only: Whether to exclude notifications already marked read.
        vehicle_id: Vehicle filter, if any.
        notification_type: Type filter, if any.
        severity: Severity filter, if any.
        order: ``asc`` (polling) or ``desc`` (newest first).
        db: Database session managed by the dependency.

    Returns:
        The notifications, plus the next poll cursor.
    """
    return await notification_service.list_notifications(
        db,
        after_id=after_id,
        limit=limit,
        unread_only=unread_only,
        vehicle_id=vehicle_id,
        notification_type=notification_type,
        severity=severity,
        order=order,
    )


@router.get(
    "/unread-count",
    response_model=NotificationUnreadCountResponse,
    summary="Count unread notifications",
)
async def count_unread_notifications_endpoint(
    vehicle_id: UUID | None = Query(
        None, description="Only count notifications about this vehicle"
    ),
    db: AsyncSession = Depends(get_db),
) -> NotificationUnreadCountResponse:
    """Return the number of notifications not yet marked read.

    Args:
        vehicle_id: Vehicle filter, if any.
        db: Database session managed by the dependency.

    Returns:
        The unread count.
    """
    return await notification_service.count_unread_notifications(db, vehicle_id)


@router.post(
    "/mark-all-read",
    response_model=NotificationMarkAllReadResponse,
    summary="Mark every unread notification as read",
)
async def mark_all_notifications_read_endpoint(
    vehicle_id: UUID | None = Query(
        None, description="Only mark notifications about this vehicle"
    ),
    db: AsyncSession = Depends(get_db),
) -> NotificationMarkAllReadResponse:
    """Mark every unread notification read; read ones keep their ``read_at``.

    Args:
        vehicle_id: Vehicle filter, if any.
        db: Database session managed by the dependency.

    Returns:
        How many notifications were marked read.
    """
    return await notification_service.mark_all_notifications_read(db, vehicle_id)


@router.get(
    "/{notification_id}",
    response_model=NotificationResponse,
    summary="Get a notification",
)
async def get_notification_endpoint(
    notification_id: int, db: AsyncSession = Depends(get_db)
) -> NotificationResponse:
    """Return one notification by ID.

    Args:
        notification_id: Internal ID of the notification.
        db: Database session managed by the dependency.

    Returns:
        The notification.

    Raises:
        NotificationNotFoundError: The notification does not exist (HTTP 404).
    """
    return await notification_service.get_notification(db, notification_id)


@router.patch(
    "/{notification_id}/read",
    response_model=NotificationResponse,
    summary="Mark a notification as read",
)
async def mark_notification_read_endpoint(
    notification_id: int, db: AsyncSession = Depends(get_db)
) -> NotificationResponse:
    """Mark a notification read; marking it again keeps the first ``read_at``.

    Args:
        notification_id: Internal ID of the notification to mark read.
        db: Database session managed by the dependency.

    Returns:
        The notification, with ``read_at`` set.

    Raises:
        NotificationNotFoundError: The notification does not exist (HTTP 404).
    """
    return await notification_service.mark_notification_read(db, notification_id)
