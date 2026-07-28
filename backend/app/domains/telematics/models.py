"""SQLAlchemy model cho thiết bị Telematic vật lý."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.telematics.types import TelematicStatus
from app.libs.db.base import Base


def utc_now() -> datetime:
    """Trả về thời điểm UTC có timezone."""
    return datetime.now(timezone.utc)


class Telematic(Base):
    """Thiết bị Telematic vật lý được lắp trên xe.

    Attributes:
        telematic_id: ID nội bộ của thiết bị.
        telematic_serial: Serial duy nhất in trên thiết bị.
        vehicle_id: ID xe được gán, có thể NULL.
        status: Trạng thái vận hành.
        firmware_version: Phiên bản firmware hiện tại.
        deleted_at: Thời điểm soft delete.
    """

    __tablename__ = "telematics"

    telematic_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    telematic_serial: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    vehicle_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("vehicles.vehicle_id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[TelematicStatus] = mapped_column(SQLEnum(TelematicStatus), nullable=False, index=True)
    firmware_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("vehicle_id", name="uq_telematics_vehicle_id"),)
