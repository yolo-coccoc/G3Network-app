"""Pydantic schemas for the notifications HTTP contract."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domains.notifications.types import NotificationSeverity, NotificationType
from app.libs.common.reason import Reason


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
        seen_at: In an inbox list: when the caller first saw it; ``None``
            when unseen, and always ``None`` in the organization view.
        read_at: In an inbox list: when the caller first opened it
            (unread is ``None``, NT-04); always ``None`` in the organization
            view.
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
    seen_at: datetime | None = None
    read_at: datetime | None = None


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
        unseen_count: Inbox rows with no seen time: the badge on the bell,
            cleared when the person opens the list (NT-10).
    """

    unread_count: int = Field(..., ge=0)
    unseen_count: int = Field(..., ge=0)


class NotificationMarkSeenResponse(BaseModel):
    """Outcome of marking every unseen alert of a person seen.

    Attributes:
        marked_count: Alerts that were unseen and are now seen.
    """

    marked_count: int = Field(..., ge=0)


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


class NotificationSettingResponse(BaseModel):
    """One organization's push and e-mail switches for one kind of alert.

    Attributes:
        notification_type: The kind of alert.
        push_enabled: Whether it is sent as a push to the organization's
            recipients.
        email_enabled: Whether it is sent by e-mail to recipients with an
            address on file.
        is_default: True when the organization has not saved this kind and
            the default from code applies.
        updated_at: When the organization last saved it; ``None`` for a
            default.
    """

    notification_type: NotificationType
    push_enabled: bool
    email_enabled: bool
    is_default: bool
    updated_at: datetime | None


class NotificationSettingListResponse(BaseModel):
    """Every kind of alert with an organization's effective switches.

    Attributes:
        organization_id: The organization the switches belong to.
        settings: One entry per kind of alert; the in-app inbox is always on
            and not listed (NT-03).
    """

    organization_id: UUID
    settings: list[NotificationSettingResponse]


class NotificationSettingUpdateRequest(BaseModel):
    """New switches for one kind of alert.

    Attributes:
        push_enabled: Send this kind as a push.
        email_enabled: Send it by e-mail to recipients with an address.
        reason: Why the switches change, recorded in the change history; a
            fixed text is used when omitted.
    """

    push_enabled: bool
    email_enabled: bool
    reason: Reason | None = Field(default=None)
