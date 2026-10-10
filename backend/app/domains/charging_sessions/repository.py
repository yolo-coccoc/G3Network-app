"""Async repository of the charging sessions and their measurements.

The repository only queries, creates and flushes rows; it holds no lifecycle
rule (those live in the service) and never commits or rolls back the
transaction. Besides the lifecycle writes it serves the monitoring reads: the
filtered session list, the per-session measurement lookups behind the session
summary (first/last/maximum value) and the per-session energy samples behind
the station energy series (F-C5).
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Sequence, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.charging_sessions.models import (
    ChargingSessionMeasurementModel,
    ChargingSessionModel,
)
from app.domains.charging_sessions.types import (
    ENERGY_ACTIVE_IMPORT_REGISTER,
    MEASUREMENT_LOCATION_OUTLET,
    ChargingSessionListFilter,
    SessionStatus,
)
from app.libs.common.clock import utc_now

# Integer transaction IDs for OCPP 1.6J, which requires the backend to assign
# them. The sequence is created by the ``0001_baseline_schema`` migration (it
# is not part of the model metadata); declared here only to call nextval().
_OCPP16_TRANSACTION_ID_SEQUENCE = Sequence("charging_ocpp16_transaction_id_seq")


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
    query_result = await db.execute(
        select(ChargingSessionModel).where(
            ChargingSessionModel.station_id == station_id,
            ChargingSessionModel.ocpp_transaction_id == transaction_id,
        )
    )
    return query_result.scalar_one_or_none()


async def find_pending_session_by_token(
    db: AsyncSession,
    station_id: UUID,
    id_token: str,
    *,
    created_after: datetime | None = None,
) -> ChargingSessionModel | None:
    """Find the PENDING session a charger's start message belongs to (CE-11).

    Args:
        db: The async session owned by the entry boundary.
        station_id: UUID of the charger that sent the start message.
        id_token: The token the message carries.
        created_after: Only a scan made after this time counts (the pending
            window); `None` means no limit.

    Returns:
        The PENDING session of that charger holding the token (the newest if
        several), or ``None`` when no scan issued it.
    """
    conditions: list[ColumnElement[bool]] = [
        ChargingSessionModel.station_id == station_id,
        ChargingSessionModel.id_token == id_token,
        ChargingSessionModel.status == SessionStatus.PENDING,
    ]
    if created_after is not None:
        conditions.append(ChargingSessionModel.created_at > created_after)
    query_result = await db.execute(
        select(ChargingSessionModel)
        .where(*conditions)
        .order_by(ChargingSessionModel.created_at.desc())
        .limit(1)
    )
    return query_result.scalar_one_or_none()


async def count_sessions_with_token(
    db: AsyncSession,
    station_id: UUID,
    id_token: str,
    *,
    pending_created_after: datetime,
) -> int:
    """Count the sessions of a charger that a presented token belongs to.

    A token belongs to a session that is still waiting for the charger (PENDING,
    scanned after ``pending_created_after``) or already running (ACTIVE, so the
    same token may be shown again to stop it).

    Args:
        db: The async session owned by the entry boundary.
        station_id: UUID of the charger that presented the token.
        id_token: The presented token.
        pending_created_after: Oldest scan time a PENDING session may have.

    Returns:
        The number of matching sessions.
    """
    query_result = await db.execute(
        select(func.count())
        .select_from(ChargingSessionModel)
        .where(
            ChargingSessionModel.station_id == station_id,
            ChargingSessionModel.id_token == id_token,
            or_(
                ChargingSessionModel.status == SessionStatus.ACTIVE,
                and_(
                    ChargingSessionModel.status == SessionStatus.PENDING,
                    ChargingSessionModel.created_at > pending_created_after,
                ),
            ),
        )
    )
    return int(query_result.scalar_one())


async def count_open_sessions_by_user(
    db: AsyncSession, user_id: UUID, *, pending_created_after: datetime
) -> int:
    """Count the open sessions a person started (ACTIVE, or a live PENDING scan).

    Args:
        db: The async session owned by the entry boundary.
        user_id: The scanning user.
        pending_created_after: Oldest scan time a PENDING session may have to
            still count as open.

    Returns:
        The number of open sessions of that user.
    """
    query_result = await db.execute(
        select(func.count())
        .select_from(ChargingSessionModel)
        .where(
            ChargingSessionModel.started_by == user_id,
            or_(
                ChargingSessionModel.status == SessionStatus.ACTIVE,
                and_(
                    ChargingSessionModel.status == SessionStatus.PENDING,
                    ChargingSessionModel.created_at > pending_created_after,
                ),
            ),
        )
    )
    return int(query_result.scalar_one())


async def abandon_pending_session(db: AsyncSession, session_id: UUID) -> bool:
    """Turn one PENDING session ABANDONED (a no-op for any other status).

    Args:
        db: The async session owned by the entry boundary.
        session_id: UUID of the session.

    Returns:
        ``True`` when a PENDING row was changed.

    Side Effects:
        One UPDATE; flushes, does not commit.
    """
    result = await db.execute(
        update(ChargingSessionModel)
        .where(
            ChargingSessionModel.session_id == session_id,
            ChargingSessionModel.status == SessionStatus.PENDING,
        )
        .values(status=SessionStatus.ABANDONED, updated_at=utc_now())
    )
    await db.flush()
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def abandon_pending_sessions_created_before(
    db: AsyncSession, created_before: datetime
) -> int:
    """Turn every PENDING session scanned before a time ABANDONED.

    Args:
        db: The async session owned by the entry boundary.
        created_before: Scans older than this have waited too long.

    Returns:
        The number of sessions changed.

    Side Effects:
        One UPDATE; flushes, does not commit.
    """
    result = await db.execute(
        update(ChargingSessionModel)
        .where(
            ChargingSessionModel.status == SessionStatus.PENDING,
            ChargingSessionModel.created_at < created_before,
        )
        .values(status=SessionStatus.ABANDONED, updated_at=utc_now())
    )
    await db.flush()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def get_session_by_id(
    db: AsyncSession,
    session_id: UUID,
    *,
    organization_id: UUID | None = None,
    started_by: UUID | None = None,
) -> ChargingSessionModel | None:
    """Find the aggregate by internal UUID.

    Args:
        db: The async session owned by the entry boundary.
        session_id: UUID of the aggregate to query.
        organization_id: Data scope: only a session paid by this organization
            is found; `None` means no restriction.
        started_by: Only a session started by this user is found; `None`
            means no restriction (a driver sees only their own scans).

    Returns:
        The matching aggregate, or ``None`` if it does not exist or is out of
        scope.
    """
    conditions: list[ColumnElement[bool]] = [
        ChargingSessionModel.session_id == session_id
    ]
    if organization_id is not None:
        conditions.append(ChargingSessionModel.organization_id == organization_id)
    if started_by is not None:
        conditions.append(ChargingSessionModel.started_by == started_by)
    query_result = await db.execute(select(ChargingSessionModel).where(*conditions))
    return query_result.scalar_one_or_none()


def _session_list_conditions(
    filters: ChargingSessionListFilter,
) -> list[ColumnElement[bool]]:
    """Build the WHERE conditions shared by ``list_sessions``/``count_sessions``.

    Args:
        filters: The optional filters, already validated (UTC bounds) by the
            service; a ``None`` field adds no condition.

    Returns:
        The conditions, so the list and its count can never drift apart.
    """
    conditions: list[ColumnElement[bool]] = []
    if filters.station_id is not None:
        conditions.append(ChargingSessionModel.station_id == filters.station_id)
    if filters.connector_id is not None:
        conditions.append(ChargingSessionModel.connector_id == filters.connector_id)
    if filters.organization_id is not None:
        conditions.append(
            ChargingSessionModel.organization_id == filters.organization_id
        )
    if filters.started_by is not None:
        conditions.append(ChargingSessionModel.started_by == filters.started_by)
    if filters.vehicle_id is not None:
        conditions.append(ChargingSessionModel.vehicle_id == filters.vehicle_id)
    if filters.status is not None:
        conditions.append(ChargingSessionModel.status == filters.status)
    if filters.started_from is not None:
        conditions.append(ChargingSessionModel.started_at >= filters.started_from)
    if filters.started_to is not None:
        conditions.append(ChargingSessionModel.started_at < filters.started_to)
    return conditions


async def list_sessions(
    db: AsyncSession,
    *,
    filters: ChargingSessionListFilter,
    offset: int,
    limit: int,
) -> list[ChargingSessionModel]:
    """Get the list of session aggregates matching the filters, newest first.

    Args:
        db: The async session owned by the entry boundary.
        filters: Optional station/connector/status/start-time filters.
        offset: The number of sessions to skip.
        limit: The maximum number of sessions to return.

    Returns:
        Sessions sorted stably by descending creation time and descending
        UUID, to make it easy to find a session just run by the simulator.
    """
    query_result = await db.execute(
        select(ChargingSessionModel)
        .where(*_session_list_conditions(filters))
        .order_by(
            ChargingSessionModel.created_at.desc(),
            ChargingSessionModel.session_id.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count_sessions(db: AsyncSession, filters: ChargingSessionListFilter) -> int:
    """Count the session aggregates matching the filters.

    Args:
        db: The async session owned by the entry boundary.
        filters: The same filters as ``list_sessions``.

    Returns:
        The number of matching sessions.
    """
    query_result = await db.execute(
        select(func.count(ChargingSessionModel.session_id)).where(
            *_session_list_conditions(filters)
        )
    )
    return int(query_result.scalar() or 0)


async def list_sessions_by_station_in_window(
    db: AsyncSession,
    station_id: UUID,
    *,
    start_time: datetime,
    end_time: datetime,
) -> list[ChargingSessionModel]:
    """Get a station's sessions that may have metered energy within a window.

    A session qualifies when it started before ``end_time`` and has not
    ended before ``start_time`` (an active session always qualifies once
    started). Used by the station energy series (F-C5).

    Args:
        db: The current async session.
        station_id: UUID of the station.
        start_time: Window start (UTC, inclusive).
        end_time: Window end (UTC, exclusive).

    Returns:
        The matching sessions ordered by start time, then UUID.
    """
    query_result = await db.execute(
        select(ChargingSessionModel)
        .where(
            ChargingSessionModel.station_id == station_id,
            ChargingSessionModel.started_at < end_time,
            or_(
                ChargingSessionModel.ended_at.is_(None),
                ChargingSessionModel.ended_at >= start_time,
            ),
        )
        .order_by(
            ChargingSessionModel.started_at.asc(),
            ChargingSessionModel.session_id.asc(),
        )
    )
    return list(query_result.scalars().all())


async def list_energy_samples_before(
    db: AsyncSession, session_id: UUID, *, end_time: datetime
) -> list[tuple[datetime, Decimal]]:
    """Get a session's energy-register samples taken before a point in time.

    Unpaginated on purpose: the energy series needs every sample before the
    window end, including the last one before the window start (the
    baseline of the first delta inside the window).

    Args:
        db: The current async session.
        session_id: UUID of the session.
        end_time: Exclusive upper bound on ``sampled_at`` (UTC).

    Returns:
        ``(sampled_at, value_wh)`` pairs in ascending time order.
    """
    query_result = await db.execute(
        select(
            ChargingSessionMeasurementModel.sampled_at,
            ChargingSessionMeasurementModel.value,
        )
        .where(
            ChargingSessionMeasurementModel.session_id == session_id,
            ChargingSessionMeasurementModel.measurand == ENERGY_ACTIVE_IMPORT_REGISTER,
            ChargingSessionMeasurementModel.measurement_location
            == MEASUREMENT_LOCATION_OUTLET,
            ChargingSessionMeasurementModel.sampled_at < end_time,
        )
        .order_by(
            ChargingSessionMeasurementModel.sampled_at.asc(),
            ChargingSessionMeasurementModel.measurement_id.asc(),
        )
    )
    return [(sampled_at, value) for sampled_at, value in query_result.all()]


async def find_first_measurement_value(
    db: AsyncSession, session_id: UUID, *, measurand: str
) -> Decimal | None:
    """Get the value of a session's earliest sample of one measurand.

    Args:
        db: The current async session.
        session_id: UUID of the session.
        measurand: The measurand name, e.g. ``SoC``.

    Returns:
        The earliest value, or ``None`` if the session has no such sample.
    """
    query_result = await db.execute(
        select(ChargingSessionMeasurementModel.value)
        .where(
            ChargingSessionMeasurementModel.session_id == session_id,
            ChargingSessionMeasurementModel.measurand == measurand,
        )
        .order_by(
            ChargingSessionMeasurementModel.sampled_at.asc(),
            ChargingSessionMeasurementModel.measurement_id.asc(),
        )
        .limit(1)
    )
    return query_result.scalar_one_or_none()


async def find_last_measurement_value(
    db: AsyncSession,
    session_id: UUID,
    *,
    measurand: str,
    measurement_location: str | None = None,
) -> Decimal | None:
    """Get the value of a session's latest sample of one measurand.

    Args:
        db: The current async session.
        session_id: UUID of the session.
        measurand: The measurand name, e.g. ``SoC``.
        measurement_location: Only samples measured there, or any location
            if ``None``.

    Returns:
        The latest value, or ``None`` if the session has no such sample.
    """
    statement = select(ChargingSessionMeasurementModel.value).where(
        ChargingSessionMeasurementModel.session_id == session_id,
        ChargingSessionMeasurementModel.measurand == measurand,
    )
    if measurement_location is not None:
        statement = statement.where(
            ChargingSessionMeasurementModel.measurement_location == measurement_location
        )
    query_result = await db.execute(
        statement.order_by(
            ChargingSessionMeasurementModel.sampled_at.desc(),
            ChargingSessionMeasurementModel.measurement_id.desc(),
        ).limit(1)
    )
    return query_result.scalar_one_or_none()


async def find_max_measurement_value(
    db: AsyncSession,
    session_id: UUID,
    *,
    measurand: str,
    measurement_location: str,
) -> Decimal | None:
    """Get the maximum value of one measurand of a session at one location.

    Values are stored in one fixed unit per measurand (CE-14), so no unit
    handling is needed here.

    Args:
        db: The current async session.
        session_id: UUID of the session.
        measurand: The measurand name, e.g. ``Power.Active.Import``.
        measurement_location: Only samples measured there, e.g. ``Outlet``.

    Returns:
        The maximum value, or ``None`` if the session has no such sample.
    """
    query_result = await db.execute(
        select(func.max(ChargingSessionMeasurementModel.value)).where(
            ChargingSessionMeasurementModel.session_id == session_id,
            ChargingSessionMeasurementModel.measurand == measurand,
            ChargingSessionMeasurementModel.measurement_location
            == measurement_location,
        )
    )
    return query_result.scalar_one_or_none()


async def list_energy_measurements(
    db: AsyncSession,
    session_id: UUID,
    *,
    offset: int,
    limit: int,
) -> list[ChargingSessionMeasurementModel]:
    """Get the energy-register samples of a session in ascending time order.

    The measurements table also holds other measurands and locations; this
    listing (the ``/meter-values`` view) only returns the energy register
    measured at the outlet (CE-14).

    Args:
        db: The current async session.
        session_id: UUID of the session to query.
        offset: The number of samples to skip.
        limit: The maximum number of samples to return.

    Returns:
        The energy history, stably paginated.
    """
    query_result = await db.execute(
        select(ChargingSessionMeasurementModel)
        .where(
            ChargingSessionMeasurementModel.session_id == session_id,
            ChargingSessionMeasurementModel.measurand == ENERGY_ACTIVE_IMPORT_REGISTER,
            ChargingSessionMeasurementModel.measurement_location
            == MEASUREMENT_LOCATION_OUTLET,
        )
        .order_by(
            ChargingSessionMeasurementModel.sampled_at.asc(),
            ChargingSessionMeasurementModel.measurement_id.asc(),
        )
        .offset(offset)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count_energy_measurements(db: AsyncSession, session_id: UUID) -> int:
    """Count the energy-register samples of a session.

    Args:
        db: The current async session.
        session_id: UUID of the session whose samples to count.

    Returns:
        The total number of energy-register samples for the session.
    """
    query_result = await db.execute(
        select(func.count(ChargingSessionMeasurementModel.measurement_id)).where(
            ChargingSessionMeasurementModel.session_id == session_id,
            ChargingSessionMeasurementModel.measurand == ENERGY_ACTIVE_IMPORT_REGISTER,
            ChargingSessionMeasurementModel.measurement_location
            == MEASUREMENT_LOCATION_OUTLET,
        )
    )
    return int(query_result.scalar() or 0)


async def get_station_energy_summary(
    db: AsyncSession,
    *,
    station_id: UUID,
    start_time: datetime,
    end_time: datetime,
) -> tuple[Decimal, int]:
    """Sum delivered energy and count completed sessions for a station (F-C5).

    The energy of a session is the charger's declared stop reading minus its
    start reading (CE-12); a completed session whose stop message carried no
    reading is left out.

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
    query_result = await db.execute(
        select(
            func.coalesce(
                func.sum(
                    ChargingSessionModel.meter_stop_wh
                    - ChargingSessionModel.meter_start_wh
                ),
                0,
            ),
            func.count(ChargingSessionModel.session_id),
        ).where(
            ChargingSessionModel.station_id == station_id,
            ChargingSessionModel.status == SessionStatus.COMPLETED,
            ChargingSessionModel.meter_stop_wh.is_not(None),
            ChargingSessionModel.ended_at.is_not(None),
            ChargingSessionModel.ended_at >= start_time,
            ChargingSessionModel.ended_at <= end_time,
        )
    )
    total_energy_wh, session_count = query_result.one()
    return Decimal(total_energy_wh), int(session_count)


async def create_pending_session(
    db: AsyncSession,
    *,
    station_id: UUID,
    organization_id: UUID,
    started_by: UUID,
    vehicle_id: UUID | None,
    id_token: str,
) -> ChargingSessionModel:
    """Create a PENDING session at the QR scan and flush it (CE-10).

    Args:
        db: The current async session; the repository does not commit the
            transaction.
        station_id: UUID of the charger the scan named.
        organization_id: UUID of the organization that pays.
        started_by: UUID of the user who scanned the code.
        vehicle_id: UUID of the truck being charged, if known (CE-13).
        id_token: The single-use token sent in the remote start.

    Returns:
        The PENDING session just added.

    Side Effects:
        Adds an ORM record and calls ``flush`` to obtain the UUID / detect
        constraint violations (an unknown station, organization, user or
        vehicle).
    """
    # One clock read for both timestamps, so a new row starts with
    # created_at == updated_at instead of two model defaults a tick apart.
    created_at = utc_now()
    session_record = ChargingSessionModel(
        station_id=station_id,
        organization_id=organization_id,
        started_by=started_by,
        vehicle_id=vehicle_id,
        id_token=id_token,
        status=SessionStatus.PENDING,
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(session_record)
    await db.flush()
    return session_record


async def insert_measurement(
    db: AsyncSession,
    *,
    session_id: UUID,
    sampled_at: datetime,
    measurand: str,
    value: Decimal,
    unit: str | None,
    context: str,
    phase: str | None = None,
    measurement_location: str,
) -> ChargingSessionMeasurementModel:
    """Append one measurement of a session and flush it.

    Args:
        db: The current async session; the repository does not commit the
            transaction.
        session_id: UUID of the aggregate that owns the measurement.
        sampled_at: The measurement time, already normalized to UTC.
        measurand: The OCPP measurand name.
        value: The reading; canonical Wh for the energy register.
        unit: Unit of ``value``, if known.
        context: OCPP reading context (the default already filled in).
        phase: Electrical phase, if any.
        measurement_location: Measurement location (the default already
            filled in).

    Returns:
        The ORM measurement just added.

    Side Effects:
        Adds a record and calls ``flush`` in the current transaction. The
        table is append-only.
    """
    measurement_record = ChargingSessionMeasurementModel(
        session_id=session_id,
        sampled_at=sampled_at,
        measurand=measurand,
        value=value,
        unit=unit,
        context=context,
        phase=phase,
        measurement_location=measurement_location,
    )
    db.add(measurement_record)
    await db.flush()
    return measurement_record


async def next_ocpp16_transaction_id(db: AsyncSession) -> int:
    """Take the next OCPP 1.6J transaction ID from the database sequence.

    Args:
        db: The current async session.

    Returns:
        The next integer, unique across all stations and restarts.

    Side Effects:
        Advances the sequence. A sequence is not transactional, so a rolled-back
        transaction leaves a harmless gap in the numbers.
    """
    query_result = await db.execute(
        select(_OCPP16_TRANSACTION_ID_SEQUENCE.next_value())
    )
    return int(query_result.scalar_one())


async def count_active_sessions_by_connector_id(
    db: AsyncSession, connector_id: UUID
) -> int:
    """Count the active sessions currently open on one connector.

    Args:
        db: The current async session.
        connector_id: UUID of the connector.

    Returns:
        The number of sessions with status ``ACTIVE`` on the connector.
    """
    query_result = await db.execute(
        select(func.count())
        .select_from(ChargingSessionModel)
        .where(
            ChargingSessionModel.connector_id == connector_id,
            ChargingSessionModel.status == SessionStatus.ACTIVE,
        )
    )
    return int(query_result.scalar_one())


async def list_measurements(
    db: AsyncSession,
    session_id: UUID,
    *,
    measurand: str | None,
    offset: int,
    limit: int,
) -> list[ChargingSessionMeasurementModel]:
    """Get a session's measurements in ascending time order.

    Args:
        db: The current async session.
        session_id: UUID of the session to query.
        measurand: Return only this measurand, or every measurand if ``None``.
        offset: The number of measurements to skip.
        limit: The maximum number of measurements to return.

    Returns:
        The measurements, stably paginated.
    """
    statement = select(ChargingSessionMeasurementModel).where(
        ChargingSessionMeasurementModel.session_id == session_id
    )
    if measurand is not None:
        statement = statement.where(
            ChargingSessionMeasurementModel.measurand == measurand
        )
    query_result = await db.execute(
        statement.order_by(
            ChargingSessionMeasurementModel.sampled_at.asc(),
            ChargingSessionMeasurementModel.measurement_id.asc(),
        )
        .offset(offset)
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def count_measurements(
    db: AsyncSession, session_id: UUID, *, measurand: str | None
) -> int:
    """Count a session's measurements.

    Args:
        db: The current async session.
        session_id: UUID of the session whose measurements to count.
        measurand: Count only this measurand, or every measurand if ``None``.

    Returns:
        The number of matching measurements.
    """
    statement = select(
        func.count(ChargingSessionMeasurementModel.measurement_id)
    ).where(ChargingSessionMeasurementModel.session_id == session_id)
    if measurand is not None:
        statement = statement.where(
            ChargingSessionMeasurementModel.measurand == measurand
        )
    query_result = await db.execute(statement)
    return int(query_result.scalar() or 0)
