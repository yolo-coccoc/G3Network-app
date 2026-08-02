"""SQLAlchemy models tối thiểu cho lifecycle phiên sạc happy path.

Module chỉ lưu aggregate session, TransactionEvent history và energy samples
canonical Wh của MVP lý tưởng.
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
    """Trả về thời điểm hiện tại với timezone UTC."""
    return datetime.now(timezone.utc)


def enum_values(enum_type: type[object]) -> list[str]:
    """Lấy value của enum để PostgreSQL lưu đúng contract public."""
    return [member.value for member in enum_type]  # type: ignore[attr-defined]


class ChargingSession(Base):
    """Aggregate của một OCPP transaction trong happy path.

    Attributes:
        session_id: UUID nội bộ.
        station_id: Station sở hữu transaction.
        evse_id: EVSE sở hữu transaction.
        connector_id: Connector đang sạc.
        ocpp_transaction_id: Transaction identity do trụ cấp.
        status: Chỉ active hoặc completed trong MVP.
        started_at: Thời điểm Started.
        ended_at: Thời điểm Ended, nullable khi đang active.
        meter_start_wh: Meter đầu phiên.
        meter_end_wh: Meter mới nhất/đầu cuối.
        energy_delivered_wh: Hiệu giữa meter cuối và meter đầu.
        created_at: Thời điểm tạo record.
        updated_at: Thời điểm cập nhật record.
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


class ChargingSessionEvent(Base):
    """History tối thiểu của TransactionEvent dưới dạng hypertable."""

    __tablename__ = "charging_session_events"

    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
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


class ChargingSessionMeterValue(Base):
    """Energy sample canonical Wh của session dưới dạng hypertable."""

    __tablename__ = "charging_session_meter_values"

    meter_value_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
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
