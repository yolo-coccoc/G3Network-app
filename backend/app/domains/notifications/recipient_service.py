"""Applies the routing table to one new alert (NTF-06, NT-15).

``routing.py`` says who may receive each kind of alert; this module finds the
people. Role holders and our own staff come from the identity service. The two
truck-related questions (who is at the wheel, which fleet-limited managers may
see the truck) are answered by the ``drivers`` and ``fleet`` domains, which
this domain may not import (they depend on it, through the producers), so the
HTTP layer registers them once at start-up (``VehicleAudienceHooks``). A
process that did not register them (a script) still routes by role: the
driver is skipped and fleet-limited managers receive every alert of the
organization, which errs on telling too many people, never too few.
"""

import logging
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.service as identity_service
from app.domains.notifications.routing import get_routing_rule
from app.domains.notifications.types import NotificationContext, VehicleAudienceHooks

logger = logging.getLogger(__name__)

# The drivers / fleet answers, filled once at start-up by
# ``app/api/notification_hooks.py`` (None until then).
_vehicle_audience_hooks: VehicleAudienceHooks | None = None


def register_vehicle_audience_hooks(hooks: VehicleAudienceHooks) -> None:
    """Register how routing asks the drivers and fleet domains about a truck.

    Args:
        hooks: The two answers; replaces any earlier registration.

    Side Effects:
        Sets the process-wide hooks (done at start-up of every process, through
        ``app.api.startup.register_all_hooks``).
    """
    global _vehicle_audience_hooks
    _vehicle_audience_hooks = hooks


async def resolve_recipient_user_ids(
    db_session: AsyncSession,
    context: NotificationContext,
    *,
    extra_user_ids: Sequence[UUID] = (),
) -> list[UUID]:
    """Find the people who receive an alert.

    Rule:
        The union of (1) the active holders of the rule's roles in the alert's
        organization, narrowed for fleet-limited managers when the rule is
        fleet-scoped and the alert has a truck (FL-10), (2) the active holders
        of the rule's internal roles in our own organizations, (3) the driver
        checked in to the truck when the rule asks (NT-07), and (4) the people
        the producer named. Each person once.

    Args:
        db_session: Session owned by the producer's entry boundary.
        context: The alert.
        extra_user_ids: People the producer addresses explicitly (a trip's
            driver).

    Returns:
        The distinct user IDs, in discovery order.

    Side Effects:
        Read-only queries.
    """
    rule = get_routing_rule(context.notification_type)
    recipients: dict[UUID, None] = {}

    if rule.roles:
        holders = await identity_service.list_organization_role_holders(
            db_session,
            context.organization_id,
            sorted(rule.roles, key=lambda role: role.value),
        )
        limited_holders = [holder for holder in holders if holder.is_fleet_limited]
        use_scope = (
            rule.is_fleet_scoped
            and context.vehicle_id is not None
            and _vehicle_audience_hooks is not None
        )
        for holder in holders:
            if not use_scope or not holder.is_fleet_limited:
                recipients[holder.user_id] = None
        if use_scope and limited_holders and _vehicle_audience_hooks is not None:
            assert context.vehicle_id is not None
            visible_user_ids = (
                await _vehicle_audience_hooks.filter_role_holders_by_vehicle(
                    db_session,
                    context.organization_id,
                    context.vehicle_id,
                    limited_holders,
                )
            )
            for user_id in visible_user_ids:
                recipients[user_id] = None

    if rule.internal_roles:
        for user_id in await identity_service.list_internal_role_holder_user_ids(
            db_session, sorted(rule.internal_roles, key=lambda role: role.value)
        ):
            recipients[user_id] = None

    if (
        rule.include_checked_in_driver
        and context.vehicle_id is not None
        and _vehicle_audience_hooks is not None
    ):
        driver_user_id = await _vehicle_audience_hooks.find_checked_in_driver_user_id(
            db_session, context.vehicle_id
        )
        if driver_user_id is not None:
            recipients[driver_user_id] = None

    for user_id in extra_user_ids:
        recipients[user_id] = None
    return list(recipients)
