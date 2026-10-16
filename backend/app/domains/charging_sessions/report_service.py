"""Station energy reports (F-C5, STN-14, CHG-05).

The energy a station delivered in a window (total and session count), the
hourly / daily series cut in ``APP_REPORT_TIMEZONE``, and the per-station
total ``charging_stations`` asks for in its all-stations ranking. Internal
module: the domain's router calls it; ``service.py`` wraps
``resolve_station_energy_total`` for ``charging_stations``. Runs in the
caller's transaction and never commits.
"""

import bisect
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.access as session_access
import app.domains.charging_sessions.mappers as session_mappers
import app.domains.charging_sessions.repository as charging_session_repository
from app.domains.charging_sessions.exceptions import (
    ChargingSessionInputError,
)
from app.domains.charging_sessions.models import (
    ChargingSessionModel,
)
from app.domains.charging_sessions.schemas import (
    StationEnergySeriesBucketResponse,
    StationEnergySeriesResponse,
    StationEnergySummaryResponse,
)
from app.domains.charging_sessions.types import (
    EnergySeriesGranularity,
    StationEnergyTotal,
)
from app.libs.common.config import settings


async def get_station_energy_summary(
    db: AsyncSession,
    *,
    station_id: UUID,
    start_time: datetime,
    end_time: datetime,
) -> StationEnergySummaryResponse:
    """Total energy sold at a station within a time window (F-C5).

    Pure aggregation over already-stored session data; no writes.

    Args:
        db: The async session owned by the HTTP boundary.
        station_id: UUID of the station to aggregate over.
        start_time: Inclusive lower bound on ``ended_at``.
        end_time: Inclusive upper bound on ``ended_at``.

    Returns:
        Total energy (kWh) and count of completed sessions ending within
        the window. An unknown ``station_id`` returns a zero summary rather
        than a 404 - this domain doesn't own station existence, and a
        report endpoint legitimately answers "no sessions" for one.

    Raises:
        ChargingSessionInputError: If either timestamp lacks a timezone, or
            ``end_time`` isn't after ``start_time``.
    """
    energy_total = await resolve_station_energy_total(
        db, station_id=station_id, start_time=start_time, end_time=end_time
    )
    return StationEnergySummaryResponse(
        station_id=station_id,
        start_time=session_access.normalize_utc(start_time, "start_time"),
        end_time=session_access.normalize_utc(end_time, "end_time"),
        total_energy_kwh=float(
            energy_total.total_energy_wh / session_mappers.WH_PER_KWH
        ),
        session_count=energy_total.session_count,
    )


async def resolve_station_energy_total(
    db: AsyncSession,
    *,
    station_id: UUID,
    start_time: datetime,
    end_time: datetime,
) -> StationEnergyTotal:
    """Total energy sold at a station within a window, as a DTO (F-C5).

    Public entry point for ``charging_stations``' all-stations report, and
    the core of ``get_station_energy_summary``: completed sessions of the
    station whose ``ended_at`` falls within the inclusive window, summing
    ``energy_delivered_wh``.

    Args:
        db: The async session owned by the caller's entry boundary.
        station_id: UUID of the station to aggregate over.
        start_time: Inclusive lower bound on ``ended_at``; must carry a
            timezone.
        end_time: Inclusive upper bound on ``ended_at``; must carry a
            timezone and be after ``start_time``.

    Returns:
        The total in Wh and the session count; zero for a station with no
        matching session (existence is not checked here).

    Raises:
        ChargingSessionInputError: If either timestamp lacks a timezone, or
            ``end_time`` isn't after ``start_time``.

    Side Effects:
        Performs one aggregate query; does not commit or roll back.
    """
    normalized_start = session_access.normalize_utc(start_time, "start_time")
    normalized_end = session_access.normalize_utc(end_time, "end_time")
    if normalized_end <= normalized_start:
        raise ChargingSessionInputError("end_time must be after start_time")
    (
        total_energy_wh,
        session_count,
    ) = await charging_session_repository.get_station_energy_summary(
        db,
        station_id=station_id,
        start_time=normalized_start,
        end_time=normalized_end,
    )
    return StationEnergyTotal(
        station_id=station_id,
        total_energy_wh=total_energy_wh,
        session_count=session_count,
    )


def _floor_to_bucket_start(
    instant: datetime,
    *,
    granularity: EnergySeriesGranularity,
    report_zone: ZoneInfo,
) -> datetime:
    """Return the UTC start of the report-time-zone bucket containing an instant.

    Args:
        instant: A timezone-aware instant.
        granularity: Hour or day buckets.
        report_zone: The time zone that defines an hour/day (D4).

    Returns:
        The bucket's start, as a UTC instant.
    """
    local_instant = instant.astimezone(report_zone)
    if granularity is EnergySeriesGranularity.HOUR:
        local_start = local_instant.replace(minute=0, second=0, microsecond=0)
    else:
        local_start = local_instant.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_start.astimezone(timezone.utc)


def _next_bucket_start(
    bucket_start: datetime,
    *,
    granularity: EnergySeriesGranularity,
    report_zone: ZoneInfo,
) -> datetime:
    """Return the UTC start of the bucket following ``bucket_start``.

    Hours advance by one absolute hour; days advance to the next local
    midnight, so a daylight-saving day of 23 or 25 hours stays one bucket.

    Args:
        bucket_start: A bucket start produced by ``_floor_to_bucket_start``.
        granularity: Hour or day buckets.
        report_zone: The time zone that defines a day.

    Returns:
        The next bucket's start, as a UTC instant.
    """
    if granularity is EnergySeriesGranularity.HOUR:
        return bucket_start + timedelta(hours=1)
    next_local_date = bucket_start.astimezone(report_zone).date() + timedelta(days=1)
    return datetime(
        next_local_date.year,
        next_local_date.month,
        next_local_date.day,
        tzinfo=report_zone,
    ).astimezone(timezone.utc)


def build_energy_series_bucket_starts(
    *,
    start_time: datetime,
    end_time: datetime,
    granularity: EnergySeriesGranularity,
    report_zone: ZoneInfo,
) -> list[datetime]:
    """List every bucket start of a window, for a dense series (F-C5, D4).

    Args:
        start_time: Inclusive window start (UTC).
        end_time: Exclusive window end (UTC), after ``start_time``.
        granularity: Hour or day buckets.
        report_zone: The time zone buckets are cut in.

    Returns:
        UTC bucket starts in time order, from the bucket containing
        ``start_time`` to the last one starting before ``end_time``.
    """
    bucket_starts: list[datetime] = []
    bucket_start = _floor_to_bucket_start(
        start_time, granularity=granularity, report_zone=report_zone
    )
    while bucket_start < end_time:
        bucket_starts.append(bucket_start)
        bucket_start = _next_bucket_start(
            bucket_start, granularity=granularity, report_zone=report_zone
        )
    return bucket_starts


def calculate_energy_deltas(
    readings: Sequence[tuple[datetime, Decimal]],
) -> list[tuple[datetime, Decimal]]:
    """Turn one session's energy-register readings into timed deltas (D5).

    Rule:
        Readings are sorted by time (stable, so readings sharing a time keep
        their given order); each consecutive pair yields ``(later reading's
        time, later value - earlier value)``. A negative delta (a register
        that went backwards, e.g. a meter reset) yields nothing:
        reconciling it is the deferred reliability path (``deferred.md`` item
        27), and a negative bucket would be meaningless.

    Args:
        readings: ``(time, register_wh)`` pairs of one session, in any order.

    Returns:
        ``(time, delta_wh)`` pairs, one per non-negative consecutive delta.
    """
    ordered_readings = sorted(readings, key=lambda reading: reading[0])
    deltas: list[tuple[datetime, Decimal]] = []
    for (_, previous_wh), (current_at, current_wh) in zip(
        ordered_readings, ordered_readings[1:], strict=False
    ):
        delta_wh = current_wh - previous_wh
        if delta_wh >= 0:
            deltas.append((current_at, delta_wh))
    return deltas


def _session_energy_readings(
    session_record: ChargingSessionModel,
    samples: Sequence[tuple[datetime, Decimal]],
) -> list[tuple[datetime, Decimal]]:
    """Assemble every energy-register reading known for one session.

    The stored samples alone miss energy: ``meter_start_wh`` (taken at
    ``started_at``) and the charger's closing ``meter_stop_wh`` (taken at
    ``ended_at``) are declared on the session row, not stored as samples.
    Adding both as readings makes a finished session's deltas sum to its
    delivered energy; when the closing reading is also a stored sample it only
    adds a zero delta.

    Args:
        session_record: The session.
        samples: The session's stored outlet energy samples, ``(sampled_at,
            value_wh)``.

    Returns:
        The start reading (if any), the samples, then the closing reading (if
        any), in that order before sorting by time.
    """
    readings: list[tuple[datetime, Decimal]] = []
    if session_record.meter_start_wh is not None and session_record.started_at:
        readings.append((session_record.started_at, session_record.meter_start_wh))
    readings.extend(samples)
    if session_record.meter_stop_wh is not None and session_record.ended_at:
        readings.append((session_record.ended_at, session_record.meter_stop_wh))
    return readings


async def get_station_energy_series(
    db: AsyncSession,
    *,
    station_id: UUID,
    start_time: datetime,
    end_time: datetime,
    granularity: EnergySeriesGranularity,
) -> StationEnergySeriesResponse:
    """Energy metered at a station per hour or day within a window (F-C5).

    Rule (decisions D4 and D5 of the happy-path completion planner):
        1. The window is ``[start_time, end_time)``, timezone-aware, at most
           ``CHARGING_ENERGY_SERIES_MAX_RANGE_DAYS`` long.
        2. Buckets are clock hours or calendar days of
           ``APP_REPORT_TIMEZONE``, returned as UTC instants; every bucket
           from the one containing ``start_time`` is listed (dense series).
        3. For each session of the station that overlaps the window (active
           sessions included), consecutive energy-register readings yield
           deltas (``calculate_energy_deltas``); a delta belongs to the
           bucket of its later reading and counts only when that reading
           falls inside the window - so energy crossing a bucket boundary is
           split at the readings, not attributed whole at ``ended_at``.

    Args:
        db: The async session owned by the HTTP boundary.
        station_id: UUID of the station. An unknown station yields an
            all-zero series, like ``get_station_energy_summary``.
        start_time: Inclusive window start; must carry a timezone.
        end_time: Exclusive window end; must carry a timezone.
        granularity: ``hour`` or ``day``.

    Returns:
        The dense series and its total, in kWh.

    Raises:
        ChargingSessionInputError: If a bound lacks a timezone, ``end_time``
            is not after ``start_time``, or the window is longer than
            ``CHARGING_ENERGY_SERIES_MAX_RANGE_DAYS``.

    Side Effects:
        Performs one session query plus one sample query per overlapping
        session (deliberately unbatched); does not commit or roll back.
    """
    normalized_start = session_access.normalize_utc(start_time, "start_time")
    normalized_end = session_access.normalize_utc(end_time, "end_time")
    if normalized_end <= normalized_start:
        raise ChargingSessionInputError("end_time must be after start_time")
    max_range = timedelta(days=settings.CHARGING_ENERGY_SERIES_MAX_RANGE_DAYS)
    if normalized_end - normalized_start > max_range:
        raise ChargingSessionInputError(
            "The time window must not exceed "
            f"{settings.CHARGING_ENERGY_SERIES_MAX_RANGE_DAYS} days"
        )
    report_zone = ZoneInfo(settings.APP_REPORT_TIMEZONE)
    bucket_starts = build_energy_series_bucket_starts(
        start_time=normalized_start,
        end_time=normalized_end,
        granularity=granularity,
        report_zone=report_zone,
    )
    bucket_energy_wh = [Decimal(0)] * len(bucket_starts)

    sessions = await charging_session_repository.list_sessions_by_station_in_window(
        db, station_id, start_time=normalized_start, end_time=normalized_end
    )
    for session_record in sessions:
        samples = await charging_session_repository.list_energy_samples_before(
            db, session_record.session_id, end_time=normalized_end
        )
        readings = _session_energy_readings(session_record, samples)
        for delta_at, delta_wh in calculate_energy_deltas(readings):
            if not normalized_start <= delta_at < normalized_end:
                continue
            # bucket_starts[0] <= normalized_start <= delta_at, so the index
            # is never negative.
            bucket_index = bisect.bisect_right(bucket_starts, delta_at) - 1
            bucket_energy_wh[bucket_index] += delta_wh

    return StationEnergySeriesResponse(
        station_id=station_id,
        start_time=normalized_start,
        end_time=normalized_end,
        granularity=granularity,
        report_timezone=settings.APP_REPORT_TIMEZONE,
        total_energy_kwh=float(
            sum(bucket_energy_wh, Decimal(0)) / session_mappers.WH_PER_KWH
        ),
        items=[
            StationEnergySeriesBucketResponse(
                bucket_start=bucket_start,
                energy_kwh=float(energy_wh / session_mappers.WH_PER_KWH),
            )
            for bucket_start, energy_wh in zip(
                bucket_starts, bucket_energy_wh, strict=True
            )
        ],
    )
