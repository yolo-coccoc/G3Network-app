"""Minimal SQLAlchemy models for the charging session lifecycle happy path.

The module only stores the session aggregate, TransactionEvent history and
canonical Wh energy samples for the ideal MVP.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.charging_sessions.types import SessionEventType, SessionStatus
from app.libs.db.base import Base


def utc_now() -> datetime:
    """Get the current time in UTC to use as the model's default value.

    Returns:
        The current time as a timezone-aware UTC ``datetime``.
    """
    return datetime.now(timezone.utc)


def enum_values(enum_type: type[object]) -> list[str]:
    """Get the enum values so PostgreSQL stores the correct public contract.

    Args:
        enum_type: An enum whose members carry a ``value`` attribute.

    Returns:
        The list of values in the enum's declaration order.
    """
    return [member.value for member in enum_type]  # type: ignore[attr-defined]


class ChargingSessionModel(Base):
    """Aggregate for one OCPP transaction in the happy path.

    Attributes:
        session_id: Internal UUID.
        station_id: The station that owns the transaction.
        evse_id: The EVSE that owns the transaction.
        connector_id: The connector currently charging.
        ocpp_transaction_id: The transaction identity issued by the station.
        status: Only active or completed in the MVP.
        started_at: The time of Started.
        ended_at: The time of Ended, nullable while still active.
        meter_start_wh: The meter reading at the start of the session.
        meter_end_wh: The latest/final meter reading.
        energy_delivered_wh: The difference between the end and start meter
            readings.
        created_at: The time the record was created.
        updated_at: The time the record was last updated.
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
    evse_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_evses.evse_id", ondelete="RESTRICT"),
        nullable=False,
    )
    connector_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_connectors.connector_id", ondelete="RESTRICT"),
        nullable=False,
    )
    ocpp_transaction_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[SessionStatus] = mapped_column(
        SQLEnum(
            SessionStatus,
            name="chargingsessionstatus",
            values_callable=enum_values,
        ),
        nullable=False,
        default=SessionStatus.ACTIVE,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    meter_start_wh: Mapped[Decimal | None] = mapped_column(
        Numeric(24, 3), nullable=True
    )
    meter_end_wh: Mapped[Decimal | None] = mapped_column(Numeric(24, 3), nullable=True)
    energy_delivered_wh: Mapped[Decimal | None] = mapped_column(
        Numeric(24, 3), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "station_id",
            "ocpp_transaction_id",
            name="uq_charging_sessions_station_transaction",
        ),
        Index("ix_charging_sessions_status_updated", "status", "updated_at"),
        Index("ix_charging_sessions_station_status", "station_id", "status"),
        Index("ix_charging_sessions_evse_status", "evse_id", "status"),
        Index("ix_charging_sessions_connector_status", "connector_id", "status"),
        Index("ix_charging_sessions_started_at", "started_at"),
        Index("ix_charging_sessions_ended_at", "ended_at"),
    )


class ChargingSessionEventModel(Base):
    """Minimal TransactionEvent history stored as a hypertable.

    Attributes:
        event_id: Internal UUID of the event.
        event_occurred_at: The time the event occurred; also the time
            partitioning key.
        session_id: The UUID of the session aggregate that owns the event.
        event_type: The type — ``Started``, ``Updated`` or ``Ended``.
    """

    __tablename__ = "charging_session_events"

    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    # TimescaleDB needs a time column in the key to partition the hypertable
    # while still allowing multiple events for the same session at different
    # times.
    event_occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    session_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_sessions.session_id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_type: Mapped[SessionEventType] = mapped_column(
        SQLEnum(
            SessionEventType,
            name="chargingsessioneventtype",
            values_callable=enum_values,
        ),
        nullable=False,
    )
    __table_args__ = (
        Index(
            "ix_charging_session_events_session_time",
            "session_id",
            "event_occurred_at",
            "event_id",
        ),
    )


class ChargingSessionMeterValueModel(Base):
    """A canonical Wh energy sample for the session, stored as a hypertable.

    Attributes:
        meter_value_id: Internal UUID of the sample.
        sampled_at: The time of measurement; also the time partitioning key.
        session_id: The UUID of the session aggregate that owns the sample.
        value_wh: The meter reading normalized to Wh.
    """

    __tablename__ = "charging_session_meter_values"

    meter_value_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    # Sample time is both the axis for historical queries and the
    # hypertable's partition key; meter_value_id preserves uniqueness when
    # two samples share a timestamp.
    sampled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    session_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("charging_sessions.session_id", ondelete="RESTRICT"),
        nullable=False,
    )
    value_wh: Mapped[Decimal] = mapped_column(Numeric(24, 3), nullable=False)
    __table_args__ = (
        Index(
            "ix_charging_meter_session_sampled",
            "session_id",
            "sampled_at",
            "meter_value_id",
        ),
    )
