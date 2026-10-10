"""Pydantic schemas for the notifications HTTP contract."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domains.notifications.types import NotificationSeverity, NotificationType


class NotificationResponse(BaseModel):
    """A single notification as returned to a polling client.

    Attributes:
        notification_id: Internal ID; also the poll cursor for later calls.
        organization_id: The organization the alert belongs to.
        notification_type: Kind of event that raised the notification.
        severity: Severity independent of type.
        vehicle_id: Vehicle the notification is about, nullable.
        subject_type: What the alert is about (which screen to open),
            nullable.
        subject_id: ID of that object, nullable.
        title: Short human-readable summary.
        body: Longer human-readable description.
        payload: Type-specific structured data.
        created_at: Time the notification was raised.
    """

    model_config = ConfigDict(from_attributes=True)

    notification_id: int
    organization_id: UUID
    notification_type: NotificationType
    severity: NotificationSeverity
    vehicle_id: UUID | None
    subject_type: str | None
    subject_id: UUID | None
    title: str
    body: str
    payload: dict[str, object]
    created_at: datetime


class NotificationListResponse(BaseModel):
    """A page of notifications: newer than the caller's cursor, or newest.

    Attributes:
        notifications: Notifications ordered oldest first (``order=asc``,
            the polling contract) or newest first (``order=desc``).
        count: Number of notifications in this response.
        latest_notification_id: Highest ``notification_id`` returned, or the
            caller's own ``after_id`` if nothing was found - the value to
            pass as ``after_id`` on the next poll.
    """

    notifications: list[NotificationResponse]
    count: int = Field(..., ge=0)
    latest_notification_id: int = Field(..., ge=0)


class NotificationUnreadCountResponse(BaseModel):
    """Number of alerts a person has not read (their badge count).

    Attributes:
        unread_count: Inbox rows of the person with no read time.
    """

    unread_count: int = Field(..., ge=0)


class NotificationMarkAllReadResponse(BaseModel):
    """Outcome of marking every unread alert of a person read.

    Attributes:
        marked_count: Alerts that were unread and are now read; already-read
            ones keep their first ``read_at`` and are not counted.
    """

    marked_count: int = Field(..., ge=0)


class NotificationReadResponse(BaseModel):
    """One person's inbox state for an alert after it was marked read.

    Attributes:
        notification_id: The alert.
        user_id: The person.
        seen_at: When the person first saw it (set with the read at the latest).
        read_at: When the person first opened it.
    """

    notification_id: int
    user_id: UUID
    seen_at: datetime | None
    read_at: datetime | None
