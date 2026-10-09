"""SQLAlchemy ORM model of the warranties domain: the ``warranties`` table.

Change history is on (``warranty_history``, created by the baseline migration
with a trigger; not modelled). A warranty has no ``organization_id`` (DM-24):
it follows its object, and whoever owns the object now sees it.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.libs.common.clock import utc_now
from app.libs.db.base import Base


class WarrantyModel(Base):
    """ORM record of one warranty of exactly one truck, battery, T-Box or charger.

    Attributes:
        warranty_id: Primary key (UUID).
        vehicle_id: The truck covered; set only for a truck warranty.
        battery_id: The battery covered; set only for a battery warranty.
        telematic_id: The T-Box covered; set only for a device warranty.
        station_id: The charger covered; set only for a charger warranty.
            Exactly one of the four links is set (``ck_warranties_one_link``).
        warranty_type: ``STANDARD`` / ``EXTENDED`` (``WarrantyType``).
        contract_reference: Number of the warranty contract; ``None`` if none.
        starts_on: First day of coverage.
        ends_on: Last day of coverage.
        limits: Other limits as the counter reading at which coverage ends
            (keys depend on the covered object); ``None`` when there is none.
        status: ``ACTIVE`` / ``VOIDED`` (``WarrantyStatus``).
        status_reason: Why the warranty was voided; ``None`` when ACTIVE.
        created_at: Creation time.
        updated_at: Last update time.
        deleted_at: Soft-delete time; a deleted warranty is always VOIDED.
    """

    __tablename__ = "warranties"

    warranty_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    vehicle_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vehicles.vehicle_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    battery_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("batteries.battery_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    telematic_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("telematics.telematic_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    station_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_stations.station_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    warranty_type: Mapped[str] = mapped_column(String(20), nullable=False)
    contract_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    limits: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
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
        # The covered object is the one link that is set (VH-18, VH-19).
        CheckConstraint(
            "num_nonnulls(vehicle_id, battery_id, telematic_id, station_id) = 1",
            name="ck_warranties_one_link",
        ),
        # A warranty entered by mistake is also VOIDED (DM-25).
        CheckConstraint(
            "deleted_at IS NULL OR status = 'VOIDED'",
            name="ck_warranties_deleted_voided",
        ),
    )

    def __repr__(self) -> str:
        """Return a concise representation for debugging a warranty record."""
        return f"<WarrantyModel {self.warranty_id} ({self.status})>"
