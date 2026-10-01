"""The device-silence rule shared by the health monitor and the device API (F-J1).

Pure, no I/O. One rule, so the silent-device alert raised by
``device_health_monitor`` and the ``is_silent`` flag on ``TelematicResponse``
can never disagree.

Eligibility is the caller's precondition, and both callers apply the same
one: the device is ``ACTIVE`` and mounted on a live (not soft-deleted)
vehicle. A device taken out of service (``INACTIVE``/``MAINTENANCE``) or
not mounted is never silent: its quiet is expected, not an incident.
"""

from datetime import datetime, timedelta

from app.libs.common.config import settings


def calculate_is_device_silent(last_seen_at: datetime | None, *, now: datetime) -> bool:
    """Decide whether an eligible device has gone silent.

    Rule:
        Silent when its newest telemetry arrived at least
        ``settings.TELEMATICS_SILENT_THRESHOLD_MINUTES`` before ``now``. A
        device that never reported is **not** silent: that is a
        provisioning gap (F-F2) with no anchor to measure silence from, and
        the monitor raises no alert for it either.

    Args:
        last_seen_at: Newest telemetry ``received_at`` of the device's
            vehicle, or ``None`` if it never reported.
        now: Current time (UTC), injected so one sweep uses one instant.

    Returns:
        ``True`` if the device is silent under the rule above.
    """
    if last_seen_at is None:
        return False
    silent_threshold = timedelta(minutes=settings.TELEMATICS_SILENT_THRESHOLD_MINUTES)
    return now - last_seen_at >= silent_threshold
