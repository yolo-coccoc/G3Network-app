"""SQLAlchemy model for the physical Telematic device."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.telematics.types import TelematicStatus
from app.libs.common.clock import utc_now
from app.libs.db.base import Base


class TelematicModel(Base):
    """Physical Telematic device installed on a vehicle.

    Attributes:
        telematic_id: Internal ID of the device.
        telematic_serial: Unique serial printed on the device.
        vehicle_id: ID of the assigned vehicle, may be NULL.
        status: Operating status.
        firmware_version: Current firmware version.
        telemetry_interval_seconds: Telemetry publish interval last
            successfully pushed to the device over MQTT (F-J2), NULL
            until the first successful push - there is no ack topic, so
            this means "the last value we handed to the broker", never
            "the value we wish we'd sent".
        config_pushed_at: When ``telemetry_interval_seconds`` was last
            successfully pushed, NULL until the first push.
        created_at: Creation time.
        updated_at: Last update time, refreshed by the ORM ``onupdate`` hook
            on every flushed UPDATE of the row.
        deleted_at: Soft-delete timestamp; NULL while the device is live.
    """

    __tablename__ = "telematics"

    telematic_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    telematic_serial: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    vehicle_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vehicles.vehicle_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[TelematicStatus] = mapped_column(
        SQLEnum(TelematicStatus), nullable=False, index=True
    )
    firmware_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    telemetry_interval_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    config_pushed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # At most one LIVE device per vehicle. Partial (WHERE deleted_at IS NULL),
    # so a soft-deleted device that still records its last vehicle doesn't
    # block mounting a replacement (deferred.md item 82).
    __table_args__ = (
        Index(
            "uq_telematics_active_vehicle",
            "vehicle_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )
