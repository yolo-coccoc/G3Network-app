"""SQLAlchemy ORM models for the drivers domain."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.drivers.types import DriverStatus
from app.libs.db.base import Base


def utc_now() -> datetime:
    """Return the current UTC time with timezone info."""
    return datetime.now(timezone.utc)


class DriverModel(Base):
    """ORM record representing a driver.

    Attributes:
        driver_id: Primary key (UUID).
        full_name: Driver's full name.
        phone_number: Contact phone number (unique).
        license_number: Driving license number (unique) - the driver's
            natural business key, the same role `vin` plays for a vehicle.
        status: Driver lifecycle status.
        created_at: Creation time.
        updated_at: Last update time.
        deleted_at: Soft-delete timestamp.
    """

    __tablename__ = "drivers"

    driver_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone_number: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )
    license_number: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    status: Mapped[DriverStatus] = mapped_column(
        SQLEnum(DriverStatus), default=DriverStatus.ACTIVE, nullable=False, index=True
    )
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
        """Return a concise representation for debugging a driver record."""
        return f"<DriverModel {self.full_name} ({self.license_number})>"


class DriverVehicleAssignmentModel(Base):
    """A driver-to-vehicle assignment, open or closed (F-E4).

    Unlike the single-column `telematics.vehicle_id` current-state
    pointer, this is a genuine history table: a row is never deleted or
    overwritten, only closed by setting `unassigned_at`. This is what lets
    `GET /drivers/{driver_id}/assignments` show a driver's past vehicles,
    not just their current one.

    Attributes:
        assignment_id: Primary key (UUID).
        driver_id: The assigned driver. `ondelete=RESTRICT` - drivers are
            only ever soft-deleted in this backend, so a hard delete
            orphaning this history should never silently happen.
        vehicle_id: The assigned vehicle. Same `RESTRICT` reasoning.
        assigned_at: When this assignment began.
        unassigned_at: When this assignment ended, nullable while active.
        created_at: Creation time.
        updated_at: Last update time (moves when the row is closed).
    """

    __tablename__ = "driver_vehicle_assignments"

    assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    driver_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("drivers.driver_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    vehicle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vehicles.vehicle_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    unassigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        # Partial unique indexes (this backend's first use of one): a
        # vehicle or a driver can have at most one OPEN assignment at a
        # time, but any number of CLOSED ones - the "WHERE" clause is what
        # makes reassignment possible at all, unlike telematics.vehicle_id's
        # table-wide UniqueConstraint, which also blocks on soft-deleted
        # rows since it has no such predicate.
        Index(
            "uq_driver_vehicle_assignments_active_vehicle",
            "vehicle_id",
            unique=True,
            postgresql_where=text("unassigned_at IS NULL"),
        ),
        Index(
            "uq_driver_vehicle_assignments_active_driver",
            "driver_id",
            unique=True,
            postgresql_where=text("unassigned_at IS NULL"),
        ),
        Index(
            "ix_driver_vehicle_assignments_driver_time",
            "driver_id",
            "assigned_at",
        ),
    )
