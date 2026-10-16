"""Who may manage driver profiles and sessions, and whether a licence is valid (DRV-01, DR-07).

Shared by the profile functions in ``service.py`` and the driving-session
functions in ``driving_session_service.py``. Internal module: other domains
use ``service.py``.
"""

from datetime import date
from zoneinfo import ZoneInfo

from app.domains.identity.types import (
    UserRole,
    roles_for,
)
from app.libs.common.clock import utc_now
from app.libs.common.config import settings

# Roles that may act for another driver (check a driver in or out): the
# users of DRV-02 except the driver themselves; a DRIVER-only caller acts on
# their own profile.
DRIVER_MANAGER_ROLES = roles_for("DRV-02") - {UserRole.DRIVER}


def is_license_expired(license_expires_on: date) -> bool:
    """Tell whether a licence expiry date is already past (computed, DR-09).

    Args:
        license_expires_on: Expiry date printed on the licence.

    Returns:
        True when the date is before today in ``APP_REPORT_TIMEZONE`` (the
        printed expiry is a local calendar date); the expiry day itself is
        still valid.
    """
    local_today = utc_now().astimezone(ZoneInfo(settings.APP_REPORT_TIMEZONE)).date()
    return license_expires_on < local_today
