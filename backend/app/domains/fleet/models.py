"""SQLAlchemy ORM models for the fleet domain."""

from datetime import datetime
from uuid import UUID, uuid4

from geoalchemy2 import Geography
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.libs.common.clock import utc_now
from app.libs.db.base import Base


class FleetModel(Base):
    """ORM record representing a fleet: a named group of vehicles (F-E1).

    A fleet is a node of the customer's own structure (region, branch,
    depot...), nested through ``parent_fleet_id`` (FL-02). It has no status:
    a fleet is a grouping, so it either exists or is soft-deleted (FL-08).
    The owning ``organization_id`` and the per-organization code index come
    with the organizations table (planned in the DBML); until then
    ``fleet_code`` is unique across all fleets.

    Attributes:
        fleet_id: Primary key (UUID).
        parent_fleet_id: The fleet this one sits under; ``None`` for a
            top-level fleet. ``RESTRICT``: fleets are only soft-deleted.
        fleet_code: Short code chosen by the customer; optional, unique
            across all fleets for now.
        name: Display name chosen by the customer; optional. The check
            constraint ``ck_fleets_name_or_code`` requires a name or a code.
        created_at: Creation time.
        updated_at: Last update time.
        deleted_at: Soft-delete timestamp; ``None`` while the fleet exists.
    """

    __tablename__ = "fleets"

    fleet_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    parent_fleet_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fleets.fleet_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    fleet_code: Mapped[str | None] = mapped_column(
        String(50), unique=True, nullable=True, index=True
    )
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    __table_args__ = (
        # A fleet with neither a name nor a code could not be told apart
        # (FL-08).
        CheckConstraint(
            "num_nonnulls(name, fleet_code) >= 1", name="ck_fleets_name_or_code"
        ),
    )

    def __repr__(self) -> str:
        """Return a concise representation for debugging a fleet record."""
        return f"<FleetModel {self.name} ({self.fleet_code})>"


class FleetVehicleMembershipModel(Base):
    """A fleet-to-vehicle membership period, open or closed (F-E1).

    Mirrors `driver_vehicle_assignments`'s open/close history-table shape,
    with one structural difference: a fleet holds many vehicles at once,
    so only `vehicle_id` gets a partial unique index (one active fleet per
    vehicle), not `fleet_id`. Who added or removed the vehicle
    (``added_by`` / ``removed_by``, FL-09) comes with the users table
    (planned in the DBML).

    Attributes:
        fleet_vehicle_membership_id: Primary key (UUID).
        fleet_id: The owning fleet. `ondelete=RESTRICT` - fleets are only
            ever soft-deleted in this backend, so a hard delete orphaning
            this history should never silently happen.
        vehicle_id: The member vehicle. Same `RESTRICT` reasoning.
        added_at: When the vehicle was added to the fleet.
        removed_at: When the vehicle was removed, ``None`` while a member.
        created_at: Creation time.
        updated_at: Last update time (moves when the row is closed).
    """

    __tablename__ = "fleet_vehicle_memberships"

    fleet_vehicle_membership_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    # No single-column index: ix_fleet_vehicle_memberships_fleet_time
    # (fleet_id, added_at) already serves fleet_id lookups (FL-09).
    fleet_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fleets.fleet_id", ondelete="RESTRICT"),
        nullable=False,
    )
    vehicle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vehicles.vehicle_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    removed_at: Mapped[datetime | None] = mapped_column(
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
            postgresql_where=text("removed_at IS NULL"),
        ),
        Index(
            "ix_fleet_vehicle_memberships_fleet_time",
            "fleet_id",
            "added_at",
        ),
    )


class GeofenceModel(Base):
    """A fleet-scoped area whose entry or exit by a member vehicle raises an
    alert (F-A5).

    Scoped to a fleet because customer accounts (the designed owner, decision
    D1) don't exist yet; ``account_id`` is a planned column in the DBML. A
    geofence applies to every vehicle that is currently a member of its fleet.
    No GIST index: the containment check always filters by ``fleet_id`` first
    and a fleet has few geofences.

    Attributes:
        geofence_id: Primary key (UUID).
        fleet_id: The fleet whose vehicles the area applies to. ``RESTRICT``:
            fleets are only soft-deleted.
        name: Name shown in alerts.
        boundary: The area as a WGS84 ``geography(Polygon, 4326)``.
        created_at: Creation time.
        updated_at: Last update time.
        deleted_at: Soft-delete time; ``None`` while the geofence is live.
    """

    __tablename__ = "geofences"

    geofence_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    fleet_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fleets.fleet_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    boundary: Mapped[object] = mapped_column(
        Geography(geometry_type="POLYGON", srid=4326, spatial_index=False),
        nullable=False,
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
