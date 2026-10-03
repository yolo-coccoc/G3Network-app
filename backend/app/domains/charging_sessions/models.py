"""Minimal SQLAlchemy models for the charging session lifecycle happy path.

The module stores the session aggregate, TransactionEvent history and the
measurements (canonical Wh energy samples and, for OCPP 1.6J, other measurands)
of each session.

The status and event-type enums store their public values (``active``,
``Started``) via ``enum_values``, not the Python member names.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.charging_sessions.types import (
    STOP_REASON_MAX_LENGTH,
    SessionEventType,
    SessionStatus,
)
from app.libs.common.clock import utc_now
from app.libs.db.base import Base
from app.libs.db.enums import enum_values


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
        meter_end_wh: The most recently observed meter reading, as of
            ``meter_end_sampled_at`` - not necessarily the numerically
            latest, since a sample older than the current watermark is
            discarded (F-B2).
        meter_end_sampled_at: The measurement time of ``meter_end_wh``,
            nullable - an existing row's true sample time is genuinely
            unknown (F-B2). Never moves backward: a sample timestamped
            earlier than this value is discarded, not applied.
        energy_delivered_wh: The difference between the end and start meter
            readings.
        id_tag: The idTag (RFID/token) that started the session, nullable
            (OCPP 1.6J; at most 20 characters). Stored as sent, not
            validated: every tag is accepted for now.
        stop_reason: Why the session stopped, as reported (OCPP 1.6J
            ``StopTransaction.reason``), nullable.
        meter_stop_wh: The charger's authoritative closing meter reading from
            ``StopTransaction.meterStop``, nullable. Kept apart from
            ``meter_end_wh``, which follows the latest sample.
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
    meter_end_sampled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    energy_delivered_wh: Mapped[Decimal | None] = mapped_column(
        Numeric(24, 3), nullable=True
    )
    id_tag: Mapped[str | None] = mapped_column(String(20), nullable=True)
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
        seq_no: OCPP's own per-transaction sequence counter, nullable - a
            row written before this column existed has no truthful value,
            and 0 would collide with a real ``seqNo`` of 0. Captured so
            ordering/duplicate detection become possible later
            (`deferred.md` item 27); no uniqueness is enforced on it yet.
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
    seq_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    __table_args__ = (
        Index(
            "ix_charging_session_events_session_time",
            "session_id",
            "event_occurred_at",
            "event_id",
        ),
    )


class ChargingSessionMeasurementModel(Base):
    """One measurement of a session, stored as a hypertable.

    Holds every measurand a charger reports during a session — the cumulative
    energy register that drives the session total, and any other reading —
    for both OCPP protocols. Normalization (measurand filtering, unit and
    multiplier conversion) is owned by the OCPP adapter; this table only ever
    stores the canonical result, never the raw pre-normalization payload (see
    the raw OCPP message log for that).

    Attributes:
        measurement_id: Internal UUID of the measurement.
        sampled_at: The time of measurement; also the time partitioning key.
        session_id: The UUID of the session aggregate that owns the sample.
        measurand: What was measured, as an OCPP measurand name
            (``Energy.Active.Import.Register``, ``SoC``, ``Power.Active.Import``…).
            Vendor-specific names are stored as sent.
        value: The reading. For the energy register it is canonical Wh; every
            other measurand keeps the value as sent, in ``unit``.
        unit: The unit of ``value`` (``Wh`` for the energy register), nullable.
        context: OCPP reading context (``Sample.Periodic``,
            ``Transaction.End``…), nullable.
        phase: Electrical phase the value refers to, nullable.
        location: Where it was measured (``EV``, ``Outlet``…), nullable.
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
    context: Mapped[str | None] = mapped_column(String(30), nullable=True)
    phase: Mapped[str | None] = mapped_column(String(10), nullable=True)
    location: Mapped[str | None] = mapped_column(String(20), nullable=True)
    __table_args__ = (
        Index(
            "ix_charging_measurements_session_measurand_time",
            "session_id",
            "measurand",
            "sampled_at",
        ),
    )
