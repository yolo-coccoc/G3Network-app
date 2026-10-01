"""HTTP router for polling and acknowledging notifications.

This module only turns requests into service calls and maps business
exceptions to HTTP status codes; it contains no database queries or
business logic.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.notifications.exceptions import NotificationNotFoundError
from app.domains.notifications.schemas import (
    NotificationListResponse,
    NotificationResponse,
)
from app.domains.notifications.service import (
    list_notifications,
    mark_notification_read,
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
    return await list_notifications(
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
    """Mark a notification read.

    Args:
        notification_id: Internal ID of the notification to mark read.
        db: Database session managed by the dependency.

    Returns:
        The updated notification.

    Raises:
        HTTPException: When the notification does not exist.
    """
    try:
        return await mark_notification_read(db, notification_id)
    except NotificationNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
