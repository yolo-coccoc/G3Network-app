"""Repository bất đồng bộ cho aggregate session và hai bảng history.

Repository chỉ thực hiện truy vấn, khóa row và ``flush`` trong transaction hiện
tại. Nó không quyết định state machine, không commit/rollback và không import
model của bounded context ``charging_stations``.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.charging_sessions.models import (
    ChargingSession,
    ChargingSessionEvent,
    ChargingSessionMeterValue,
)
from app.domains.charging_sessions.types import SessionStatus


def utc_now() -> datetime:
    """Trả về thời điểm hiện tại với timezone UTC."""
    return datetime.now(timezone.utc)


async def get_session_by_transaction(
    db: AsyncSession,
    station_id: UUID,
    transaction_id: str,
    *,
    for_update: bool = False,
) -> ChargingSession | None:
    """Tìm session theo identity station/transaction và tùy chọn khóa row.

    Args:
        db: Async session do entry boundary sở hữu.
        station_id: UUID station đã được adapter resolve.
        transaction_id: Transaction identity từ trụ sạc.
        for_update: Khóa row để serialize các event cùng transaction.

    Returns:
        Aggregate phù hợp hoặc ``None`` nếu transaction chưa tồn tại.
    """
    statement = select(ChargingSession).where(
        ChargingSession.station_id == station_id,
        ChargingSession.ocpp_transaction_id == transaction_id,
    )
    if for_update:
        statement = statement.with_for_update()
    result = await db.execute(statement)
    return result.scalar_one_or_none()


async def get_session_by_id(
    db: AsyncSession, session_id: UUID, *, for_update: bool = False
) -> ChargingSession | None:
    """Tìm aggregate theo UUID nội bộ và tùy chọn khóa row."""
    statement = select(ChargingSession).where(ChargingSession.session_id == session_id)
    if for_update:
        statement = statement.with_for_update()
    result = await db.execute(statement)
    return result.scalar_one_or_none()


async def create_session(
    db: AsyncSession,
    *,
    station_id: UUID,
    evse_id: UUID,
    connector_id: UUID,
    transaction_id: str,
    status: SessionStatus,
    started_at: datetime,
    event_occurred_at: datetime,
    seq_no: int,
    meter_start_wh: Decimal | None,
) -> ChargingSession:
    """Tạo aggregate và flush để phát hiện FK/unique constraint.

    Hàm không commit/rollback; transaction boundary của worker hoặc HTTP caller
    vẫn sở hữu toàn bộ operation.
    """
    session = ChargingSession(
        station_id=station_id,
        evse_id=evse_id,
        connector_id=connector_id,
        ocpp_transaction_id=transaction_id,
        status=status,
        started_at=started_at,
        last_event_at=event_occurred_at,
        last_transaction_seq_no=seq_no,
        meter_start_wh=meter_start_wh,
        updated_at=utc_now(),
    )
    db.add(session)
    await db.flush()
    return session


async def find_event_by_logical_key(
    db: AsyncSession, session_id: UUID, seq_no: int
) -> list[ChargingSessionEvent]:
    """Lấy các event cùng logical key ``(session_id, seq_no)``.

    Database unique index còn chứa ``event_occurred_at`` vì hypertable yêu cầu
    partition key. Service dùng truy vấn rộng hơn để nhận diện duplicate khi
    cùng event được replay với timestamp partition khác nhau.
    """
    result = await db.execute(
        select(ChargingSessionEvent)
        .where(
            ChargingSessionEvent.session_id == session_id,
            ChargingSessionEvent.seq_no == seq_no,
        )
        .order_by(ChargingSessionEvent.event_occurred_at.asc())
    )
    return list(result.scalars().all())


async def insert_event(
    db: AsyncSession,
    *,
    session_id: UUID,
    event_occurred_at: datetime,
    event_type: object,
    seq_no: int,
    end_reason: object | None,
    charging_state: object | None,
    idempotency_key: str,
    received_at: datetime,
    sanitized_raw_payload: dict[str, object] | None,
) -> ChargingSessionEvent:
    """Append TransactionEvent history và flush constraint trong transaction."""
    event = ChargingSessionEvent(
        session_id=session_id,
        event_occurred_at=event_occurred_at,
        event_type=event_type,
        seq_no=seq_no,
        end_reason=end_reason,
        charging_state=charging_state,
        idempotency_key=idempotency_key,
        received_at=received_at,
        sanitized_raw_payload=sanitized_raw_payload,
    )
    db.add(event)
    await db.flush()
    return event


async def find_meter_by_logical_identity(
    db: AsyncSession,
    *,
    session_id: UUID,
    sampled_at: datetime,
    measurand: str,
    phase: str | None,
    context: str | None,
) -> ChargingSessionMeterValue | None:
    """Tìm sample theo identity transaction/time/measurand/phase/context."""
    conditions: list[ColumnElement[bool]] = [
        ChargingSessionMeterValue.session_id == session_id,
        ChargingSessionMeterValue.sampled_at == sampled_at,
        ChargingSessionMeterValue.measurand == measurand,
    ]
    conditions.append(
        ChargingSessionMeterValue.phase.is_(None)
        if phase is None
        else ChargingSessionMeterValue.phase == phase
    )
    conditions.append(
        ChargingSessionMeterValue.context.is_(None)
        if context is None
        else ChargingSessionMeterValue.context == context
    )
    result = await db.execute(
        select(ChargingSessionMeterValue).where(and_(*conditions)).limit(1)
    )
    return result.scalar_one_or_none()


async def get_latest_meter(
    db: AsyncSession, session_id: UUID, *, for_update: bool = False
) -> ChargingSessionMeterValue | None:
    """Lấy sample mới nhất theo thời điểm phát sinh."""
    statement = (
        select(ChargingSessionMeterValue)
        .where(ChargingSessionMeterValue.session_id == session_id)
        .order_by(
            ChargingSessionMeterValue.sampled_at.desc(),
            ChargingSessionMeterValue.meter_value_id.desc(),
        )
        .limit(1)
    )
    if for_update:
        statement = statement.with_for_update()
    result = await db.execute(statement)
    return result.scalar_one_or_none()


async def insert_meter_value(
    db: AsyncSession,
    *,
    session_id: UUID,
    sampled_at: datetime,
    measurand: str,
    phase: str | None,
    context: str | None,
    source_value: Decimal,
    source_unit: str,
    value_wh: Decimal,
    seq_no: int | None,
    sample_idempotency_key: str,
    received_at: datetime,
    sanitized_raw_payload: dict[str, object] | None,
) -> ChargingSessionMeterValue:
    """Append một meter sample và flush constraint trong transaction."""
    meter = ChargingSessionMeterValue(
        session_id=session_id,
        sampled_at=sampled_at,
        measurand=measurand,
        phase=phase,
        context=context,
        source_value=source_value,
        source_unit=source_unit,
        value_wh=value_wh,
        seq_no=seq_no,
        sample_idempotency_key=sample_idempotency_key,
        received_at=received_at,
        sanitized_raw_payload=sanitized_raw_payload,
    )
    db.add(meter)
    await db.flush()
    return meter


async def list_interruptible_sessions(
    db: AsyncSession, station_id: UUID
) -> list[ChargingSession]:
    """Lấy session chưa terminal của station và khóa để cập nhật atomic."""
    result = await db.execute(
        select(ChargingSession)
        .where(
            ChargingSession.station_id == station_id,
            ChargingSession.status.in_(
                [SessionStatus.PENDING, SessionStatus.ACTIVE, SessionStatus.ENDING]
            ),
        )
        .with_for_update()
    )
    return list(result.scalars().all())
