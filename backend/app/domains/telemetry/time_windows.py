"""Validation of the time windows accepted by the telemetry read API.

Feature code: F-A5 (telemetry history), F-A6 (operating report), F-C6
(energy-usage report).

Internal to the telemetry domain: other domains never import this module
(they go through ``telemetry/service.py``). Pure functions, no I/O. Every
time-windowed telemetry query applies the same rules - both bounds carry a
timezone, ``end_time`` is after ``start_time``, the span stays within a
per-query maximum - and only the maximum differs
(``settings.TELEMETRY_HISTORY_MAX_RANGE_DAYS`` for history,
``settings.TELEMETRY_REPORT_MAX_RANGE_DAYS`` for reports,
``settings.TELEMETRY_BATTERY_HEALTH_MAX_RANGE_DAYS`` for the F-A3 trend),
so the caller passes it in.

It also cuts a validated window into calendar periods (F-A6 breakdown) in
the report time zone the caller passes in (planner D4); the repository's
``date_trunc`` grouping must produce the same period starts.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.domains.telemetry.exceptions import TelemetryInvalidRangeError
from app.domains.telemetry.types import ReportGranularity, TelemetryReportPeriod


def normalize_time_bound(value: datetime, field_name: str) -> datetime:
    """Require a timezone-aware bound and normalize it to UTC.

    Args:
        value: A ``start_time``/``end_time`` query parameter as parsed by
            FastAPI/Pydantic - naive if the caller omitted a UTC offset.
        field_name: Name to report in the error message.

    Returns:
        The value normalized to UTC.

    Raises:
        TelemetryInvalidRangeError: If ``value`` has no timezone.
    """
    if value.utcoffset() is None:
        raise TelemetryInvalidRangeError(f"{field_name} must have a timezone")
    return value.astimezone(timezone.utc)


def validate_time_window(
    start_time: datetime, end_time: datetime, max_range_days: int
) -> tuple[datetime, datetime]:
    """Validate a ``[start_time, end_time]`` query window and normalize it to UTC.

    Args:
        start_time: Inclusive lower bound; must carry a timezone.
        end_time: Inclusive upper bound; must carry a timezone.
        max_range_days: Longest span the calling query accepts, in days.

    Returns:
        ``(normalized_start, normalized_end)``, both in UTC.

    Raises:
        TelemetryInvalidRangeError: If either bound is missing a timezone,
            ``end_time`` is not after ``start_time``, or the span exceeds
            ``max_range_days``.
    """
    normalized_start = normalize_time_bound(start_time, "start_time")
    normalized_end = normalize_time_bound(end_time, "end_time")

    if normalized_end <= normalized_start:
        raise TelemetryInvalidRangeError("end_time must be after start_time")

    if normalized_end - normalized_start > timedelta(days=max_range_days):
        raise TelemetryInvalidRangeError(
            f"Requested range exceeds the maximum of {max_range_days} day(s)"
        )
    return normalized_start, normalized_end


def _truncate_local(local_time: datetime, granularity: ReportGranularity) -> datetime:
    """Truncate a local wall-clock time to the start of its calendar period.

    Mirrors PostgreSQL's ``date_trunc``: a week starts on Monday (ISO).

    Args:
        local_time: Timezone-aware datetime in the report time zone.
        granularity: Calendar period to truncate to.

    Returns:
        Local midnight of the period's first day, as a naive wall-clock
        datetime (the caller re-attaches the zone, which resolves the UTC
        offset of that date rather than reusing ``local_time``'s).
    """
    day_start = datetime(local_time.year, local_time.month, local_time.day)
    if granularity is ReportGranularity.WEEK:
        return day_start - timedelta(days=day_start.weekday())
    if granularity is ReportGranularity.MONTH:
        return day_start.replace(day=1)
    return day_start


def _next_local_period_start(
    period_start: datetime, granularity: ReportGranularity
) -> datetime:
    """Advance a naive local period start to the next period's start.

    Args:
        period_start: Naive local midnight that starts a period.
        granularity: Calendar period length.

    Returns:
        Naive local midnight starting the following period.
    """
    if granularity is ReportGranularity.MONTH:
        if period_start.month == 12:
            return period_start.replace(year=period_start.year + 1, month=1)
        return period_start.replace(month=period_start.month + 1)
    if granularity is ReportGranularity.WEEK:
        return period_start + timedelta(days=7)
    return period_start + timedelta(days=1)


def build_report_periods(
    start_time: datetime,
    end_time: datetime,
    *,
    granularity: ReportGranularity,
    time_zone: str,
) -> list[TelemetryReportPeriod]:
    """Cut a validated window into calendar periods of the report time zone.

    Period boundaries are local midnights in ``time_zone`` (day, Monday of
    an ISO week, first of a month), stepped in wall-clock time so a DST
    zone still gets whole local days. The first and last periods are
    clipped to the requested window; every period in between is whole.

    Args:
        start_time: Normalized (UTC) lower bound of the window.
        end_time: Normalized (UTC) upper bound; after ``start_time``.
        granularity: Calendar period length.
        time_zone: IANA zone name (``settings.APP_REPORT_TIMEZONE``).

    Returns:
        Periods in chronological order, covering the window without gaps;
        never empty since ``end_time > start_time``.
    """
    zone = ZoneInfo(time_zone)
    local_bucket_start = _truncate_local(start_time.astimezone(zone), granularity)
    periods: list[TelemetryReportPeriod] = []
    while True:
        bucket_start = local_bucket_start.replace(tzinfo=zone).astimezone(timezone.utc)
        if bucket_start > end_time or (periods and bucket_start == end_time):
            # A period starting exactly at the inclusive end_time would be a
            # zero-length tail; that instant belongs to the period before.
            break
        local_next_start = _next_local_period_start(local_bucket_start, granularity)
        next_bucket_start = local_next_start.replace(tzinfo=zone).astimezone(
            timezone.utc
        )
        periods.append(
            TelemetryReportPeriod(
                bucket_start=bucket_start,
                period_start=max(bucket_start, start_time),
                period_end=min(next_bucket_start, end_time),
            )
        )
        local_bucket_start = local_next_start
    return periods
