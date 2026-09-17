"""SQLAlchemy ORM model for the vehicle record in the vehicles domain."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Double
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import Integer, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.vehicles.types import VehicleActivationStatus, VehicleStatus
from app.libs.db.base import Base


def utc_now() -> datetime:
    """Return the current UTC time with timezone info."""
    return datetime.now(timezone.utc)


class VehicleModel(Base):
    """ORM record representing an electric truck.

    Attributes:
        vehicle_id: Primary key (UUID)
        license_plate: License plate (unique)
        vin: VIN (chassis number, unique)
        make: Manufacturer
        model: Model
        year: Manufacturing year
        status: Vehicle status
        fleet_id: Fleet ID (nullable, may not be assigned yet)
        activation_status: Progress through the F-F2 device-provisioning
            flow (`PENDING`/`DEVICE_ASSIGNED`/`ACTIVATED`), independent of
            `status`. Defaults to `PENDING`.
        battery_capacity_kwh: Nominal usable battery pack capacity in kWh,
            nullable - not every vehicle's spec has been recorded. The
            F-A6/F-C6 reports fall back to a documented default when it's
            NULL. Nominal, not SOH-adjusted.
        created_at: Creation time
        updated_at: Last update time
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
    activation_status: Mapped[VehicleActivationStatus] = mapped_column(
        SQLEnum(VehicleActivationStatus),
        default=VehicleActivationStatus.PENDING,
        nullable=False,
    )
    battery_capacity_kwh: Mapped[float | None] = mapped_column(Double(), nullable=True)
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
        """Return a concise representation for debugging a vehicle record."""
        return f"<VehicleModel {self.license_plate} ({self.vin})>"
