"""SQLAlchemy ORM models for the fleet domain."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.fleet.types import FleetStatus
from app.libs.common.clock import utc_now
from app.libs.db.base import Base


class FleetModel(Base):
    """ORM record representing a fleet.

    Attributes:
        fleet_id: Primary key (UUID).
        fleet_code: Natural business key (unique) - the same role
            `license_number` plays for a driver.
        name: Fleet's display name.
        status: Fleet lifecycle status.
        created_at: Creation time.
        updated_at: Last update time.
        deleted_at: Soft-delete timestamp.
    """

    __tablename__ = "fleets"

    fleet_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    fleet_code: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[FleetStatus] = mapped_column(
        SQLEnum(FleetStatus), default=FleetStatus.ACTIVE, nullable=False, index=True
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

    def __repr__(self) -> str:
        """Return a concise representation for debugging a fleet record."""
        return f"<FleetModel {self.name} ({self.fleet_code})>"


class FleetVehicleMembershipModel(Base):
    """A fleet-to-vehicle membership, open or closed (F-E1).

    Mirrors `driver_vehicle_assignments`'s open/close history-table shape,
    with one structural difference: a fleet holds many vehicles at once,
    so only `vehicle_id` gets a partial unique index (one active fleet per
    vehicle), not `fleet_id`.

    Attributes:
        membership_id: Primary key (UUID).
        fleet_id: The owning fleet. `ondelete=RESTRICT` - fleets are only
            ever soft-deleted in this backend, so a hard delete orphaning
            this history should never silently happen.
        vehicle_id: The member vehicle. Same `RESTRICT` reasoning.
        joined_at: When this membership began.
        left_at: When this membership ended, nullable while active.
        created_at: Creation time.
        updated_at: Last update time (moves when the row is closed).
    """

    __tablename__ = "fleet_vehicle_memberships"

    membership_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    fleet_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fleets.fleet_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    vehicle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vehicles.vehicle_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    left_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        # Partial unique index (drivers' pattern): a vehicle can belong to
        # at most one OPEN membership at a time, but any number of CLOSED
        # ones. Deliberately no equivalent unique index on fleet_id alone -
        # unlike a driver-vehicle assignment, a fleet legitimately holds
        # many vehicles at once.
        Index(
            "uq_fleet_vehicle_memberships_active_vehicle",
            "vehicle_id",
            unique=True,
            postgresql_where=text("left_at IS NULL"),
        ),
        Index(
            "ix_fleet_vehicle_memberships_fleet_time",
            "fleet_id",
            "joined_at",
        ),
    )
