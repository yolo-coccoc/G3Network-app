"""Fleet reach and the fleet limit (FL-10, FL-13).

Which fleets and trucks a caller may see: the fleets assigned to a member and
every fleet below them, the trucks currently in those fleets (``None`` = no
limit), and the loader that returns a fleet only inside the caller's data
scope and limit (otherwise "not found"). Shared by ``service.py`` and the
area modules; ``service.py`` wraps the two visibility functions other code
calls. Internal module: other domains use ``service.py``.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.fleet.repository as fleet_repository
from app.domains.fleet.exceptions import (
    FleetNotFoundError,
)
from app.domains.fleet.models import (
    FleetModel,
)
from app.domains.identity.types import Principal


async def get_fleet_record(
    db_session: AsyncSession, fleet_id: UUID, principal: Principal
) -> FleetModel:
    """Load a live fleet inside the caller's data reach, or raise.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.
        principal: The caller; a fleet of another organization does not exist
            for them unless they are internal, nor does a fleet outside the
            fleets they are limited to (FL-10).

    Returns:
        The fleet record.

    Raises:
        FleetNotFoundError: When the fleet does not exist, was soft-deleted
            or is outside the caller's reach.
    """
    fleet_record = await fleet_repository.get_by_id(
        db_session, fleet_id, organization_id=principal.data_scope
    )
    if fleet_record is None:
        raise FleetNotFoundError(f"Fleet with id '{fleet_id}' not found")
    visible_fleet_ids = await resolve_principal_visible_fleet_ids(db_session, principal)
    if visible_fleet_ids is not None and fleet_id not in visible_fleet_ids:
        raise FleetNotFoundError(f"Fleet with id '{fleet_id}' not found")
    return fleet_record


async def list_descendant_fleet_ids(db: AsyncSession, fleet_id: UUID) -> list[UUID]:
    """List every live fleet below a fleet, at any depth. Public cross-domain entry point.

    Walks the tree one fleet at a time (one query per fleet, no batching;
    trees are a few levels deep) with a visited set, so a damaged loop in the
    data still ends. The fleet itself is not included.

    Args:
        db: Session owned by the caller's entry boundary.
        fleet_id: Internal ID of the top fleet. Not checked: an unknown or
            deleted fleet simply has no descendants.

    Returns:
        IDs of the fleets below, nearest first.

    Side Effects:
        Read-only; does not commit or rollback.
    """
    descendant_ids: list[UUID] = []
    visited_ids: set[UUID] = {fleet_id}
    pending_ids = [fleet_id]
    while pending_ids:
        parent_id = pending_ids.pop(0)
        for child_id in await fleet_repository.list_child_fleet_ids(db, parent_id):
            if child_id in visited_ids:
                continue
            visited_ids.add(child_id)
            descendant_ids.append(child_id)
            pending_ids.append(child_id)
    return descendant_ids


async def resolve_visible_fleet_ids(
    db: AsyncSession, principal_membership_id: UUID, organization_id: UUID
) -> frozenset[UUID] | None:
    """Resolve the fleets a membership's fleet-level roles are limited to (FL-10).

    Public cross-domain entry point. The rule is the union of the fleets
    assigned to the membership (open `fleet_user_assignments` rows) and every
    fleet below them.

    Args:
        db: Session owned by the caller's entry boundary.
        principal_membership_id: The membership (person in the organization).
        organization_id: The organization the membership acts for; an
            assigned fleet owned by another organization (or deleted since)
            adds nothing.

    Returns:
        `None` when the membership has no open assignment: no limit, the
        whole organization. Otherwise the visible fleet IDs; the set is empty
        when every assigned fleet has vanished, so the limit then fails closed
        and shows nothing rather than everything.

    Side Effects:
        Read-only; one query for the assignments, then one per assigned fleet
        and per fleet of its subtree (no batching).
    """
    assignment_records = await fleet_repository.list_open_assignments_by_membership(
        db, principal_membership_id
    )
    if not assignment_records:
        return None
    visible_ids: set[UUID] = set()
    for assignment_record in assignment_records:
        fleet_record = await fleet_repository.get_by_id(
            db, assignment_record.fleet_id, organization_id=organization_id
        )
        if fleet_record is None:
            continue
        visible_ids.add(fleet_record.fleet_id)
        visible_ids.update(await list_descendant_fleet_ids(db, fleet_record.fleet_id))
    return frozenset(visible_ids)


async def resolve_visible_vehicle_ids(
    db: AsyncSession, principal_membership_id: UUID, organization_id: UUID
) -> frozenset[UUID] | None:
    """Resolve the trucks a membership's fleet limit lets it see (FL-10).

    Public cross-domain entry point.

    Args:
        db: Session owned by the caller's entry boundary.
        principal_membership_id: The membership (person in the organization).
        organization_id: The organization the membership acts for.

    Returns:
        `None` when the membership is not limited. Otherwise the IDs of the
        trucks that currently have an open fleet membership in a visible
        fleet (a truck in no fleet is not visible to a limited member).

    Side Effects:
        Read-only; see `resolve_visible_fleet_ids`, plus one query for the
        trucks.
    """
    visible_fleet_ids = await resolve_visible_fleet_ids(
        db, principal_membership_id, organization_id
    )
    if visible_fleet_ids is None:
        return None
    if not visible_fleet_ids:
        return frozenset()
    return frozenset(
        await fleet_repository.list_active_vehicle_ids_by_fleet_ids(
            db, visible_fleet_ids
        )
    )


async def resolve_principal_visible_fleet_ids(
    db: AsyncSession, principal: Principal
) -> frozenset[UUID] | None:
    """Resolve the fleet limit of an HTTP caller (FL-10).

    Public cross-domain entry point for endpoints. Internal staff, the
    organization administrator and roles that are not fleet-level are never
    limited and cost no query.

    Args:
        db: Session owned by the caller's entry boundary.
        principal: The authenticated caller.

    Returns:
        `None` for no limit, else the visible fleet IDs
        (see `resolve_visible_fleet_ids`).
    """
    if not principal.is_fleet_limited:
        return None
    return await resolve_visible_fleet_ids(
        db, principal.membership_id, principal.organization_id
    )


async def resolve_principal_visible_vehicle_ids(
    db: AsyncSession, principal: Principal
) -> frozenset[UUID] | None:
    """Resolve the truck limit of an HTTP caller (FL-10).

    Public cross-domain entry point; the function the HTTP layer registers
    for the vehicle endpoints (`app/api/fleet_visibility.py`).

    Args:
        db: Session owned by the caller's entry boundary.
        principal: The authenticated caller.

    Returns:
        `None` for no limit, else the visible truck IDs
        (see `resolve_visible_vehicle_ids`).
    """
    if not principal.is_fleet_limited:
        return None
    return await resolve_visible_vehicle_ids(
        db, principal.membership_id, principal.organization_id
    )
