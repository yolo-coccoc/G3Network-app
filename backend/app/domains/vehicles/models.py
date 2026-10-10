"""SQLAlchemy ORM models of the vehicles domain: vehicles and vehicle models.

``vehicles`` and ``vehicle_models`` both have change history (``@tracked *``):
their ``vehicle_history`` / ``vehicle_model_history`` tables are created by the
baseline migration with a trigger (``app.libs.db.history_ddl``) and are not
modelled. The view ``vehicle_ownership_periods`` (VH-10) is also created by the
migration; code reads ownership periods only through it.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.vehicles.types import VehicleStatus
from app.libs.common.clock import utc_now
from app.libs.db.base import Base


class VehicleModelModel(Base):
    """ORM record of a truck model in the shared catalog (VH-15).

    "Model" appears twice because the class name is the business noun
    (vehicle model) plus the ``Model`` suffix every ORM class carries.

    Attributes:
        vehicle_model_id: Primary key (UUID).
        make: Manufacturer.
        model_name: Model line, unique per manufacturer among live rows.
        gross_vehicle_weight_kg: Gross vehicle weight; ``None`` until known.
        max_payload_kg: Maximum payload; ``None`` until known.
        nominal_battery_capacity_kwh: Battery capacity the model is delivered
            with; a plain number, not a link to ``battery_models``.
        consumption_curve: Reference energy consumption by load, a list of
            ``{"load_percent", "kwh_per_km"}`` points; ``None`` until known.
        created_at: Creation time.
        updated_at: Last update time.
        deleted_at: Soft-delete time; ``None`` while the model is offered.
    """

    __tablename__ = "vehicle_models"

    vehicle_model_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    make: Mapped[str] = mapped_column(String(50), nullable=False)
    model_name: Mapped[str] = mapped_column(String(50), nullable=False)
    gross_vehicle_weight_kg: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_payload_kg: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nominal_battery_capacity_kwh: Mapped[Decimal | None] = mapped_column(
        Numeric(7, 1), nullable=True
    )
    consumption_curve: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, nullable=True
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

    __table_args__ = (
        Index(
            "uq_vehicle_models_live_make_model",
            "make",
            "model_name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    def __repr__(self) -> str:
        """Return a concise representation for debugging a vehicle model."""
        return f"<VehicleModelModel {self.make} {self.model_name}>"


class VehicleModel(Base):
    """ORM record representing an electric truck (the profile, no state table).

    Change history is on (``vehicle_history``): the previous owners and their
    handover dates are read through the view ``vehicle_ownership_periods``.

    Attributes:
        vehicle_id: Primary key (UUID).
        organization_id: The organization that owns the truck now (its own
            owner, DM-24 case A); changes on an ownership transfer.
        acquired_at: When the current owner took the truck.
        license_plate: Registration plate; unique among live vehicles.
        vin: VIN (chassis number); unique among live vehicles.
        vehicle_model_id: The truck's model and specifications.
        year: Manufacturing year.
        status: Service status (``VehicleStatus``): ACTIVE / INACTIVE.
        status_reason: Why the vehicle has its status, or why it left the
            system; ``None`` when ACTIVE.
        created_at: Creation time.
        updated_at: Last update time, refreshed by the ORM ``onupdate`` hook
            on every flushed UPDATE of the row.
        deleted_at: Soft-delete timestamp; ``None`` while the vehicle is part
            of the system. A deleted vehicle is always INACTIVE (DM-25).
    """

    __tablename__ = "vehicles"

    vehicle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    license_plate: Mapped[str] = mapped_column(String(20), nullable=False)
    vin: Mapped[str] = mapped_column(String(17), nullable=False)
    vehicle_model_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vehicle_models.vehicle_model_id", ondelete="RESTRICT"),
        nullable=False,
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[VehicleStatus] = mapped_column(
        SQLEnum(VehicleStatus), default=VehicleStatus.ACTIVE, nullable=False, index=True
    )
    status_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # A plate or VIN names one vehicle among those still in the system, so
        # the plate of a truck that left can move to another truck (VH-07).
        Index(
            "uq_vehicles_live_vin",
            func.upper(vin),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_vehicles_live_license_plate",
            func.upper(license_plate),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # A vehicle that left the system is always INACTIVE (DM-25).
        CheckConstraint(
            "deleted_at IS NULL OR status = 'INACTIVE'",
            name="ck_vehicles_deleted_inactive",
        ),
    )

    def __repr__(self) -> str:
        """Return a concise representation for debugging a vehicle record."""
        return f"<VehicleModel {self.license_plate} ({self.vin})>"
