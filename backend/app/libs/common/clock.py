"""The backend's single source of "now".

All persisted timestamps are timezone-aware UTC (see the time convention in
``.claude/rules/backend-runtime-conventions.md``); ORM column defaults,
repositories and services use this function instead of each module
defining its own.
"""

from datetime import datetime, timedelta, timezone

# How far a date typed on a client (a purchase, a fitting, a handover) may run
# ahead of the server clock before it counts as "in the future" and is refused
# (RV-AS4). One value for every domain that checks such a date.
CLIENT_CLOCK_SKEW = timedelta(minutes=5)


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime.

    Returns:
        ``datetime.now(timezone.utc)``.
    """
    return datetime.now(timezone.utc)
