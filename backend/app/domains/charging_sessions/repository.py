"""Minimal async repository for the charging session happy path.

The repository only queries, creates and flushes the aggregate/history; it
does not commit or roll back the transaction.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.charging_sessions.models import (
    ChargingSessionEventModel,
    ChargingSessionMeterValueModel,
    ChargingSessionModel,
)
from app.domains.charging_sessions.types import SessionEventType, SessionStatus


def utc_now() -> datetime:
    """Get the UTC time used when updating a session aggregate.

    Returns:
        The current time as a timezone-aware UTC ``datetime``.
    """
    return datetime.now(timezone.utc)


async def get_session_by_transaction(
    db: AsyncSession,
    station_id: UUID,
    transaction_id: str,
) -> ChargingSessionModel | None:
    """Find the aggregate by station and OCPP transaction identity pair.

    Args:
        db: The async session owned by the entry boundary.
        station_id: UUID of the station that raised the transaction.
        transaction_id: The transaction identity issued by the station.

    Returns:
        The matching aggregate, or ``None`` if there isn't one yet.
    """
    # Reconnect, unknown-transaction and duplicate resolution are production
    # contracts deferred for later; the repository only provides a primitive
    # lookup for the MVP.
    result = await db.execute(
        select(ChargingSessionModel).where(
            ChargingSessionModel.station_id == station_id,
            ChargingSessionModel.ocpp_transaction_id == transaction_id,
        )
    )
    return result.scalar_one_or_none()


async def get_session_by_id(
    db: AsyncSession, session_id: UUID
) -> ChargingSessionModel | None:
    """Find the aggregate by internal UUID.

    Args:
        db: The async session owned by the entry boundary.
        session_id: UUID of the aggregate to query.

    Returns:
        The matching aggregate, or ``None`` if it does not exist.
    """
    result = await db.execute(
        select(ChargingSessionModel).where(
            ChargingSessionModel.session_id == session_id
        )
    )
    return result.scalar_one_or_none()


async def list_charging_sessions(
    db: AsyncSession,
    *,
    offset: int,
    limit: int,
) -> list[ChargingSessionModel]:
    """Get the list of session aggregates, newest first.

    Args:
        db: The async session owned by the entry boundary.
        offset: The number of sessions to skip.
        limit: The maximum number of sessions to return.

    Returns:
        Sessions sorted stably by descending creation time and descending
        UUID, to make it easy to find a session just run by the simulator.
    """
    result = await db.execute(
        select(ChargingSessionModel)
        .order_by(
            ChargingSessionModel.created_at.desc(),
            ChargingSessionModel.session_id.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_sessions(db: AsyncSession) -> int:
    """Count the total number of session aggregates.

    Args:
        db: The async session owned by the entry boundary.

    Returns:
        The total number of sessions in the database.
    """
    result = await db.execute(select(func.count(ChargingSessionModel.session_id)))
    return int(result.scalar() or 0)


async def list_charging_session_events(
    db: AsyncSession,
    session_id: UUID,
    *,
    offset: int,
    limit: int,
) -> list[ChargingSessionEventModel]:
    """Get the lifecycle events of a session in ascending time order.

    Args:
        db: The current async session.
        session_id: UUID of the session to query.
        offset: The number of events to skip.
        limit: The maximum number of events to return.

    Returns:
        The event history, stably paginated.
    """
    result = await db.execute(
        select(ChargingSessionEventModel)
        .where(ChargingSessionEventModel.session_id == session_id)
        .order_by(
            ChargingSessionEventModel.event_occurred_at.asc(),
            ChargingSessionEventModel.event_id.asc(),
        )
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_session_events(db: AsyncSession, session_id: UUID) -> int:
    """Count the lifecycle events of a session.

    Args:
        db: The current async session.
        session_id: UUID of the session whose events to count.

    Returns:
        The total number of events for the session.
    """
    result = await db.execute(
        select(func.count(ChargingSessionEventModel.event_id)).where(
            ChargingSessionEventModel.session_id == session_id
        )
    )
    return int(result.scalar() or 0)


async def list_charging_session_meter_values(
    db: AsyncSession,
    session_id: UUID,
    *,
    offset: int,
    limit: int,
) -> list[ChargingSessionMeterValueModel]:
    """Get the meter samples of a session in ascending time order.

    Args:
        db: The current async session.
        session_id: UUID of the session to query.
        offset: The number of samples to skip.
        limit: The maximum number of samples to return.

    Returns:
        The meter history, stably paginated.
    """
    result = await db.execute(
        select(ChargingSessionMeterValueModel)
        .where(ChargingSessionMeterValueModel.session_id == session_id)
        .order_by(
            ChargingSessionMeterValueModel.sampled_at.asc(),
            ChargingSessionMeterValueModel.meter_value_id.asc(),
        )
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_session_meter_values(db: AsyncSession, session_id: UUID) -> int:
    """Count the meter samples of a session.

    Args:
        db: The current async session.
        session_id: UUID of the session whose samples to count.

    Returns:
        The total number of meter samples for the session.
    """
    result = await db.execute(
        select(func.count(ChargingSessionMeterValueModel.meter_value_id)).where(
            ChargingSessionMeterValueModel.session_id == session_id
        )
    )
    return int(result.scalar() or 0)


async def get_station_energy_summary(
    db: AsyncSession,
    *,
    station_id: UUID,
    start_time: datetime,
    end_time: datetime,
) -> tuple[Decimal, int]:
    """Sum delivered energy and count completed sessions for a station (F-C5).

    Args:
        db: The async session owned by the entry boundary.
        station_id: UUID of the station to aggregate over.
        start_time: Inclusive lower bound on ``ended_at``, already
            normalized to UTC by the service.
        end_time: Inclusive upper bound on ``ended_at``, already normalized
            to UTC by the service.

    Returns:
        Tuple of ``(total_energy_wh, session_count)`` for completed
        sessions of the station that ended within the window; ``(Decimal(0),
        0)`` if none match. An unknown ``station_id`` legitimately returns
        the same zero result - this function doesn't check that the station
        exists.
    """
    result = await db.execute(
        select(
            func.coalesce(func.sum(ChargingSessionModel.energy_delivered_wh), 0),
            func.count(ChargingSessionModel.session_id),
        ).where(
            ChargingSessionModel.station_id == station_id,
            ChargingSessionModel.status == SessionStatus.COMPLETED,
            ChargingSessionModel.ended_at.is_not(None),
            ChargingSessionModel.ended_at >= start_time,
            ChargingSessionModel.ended_at <= end_time,
        )
    )
    total_energy_wh, session_count = result.one()
    return Decimal(total_energy_wh), int(session_count)


async def create_session(
    db: AsyncSession,
    *,
    station_id: UUID,
    evse_id: UUID,
    connector_id: UUID,
    transaction_id: str,
    started_at: datetime,
    meter_start_wh: Decimal | None,
) -> ChargingSessionModel:
    """Create an active session and flush constraints in the current transaction.

    Args:
        db: The current async session; the repository does not commit the
            transaction.
        station_id: UUID of the station that owns the transaction.
        evse_id: UUID of the EVSE that owns the transaction.
        connector_id: UUID of the connector currently delivering power.
        transaction_id: The OCPP transaction identity already normalized by
            the service.
        started_at: The ``Started`` time, already normalized to UTC.
        meter_start_wh: The meter reading at the start of the session,
            nullable if absent from the payload.

    Returns:
        The active aggregate just added to the session.

    Side Effects:
        Adds an ORM record and calls ``flush`` to obtain the UUID / detect
        constraint violations.
    """
    session = ChargingSessionModel(
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
    seq_no: int | None,
) -> ChargingSessionEventModel:
    """Append one TransactionEvent history record and flush it.

    Args:
        db: The current async session; the repository does not commit the
            transaction.
        session_id: UUID of the aggregate that owns the event.
        event_occurred_at: The event time, already normalized to UTC.
        event_type: The canonical TransactionEvent type.
        seq_no: OCPP's own sequence number, already validated by the
            service; nullable for callers that have none (F-B2).

    Returns:
        The ORM event just added.

    Side Effects:
        Adds a history record and calls ``flush`` in the current
        transaction.
    """
    event = ChargingSessionEventModel(
        session_id=session_id,
        event_occurred_at=event_occurred_at,
        event_type=event_type,
        seq_no=seq_no,
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
) -> ChargingSessionMeterValueModel:
    """Append one canonical Wh energy sample and flush it.

    Args:
        db: The current async session; the repository does not commit the
            transaction.
        session_id: UUID of the aggregate that owns the sample.
        sampled_at: The measurement time, already normalized to UTC.
        value_wh: The non-negative energy value in Wh.

    Returns:
        The ORM meter sample just added.

    Side Effects:
        Adds a sample record and calls ``flush`` in the current
        transaction.
    """
    meter = ChargingSessionMeterValueModel(
        session_id=session_id,
        sampled_at=sampled_at,
        value_wh=value_wh,
    )
    db.add(meter)
    await db.flush()
    return meter
