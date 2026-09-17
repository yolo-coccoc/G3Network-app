"""Business exceptions for the telemetry domain."""


class TelemetryNotFoundError(Exception):
    """Raised when the vehicle or its corresponding telemetry cannot be found."""


class TelemetryInvalidRangeError(Exception):
    """Raised when a telemetry history query's time range is invalid (F-A5).

    Covers a missing timezone on ``start_time``/``end_time``, ``end_time``
    at or before ``start_time``, and a span exceeding
    ``settings.TELEMETRY_HISTORY_MAX_RANGE_DAYS``.
    """
