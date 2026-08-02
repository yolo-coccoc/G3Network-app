"""SQLAlchemy models tối thiểu cho lifecycle phiên sạc happy path.

Module chỉ lưu aggregate session, TransactionEvent history và energy samples.
Các field phục vụ interruption, reconciliation, ordering, idempotency và raw
payload của planner cũ được comment rõ trong model và loại khỏi migration mới.
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

    # Legacy fields intentionally disabled for the ideal MVP. They tracked
    # out-of-order/reconciliation/interruption behavior and remain documented
    # in backend-charging.md and future.md instead of being silently restored.
    # last_event_at: Mapped[datetime | None]
    # last_meter_at: Mapped[datetime | None]
    # last_transaction_seq_no: Mapped[int | None]
    # reconciliation_status: Mapped[ReconciliationStatus]
    # reconciliation_error: Mapped[str | None]

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

    # Legacy columns intentionally disabled: seq_no, end_reason, charging_state,
    # idempotency_key, received_at và sanitized_raw_payload.
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

    # Legacy source fields intentionally disabled: measurand, phase, context,
    # source_value, source_unit, seq_no, sample_idempotency_key, received_at và
    # sanitized_raw_payload. Happy path chỉ có một energy register tính bằng Wh.
    __table_args__ = (
        Index(
            "ix_charging_meter_session_sampled",
            "session_id",
            "sampled_at",
            "meter_value_id",
        ),
    )


# ---------------------------------------------------------------------------
# LEGACY IMPLEMENTATION (COMMENTED OUT FOR THE IDEAL MVP)
# ---------------------------------------------------------------------------
# """SQLAlchemy models cho aggregate phiên sạc và time-series history.
#
# Module chỉ sở hữu dữ liệu session đã được chuẩn hóa từ boundary công khai của
# domain. Nó không tham chiếu model của ``charging_stations`` để giữ dependency
# một chiều như planner đã chốt.
# """
#
# from datetime import datetime, timezone
# from decimal import Decimal
# from uuid import UUID, uuid4
#
# from sqlalchemy import (
#     CheckConstraint,
#     DateTime,
# )
# from sqlalchemy import Enum as SQLEnum
# from sqlalchemy import (
#     ForeignKey,
#     Index,
#     Integer,
#     Numeric,
#     String,
#     Text,
#     UniqueConstraint,
#     desc,
# )
# from sqlalchemy.dialects.postgresql import JSONB
# from sqlalchemy.dialects.postgresql import UUID as PG_UUID
# from sqlalchemy.orm import Mapped, mapped_column
#
# from app.domains.charging_sessions.types import (
#     ChargingState,
#     EndReason,
#     ReconciliationStatus,
#     SessionEventType,
#     SessionStatus,
# )
# from app.libs.db.base import Base
#
#
# def utc_now() -> datetime:
#     """Trả về thời điểm hiện tại với timezone UTC."""
#     return datetime.now(timezone.utc)
#
#
# def enum_values(enum_type: type[object]) -> list[str]:
#     """Lấy value của enum để database lưu đúng contract public.
#
#     Args:
#         enum_type: Enum class được SQLAlchemy truyền vào.
#
#     Returns:
#         Danh sách value theo thứ tự khai báo.
#     """
#     return [member.value for member in enum_type]  # type: ignore[attr-defined]
#
#
# class ChargingSession(Base):
#     """Aggregate lifecycle của một transaction OCPP.
#
#     Attributes:
#         session_id: UUID nội bộ của session.
#         station_id: UUID station đã resolve ở adapter.
#         evse_id: UUID EVSE đã resolve ở adapter.
#         connector_id: UUID connector đã resolve ở adapter.
#         ocpp_transaction_id: Transaction identity do trụ cấp.
#         status: Trạng thái vận hành hiện tại.
#         started_at: Thời điểm Started đầu tiên, nullable trước khi được set.
#         ended_at: Thời điểm kết thúc, nullable.
#         last_event_at: Event time lớn nhất đã nhận theo ordering.
#         last_meter_at: Meter time lớn nhất đã nhận theo ordering.
#         last_transaction_seq_no: Sequence lớn nhất đã xử lý.
#         meter_start_wh: Meter đầu phiên theo Wh.
#         meter_end_wh: Meter cuối phiên theo Wh.
#         energy_delivered_wh: Năng lượng đối soát không âm theo Wh.
#         reconciliation_status: Trạng thái đối soát kỹ thuật.
#         reconciliation_error: Mô tả lỗi kỹ thuật, nullable.
#         created_at: Thời điểm tạo aggregate.
#         updated_at: Thời điểm cập nhật aggregate.
#     """
#
#     __tablename__ = "charging_sessions"
#
#     session_id: Mapped[UUID] = mapped_column(
#         PG_UUID(as_uuid=True), primary_key=True, default=uuid4
#     )
#     station_id: Mapped[UUID] = mapped_column(
#         PG_UUID(as_uuid=True),
#         ForeignKey("charging_stations.station_id", ondelete="RESTRICT"),
#         nullable=False,
#     )
#     evse_id: Mapped[UUID] = mapped_column(
#         PG_UUID(as_uuid=True),
#         ForeignKey("charging_evses.evse_id", ondelete="RESTRICT"),
#         nullable=False,
#     )
#     connector_id: Mapped[UUID] = mapped_column(
#         PG_UUID(as_uuid=True),
#         ForeignKey("charging_connectors.connector_id", ondelete="RESTRICT"),
#         nullable=False,
#     )
#     ocpp_transaction_id: Mapped[str] = mapped_column(String(255), nullable=False)
#     status: Mapped[SessionStatus] = mapped_column(
#         SQLEnum(
#             SessionStatus, name="chargingsessionstatus", values_callable=enum_values
#         ),
#         nullable=False,
#         default=SessionStatus.PENDING,
#     )
#     started_at: Mapped[datetime | None] = mapped_column(
#         DateTime(timezone=True), nullable=True
#     )
#     ended_at: Mapped[datetime | None] = mapped_column(
#         DateTime(timezone=True), nullable=True
#     )
#     last_event_at: Mapped[datetime | None] = mapped_column(
#         DateTime(timezone=True), nullable=True
#     )
#     last_meter_at: Mapped[datetime | None] = mapped_column(
#         DateTime(timezone=True), nullable=True
#     )
#     last_transaction_seq_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
#     meter_start_wh: Mapped[Decimal | None] = mapped_column(
#         Numeric(24, 3), nullable=True
#     )
#     meter_end_wh: Mapped[Decimal | None] = mapped_column(Numeric(24, 3), nullable=True)
#     energy_delivered_wh: Mapped[Decimal | None] = mapped_column(
#         Numeric(24, 3), nullable=True
#     )
#     reconciliation_status: Mapped[ReconciliationStatus] = mapped_column(
#         SQLEnum(
#             ReconciliationStatus,
#             name="chargingreconciliationstatus",
#             values_callable=enum_values,
#         ),
#         nullable=False,
#         default=ReconciliationStatus.PENDING,
#     )
#     reconciliation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
#     created_at: Mapped[datetime] = mapped_column(
#         DateTime(timezone=True), nullable=False, default=utc_now
#     )
#     updated_at: Mapped[datetime] = mapped_column(
#         DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
#     )
#
#     __table_args__ = (
#         UniqueConstraint(
#             "station_id",
#             "ocpp_transaction_id",
#             name="uq_charging_sessions_station_transaction",
#         ),
#         CheckConstraint(
#             "last_transaction_seq_no IS NULL OR last_transaction_seq_no >= 0",
#             name="ck_charging_sessions_seq_non_negative",
#         ),
#         CheckConstraint(
#             "meter_start_wh IS NULL OR meter_start_wh >= 0",
#             name="ck_charging_sessions_meter_start_non_negative",
#         ),
#         CheckConstraint(
#             "meter_end_wh IS NULL OR meter_end_wh >= 0",
#             name="ck_charging_sessions_meter_end_non_negative",
#         ),
#         CheckConstraint(
#             "energy_delivered_wh IS NULL OR energy_delivered_wh >= 0",
#             name="ck_charging_sessions_energy_non_negative",
#         ),
#         Index("ix_charging_sessions_status_updated", "status", desc("updated_at")),
#         Index("ix_charging_sessions_station_status", "station_id", "status"),
#         Index("ix_charging_sessions_evse_status", "evse_id", "status"),
#         Index("ix_charging_sessions_connector_status", "connector_id", "status"),
#         Index("ix_charging_sessions_started_at", "started_at"),
#         Index("ix_charging_sessions_ended_at", "ended_at"),
#     )
#
#
# class ChargingSessionEvent(Base):
#     """History TransactionEvent của một session dưới dạng hypertable.
#
#     Attributes:
#         event_id: UUID nội bộ của event history.
#         event_occurred_at: Thời điểm event phát sinh và partition key.
#         session_id: Session sở hữu event.
#         event_type: Loại lifecycle event.
#         seq_no: Sequence logic không âm.
#         end_reason: Lý do Ended/Interrupted, nullable.
#         charging_state: State chuẩn hóa, nullable.
#         idempotency_key: Logical identity do adapter tạo.
#         received_at: Thời điểm backend nhận event.
#         sanitized_raw_payload: Payload đã redact, nullable.
#     """
#
#     __tablename__ = "charging_session_events"
#
#     event_id: Mapped[UUID] = mapped_column(
#         PG_UUID(as_uuid=True), primary_key=True, default=uuid4
#     )
#     event_occurred_at: Mapped[datetime] = mapped_column(
#         DateTime(timezone=True), primary_key=True, nullable=False
#     )
#     session_id: Mapped[UUID] = mapped_column(
#         PG_UUID(as_uuid=True),
#         ForeignKey("charging_sessions.session_id", ondelete="RESTRICT"),
#         nullable=False,
#     )
#     event_type: Mapped[SessionEventType] = mapped_column(
#         SQLEnum(
#             SessionEventType,
#             name="chargingsessioneventtype",
#             values_callable=enum_values,
#         ),
#         nullable=False,
#     )
#     seq_no: Mapped[int] = mapped_column(Integer, nullable=False)
#     end_reason: Mapped[EndReason | None] = mapped_column(
#         SQLEnum(EndReason, name="chargingendreason", values_callable=enum_values),
#         nullable=True,
#     )
#     charging_state: Mapped[ChargingState | None] = mapped_column(
#         SQLEnum(ChargingState, name="chargingstate", values_callable=enum_values),
#         nullable=True,
#     )
#     idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
#     received_at: Mapped[datetime] = mapped_column(
#         DateTime(timezone=True), nullable=False
#     )
#     sanitized_raw_payload: Mapped[dict[str, object] | None] = mapped_column(
#         JSONB, nullable=True
#     )
#
#     __table_args__ = (
#         CheckConstraint(
#             "seq_no >= 0", name="ck_charging_session_events_seq_non_negative"
#         ),
#         UniqueConstraint(
#             "session_id",
#             "seq_no",
#             "event_occurred_at",
#             name="uq_charging_session_events_session_seq_time",
#         ),
#         Index(
#             "ix_charging_session_events_session_time",
#             "session_id",
#             "event_occurred_at",
#             "event_id",
#         ),
#         Index(
#             "ix_charging_session_events_session_seq_time",
#             "session_id",
#             "seq_no",
#             "event_occurred_at",
#         ),
#     )
#
#
# class ChargingSessionMeterValue(Base):
#     """Energy meter sample của session dưới dạng hypertable.
#
#     Attributes:
#         meter_value_id: UUID nội bộ của sample.
#         sampled_at: Thời điểm sample phát sinh và partition key.
#         session_id: Session sở hữu sample.
#         measurand: Measurand năng lượng đã chuẩn hóa.
#         phase: Phase nguồn, nullable.
#         context: Context sample, nullable.
#         source_value: Giá trị gốc dạng Decimal.
#         source_unit: Đơn vị gốc.
#         value_wh: Giá trị đã normalize về Wh, không âm.
#         seq_no: Sequence MeterValues, nullable.
#         sample_idempotency_key: Logical identity của sample.
#         received_at: Thời điểm backend nhận sample.
#         sanitized_raw_payload: Payload đã redact, nullable.
#     """
#
#     __tablename__ = "charging_session_meter_values"
#
#     meter_value_id: Mapped[UUID] = mapped_column(
#         PG_UUID(as_uuid=True), primary_key=True, default=uuid4
#     )
#     sampled_at: Mapped[datetime] = mapped_column(
#         DateTime(timezone=True), primary_key=True, nullable=False
#     )
#     session_id: Mapped[UUID] = mapped_column(
#         PG_UUID(as_uuid=True),
#         ForeignKey("charging_sessions.session_id", ondelete="RESTRICT"),
#         nullable=False,
#     )
#     measurand: Mapped[str] = mapped_column(String(100), nullable=False)
#     phase: Mapped[str | None] = mapped_column(String(50), nullable=True)
#     context: Mapped[str | None] = mapped_column(String(50), nullable=True)
#     source_value: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
#     source_unit: Mapped[str] = mapped_column(String(20), nullable=False)
#     value_wh: Mapped[Decimal] = mapped_column(Numeric(24, 3), nullable=False)
#     seq_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
#     sample_idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
#     received_at: Mapped[datetime] = mapped_column(
#         DateTime(timezone=True), nullable=False
#     )
#     sanitized_raw_payload: Mapped[dict[str, object] | None] = mapped_column(
#         JSONB, nullable=True
#     )
#
#     __table_args__ = (
#         CheckConstraint(
#             "value_wh >= 0", name="ck_charging_meter_value_wh_non_negative"
#         ),
#         CheckConstraint(
#             "seq_no IS NULL OR seq_no >= 0", name="ck_charging_meter_seq_non_negative"
#         ),
#         UniqueConstraint(
#             "session_id",
#             "sample_idempotency_key",
#             "sampled_at",
#             name="uq_charging_meter_session_sample_time",
#         ),
#         Index(
#             "ix_charging_meter_session_sampled",
#             "session_id",
#             "sampled_at",
#             "meter_value_id",
#         ),
#         Index(
#             "ix_charging_meter_session_measurand_sampled",
#             "session_id",
#             "measurand",
#             "sampled_at",
#         ),
#     )
#
