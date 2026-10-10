"""Shared types for the notifications domain.

A single generic notification contract serves every alert-producing feature
(F-A2, F-A4, F-A3, F-J1/F-J3 today; F-B5 later) so each one only needs to
add a ``NotificationType`` member and shape its own ``payload`` - not a new
table, migration, or endpoint. See ``docs/decisions/deferred.md`` for
the gaps this intentionally leaves open. Who receives a kind of alert is the
rule table in ``routing.py`` (NTF-06), how it is delivered is ``delivery.py``
(NTF-02, NTF-04) behind the providers of ``providers.py``.
"""

import enum
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID


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
    TRIP_ASSIGNED = "TRIP_ASSIGNED"  # NT-15, DR-12
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


@dataclass(frozen=True)
class NotificationContext:
    """What routing and delivery need to know about one new alert.

    Attributes:
        notification_id: Internal ID of the stored alert.
        organization_id: The organization the alert belongs to.
        notification_type: Kind of event that raised it.
        severity: How urgent it is.
        vehicle_id: The truck it concerns, if any.
        subject_type: Which screen the app opens, if any.
        subject_id: ID of that object, if any.
        title: Short summary (the push title and e-mail subject).
        body: Longer description (the push text and e-mail body).
    """

    notification_id: int
    organization_id: UUID
    notification_type: NotificationType
    severity: NotificationSeverity
    vehicle_id: UUID | None
    subject_type: str | None
    subject_id: UUID | None
    title: str
    body: str


# ``find(db, vehicle_id) -> UUID | None`` and
# ``filter(db, organization_id, vehicle_id, holders) -> list[UUID]``; the
# parameters are spelled out in ``VehicleAudienceHooks`` below.
FindCheckedInDriverUserId = Callable[..., Awaitable[UUID | None]]
FilterRoleHoldersByVehicle = Callable[..., Awaitable[list[UUID]]]


@dataclass(frozen=True)
class VehicleAudienceHooks:
    """The two truck-related questions routing cannot answer on its own.

    ``notifications`` may not call ``drivers`` or ``fleet`` (it would close a
    cycle with the domains that raise alerts), so the HTTP layer registers the
    answers at start-up (``app/api/notification_hooks.py``, NT-15).

    Attributes:
        find_checked_in_driver_user_id: ``(db, vehicle_id)`` to the person at
            the wheel of the truck now, or ``None``.
        filter_role_holders_by_vehicle: ``(db, organization_id, vehicle_id,
            holders)`` to the user IDs among the holders who may see the truck
            (a fleet-limited manager only sees the trucks of their fleets,
            FL-10).
    """

    find_checked_in_driver_user_id: FindCheckedInDriverUserId
    filter_role_holders_by_vehicle: FilterRoleHoldersByVehicle


@dataclass(frozen=True)
class ChannelSwitches:
    """Whether push and e-mail are on for one organization and kind of alert.

    Attributes:
        push_enabled: Send the alert as a push.
        email_enabled: Send it by e-mail to recipients with an address.
        is_default: True when the organization has no row for the kind and the
            default from code applies (NT-12).
    """

    push_enabled: bool
    email_enabled: bool
    is_default: bool
