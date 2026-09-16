"""SQLAlchemy model for the physical Telematic device."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.telematics.types import TelematicStatus
from app.libs.db.base import Base


def utc_now() -> datetime:
    """Return the current UTC time with timezone info."""
    return datetime.now(timezone.utc)


class TelematicModel(Base):
    """Physical Telematic device installed on a vehicle.

    Attributes:
        telematic_id: Internal ID of the device.
        telematic_serial: Unique serial printed on the device.
        vehicle_id: ID of the assigned vehicle, may be NULL.
        status: Operating status.
        firmware_version: Current firmware version.
        deleted_at: Soft-delete timestamp.
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (UniqueConstraint("vehicle_id", name="uq_telematics_vehicle_id"),)
