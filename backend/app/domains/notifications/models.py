"""SQLAlchemy models of the notifications domain.

``notifications`` holds one alert whoever receives it, ``notification_recipients``
the per-person inbox state (seen and read, NT-10) and
``organization_notification_settings`` an organization's push and e-mail switch
per kind of alert (NT-12).
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.notifications.types import NotificationSeverity, NotificationType
from app.libs.common.clock import utc_now
from app.libs.db.base import Base


class NotificationModel(Base):
    """A single alert raised for the organization that owns what it is about.

    An ordinary relational table, not a hypertable - notification volume is
    orders of magnitude lower than the telemetry stream that produces most
    of them. Append-only: no change history, no soft delete; who read it is
    in ``notification_recipients`` (NT-09, NT-10).

    Attributes:
        notification_id: Internal ID (BIGINT identity). Monotonically
            increasing, so it also serves as the poll cursor for
            ``GET /notifications?after_id=``.
        organization_id: The organization the alert belongs to, written once:
            the truck's owner at that moment (DM-24 case C).
        notification_type: Kind of event that raised the notification.
        severity: Severity independent of type.
        vehicle_id: Vehicle the notification is about, for filtering by
            truck; nullable.
        subject_type: What the alert is about, and so which screen the app
            opens (``VEHICLE``, ``TRIP``...); nullable, set exactly when
            ``subject_id`` is. No foreign key: it points to different tables.
        subject_id: ID of that object.
        title: Short human-readable summary.
        body: Longer human-readable description.
        payload: Type-specific structured data (e.g. threshold, SOC, nearest
            station) not otherwise represented as a column.
        created_at: Time the notification was raised.
    """

    __tablename__ = "notifications"

    notification_id: Mapped[int] = mapped_column(
        BigInteger(),
        primary_key=True,
        autoincrement=True,
    )
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        nullable=False,
    )
    notification_type: Mapped[NotificationType] = mapped_column(
        SQLEnum(NotificationType),
        nullable=False,
    )
    severity: Mapped[NotificationSeverity] = mapped_column(
        SQLEnum(NotificationSeverity),
        nullable=False,
    )
    vehicle_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vehicles.vehicle_id", ondelete="RESTRICT"),
        nullable=True,
    )
    subject_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    subject_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        CheckConstraint(
            "(subject_type IS NULL) = (subject_id IS NULL)",
            name="ck_notifications_subject_both_or_neither",
        ),
        Index("ix_notifications_vehicle_id", "vehicle_id"),
        Index(
            "ix_notifications_organization_cursor",
            "organization_id",
            "notification_id",
        ),
    )

    def __repr__(self) -> str:
        """Return a concise debug representation of the notification.

        Returns:
            ``<NotificationModel {type} #{id}>``.
        """
        return f"<NotificationModel {self.notification_type} #{self.notification_id}>"


class NotificationRecipientModel(Base):
    """Who received an alert, and whether they saw and read it (NT-10).

    The inbox is per person across all their organizations, so the row reads
    its organization through the alert. Seen clears the badge count; read is
    the tap that opens the alert's screen. No change history, no soft delete.

    Attributes:
        notification_recipient_id: Internal ID (BIGINT identity): alerts times
            people is a large number.
        notification_id: The alert.
        user_id: The person who receives it.
        seen_at: When the person opened their notification list after it
            arrived; nullable.
        read_at: When the person tapped it and opened its screen; nullable
            (unread is ``NULL``, NT-04).
        created_at: When it reached their inbox.
    """

    __tablename__ = "notification_recipients"

    notification_recipient_id: Mapped[int] = mapped_column(
        BigInteger(), primary_key=True, autoincrement=True
    )
    notification_id: Mapped[int] = mapped_column(
        BigInteger(),
        ForeignKey("notifications.notification_id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        Index(
            "uq_notification_recipients_notification_user",
            "notification_id",
            "user_id",
            unique=True,
        ),
        Index("ix_notification_recipients_inbox", "user_id", "notification_id"),
        Index(
            "ix_notification_recipients_unseen",
            "user_id",
            postgresql_where=text("seen_at IS NULL"),
        ),
    )


class OrganizationNotificationSettingModel(Base):
    """An organization's push and e-mail switch for one kind of alert (NT-12).

    The app and portal inbox always shows every alert; this only switches push
    and e-mail. Only exceptions are stored: a kind with no row uses the
    default from code. Change history is on (turning a channel off is the
    ORG_ADMIN's decision).

    Attributes:
        organization_notification_setting_id: Internal UUID.
        organization_id: The organization the switch belongs to.
        notification_type: The kind of alert (a ``NotificationType`` value).
        push_enabled: Send this kind as a push to the organization's
            recipients.
        email_enabled: Send it by e-mail to recipients with an address on file.
        created_at: When the row was created.
        updated_at: When the row was last changed.
    """

    __tablename__ = "organization_notification_settings"

    organization_notification_setting_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        nullable=False,
    )
    notification_type: Mapped[str] = mapped_column(String(40), nullable=False)
    push_enabled: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    email_enabled: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        Index(
            "uq_organization_notification_settings_type",
            "organization_id",
            "notification_type",
            unique=True,
        ),
    )
