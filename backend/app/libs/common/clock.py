"""The backend's single source of "now".

All persisted timestamps are timezone-aware UTC (see the time convention in
``.claude/rules/backend-runtime-conventions.md``); ORM column defaults,
repositories and services use this function instead of each module
defining its own.
"""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime.

    Returns:
        ``datetime.now(timezone.utc)``.
    """
    return datetime.now(timezone.utc)
