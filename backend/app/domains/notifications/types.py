"""Shared types for the notifications domain.

A single generic notification contract serves every alert-producing feature
(F-A2 today; F-A4/F-B5/F-J3 later) so each one only needs to add a
``NotificationType`` member and shape its own ``payload`` - not a new table,
migration, or endpoint. See ``docs/01-requirements/future.md`` for the
recipient-scoping and multi-channel-delivery gaps this intentionally leaves
open.
"""

import enum
from dataclasses import dataclass


class NotificationType(str, enum.Enum):
    """Kind of event a notification was raised for."""

    BATTERY_ALERT = "BATTERY_ALERT"


class NotificationSeverity(str, enum.Enum):
    """Severity of a notification, independent of its type."""

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class NotificationReference:
    """Minimal reference to a notification just created.

    Attributes:
        notification_id: Internal ID of the notification.
    """

    notification_id: int
