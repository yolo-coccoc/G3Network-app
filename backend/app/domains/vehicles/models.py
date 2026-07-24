"""SQLAlchemy model for Vehicle domain."""

from datetime import datetime

from sqlalchemy import String, Integer, DateTime, ForeignKey, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Identity
import enum

from app.libs.db.session import Base


class VehicleStatus(str, enum.Enum):
    """Vehicle status enum."""
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    MAINTENANCE = "MAINTENANCE"
    DECOMMISSIONED = "DECOMMISSIONED"


class Vehicle(Base):
    """Vehicle model representing electric trucks.
    
    Attributes:
        vehicle_id: Primary key (int)
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
    
    vehicle_id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    license_plate: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    vin: Mapped[str] = mapped_column(String(17), unique=True, nullable=False, index=True)
    make: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[VehicleStatus] = mapped_column(
        SQLEnum(VehicleStatus),
        default=VehicleStatus.ACTIVE,
        nullable=False,
        index=True
    )
    fleet_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    
    # Relationships (will be defined when Fleet model is created)
    # fleet: Mapped["Fleet"] = relationship("Fleet", back_populates="vehicles")
    
    def __repr__(self) -> str:
        return f"<Vehicle {self.license_plate} ({self.vin})>"
