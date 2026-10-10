"""Shared types for the notifications domain.

A single generic notification contract serves every alert-producing feature
(F-A2, F-A4, F-A3, F-J1/F-J3 today; F-B5 later) so each one only needs to
add a ``NotificationType`` member and shape its own ``payload`` - not a new
table, migration, or endpoint. See ``docs/decisions/deferred.md`` for
the recipient-scoping and multi-channel-delivery gaps this intentionally
leaves open.
"""

import enum
from dataclasses import dataclass


class NotificationType(str, enum.Enum):
    """Kind of event a notification was raised for."""

    BATTERY_ALERT = "BATTERY_ALERT"
    ANOMALY_ALERT = "ANOMALY_ALERT"  # F-A4
    SOH_ALERT = "SOH_ALERT"  # F-A3
    DEVICE_OFFLINE_ALERT = "DEVICE_OFFLINE_ALERT"  # F-J1, F-J3 partial
    SOS_ALERT = "SOS_ALERT"  # F-I2
    GEOFENCE_ALERT = "GEOFENCE_ALERT"  # F-A5
    NO_DRIVER_CHECK_IN_ALERT = "NO_DRIVER_CHECK_IN_ALERT"  # DR-07
    OUTSIDE_DRIVER_CHECK_IN = "OUTSIDE_DRIVER_CHECK_IN"  # DR-07
    NO_TRIP_STARTED = "NO_TRIP_STARTED"  # DR-12
    LOW_WALLET_BALANCE = "LOW_WALLET_BALANCE"  # BL-14
    TOP_UP_RECEIVED = "TOP_UP_RECEIVED"  # BL-15
    CHARGING_RECEIPT = "CHARGING_RECEIPT"  # BL-10


class NotificationSeverity(str, enum.Enum):
    """Severity of a notification, independent of its type."""

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class NotificationListOrder(str, enum.Enum):
    """Order of a notification list.

    Attributes:
        ASC: Oldest first, after the ``after_id`` cursor - the polling
            contract (the client passes back ``latest_notification_id``).
        DESC: Newest first, for a notification centre; the cursor is
            ignored.
    """

    ASC = "asc"
    DESC = "desc"


@dataclass(frozen=True)
class NotificationReference:
    """Minimal reference to a notification just created.

    Attributes:
        notification_id: Internal ID of the notification.
    """

    notification_id: int
