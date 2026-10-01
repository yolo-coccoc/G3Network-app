"""Smoke tests for the shared telemetry query-window validation (F-A5, F-A6, F-C6)."""

from datetime import datetime, timedelta, timezone

import pytest

import app.domains.telemetry.time_windows as telemetry_time_windows
from app.domains.telemetry.exceptions import TelemetryInvalidRangeError


def test_validate_time_window_normalizes_offset_bounds_to_utc() -> None:
    """Bounds with a non-UTC offset come back as the same instants in UTC."""
    plus_seven = timezone(timedelta(hours=7))
    start = datetime(2026, 9, 1, 7, tzinfo=plus_seven)
    end = datetime(2026, 9, 2, 7, tzinfo=plus_seven)

    normalized_start, normalized_end = telemetry_time_windows.validate_time_window(
        start, end, max_range_days=1
    )

    assert normalized_start == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert normalized_start.tzinfo is timezone.utc
    assert normalized_end == datetime(2026, 9, 2, tzinfo=timezone.utc)


def test_validate_time_window_accepts_span_exactly_at_maximum() -> None:
    """A span equal to the maximum is allowed; only a longer one is rejected."""
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)

    telemetry_time_windows.validate_time_window(
        start, start + timedelta(days=3), max_range_days=3
    )
    with pytest.raises(TelemetryInvalidRangeError, match="3 day"):
        telemetry_time_windows.validate_time_window(
            start, start + timedelta(days=3, seconds=1), max_range_days=3
        )


def test_validate_time_window_rejects_naive_end_time() -> None:
    """A bound without a timezone is rejected and named in the message."""
    with pytest.raises(TelemetryInvalidRangeError, match="end_time"):
        telemetry_time_windows.validate_time_window(
            datetime(2026, 9, 1, tzinfo=timezone.utc),
            datetime(2026, 9, 2),
            max_range_days=7,
        )
