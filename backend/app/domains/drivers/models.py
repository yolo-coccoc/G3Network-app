"""SQLAlchemy ORM models for the drivers domain (F-E4, F-A9).

Tables of the DBML ``drivers`` group: the driver profile, the driving session
(who is at the wheel of which truck, DR-07) and the trip (DR-12). Every foreign
key is ``RESTRICT``: rows are soft-deleted or closed, never removed. The
change-history tables ``driver_history`` and ``trip_history`` are not modelled:
the baseline migration creates them with ``app.libs.db.history_ddl`` and a
database trigger fills them. Statuses and other closed lists are plain
``varchar`` columns (except ``DriverStatus``); the allowed values are the enums
in ``types.py``.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from geoalchemy2 import Geography
from geoalchemy2.elements import WKBElement
from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    text,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.drivers.types import DriverStatus
from app.libs.common.clock import utc_now
from app.libs.db.base import Base


class DriverModel(Base):
    """A driver profile: the facts about one membership's job as a driver.

    Who the person is (name, phone) lives on ``users``; the organization is
    read through the membership (DR-09, DM-24). Change history is on
    (``driver_history``).

    Attributes:
        driver_id: Primary key (UUID).
        membership_id: The membership (one person in one organization) the
            profile belongs to; unique, a new membership gets a new profile.
        license_number: Licence number as this organization recorded it; not
            unique (the same person may have a profile in two organizations).
        license_class: Licence class (``LicenseClass``).
        license_expires_on: Expiry date printed on the licence; "expired" is
            computed from it, never stored.
        status: ``ACTIVE`` / ``INACTIVE`` (``DriverStatus``), decided by the
            organization.
        status_reason: Why the profile has its status or left the system.
        created_at: Creation time.
        updated_at: Last update time.
        deleted_at: Soft-delete time (DM-25); a deleted profile is INACTIVE.
    """

    __tablename__ = "drivers"

    driver_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    membership_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("memberships.membership_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    license_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    license_class: Mapped[str] = mapped_column(String(5), nullable=False)
    license_expires_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[DriverStatus] = mapped_column(
        SQLEnum(DriverStatus), default=DriverStatus.ACTIVE, nullable=False
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
        # A profile that left the system is always INACTIVE (DM-25).
        CheckConstraint(
            "deleted_at IS NULL OR status = 'INACTIVE'",
            name="ck_drivers_deleted_is_inactive",
        ),
    )

    def __repr__(self) -> str:
        """Return a concise representation for debugging a driver record."""
        return f"<DriverModel {self.driver_id} ({self.license_number})>"


class DrivingSessionModel(Base):
    """Who was at the wheel of which truck, and when (DR-07).

    An open/close table: a closed row is never edited, so it has no change
    history. At most one open session per truck and per driver (partial unique
    indexes), so checking in to a truck first ends the driver's other session
    and the truck's current one.

    Attributes:
        driving_session_id: Primary key (UUID).
        organization_id: The truck's owner when the session was recorded
            (DM-24 case C); the driver may belong to another organization.
        driver_id: Driver profile at the wheel.
        vehicle_id: Truck being driven.
        check_in_method: ``QR`` / ``APP`` / ``PORTAL`` (``CheckInMethod``).
        check_in_location: Phone position at check-in; ``None`` for PORTAL.
        started_at: Check-in time.
        ended_at: When the session ended; ``None`` while open.
        end_cause: Why it ended (``DrivingSessionEndCause``); ``None`` while
            open.
        created_at: Creation time.
        updated_at: Last update time (moves when the session ends).
    """

    __tablename__ = "driving_sessions"

    driving_session_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        nullable=False,
    )
    driver_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("drivers.driver_id", ondelete="RESTRICT"),
        nullable=False,
    )
    vehicle_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vehicles.vehicle_id", ondelete="RESTRICT"),
        nullable=False,
    )
    check_in_method: Mapped[str] = mapped_column(String(10), nullable=False)
    # Explicit index policy (database.md): no GIST index on a phone position
    # that is only compared with one truck's last position at check-in.
    check_in_location: Mapped[WKBElement | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    end_cause: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        # One driver at the wheel per truck, one truck per driver (DR-07).
        Index(
            "uq_driving_sessions_open_vehicle",
            "vehicle_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
        Index(
            "uq_driving_sessions_open_driver",
            "driver_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
        Index("ix_driving_sessions_vehicle_time", "vehicle_id", "started_at"),
        Index("ix_driving_sessions_driver_time", "driver_id", "started_at"),
    )


class TripModel(Base):
    """One trip: a job planned by a manager and its execution by the driver (DR-12).

    The plan columns are optional (a personal trip has none); the actual
    columns are filled when the driver presses Start and Finish. Distance,
    energy and cost are differences of the stored readings, computed when
    read. Change history is on (``trip_history``).

    Attributes:
        trip_id: Primary key (UUID).
        organization_id: Written once (DM-24 case C): the planner's
            organization, or the truck's owner for a personal trip.
        status: ``PLANNED`` / ``IN_PROGRESS`` / ``COMPLETED`` / ``CANCELLED``
            (``TripStatus``).
        status_reason: Why the trip has its status; ``None`` if nothing to say.
        planned_by: Manager who planned it; ``None`` for a personal trip.
        planned_driver_id: Driver profile assigned in the plan.
        planned_vehicle_id: Truck assigned in the plan.
        origin_name: Where the trip starts, as text.
        destination_name: Where it ends, as text.
        planned_start_at: Planned departure.
        planned_end_at: Planned arrival.
        driving_session_id: Session the trip was started in (the actual
            driver and truck).
        started_at: When Start was pressed.
        ended_at: When Finish was pressed or the session closed the trip.
        start_location: Truck's latest T-Box position at Start.
        end_location: Truck's latest T-Box position at the end.
        start_odometer_km: Odometer at Start.
        end_odometer_km: Odometer at the end.
        start_soc_percent: Battery percentage at Start.
        end_soc_percent: Battery percentage at the end.
        declared_load_status: ``LOADED`` / ``EMPTY`` declared at Start.
        created_at: Creation time.
        updated_at: Last update time.
    """

    __tablename__ = "trips"

    trip_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    planned_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=True,
    )
    planned_driver_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("drivers.driver_id", ondelete="RESTRICT"),
        nullable=True,
    )
    planned_vehicle_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vehicles.vehicle_id", ondelete="RESTRICT"),
        nullable=True,
    )
    origin_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    destination_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    planned_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    planned_end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    driving_session_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("driving_sessions.driving_session_id", ondelete="RESTRICT"),
        nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    start_location: Mapped[WKBElement | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=True,
    )
    end_location: Mapped[WKBElement | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=True,
    )
    start_odometer_km: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 1), nullable=True
    )
    end_odometer_km: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 1), nullable=True
    )
    start_soc_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    end_soc_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    declared_load_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        # The dispatch board and a driver's assigned trips.
        Index(
            "ix_trips_organization_planned_start", "organization_id", "planned_start_at"
        ),
        Index("ix_trips_planned_driver_status", "planned_driver_id", "status"),
        # One trip in progress per driving session (DR-12).
        Index(
            "uq_trips_session_in_progress",
            "driving_session_id",
            unique=True,
            postgresql_where=text("status = 'IN_PROGRESS'"),
        ),
        Index("ix_trips_session_started", "driving_session_id", "started_at"),
        # A planned trip has no actual data yet (DR-12).
        CheckConstraint(
            "status <> 'PLANNED' OR (driving_session_id IS NULL "
            "AND started_at IS NULL AND ended_at IS NULL)",
            name="ck_trips_planned_has_no_actuals",
        ),
        # A trip in progress or completed has a session and a start time.
        CheckConstraint(
            "status NOT IN ('IN_PROGRESS', 'COMPLETED') "
            "OR (driving_session_id IS NOT NULL AND started_at IS NOT NULL)",
            name="ck_trips_started_has_session",
        ),
        # A completed trip has an end time.
        CheckConstraint(
            "status <> 'COMPLETED' OR ended_at IS NOT NULL",
            name="ck_trips_completed_has_end",
        ),
    )
