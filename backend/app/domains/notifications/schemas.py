"""Pydantic schemas for the notifications HTTP contract."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domains.notifications.types import NotificationSeverity, NotificationType


class NotificationResponse(BaseModel):
    """A single notification as returned to a polling client.

    Attributes:
        notification_id: Internal ID; also the poll cursor for later calls.
        notification_type: Kind of event that raised the notification.
        severity: Severity independent of type.
        vehicle_id: Vehicle the notification is about, nullable.
        title: Short human-readable summary.
        body: Longer human-readable description.
        payload: Type-specific structured data.
        created_at: Time the notification was raised.
        read_at: Time an operator marked it read, nullable.
    """

    model_config = ConfigDict(from_attributes=True)

    notification_id: int
    notification_type: NotificationType
    severity: NotificationSeverity
    vehicle_id: UUID | None
    title: str
    body: str
    payload: dict[str, object]
    created_at: datetime
    read_at: datetime | None


class NotificationListResponse(BaseModel):
    """A page of notifications newer than the caller's cursor.

    Attributes:
        notifications: Notifications ordered oldest first.
        count: Number of notifications in this response.
        latest_notification_id: Highest ``notification_id`` returned, or the
            caller's own ``after_id`` if nothing new was found - the value to
            pass as ``after_id`` on the next poll.
    """

    notifications: list[NotificationResponse]
    count: int = Field(..., ge=0)
    latest_notification_id: int = Field(..., ge=0)
