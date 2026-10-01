"""SQLAlchemy model for backend/operator-facing notifications."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.notifications.types import NotificationSeverity, NotificationType
from app.libs.common.clock import utc_now
from app.libs.db.base import Base


class NotificationModel(Base):
    """A single notification raised for an operator-facing consumer.

    An ordinary relational table, not a hypertable - notification volume is
    orders of magnitude lower than the telemetry stream that produces most
    of them.

    Attributes:
        notification_id: Internal ID (BIGINT identity). Monotonically
            increasing, so it also serves as the poll cursor for
            ``GET /notifications?after_id=``.
        notification_type: Kind of event that raised the notification.
        severity: Severity independent of type.
        vehicle_id: Vehicle the notification is about, nullable so a future
            non-vehicle notification type can reuse this table.
        title: Short human-readable summary.
        body: Longer human-readable description.
        payload: Type-specific structured data (e.g. threshold, SOC, nearest
            station) not otherwise represented as a column.
        created_at: Time the notification was raised.
        read_at: Time an operator marked it read, nullable - unread means
            ``NULL``.
    """

    __tablename__ = "notifications"

    notification_id: Mapped[int] = mapped_column(
        BigInteger(),
        primary_key=True,
        autoincrement=True,
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
        ForeignKey("vehicles.vehicle_id", ondelete="CASCADE"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (Index("ix_notifications_vehicle_id", "vehicle_id"),)

    def __repr__(self) -> str:
        """Return a concise debug representation of the notification.

        Returns:
            ``<NotificationModel {type} #{id}>``.
        """
        return f"<NotificationModel {self.notification_type} #{self.notification_id}>"
