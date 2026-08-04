"""SQLAlchemy ORM model cho hồ sơ xe trong domain vehicles."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import Integer, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.vehicles.types import VehicleStatus
from app.libs.db.base import Base


def utc_now() -> datetime:
    """Trả về thời điểm UTC có timezone."""
    return datetime.now(timezone.utc)


class VehicleModel(Base):
    """Bản ghi ORM đại diện cho xe tải điện.

    Attributes:
        vehicle_id: Primary key (UUID)
        license_plate: Biển số xe (unique)
        vin: Số khung (unique)
        make: Hãng xe
        model: Dòng xe
        year: Năm sản xuất
        status: Trạng thái xe
        fleet_id: ID đội xe (nullable, có thể chưa phân bổ)
        created_at: Thời gian tạo
        updated_at: Thời gian cập nhật
    """

    __tablename__ = "vehicles"

    vehicle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    license_plate: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )
    vin: Mapped[str] = mapped_column(
        String(17), unique=True, nullable=False, index=True
    )
    make: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[VehicleStatus] = mapped_column(
        SQLEnum(VehicleStatus), default=VehicleStatus.ACTIVE, nullable=False, index=True
    )
    fleet_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        """Trả về biểu diễn ngắn gọn để debug bản ghi xe."""
        return f"<VehicleModel {self.license_plate} ({self.vin})>"
