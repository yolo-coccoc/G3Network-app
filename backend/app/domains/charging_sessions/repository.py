"""Repository async tối thiểu cho charging session happy path.

Repository chỉ truy vấn, tạo và flush aggregate/history; không commit hoặc
rollback transaction.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.charging_sessions.models import (
    ChargingSession,
    ChargingSessionEvent,
    ChargingSessionMeterValue,
)
from app.domains.charging_sessions.types import SessionEventType, SessionStatus


def utc_now() -> datetime:
    """Trả về thời điểm hiện tại với timezone UTC."""
    return datetime.now(timezone.utc)


async def get_session_by_transaction(
    db: AsyncSession,
    station_id: UUID,
    transaction_id: str,
) -> ChargingSession | None:
    """Tìm session theo station và transaction identity."""
    result = await db.execute(
        select(ChargingSession).where(
            ChargingSession.station_id == station_id,
            ChargingSession.ocpp_transaction_id == transaction_id,
        )
    )
    return result.scalar_one_or_none()


async def get_session_by_id(
    db: AsyncSession, session_id: UUID
) -> ChargingSession | None:
    """Tìm aggregate theo UUID nội bộ."""
    result = await db.execute(
        select(ChargingSession).where(ChargingSession.session_id == session_id)
    )
    return result.scalar_one_or_none()


async def create_session(
    db: AsyncSession,
    *,
    station_id: UUID,
    evse_id: UUID,
    connector_id: UUID,
    transaction_id: str,
    started_at: datetime,
    meter_start_wh: Decimal | None,
) -> ChargingSession:
    """Tạo session active và flush constraint trong transaction hiện tại."""
    session = ChargingSession(
        station_id=station_id,
        evse_id=evse_id,
        connector_id=connector_id,
        ocpp_transaction_id=transaction_id,
        status=SessionStatus.ACTIVE,
        started_at=started_at,
        meter_start_wh=meter_start_wh,
        updated_at=utc_now(),
    )
    db.add(session)
    await db.flush()
    return session


async def insert_event(
    db: AsyncSession,
    *,
    session_id: UUID,
    event_occurred_at: datetime,
    event_type: SessionEventType,
) -> ChargingSessionEvent:
    """Append một TransactionEvent history và flush record."""
    event = ChargingSessionEvent(
        session_id=session_id,
        event_occurred_at=event_occurred_at,
        event_type=event_type,
    )
    db.add(event)
    await db.flush()
    return event


async def insert_meter_value(
    db: AsyncSession,
    *,
    session_id: UUID,
    sampled_at: datetime,
    value_wh: Decimal,
) -> ChargingSessionMeterValue:
    """Append một energy sample canonical Wh và flush record."""
    meter = ChargingSessionMeterValue(
        session_id=session_id,
        sampled_at=sampled_at,
        value_wh=value_wh,
    )
    db.add(meter)
    await db.flush()
    return meter
