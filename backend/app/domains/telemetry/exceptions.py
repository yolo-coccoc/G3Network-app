"""Business exceptions for the telemetry domain."""

from app.libs.common.errors import InvalidInputError, NotFoundError


class TelemetryNotFoundError(NotFoundError):
    """Raised when the vehicle or its corresponding telemetry cannot be found."""


class TelemetryInvalidRangeError(InvalidInputError):
    """Raised when a telemetry history query's time range is invalid (F-A5).

    Covers a missing timezone on ``start_time``/``end_time``, ``end_time``
    at or before ``start_time``, and a span exceeding
    ``settings.TELEMETRY_HISTORY_MAX_RANGE_DAYS``.
    """
