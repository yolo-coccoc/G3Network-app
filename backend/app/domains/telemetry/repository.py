"""Data access repository for the telemetry domain.

Feature code: F-A1 (Real-time vehicle telemetry ingestion)

Single-row operations for the per-message ingestion flow, plus the query
API's reads: latest/history (F-A1/F-A5), the folded report window and its
per-period breakdown (F-A6/F-C6), and the daily battery-health trend
(F-A3). Calendar bucketing uses PostgreSQL's three-argument
``date_trunc(field, timestamptz, zone)``, so a "day" is a day of the zone
the service passes in (``settings.APP_REPORT_TIMEZONE``) while every
returned timestamp stays UTC. The repository does not own the transaction: the entry
boundary passes in the session and decides whether to commit or roll back.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import DateTime, Float, Integer, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.telemetry.models import TelemetryModel
from app.domains.telemetry.types import (
    ReportGranularity,
    VehicleBatteryHealthDay,
    VehicleTelemetryWindowSummary,
)

logger = logging.getLogger(__name__)


async def insert_telemetry(
    db: AsyncSession,
    telemetry_values: dict[str, object],
) -> int:
    """Insert a single telemetry record using SQLAlchemy Core.

    Args:
        db: Database session owned by the entry boundary.
        telemetry_values: Column values already converted by the service
            (``TelemetryMessage.to_vehicle_telemetry_values``) to match
            the database model.

    Returns:
        Number of rows the database reports as inserted.

    Side Effects:
        Writes one row into the current session. The function does not
        commit or roll back.
    """
    query_result = cast(
        CursorResult[Any],
        await db.execute(insert(TelemetryModel).values(telemetry_values)),
    )
    logger.debug(
        "insert_telemetry",
        extra={"rows_inserted": query_result.rowcount},
    )
    return query_result.rowcount


async def get_latest_vehicle_telemetry(
    db: AsyncSession, vehicle_id: UUID
) -> TelemetryModel | None:
    """Get the latest telemetry record for a vehicle.

    Args:
        db: Current database session.
        vehicle_id: Internal ID of the vehicle.

    Returns:
        The record with the largest `recorded_at`, or None if there is no
        data yet.
    """
    query_result = await db.execute(
        select(TelemetryModel)
        .where(TelemetryModel.vehicle_id == vehicle_id)
        .order_by(TelemetryModel.recorded_at.desc())
        .limit(1)
    )
    return query_result.scalar_one_or_none()


async def find_latest_received_at(
    db: AsyncSession, vehicle_id: UUID
) -> datetime | None:
    """Get the newest backend receive time among a vehicle's telemetry rows.

    Ordered by `received_at` (the backend's clock), not `recorded_at` (the
    device's): a row with a future-dated `recorded_at` must not stay
    "latest" and hide newer arrivals. Served by
    `ix_telemetry_vehicle_received`.

    Args:
        db: Current database session.
        vehicle_id: Internal ID of the vehicle.

    Returns:
        The largest `received_at`, or None if the vehicle never reported.
    """
    query_result = await db.execute(
        select(TelemetryModel.received_at)
        .where(TelemetryModel.vehicle_id == vehicle_id)
        .order_by(TelemetryModel.received_at.desc())
        .limit(1)
    )
    return query_result.scalar_one_or_none()


async def get_vehicle_telemetry_history(
    db: AsyncSession,
    *,
    vehicle_id: UUID,
    start_time: datetime,
    end_time: datetime,
    limit: int,
) -> list[TelemetryModel]:
    """Get telemetry records for a vehicle within a time range (F-A5).

    Args:
        db: Current database session.
        vehicle_id: Internal ID of the vehicle.
        start_time: Inclusive lower bound, already validated and normalized
            to UTC by the service.
        end_time: Inclusive upper bound, already validated and normalized to
            UTC by the service.
        limit: Maximum number of records to return, already clamped by the
            service.

    Returns:
        Records ordered by ``recorded_at`` ascending (chronological, for
        trip replay), oldest first, capped at ``limit``.

    Side Effects:
        Reuses the existing ``ix_telemetry_vehicle_time`` index
        (btree on ``vehicle_id, recorded_at DESC``) - PostgreSQL can scan it
        backwards for this ascending range scan, so no new index is needed.
    """
    query_result = await db.execute(
        select(TelemetryModel)
        .where(
            TelemetryModel.vehicle_id == vehicle_id,
            TelemetryModel.recorded_at >= start_time,
            TelemetryModel.recorded_at <= end_time,
        )
        .order_by(TelemetryModel.recorded_at.asc())
        .limit(limit)
    )
    return list(query_result.scalars().all())


async def get_vehicle_window_summary(
    db: AsyncSession,
    *,
    vehicle_id: UUID,
    start_time: datetime,
    end_time: datetime,
) -> VehicleTelemetryWindowSummary:
    """Fold one vehicle's telemetry deltas over a time window (F-A6/F-C6).

    A single SQL pass computes ``lag(soc)``/``lag(odometer)`` over
    ``(PARTITION BY vehicle_id ORDER BY recorded_at)`` within the
    time-bounded window (an inner query), then sums the clamped
    positive deltas in an outer aggregate - a window function cannot be
    nested inside an aggregate in the same ``SELECT``. Reuses
    ``ix_telemetry_vehicle_time`` for both the filter and the
    window's sort order; no new index is needed. Deliberately not built
    on ``get_vehicle_telemetry_history`` - that function's hard ``limit``
    would silently truncate a month of frequent telemetry.

    Args:
        db: Current database session.
        vehicle_id: Internal ID of the vehicle to fold.
        start_time: Inclusive lower bound, already validated and
            normalized to UTC by the service.
        end_time: Inclusive upper bound, already validated and normalized
            to UTC by the service.

    Returns:
        The folded summary. An empty window (no rows) yields zeroed sums
        and ``None`` timestamps, never ``NULL`` sums - see the outer
        ``coalesce`` below.

    Side Effects:
        Read-only. Single SQL round trip; no I/O beyond the query.
    """
    # lag() must be computed in an inner SELECT: SQL forbids a window
    # function inside an aggregate in the same select list.
    # partition_by is redundant while the WHERE pins one vehicle, but it
    # keeps the fold correct by construction if the filter is ever
    # widened, and costs nothing - PostgreSQL proves vehicle_id constant
    # and reuses ix_telemetry_vehicle_time's ordering instead of
    # sorting.
    previous_soc = func.lag(TelemetryModel.soc_percent).over(
        partition_by=TelemetryModel.vehicle_id,
        order_by=TelemetryModel.recorded_at.asc(),
    )
    previous_odometer = func.lag(TelemetryModel.odometer_km).over(
        partition_by=TelemetryModel.vehicle_id,
        order_by=TelemetryModel.recorded_at.asc(),
    )

    deltas = (
        select(
            TelemetryModel.recorded_at.label("recorded_at"),
            TelemetryModel.odometer_km.label("odometer"),
            (previous_soc - TelemetryModel.soc_percent).label("soc_drop"),
            (TelemetryModel.soc_percent - previous_soc).label("soc_rise"),
            (TelemetryModel.odometer_km - previous_odometer).label("odometer_delta"),
        )
        .where(
            TelemetryModel.vehicle_id == vehicle_id,
            TelemetryModel.recorded_at >= start_time,
            TelemetryModel.recorded_at <= end_time,
        )
        .subquery()
    )

    # coalesce(delta, 0.0) before greatest(): the window's first row has
    # no predecessor (lag() is NULL), and an odometer the device didn't
    # report is NULL too. PostgreSQL's GREATEST happens to skip NULL
    # arguments, but relying on that non-standard behaviour would make
    # the clamp unreadable - be explicit instead.
    # greatest(..., 0.0) drops negative deltas: a drop is not a rise, a
    # rise is not a drop, and an odometer that moved backwards (device
    # reset) contributes nothing rather than a negative distance.
    # The outer coalesce covers the empty window, where sum() is NULL.
    clamped_drop = func.greatest(func.coalesce(deltas.c.soc_drop, 0.0), 0.0)
    clamped_rise = func.greatest(func.coalesce(deltas.c.soc_rise, 0.0), 0.0)
    clamped_distance = func.greatest(func.coalesce(deltas.c.odometer_delta, 0.0), 0.0)

    query_result = await db.execute(
        select(
            func.coalesce(func.sum(clamped_drop), 0.0),
            func.coalesce(func.sum(clamped_rise), 0.0),
            func.coalesce(func.sum(clamped_distance), 0.0),
            func.count(),
            func.count(deltas.c.odometer),
            func.min(deltas.c.recorded_at),
            func.max(deltas.c.recorded_at),
        ).select_from(deltas)
    )
    (
        soc_discharge_percent,
        soc_charge_percent,
        distance_km,
        sample_count,
        odometer_sample_count,
        first_recorded_at,
        last_recorded_at,
    ) = query_result.one()
    return VehicleTelemetryWindowSummary(
        soc_discharge_percent=float(soc_discharge_percent),
        soc_charge_percent=float(soc_charge_percent),
        distance_km=float(distance_km),
        sample_count=int(sample_count),
        odometer_sample_count=int(odometer_sample_count),
        first_recorded_at=first_recorded_at,
        last_recorded_at=last_recorded_at,
    )


async def list_vehicle_period_summaries(
    db: AsyncSession,
    *,
    vehicle_id: UUID,
    start_time: datetime,
    end_time: datetime,
    granularity: ReportGranularity,
    time_zone: str,
) -> dict[datetime, VehicleTelemetryWindowSummary]:
    """Fold one vehicle's telemetry deltas per calendar period (F-A6 breakdown).

    Same lag fold as ``get_vehicle_window_summary``, but the outer
    aggregate is grouped by the reading's calendar period in
    ``time_zone``. ``lag()`` runs over the whole window, not per period,
    so the delta between the last reading of one period and the first of
    the next counts in the later period - the periods therefore sum to
    exactly the whole-window summary.

    Args:
        db: Current database session.
        vehicle_id: Internal ID of the vehicle to fold.
        start_time: Inclusive lower bound, already validated and
            normalized to UTC by the service.
        end_time: Inclusive upper bound, already validated and normalized
            to UTC by the service.
        granularity: Calendar period to group by (its value is the
            ``date_trunc`` field).
        time_zone: IANA zone the periods are cut in.

    Returns:
        One summary per period that holds at least one reading, keyed by
        the period's unclipped start (local midnight as a UTC timestamp,
        matching ``time_windows.build_report_periods``'s ``bucket_start``).
        Periods without readings are absent.

    Side Effects:
        Read-only. Single SQL round trip.
    """
    previous_soc = func.lag(TelemetryModel.soc_percent).over(
        partition_by=TelemetryModel.vehicle_id,
        order_by=TelemetryModel.recorded_at.asc(),
    )
    previous_odometer = func.lag(TelemetryModel.odometer_km).over(
        partition_by=TelemetryModel.vehicle_id,
        order_by=TelemetryModel.recorded_at.asc(),
    )
    # The window is inclusive of end_time. When end_time sits exactly on a
    # period boundary, a reading stamped at end_time would otherwise open a
    # zero-length period the service never lists; truncating one
    # microsecond earlier (the timestamp resolution) keeps it in the last
    # listed period and changes no other reading's bucket.
    bucketed_time = func.least(
        TelemetryModel.recorded_at, end_time - timedelta(microseconds=1)
    )
    period_start = func.date_trunc(
        granularity.value,
        bucketed_time,
        time_zone,
        type_=DateTime(timezone=True),
    )

    deltas = (
        select(
            period_start.label("period_start"),
            TelemetryModel.recorded_at.label("recorded_at"),
            TelemetryModel.odometer_km.label("odometer"),
            (previous_soc - TelemetryModel.soc_percent).label("soc_drop"),
            (TelemetryModel.soc_percent - previous_soc).label("soc_rise"),
            (TelemetryModel.odometer_km - previous_odometer).label("odometer_delta"),
        )
        .where(
            TelemetryModel.vehicle_id == vehicle_id,
            TelemetryModel.recorded_at >= start_time,
            TelemetryModel.recorded_at <= end_time,
        )
        .subquery()
    )

    # Same clamps as get_vehicle_window_summary; no outer coalesce needed
    # since every group holds at least one row (sum over clamped values of
    # a non-empty group is never NULL).
    clamped_drop = func.greatest(func.coalesce(deltas.c.soc_drop, 0.0), 0.0)
    clamped_rise = func.greatest(func.coalesce(deltas.c.soc_rise, 0.0), 0.0)
    clamped_distance = func.greatest(func.coalesce(deltas.c.odometer_delta, 0.0), 0.0)

    query_result = await db.execute(
        select(
            deltas.c.period_start,
            func.sum(clamped_drop),
            func.sum(clamped_rise),
            func.sum(clamped_distance),
            func.count(),
            func.count(deltas.c.odometer),
            func.min(deltas.c.recorded_at),
            func.max(deltas.c.recorded_at),
        )
        .select_from(deltas)
        .group_by(deltas.c.period_start)
        .order_by(deltas.c.period_start)
    )
    return {
        row_period_start: VehicleTelemetryWindowSummary(
            soc_discharge_percent=float(soc_discharge_percent),
            soc_charge_percent=float(soc_charge_percent),
            distance_km=float(distance_km),
            sample_count=int(sample_count),
            odometer_sample_count=int(odometer_sample_count),
            first_recorded_at=first_recorded_at,
            last_recorded_at=last_recorded_at,
        )
        for (
            row_period_start,
            soc_discharge_percent,
            soc_charge_percent,
            distance_km,
            sample_count,
            odometer_sample_count,
            first_recorded_at,
            last_recorded_at,
        ) in query_result.all()
    }


async def list_vehicle_battery_health_days(
    db: AsyncSession,
    *,
    vehicle_id: UUID,
    start_time: datetime,
    end_time: datetime,
    time_zone: str,
) -> list[VehicleBatteryHealthDay]:
    """Get a vehicle's last reported SOH and cycle count per day (F-A3).

    Groups the window's readings by local day in ``time_zone`` and takes,
    per day, the value of the latest reading that reported it (TimescaleDB
    ``last(value, time)`` with ``FILTER (WHERE value IS NOT NULL)``, so a
    final reading without SOH doesn't blank out an earlier one). Reuses
    ``ix_telemetry_vehicle_time`` for the range filter.

    Args:
        db: Current database session.
        vehicle_id: Internal ID of the vehicle.
        start_time: Inclusive lower bound, already validated and normalized
            to UTC by the service.
        end_time: Inclusive upper bound, already validated and normalized
            to UTC by the service.
        time_zone: IANA zone that defines a day.

    Returns:
        One entry per day that has at least one SOH or cycle-count value,
        oldest first; days without such data are omitted.

    Side Effects:
        Read-only. Single SQL round trip.
    """
    day_start = func.date_trunc(
        "day",
        TelemetryModel.recorded_at,
        time_zone,
        type_=DateTime(timezone=True),
    )
    readings = (
        select(
            day_start.label("day_start"),
            TelemetryModel.recorded_at.label("recorded_at"),
            TelemetryModel.soh_percent.label("soh_percent"),
            TelemetryModel.cycle_count.label("cycle_count"),
        )
        .where(
            TelemetryModel.vehicle_id == vehicle_id,
            TelemetryModel.recorded_at >= start_time,
            TelemetryModel.recorded_at <= end_time,
        )
        .subquery()
    )
    last_soh = func.last(
        readings.c.soh_percent, readings.c.recorded_at, type_=Float
    ).filter(readings.c.soh_percent.is_not(None))
    last_cycle_count = func.last(
        readings.c.cycle_count, readings.c.recorded_at, type_=Integer
    ).filter(readings.c.cycle_count.is_not(None))

    query_result = await db.execute(
        select(readings.c.day_start, last_soh, last_cycle_count)
        .select_from(readings)
        .group_by(readings.c.day_start)
        .having(
            (func.count(readings.c.soh_percent) > 0)
            | (func.count(readings.c.cycle_count) > 0)
        )
        .order_by(readings.c.day_start)
    )
    return [
        VehicleBatteryHealthDay(
            day_start=row_day_start,
            soh_percent=float(soh_percent) if soh_percent is not None else None,
            cycle_count=int(cycle_count) if cycle_count is not None else None,
        )
        for row_day_start, soh_percent, cycle_count in query_result.all()
    ]
