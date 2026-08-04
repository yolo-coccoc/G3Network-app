"""Repository async tối thiểu cho charging session happy path.

Repository chỉ truy vấn, tạo và flush aggregate/history; không commit hoặc
rollback transaction.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.charging_sessions.models import (
    ChargingSession,
    ChargingSessionEvent,
    ChargingSessionMeterValue,
)
from app.domains.charging_sessions.types import SessionEventType, SessionStatus


def utc_now() -> datetime:
    """Lấy thời điểm UTC dùng khi cập nhật aggregate phiên.

    Returns:
        Thời điểm hiện tại dưới dạng ``datetime`` có timezone UTC.
    """
    return datetime.now(timezone.utc)


async def get_session_by_transaction(
    db: AsyncSession,
    station_id: UUID,
    transaction_id: str,
) -> ChargingSession | None:
    """Tìm aggregate theo cặp station và OCPP transaction identity.

    Args:
        db: Async session do entry boundary sở hữu.
        station_id: UUID station phát sinh transaction.
        transaction_id: Identity transaction do trụ cấp.

    Returns:
        Aggregate phù hợp hoặc ``None`` nếu chưa có.
    """
    # Reconnect, unknown-transaction và duplicate resolution là contract
    # production bị hoãn; repository chỉ cung cấp lookup nguyên thủy cho MVP.
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
    """Tìm aggregate theo UUID nội bộ.

    Args:
        db: Async session do entry boundary sở hữu.
        session_id: UUID aggregate cần truy vấn.

    Returns:
        Aggregate phù hợp hoặc ``None`` nếu không tồn tại.
    """
    result = await db.execute(
        select(ChargingSession).where(ChargingSession.session_id == session_id)
    )
    return result.scalar_one_or_none()


async def list_sessions(
    db: AsyncSession,
    *,
    offset: int,
    limit: int,
) -> list[ChargingSession]:
    """Lấy danh sách aggregate session mới nhất trước.

    Args:
        db: Async session do entry boundary sở hữu.
        offset: Số session bỏ qua.
        limit: Số session tối đa trả về.

    Returns:
        Các session được sắp xếp ổn định theo thời điểm tạo giảm dần và UUID
        giảm dần để dễ tìm session vừa chạy simulator.
    """
    result = await db.execute(
        select(ChargingSession)
        .order_by(
            ChargingSession.created_at.desc(),
            ChargingSession.session_id.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_sessions(db: AsyncSession) -> int:
    """Đếm tổng số aggregate session.

    Args:
        db: Async session do entry boundary sở hữu.

    Returns:
        Tổng số session trong database.
    """
    result = await db.execute(select(func.count(ChargingSession.session_id)))
    return int(result.scalar() or 0)


async def list_session_events(
    db: AsyncSession,
    session_id: UUID,
    *,
    offset: int,
    limit: int,
) -> list[ChargingSessionEvent]:
    """Lấy lifecycle event của một session theo thứ tự thời gian tăng dần.

    Args:
        db: Async session hiện tại.
        session_id: UUID session cần truy vấn.
        offset: Số event bỏ qua.
        limit: Số event tối đa trả về.

    Returns:
        Event history đã phân trang ổn định.
    """
    result = await db.execute(
        select(ChargingSessionEvent)
        .where(ChargingSessionEvent.session_id == session_id)
        .order_by(
            ChargingSessionEvent.event_occurred_at.asc(),
            ChargingSessionEvent.event_id.asc(),
        )
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_session_events(db: AsyncSession, session_id: UUID) -> int:
    """Đếm lifecycle event của một session.

    Args:
        db: Async session hiện tại.
        session_id: UUID session cần đếm event.

    Returns:
        Tổng số event của session.
    """
    result = await db.execute(
        select(func.count(ChargingSessionEvent.event_id)).where(
            ChargingSessionEvent.session_id == session_id
        )
    )
    return int(result.scalar() or 0)


async def list_session_meter_values(
    db: AsyncSession,
    session_id: UUID,
    *,
    offset: int,
    limit: int,
) -> list[ChargingSessionMeterValue]:
    """Lấy meter sample của session theo thứ tự thời gian tăng dần.

    Args:
        db: Async session hiện tại.
        session_id: UUID session cần truy vấn.
        offset: Số sample bỏ qua.
        limit: Số sample tối đa trả về.

    Returns:
        Meter history đã phân trang ổn định.
    """
    result = await db.execute(
        select(ChargingSessionMeterValue)
        .where(ChargingSessionMeterValue.session_id == session_id)
        .order_by(
            ChargingSessionMeterValue.sampled_at.asc(),
            ChargingSessionMeterValue.meter_value_id.asc(),
        )
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_session_meter_values(db: AsyncSession, session_id: UUID) -> int:
    """Đếm meter sample của một session.

    Args:
        db: Async session hiện tại.
        session_id: UUID session cần đếm sample.

    Returns:
        Tổng số meter sample của session.
    """
    result = await db.execute(
        select(func.count(ChargingSessionMeterValue.meter_value_id)).where(
            ChargingSessionMeterValue.session_id == session_id
        )
    )
    return int(result.scalar() or 0)


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
    """Tạo session active và flush constraint trong transaction hiện tại.

    Args:
        db: Async session hiện tại; repository không commit transaction.
        station_id: UUID station sở hữu transaction.
        evse_id: UUID EVSE sở hữu transaction.
        connector_id: UUID connector đang cấp điện.
        transaction_id: OCPP transaction identity đã được service chuẩn hóa.
        started_at: Thời điểm ``Started`` đã normalize về UTC.
        meter_start_wh: Meter đầu phiên, nullable nếu payload không có.

    Returns:
        Aggregate active vừa được thêm vào session.

    Side Effects:
        Thêm record ORM và gọi ``flush`` để lấy UUID/phát hiện constraint.
    """
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
    """Append một TransactionEvent history và flush record.

    Args:
        db: Async session hiện tại; repository không commit transaction.
        session_id: UUID aggregate sở hữu event.
        event_occurred_at: Thời điểm event đã normalize về UTC.
        event_type: Loại TransactionEvent canonical.

    Returns:
        Event ORM vừa được thêm.

    Side Effects:
        Thêm history record và gọi ``flush`` trong transaction hiện tại.
    """
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
    """Append một energy sample canonical Wh và flush record.

    Args:
        db: Async session hiện tại; repository không commit transaction.
        session_id: UUID aggregate sở hữu sample.
        sampled_at: Thời điểm đo đã normalize về UTC.
        value_wh: Giá trị năng lượng không âm theo Wh.

    Returns:
        Meter sample ORM vừa được thêm.

    Side Effects:
        Thêm sample record và gọi ``flush`` trong transaction hiện tại.
    """
    meter = ChargingSessionMeterValue(
        session_id=session_id,
        sampled_at=sampled_at,
        value_wh=value_wh,
    )
    db.add(meter)
    await db.flush()
    return meter
