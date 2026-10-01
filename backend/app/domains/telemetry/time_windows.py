"""Validation of the time windows accepted by the telemetry read API.

Feature code: F-A5 (telemetry history), F-A6 (operating report), F-C6
(energy-usage report).

Internal to the telemetry domain: other domains never import this module
(they go through ``telemetry/service.py``). Pure functions, no I/O. Every
time-windowed telemetry query applies the same rules - both bounds carry a
timezone, ``end_time`` is after ``start_time``, the span stays within a
per-query maximum - and only the maximum differs
(``settings.TELEMETRY_HISTORY_MAX_RANGE_DAYS`` for history,
``settings.TELEMETRY_REPORT_MAX_RANGE_DAYS`` for reports), so the caller
passes it in.
"""

from datetime import datetime, timedelta, timezone

from app.domains.telemetry.exceptions import TelemetryInvalidRangeError


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
