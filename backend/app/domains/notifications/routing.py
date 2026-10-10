"""Who receives which kind of alert, and the channel defaults (NTF-06, NT-15).

One table, ``ROUTING_RULES``, holds for every ``NotificationType`` the roles
that receive it, whether the driver checked in to the truck does too (NT-07),
whether a fleet-limited manager only receives alerts about trucks they may see
(FL-10), and the default push / e-mail switches an organization starts with
(NT-12: push on for every kind, e-mail on for the critical kinds). A new kind
of alert adds one row here and one value to ``NotificationType``; the code
that applies the table is ``recipient_service.py``.

Roles only, no plan: until plans exist (BL-16) a person receives a kind when
their role is in the rule. Data scope is the organization of the alert, plus
the driver at the wheel (even from another organization) and our own staff
roles in ``internal_roles`` (who see every organization, ID-44).
"""

from dataclasses import dataclass

from app.domains.identity.types import UserRole
from app.domains.notifications.types import NotificationType


@dataclass(frozen=True)
class RoutingRule:
    """The audience and default channels of one kind of alert.

    Attributes:
        roles: Roles that receive the alert in the alert's organization.
        internal_roles: Roles of our own organizations that receive it
            whichever customer it belongs to (e.g. customer care for an SOS).
        include_checked_in_driver: Also the person at the wheel of the
            alert's truck, even from another organization (NT-07).
        is_fleet_scoped: A fleet-limited manager receives it only when the
            alert's truck is in a fleet they may see (FL-10); has no effect on
            an alert with no truck.
        push_by_default: Push on when the organization has no setting row.
        email_by_default: E-mail on when the organization has no setting row.
    """

    roles: frozenset[UserRole] = frozenset()
    internal_roles: frozenset[UserRole] = frozenset()
    include_checked_in_driver: bool = False
    is_fleet_scoped: bool = False
    push_by_default: bool = True
    email_by_default: bool = False


_FLEET_STAFF = frozenset({UserRole.FLEET_MANAGER, UserRole.ORG_ADMIN})
_DISPATCH_STAFF = frozenset(
    {UserRole.FLEET_MANAGER, UserRole.DISPATCHER, UserRole.ORG_ADMIN}
)

# An alert addressed to one named person (a trip assigned to a driver, a
# wallet movement, a receipt) has no role audience: the producer passes the
# person to ``create_notification`` as an explicit recipient.
_NAMED_PERSON_ONLY = RoutingRule()

ROUTING_RULES: dict[NotificationType, RoutingRule] = {
    NotificationType.BATTERY_ALERT: RoutingRule(
        roles=_FLEET_STAFF, include_checked_in_driver=True, is_fleet_scoped=True
    ),
    NotificationType.ANOMALY_ALERT: RoutingRule(
        roles=_FLEET_STAFF, is_fleet_scoped=True
    ),
    NotificationType.SOH_ALERT: RoutingRule(roles=_FLEET_STAFF, is_fleet_scoped=True),
    NotificationType.DEVICE_OFFLINE_ALERT: RoutingRule(
        roles=_FLEET_STAFF, is_fleet_scoped=True
    ),
    NotificationType.SOS_ALERT: RoutingRule(
        roles=_FLEET_STAFF,
        internal_roles=frozenset({UserRole.CUSTOMER_CARE}),
        is_fleet_scoped=True,
        email_by_default=True,
    ),
    NotificationType.GEOFENCE_ALERT: RoutingRule(
        roles=_DISPATCH_STAFF, is_fleet_scoped=True
    ),
    NotificationType.NO_DRIVER_CHECK_IN_ALERT: RoutingRule(
        roles=_DISPATCH_STAFF, is_fleet_scoped=True
    ),
    NotificationType.OUTSIDE_DRIVER_CHECK_IN: RoutingRule(
        roles=_DISPATCH_STAFF, is_fleet_scoped=True
    ),
    NotificationType.NO_TRIP_STARTED: RoutingRule(
        roles=frozenset({UserRole.DISPATCHER}),
        include_checked_in_driver=True,
        is_fleet_scoped=True,
    ),
    NotificationType.TRIP_ASSIGNED: _NAMED_PERSON_ONLY,
    NotificationType.LOW_WALLET_BALANCE: _NAMED_PERSON_ONLY,
    NotificationType.TOP_UP_RECEIVED: _NAMED_PERSON_ONLY,
    NotificationType.CHARGING_RECEIPT: _NAMED_PERSON_ONLY,
}


def get_routing_rule(notification_type: NotificationType) -> RoutingRule:
    """Return the routing rule of a kind of alert.

    Args:
        notification_type: The kind of alert.

    Returns:
        Its rule from ``ROUTING_RULES``.
    """
    return ROUTING_RULES[notification_type]
