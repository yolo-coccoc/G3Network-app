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

from sqlalchemy import DateTime, Float, Integer, and_, case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Subquery

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
    """Insert a single telemetry record, skipping a repeated reading.

    A device that sends the same reading twice (an offline buffer replayed, a
    1 Hz device with second-precision timestamps) repeats
    ``(telematic_id, recorded_at)``; ``ON CONFLICT DO NOTHING`` on
    ``uq_telemetry_telematic_recorded_at`` keeps the first row and inserts
    nothing, instead of an IntegrityError that would stop the worker (RV-OP2).

    Args:
        db: Database session owned by the entry boundary.
        telemetry_values: Column values already converted by the service
            (``TelemetryMessage.to_vehicle_telemetry_values``) to match
            the database model.

    Returns:
        Number of rows inserted: 1, or 0 for a repeated reading.

    Side Effects:
        Writes one row into the current session. The function does not
        commit or roll back.
    """
    query_result = cast(
        CursorResult[Any],
        await db.execute(
            insert(TelemetryModel)
            .values(telemetry_values)
            .on_conflict_do_nothing(index_elements=["telematic_id", "recorded_at"])
        ),
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


async def get_previous_vehicle_telemetry(
    db: AsyncSession, vehicle_id: UUID, *, before: datetime
) -> TelemetryModel | None:
    """Get the newest reading of a vehicle recorded before a given instant.

    The "previous reading" the alert detectors compare a new reading with:
    a reading that arrives late (an offline buffer replayed) must be compared
    with the one just before it in time, not with whatever has the largest
    `recorded_at` (RV-OP5).

    Args:
        db: Current database session.
        vehicle_id: Internal ID of the vehicle.
        before: The new reading's `recorded_at`; only strictly earlier
            readings count.

    Returns:
        The earlier record with the largest `recorded_at`, or None.
    """
    query_result = await db.execute(
        select(TelemetryModel)
        .where(
            TelemetryModel.vehicle_id == vehicle_id,
            TelemetryModel.recorded_at < before,
        )
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


async def find_latest_moving_received_at(
    db: AsyncSession, vehicle_id: UUID, *, min_speed_kmh: float, since: datetime
) -> datetime | None:
    """Get when the server last received a speed above a threshold.

    Read by ``received_at`` (the server clock), not ``recorded_at`` (the
    device clock): a device whose clock runs slow stamps every sample before
    the shift started, and would look as if it never moved (RV-OP6).

    Args:
        db: Current database session.
        vehicle_id: Internal ID of the vehicle.
        min_speed_kmh: A sample counts as moving when its speed is above this.
        since: Only samples received at or after this time are looked at.

    Returns:
        The largest matching ``received_at``, or `None` if the vehicle did
        not move since ``since``.
    """
    query_result = await db.execute(
        select(func.max(TelemetryModel.received_at)).where(
            TelemetryModel.vehicle_id == vehicle_id,
            TelemetryModel.received_at >= since,
            TelemetryModel.speed_kmh > min_speed_kmh,
        )
    )
    return query_result.scalar_one_or_none()


async def find_first_received_at(
    db: AsyncSession, vehicle_id: UUID, telematic_id: UUID, since: datetime
) -> datetime | None:
    """Get when a device first delivered data for a truck at or after a time.

    Served by ``ix_telemetry_vehicle_received``. Used to tell whether a truck
    was activated (VEH-05): the first sample the mounted device sent after the
    later of its mounting and the truck's handover.

    Args:
        db: Current database session.
        vehicle_id: Internal ID of the vehicle.
        telematic_id: Internal ID of the device that must have sent the sample.
        since: Only samples received at or after this time count.

    Returns:
        The smallest matching ``received_at``, or `None` if there is none.
    """
    query_result = await db.execute(
        select(TelemetryModel.received_at)
        .where(
            TelemetryModel.vehicle_id == vehicle_id,
            TelemetryModel.telematic_id == telematic_id,
            TelemetryModel.received_at >= since,
        )
        .order_by(TelemetryModel.received_at.asc())
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


# Odometer steps larger than a truck can plausibly drive are glitches, not
# distance (RV-OP8): a step counts only up to this speed over the time between
# the two readings, with that time counted as at least the minimum below, so
# readings seconds apart do not make a normal step look impossible.
MAX_PLAUSIBLE_SPEED_KMH = 200.0
MIN_ODOMETER_STEP_SECONDS = 300.0


def _plausible_odometer_distance(
    vehicle_id: UUID,
    start_time: datetime,
    end_time: datetime,
    *,
    granularity: ReportGranularity | None = None,
    time_zone: str | None = None,
) -> Subquery:
    """Build the per-reading distance steps of a window from the odometer.

    Only readings that carry an odometer are compared with each other, so a
    reading without one (or with a glitch in between) neither hides the
    distance driven nor adds a fake one. A step counts when it is positive and
    no larger than ``MAX_PLAUSIBLE_SPEED_KMH`` allows over the time between the
    two readings (at least ``MIN_ODOMETER_STEP_SECONDS``); a backwards step
    (device reset) or an impossible forward one contributes 0.

    Args:
        vehicle_id: The vehicle.
        start_time: Inclusive lower bound.
        end_time: Inclusive upper bound.
        granularity: When given, also label each step with its calendar
            period (``period_start``), cut like the period summaries do.
        time_zone: IANA zone of the periods; required with ``granularity``.

    Returns:
        A subquery with ``distance_km`` and, with ``granularity``,
        ``period_start``.
    """
    previous_odometer = func.lag(TelemetryModel.odometer_km).over(
        partition_by=TelemetryModel.vehicle_id,
        order_by=TelemetryModel.recorded_at.asc(),
    )
    previous_recorded_at = func.lag(TelemetryModel.recorded_at).over(
        partition_by=TelemetryModel.vehicle_id,
        order_by=TelemetryModel.recorded_at.asc(),
    )
    columns: list[ColumnElement[Any]] = [
        (TelemetryModel.odometer_km - previous_odometer).label("step_km"),
        func.extract("epoch", TelemetryModel.recorded_at - previous_recorded_at).label(
            "elapsed_seconds"
        ),
    ]
    if granularity is not None:
        columns.append(
            func.date_trunc(
                granularity.value,
                func.least(
                    TelemetryModel.recorded_at, end_time - timedelta(microseconds=1)
                ),
                time_zone,
                type_=DateTime(timezone=True),
            ).label("period_start")
        )
    steps = (
        select(*columns)
        .where(
            TelemetryModel.vehicle_id == vehicle_id,
            TelemetryModel.recorded_at >= start_time,
            TelemetryModel.recorded_at <= end_time,
            TelemetryModel.odometer_km.is_not(None),
        )
        .subquery()
    )
    plausible_km = (
        MAX_PLAUSIBLE_SPEED_KMH
        * func.greatest(steps.c.elapsed_seconds, MIN_ODOMETER_STEP_SECONDS)
        / 3600.0
    )
    selected: list[ColumnElement[Any]] = [
        case(
            (
                and_(steps.c.step_km > 0, steps.c.step_km <= plausible_km),
                steps.c.step_km,
            ),
            else_=0.0,
        ).label("distance_km")
    ]
    if granularity is not None:
        selected.append(steps.c.period_start)
    return select(*selected).subquery()


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
        Read-only. Two queries: the SOC fold, and the distance from the
        odometer readings only (``_plausible_odometer_distance``, RV-OP8).
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

    deltas = (
        select(
            TelemetryModel.recorded_at.label("recorded_at"),
            TelemetryModel.odometer_km.label("odometer"),
            (previous_soc - TelemetryModel.soc_percent).label("soc_drop"),
            (TelemetryModel.soc_percent - previous_soc).label("soc_rise"),
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

    query_result = await db.execute(
        select(
            func.coalesce(func.sum(clamped_drop), 0.0),
            func.coalesce(func.sum(clamped_rise), 0.0),
            func.count(),
            func.count(deltas.c.odometer),
            func.min(deltas.c.recorded_at),
            func.max(deltas.c.recorded_at),
        ).select_from(deltas)
    )
    (
        soc_discharge_percent,
        soc_charge_percent,
        sample_count,
        odometer_sample_count,
        first_recorded_at,
        last_recorded_at,
    ) = query_result.one()
    distance_steps = _plausible_odometer_distance(vehicle_id, start_time, end_time)
    distance_km = (
        await db.execute(
            select(func.coalesce(func.sum(distance_steps.c.distance_km), 0.0))
        )
    ).scalar_one()
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
        Read-only. Two queries: the SOC fold, and the distance steps from the
        odometer readings only (``_plausible_odometer_distance``, RV-OP8).
    """
    previous_soc = func.lag(TelemetryModel.soc_percent).over(
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

    query_result = await db.execute(
        select(
            deltas.c.period_start,
            func.sum(clamped_drop),
            func.sum(clamped_rise),
            func.count(),
            func.count(deltas.c.odometer),
            func.min(deltas.c.recorded_at),
            func.max(deltas.c.recorded_at),
        )
        .select_from(deltas)
        .group_by(deltas.c.period_start)
        .order_by(deltas.c.period_start)
    )
    distance_steps = _plausible_odometer_distance(
        vehicle_id,
        start_time,
        end_time,
        granularity=granularity,
        time_zone=time_zone,
    )
    distance_by_period = {
        distance_period_start: float(period_distance_km)
        for distance_period_start, period_distance_km in (
            await db.execute(
                select(
                    distance_steps.c.period_start,
                    func.sum(distance_steps.c.distance_km),
                ).group_by(distance_steps.c.period_start)
            )
        ).all()
    }
    return {
        row_period_start: VehicleTelemetryWindowSummary(
            soc_discharge_percent=float(soc_discharge_percent),
            soc_charge_percent=float(soc_charge_percent),
            distance_km=distance_by_period.get(row_period_start, 0.0),
            sample_count=int(sample_count),
            odometer_sample_count=int(odometer_sample_count),
            first_recorded_at=first_recorded_at,
            last_recorded_at=last_recorded_at,
        )
        for (
            row_period_start,
            soc_discharge_percent,
            soc_charge_percent,
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
        Read-only. Two queries, as `get_vehicle_window_summary`.
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
