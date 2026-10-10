"""Wiring of notification routing to the drivers and fleet domains (NTF-06, NT-15).

Routing an alert needs two answers that live in domains which raise alerts
themselves: who is at the wheel of a truck (``drivers``, NT-07) and which
fleet-limited managers may see a truck (``fleet``, FL-10). The
``notifications`` domain may not import them (it would close a cycle with the
producers), so this module, above the domains like ``billing_hooks.py``,
registers both answers on ``notifications.service`` at start-up. Every process
registers them through ``app.api.startup.register_all_hooks`` (CV-21); without
them routing falls back to roles only, see ``notifications.recipient_service``.
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.drivers.service as driver_service
import app.domains.fleet.service as fleet_service
import app.domains.notifications.service as notification_service
from app.domains.identity.types import RoleHolderReference
from app.domains.notifications.types import VehicleAudienceHooks


async def find_checked_in_driver_user_id(
    db_session: AsyncSession, vehicle_id: UUID
) -> UUID | None:
    """Find the person at the wheel of a truck now.

    Args:
        db_session: The producer's session.
        vehicle_id: The truck.

    Returns:
        The checked-in driver's user ID, or `None`.
    """
    return await driver_service.find_checked_in_user_id_by_vehicle(
        db_session, vehicle_id
    )


async def filter_role_holders_by_vehicle(
    db_session: AsyncSession,
    organization_id: UUID,
    vehicle_id: UUID,
    holders: Sequence[RoleHolderReference],
) -> list[UUID]:
    """Keep the fleet-limited managers who may see a truck (FL-10).

    Args:
        db_session: The producer's session.
        organization_id: The organization the managers act for.
        vehicle_id: The truck the alert is about.
        holders: Managers that may be limited to some fleets.

    Returns:
        User IDs of the holders with no limit, or whose visible fleets hold the
        truck; one fleet-visibility lookup per holder (no batching, MVP rule).
    """
    visible_user_ids: list[UUID] = []
    for holder in holders:
        visible_vehicle_ids = await fleet_service.resolve_visible_vehicle_ids(
            db_session, holder.membership_id, organization_id
        )
        if visible_vehicle_ids is None or vehicle_id in visible_vehicle_ids:
            visible_user_ids.append(holder.user_id)
    return visible_user_ids


def register_notification_hooks() -> None:
    """Register the drivers and fleet answers behind notification routing.

    Side Effects:
        Sets the process-wide hooks of `notifications.service`; idempotent.
    """
    notification_service.register_vehicle_audience_hooks(
        VehicleAudienceHooks(
            find_checked_in_driver_user_id=find_checked_in_driver_user_id,
            filter_role_holders_by_vehicle=filter_role_holders_by_vehicle,
        )
    )
