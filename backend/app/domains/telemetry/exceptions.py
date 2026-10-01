"""Business exceptions for the telemetry domain.

Each inherits one shared base from ``app/libs/common/errors.py``, which
``app/api/main.py`` maps to its HTTP status; routers do not catch them.
"""

from app.libs.common.errors import InvalidInputError, NotFoundError


class TelemetryNotFoundError(NotFoundError):
    """Raised when the vehicle or its corresponding telemetry cannot be found (HTTP 404)."""


class TelemetryInvalidRangeError(InvalidInputError):
    """Raised when a time-windowed telemetry query's range is invalid (HTTP 400).

    Applies to F-A5's history query, F-A6's operating report (and the
    operating summary other domains resolve), F-C6's energy-usage report
    and F-A3's battery-health trend. Covers a missing timezone on
    ``start_time``/``end_time``, ``end_time`` at or before ``start_time``,
    and a span exceeding the query's maximum -
    ``settings.TELEMETRY_HISTORY_MAX_RANGE_DAYS`` for history,
    ``settings.TELEMETRY_REPORT_MAX_RANGE_DAYS`` for the reports,
    ``settings.TELEMETRY_BATTERY_HEALTH_MAX_RANGE_DAYS`` for the trend.
    """
