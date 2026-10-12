"""Protocol-neutral parsing helpers shared by the OCPP adapters.

Both the OCPP 2.0.1 and the OCPP 1.6J adapters (and the gateway itself) need the
same primitives: the plain-dict payload alias and timestamp parsing and
formatting. The 2.0.1 adapter parses strictly (a timezone is required); the
1.6J adapter reads a timestamp without one as UTC and logs it (CO-16).
They live here so the adapters never import each other or the server module,
which would create an import cycle (the server imports the adapters).

Scope: only pure helpers with no protocol-specific field names and no I/O.
Anything that knows about a 2.0.1 or 1.6J message shape belongs to that
protocol's adapter module.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# python-ocpp delivers nested OCPP objects as plain dicts (snake_cased keys),
# never as ocpp.v201/ocpp.v16 datatypes dataclasses, whatever a handler's type
# annotation says. Handlers are therefore written against this alias.
OcppPayload = dict[str, Any]


def format_ocpp_timestamp(value: datetime) -> str:
    """Format a timezone-aware datetime as an OCPP UTC timestamp.

    Args:
        value: Timezone-aware datetime.

    Returns:
        ISO-8601 UTC string with milliseconds ending in ``Z``, for example
        ``2026-09-24T10:00:00.000Z`` (the form used in ``currentTime``).

    Raises:
        ValueError: If ``value`` has no timezone.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("OCPP timestamp must have a timezone")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def parse_ocpp_timestamp(value: str) -> datetime:
    """Parse an OCPP timestamp into a timezone-aware datetime.

    Args:
        value: ISO-8601 timestamp in the OCPP payload.

    Returns:
        A datetime keeping its timezone so the service can normalize it to
        UTC.

    Raises:
        ValueError: If the timestamp has no timezone or has an invalid
            format. The OCPP 2.0.1 adapter keeps this strict rule; the 1.6J
            adapter uses ``parse_ocpp_timestamp_assuming_utc`` instead (CO-16).
    """
    timestamp = parse_ocpp_timestamp_assuming_utc(value)
    if not timestamp.had_timezone:
        raise ValueError("OCPP timestamp must have a timezone")
    return timestamp.value


@dataclass(frozen=True, slots=True)
class OcppTimestamp:
    """A parsed OCPP timestamp and whether the charger sent its timezone.

    Attributes:
        value: The timezone-aware time; UTC when the charger sent none.
        had_timezone: ``False`` when the text carried no offset and UTC was
            assumed, so the caller can log it.
    """

    value: datetime
    had_timezone: bool


def parse_ocpp_timestamp_assuming_utc(value: str) -> OcppTimestamp:
    """Parse an OCPP timestamp, reading one without a timezone as UTC (CO-16).

    A charger may send local time without an offset; refusing it would fail
    every session of that charger, so the time is taken as UTC and the caller
    logs a warning (the raw message log keeps the original text). Whether the
    firmware does this is confirmed on the real charger.

    Args:
        value: ISO-8601 timestamp in the OCPP payload.

    Returns:
        The timezone-aware time and whether the text carried a timezone.

    Raises:
        ValueError: If the text is not an ISO-8601 timestamp.
    """
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        return OcppTimestamp(timestamp.replace(tzinfo=timezone.utc), False)
    return OcppTimestamp(timestamp, True)
