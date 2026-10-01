"""HTTP router for polling and acknowledging notifications.

This module only turns requests into service calls; it contains no database
queries or business logic. Domain exceptions are mapped to HTTP status codes
centrally in ``app/api/main.py``.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.notifications.service as notification_service
from app.domains.notifications.schemas import (
    NotificationListResponse,
    NotificationResponse,
)
from app.libs.common.config import settings
from app.libs.db.session import get_db

router = APIRouter(tags=["notifications"])


@router.get(
    "",
    response_model=NotificationListResponse,
    summary="Poll for notifications newer than a cursor",
)
async def list_notifications_endpoint(
    after_id: int = 0,
    limit: int = Query(
        settings.API_DEFAULT_PAGE_SIZE, ge=1, le=settings.API_MAX_PAGE_SIZE
    ),
    unread_only: bool = False,
    db: AsyncSession = Depends(get_db),
) -> NotificationListResponse:
    """Return notifications newer than a cursor, for a polling client.

    Args:
        after_id: Only return notifications with a larger ID than this
            cursor; defaults to ``0`` (from the beginning).
        limit: Maximum number of records to return (1 to
            ``API_MAX_PAGE_SIZE``, like every other list endpoint).
        unread_only: Whether to exclude notifications already marked read.
        db: Database session managed by the dependency.

    Returns:
        Notifications newer than ``after_id``, plus the next cursor.
    """
    return await notification_service.list_notifications(
        db, after_id=after_id, limit=limit, unread_only=unread_only
    )


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
