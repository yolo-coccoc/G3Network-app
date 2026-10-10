"""SQLAlchemy models for charging sessions and their measurements.

A session row is created ``PENDING`` at the QR scan in our app (CE-10) and the
charger's start message turns it ``ACTIVE``; the measurements are the
append-only readings of the charger during the session (CE-14). The
``charging_session_events`` table of the earlier design is gone (CE-15): start
and end live on the session, the full trail in the raw OCPP log.

The status enum stores the Python member names (``PENDING``...).
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
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

from app.domains.charging_sessions.types import (
    ID_TOKEN_MAX_LENGTH,
    OCPP_TRANSACTION_ID_MAX_LENGTH,
    STOP_REASON_MAX_LENGTH,
    SessionStatus,
)
from app.libs.common.clock import utc_now
from app.libs.db.base import Base


class ChargingSessionModel(Base):
    """One charge on one connector of one charger, from the QR scan to the stop.

    Attributes:
        session_id: Internal UUID.
        station_id: The charger where the session happens, known at the scan.
        evse_id: The EVSE used; ``NULL`` while ``PENDING`` and when
            ``ABANDONED`` (the driver picks the gun on the charger's screen).
        connector_id: The connector used; same nullability as ``evse_id``.
        organization_id: The organization that pays, written once at the scan
            (DM-24 case C).
        started_by: The user who scanned the QR code.
        vehicle_id: The truck being charged, taken at the scan from the
            scanning driver's open driving session (CE-13); nullable.
        ocpp_transaction_id: The charger's transaction ID, unique per charger;
            ``NULL`` while ``PENDING`` and when ``ABANDONED``.
        status: ``PENDING``, ``ACTIVE``, ``COMPLETED`` or ``ABANDONED``.
        started_at: The charger's time of the start; ``NULL`` before it.
        ended_at: The charger's time of the stop; ``NULL`` until completed.
        meter_start_wh: The meter reading the charger declares at the start.
        id_token: The single-use token we send in the remote start and the
            charger echoes in its start message; never log it (IS-07).
        stop_reason: The charger's stop reason, as sent.
        meter_stop_wh: The meter reading the charger declares in its stop
            message: the billing figure (CE-12).
        created_at: When the row was created: the scan time.
        updated_at: When the row last changed.
    """

    __tablename__ = "charging_sessions"

    session_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    station_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_stations.station_id", ondelete="RESTRICT"),
        nullable=False,
    )
    evse_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_evses.evse_id", ondelete="RESTRICT"),
        nullable=True,
    )
    connector_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_connectors.connector_id", ondelete="RESTRICT"),
        nullable=True,
    )
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.organization_id", ondelete="RESTRICT"),
        nullable=False,
    )
    started_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    vehicle_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vehicles.vehicle_id", ondelete="RESTRICT"),
        nullable=True,
    )
    ocpp_transaction_id: Mapped[str | None] = mapped_column(
        String(OCPP_TRANSACTION_ID_MAX_LENGTH), nullable=True
    )
    status: Mapped[SessionStatus] = mapped_column(
        SQLEnum(SessionStatus, name="chargingsessionstatus"),
        nullable=False,
        default=SessionStatus.PENDING,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    meter_start_wh: Mapped[Decimal | None] = mapped_column(
        Numeric(24, 3), nullable=True
    )
    id_token: Mapped[str] = mapped_column(String(ID_TOKEN_MAX_LENGTH), nullable=False)
    stop_reason: Mapped[str | None] = mapped_column(
        String(STOP_REASON_MAX_LENGTH), nullable=True
    )
    meter_stop_wh: Mapped[Decimal | None] = mapped_column(Numeric(24, 3), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint(
            "status NOT IN ('ACTIVE', 'COMPLETED') OR ("
            "ocpp_transaction_id IS NOT NULL AND evse_id IS NOT NULL "
            "AND connector_id IS NOT NULL AND started_at IS NOT NULL "
            "AND meter_start_wh IS NOT NULL)",
            name="ck_charging_sessions_started_columns",
        ),
        CheckConstraint(
            "status <> 'COMPLETED' OR ended_at IS NOT NULL",
            name="ck_charging_sessions_completed_ended_at",
        ),
        CheckConstraint(
            "status NOT IN ('PENDING', 'ABANDONED') OR ocpp_transaction_id IS NULL",
            name="ck_charging_sessions_unstarted_no_transaction",
        ),
        Index(
            "uq_charging_sessions_station_transaction",
            "station_id",
            "ocpp_transaction_id",
            unique=True,
            postgresql_where=text("ocpp_transaction_id IS NOT NULL"),
        ),
        Index(
            "ix_charging_sessions_pending_token",
            "station_id",
            "id_token",
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index(
            "ix_charging_sessions_organization_started",
            "organization_id",
            "started_at",
        ),
        Index("ix_charging_sessions_vehicle_id", "vehicle_id"),
        Index("ix_charging_sessions_status_updated", "status", "updated_at"),
        Index("ix_charging_sessions_station_status", "station_id", "status"),
        Index("ix_charging_sessions_evse_status", "evse_id", "status"),
        Index("ix_charging_sessions_connector_status", "connector_id", "status"),
        Index("ix_charging_sessions_started_at", "started_at"),
        Index("ix_charging_sessions_ended_at", "ended_at"),
    )


class ChargingSessionMeasurementModel(Base):
    """One measurement of a session, stored as a hypertable.

    Holds every measurand a charger reports during a session for both OCPP
    protocols. The gateway stores each known measurand in one fixed unit and
    fills the OCPP defaults for a missing context or location, so readers
    never convert units or guess defaults (CE-14). The raw payload is never
    stored here (see the raw OCPP message log).

    Attributes:
        measurement_id: Internal UUID of the measurement.
        sampled_at: The charger's time of the reading; also the time
            partitioning key.
        session_id: The session the sample belongs to.
        measurand: What was measured, as an OCPP measurand name
            (``Energy.Active.Import.Register``, ``SoC``...); vendor names are
            stored as sent.
        value: The reading, in the fixed unit of a known measurand.
        unit: The unit of ``value``: the fixed unit of a known measurand; as
            sent, or ``NULL``, for a vendor one.
        context: Why the charger sent the reading (``Sample.Periodic``...);
            required, the OCPP default is stored when the charger omits it.
        phase: Electrical phase the value refers to, nullable (DC has none).
        measurement_location: Where on the charging path it was measured
            (``Outlet``, ``Inlet``, ``Cable``, ``EV``, ``Body``); required, the
            OCPP default ``Outlet`` is stored when the charger omits it.
    """

    __tablename__ = "charging_session_measurements"

    measurement_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    # Sample time is both the axis for historical queries and the
    # hypertable's partition key; measurement_id preserves uniqueness when
    # two samples share a timestamp.
    sampled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    session_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_sessions.session_id", ondelete="RESTRICT"),
        nullable=False,
    )
    measurand: Mapped[str] = mapped_column(String(60), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    context: Mapped[str] = mapped_column(String(30), nullable=False)
    phase: Mapped[str | None] = mapped_column(String(10), nullable=True)
    measurement_location: Mapped[str] = mapped_column(String(20), nullable=False)
    __table_args__ = (
        Index(
            "ix_charging_measurements_session_measurand_time",
            "session_id",
            "measurand",
            "sampled_at",
        ),
    )
