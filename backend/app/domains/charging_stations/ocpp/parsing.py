"""Protocol-neutral parsing helpers shared by the OCPP adapters.

Both the OCPP 2.0.1 and the OCPP 1.6J adapters (and the gateway itself) need the
same primitives: the plain-dict payload alias and strict timestamp parsing and
formatting.
They live here so the adapters never import each other or the server module,
which would create an import cycle (the server imports the adapters).

Scope: only pure helpers with no protocol-specific field names and no I/O.
Anything that knows about a 2.0.1 or 1.6J message shape belongs to that
protocol's adapter module.
"""

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
            format. The strict rule is deliberate (decision D11 of the OCPP
            1.6J planner): it is revisited only once real charger logs show
            what the firmware actually sends.
    """
    normalized = value.replace("Z", "+00:00")
    timestamp = datetime.fromisoformat(normalized)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("OCPP timestamp must have a timezone")
    return timestamp
