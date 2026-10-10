"""SQLAlchemy ORM models of the batteries domain: battery models and batteries.

Both tables have change history (``battery_model_history``,
``battery_history``), created by the baseline migration with a trigger and not
modelled. The view ``battery_installation_periods`` (the stays of a battery in
trucks, DM-22) is created by the migration too; code reads installation
periods only through it, never from ``battery_history``. ``batteries`` points
to ``vehicles`` and ``organizations``; nothing in vehicles points back.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.libs.common.clock import utc_now
from app.libs.db.base import Base


class BatteryModelModel(Base):
    """ORM record of a battery type in the shared catalog (BAT-01, VH-13).

    Attributes:
        battery_model_id: Primary key (UUID).
        manufacturer: Battery maker.
        model_name: Model name, unique per manufacturer among live rows.
        chemistry: Cell chemistry (``BatteryChemistry``).
        design_capacity_kwh: Energy the pack holds when new; ``None`` until
            known.
        nominal_voltage_v: Nominal pack voltage; ``None`` until known.
        created_at: Creation time.
        updated_at: Last update time.
        deleted_at: Soft-delete time; ``None`` while the model is offered.
    """

    __tablename__ = "battery_models"

    battery_model_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    manufacturer: Mapped[str] = mapped_column(String(50), nullable=False)
    model_name: Mapped[str] = mapped_column(String(50), nullable=False)
    chemistry: Mapped[str] = mapped_column(String(10), nullable=False)
    design_capacity_kwh: Mapped[Decimal | None] = mapped_column(
        Numeric(7, 1), nullable=True
    )
    nominal_voltage_v: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 1), nullable=True
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
            "uq_battery_models_live_manufacturer_model",
            "manufacturer",
            "model_name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    def __repr__(self) -> str:
        """Return a concise representation for debugging a battery model."""
        return f"<BatteryModelModel {self.manufacturer} {self.model_name}>"


class BatteryModel(Base):
    """ORM record of one physical battery, managed as an asset (BAT-01, VH-16).

    Attributes:
        battery_id: Primary key (UUID).
        serial_number: Manufacturer's serial number; unique among live
            batteries.
        battery_model_id: The battery's model and specifications.
        organization_id: The organization that owns the battery now; may
            differ from the truck's owner.
        acquired_at: When the current owner took the battery.
        vehicle_id: Truck the battery is installed in now; ``None`` in stock
            or removed. At most one live battery per truck.
        installed_at: When it was installed in its current truck; ``None``
            when not installed.
        manufactured_on: Manufacturing date; ``None`` until known.
        status: ``ACTIVE`` / ``INACTIVE`` (``BatteryStatus``), set by a person.
        status_reason: Why the battery has its status; ``None`` when ACTIVE.
        created_at: Creation time.
        updated_at: Last update time.
        deleted_at: Soft-delete time; a deleted battery is always INACTIVE.
    """

    __tablename__ = "batteries"

    battery_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    serial_number: Mapped[str] = mapped_column(String(50), nullable=False)
    battery_model_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("battery_models.battery_model_id", ondelete="RESTRICT"),
        nullable=False,
    )
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        nullable=False,
    )
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    vehicle_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vehicles.vehicle_id", ondelete="RESTRICT"),
        nullable=True,
    )
    installed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    manufactured_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
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
        Index(
            "uq_batteries_live_serial_number",
            func.upper(serial_number),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # A truck holds at most one battery (VH-16).
        Index(
            "uq_batteries_installed_vehicle",
            "vehicle_id",
            unique=True,
            postgresql_where=text("vehicle_id IS NOT NULL AND deleted_at IS NULL"),
        ),
        # A battery that left the system is always INACTIVE (DM-25).
        CheckConstraint(
            "deleted_at IS NULL OR status = 'INACTIVE'",
            name="ck_batteries_deleted_inactive",
        ),
    )

    def __repr__(self) -> str:
        """Return a concise representation for debugging a battery record."""
        return f"<BatteryModel {self.serial_number}>"
