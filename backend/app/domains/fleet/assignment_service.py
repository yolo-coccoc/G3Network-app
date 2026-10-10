"""Business rules for the fleet limits of a membership (FL-10, FLT-03).

A membership with no open row in ``fleet_user_assignments`` covers every fleet
of its organization; with open rows its fleet-level roles (``FLEET_MANAGER``,
``DISPATCHER``) cover those fleets and every fleet below them. This module
gives a fleet to a membership, takes it away and lists the rows from either
side. The visible-set computation that the other endpoints use lives in
``service.py`` (it is the public cross-domain surface); this module is
internal to the fleet domain.

Only the organization administrator and internal staff call it (FLT-03); the
router enforces the role, this module enforces the data scope and the
same-organization rule of the table (the table keeps no ``organization_id`` of
its own, DM-24).
"""

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.fleet.repository as fleet_repository
import app.domains.identity.service as identity_service
from app.domains.fleet.exceptions import (
    FleetNotFoundError,
    FleetUserAssignmentConflictError,
    FleetUserAssignmentMembershipNotFoundError,
    FleetUserAssignmentNotFoundError,
    FleetUserAssignmentOrganizationMismatchError,
)
from app.domains.fleet.models import FleetUserAssignmentModel
from app.domains.fleet.schemas import (
    FleetUserAssignmentListResponse,
    FleetUserAssignmentResponse,
)
from app.domains.identity.types import MembershipPersonReference, Principal
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.pagination import normalize_page_window

# Warning code of a fleet that an assigned fleet above it already covers.
WARNING_COVERED_BY_ASSIGNED_PARENT = "FLEET_ALREADY_COVERED_BY_ASSIGNED_PARENT"


async def _resolve_membership_in_reach(
    db_session: AsyncSession, membership_id: UUID, principal: Principal
) -> MembershipPersonReference:
    """Load a membership that the caller may manage, or raise.

    Args:
        db_session: Current database session.
        membership_id: Internal ID of the membership.
        principal: The caller (data scope).

    Returns:
        The membership with the person behind it.

    Raises:
        FleetUserAssignmentMembershipNotFoundError: When the membership does
            not exist or belongs to an organization out of the caller's reach.
    """
    membership_reference = await identity_service.resolve_membership_person_reference(
        db_session, membership_id
    )
    if membership_reference is None or not principal.can_access_organization(
        membership_reference.organization_id
    ):
        raise FleetUserAssignmentMembershipNotFoundError(
            f"Membership '{membership_id}' not found"
        )
    return membership_reference


async def _find_assigned_ancestor_id(
    db_session: AsyncSession, fleet_parent_id: UUID | None, membership_id: UUID
) -> UUID | None:
    """Find the nearest fleet above a fleet that the membership already holds.

    Args:
        db_session: Current database session.
        fleet_parent_id: Parent of the fleet being assigned, or `None`.
        membership_id: The membership.

    Returns:
        The ID of the nearest assigned ancestor, or `None` when no fleet above
        is assigned.

    Side Effects:
        Walks up one fleet at a time with a visited set; read-only.
    """
    visited_fleet_ids: set[UUID] = set()
    ancestor_id = fleet_parent_id
    while ancestor_id is not None and ancestor_id not in visited_fleet_ids:
        visited_fleet_ids.add(ancestor_id)
        if (
            await fleet_repository.find_open_assignment(
                db_session, ancestor_id, membership_id
            )
            is not None
        ):
            return ancestor_id
        ancestor_record = await fleet_repository.get_by_id(db_session, ancestor_id)
        ancestor_id = (
            None if ancestor_record is None else ancestor_record.parent_fleet_id
        )
    return None


async def build_assignment_response(
    db_session: AsyncSession,
    assignment_record: FleetUserAssignmentModel,
    *,
    membership_reference: MembershipPersonReference | None = None,
    warnings: list[str] | None = None,
) -> FleetUserAssignmentResponse:
    """Build an assignment response with the fleet and the person named.

    Args:
        db_session: Current database session.
        assignment_record: The assignment row.
        membership_reference: The person, when the caller already has it.
        warnings: Non-blocking notes to attach.

    Returns:
        The response; the fleet and person fields are empty when they no
        longer resolve.

    Side Effects:
        Up to two read-only queries (the fleet, the membership's person).
    """
    fleet_record = await fleet_repository.get_by_id(
        db_session, assignment_record.fleet_id
    )
    if membership_reference is None:
        membership_reference = (
            await identity_service.resolve_membership_person_reference(
                db_session, assignment_record.membership_id
            )
        )
    return FleetUserAssignmentResponse(
        fleet_user_assignment_id=assignment_record.fleet_user_assignment_id,
        fleet_id=assignment_record.fleet_id,
        fleet_code=None if fleet_record is None else fleet_record.fleet_code,
        fleet_name=None if fleet_record is None else fleet_record.name,
        membership_id=assignment_record.membership_id,
        full_name=None
        if membership_reference is None
        else membership_reference.full_name,
        phone_number=(
            None if membership_reference is None else membership_reference.phone_number
        ),
        assigned_at=assignment_record.assigned_at,
        assigned_by=assignment_record.assigned_by,
        unassigned_at=assignment_record.unassigned_at,
        unassigned_by=assignment_record.unassigned_by,
        warnings=warnings or [],
    )


async def assign_fleet_to_membership(
    db_session: AsyncSession,
    membership_id: UUID,
    fleet_id: UUID,
    *,
    principal: Principal,
) -> FleetUserAssignmentResponse:
    """Limit a membership's fleet-level roles to one more fleet (FL-10).

    Rules: the fleet and the membership must belong to the same organization;
    a fleet already held (open row) is a conflict; a fleet below one that is
    already assigned is allowed, because it keeps the access if the tree
    changes, and the response says so in ``warnings``.

    Args:
        db_session: Database session owned by the entry boundary.
        membership_id: The membership to limit.
        fleet_id: The fleet to give.
        principal: The caller, recorded as ``assigned_by``.

    Returns:
        The new open assignment.

    Raises:
        FleetUserAssignmentMembershipNotFoundError: Unknown membership, or one
            out of the caller's data reach.
        FleetNotFoundError: Unknown or deleted fleet, or one out of reach.
        FleetUserAssignmentOrganizationMismatchError: The fleet belongs to
            another organization than the membership.
        FleetUserAssignmentConflictError: The membership already holds the
            fleet.

    Side Effects:
        Inserts one row; does not commit. The first assignment of a
        membership turns its limit on: it no longer sees the other fleets.
    """
    membership_reference = await _resolve_membership_in_reach(
        db_session, membership_id, principal
    )
    fleet_record = await fleet_repository.get_by_id(
        db_session, fleet_id, organization_id=principal.data_scope
    )
    if fleet_record is None:
        raise FleetNotFoundError(f"Fleet with id '{fleet_id}' not found")
    if fleet_record.organization_id != membership_reference.organization_id:
        raise FleetUserAssignmentOrganizationMismatchError(
            f"Fleet '{fleet_id}' does not belong to the organization of "
            f"membership '{membership_id}'"
        )
    if (
        await fleet_repository.find_open_assignment(db_session, fleet_id, membership_id)
        is not None
    ):
        raise FleetUserAssignmentConflictError(
            f"Membership '{membership_id}' already holds fleet '{fleet_id}'"
        )

    warnings: list[str] = []
    if (
        await _find_assigned_ancestor_id(
            db_session, fleet_record.parent_fleet_id, membership_id
        )
        is not None
    ):
        warnings.append(WARNING_COVERED_BY_ASSIGNED_PARENT)

    try:
        assignment_record = await fleet_repository.insert_assignment(
            db_session,
            fleet_id=fleet_id,
            membership_id=membership_id,
            assigned_at=utc_now(),
            assigned_by=principal.user_id,
        )
    except IntegrityError as error:
        raise FleetUserAssignmentConflictError(
            f"Membership '{membership_id}' already holds fleet '{fleet_id}'"
        ) from error
    return await build_assignment_response(
        db_session,
        assignment_record,
        membership_reference=membership_reference,
        warnings=warnings,
    )


async def unassign_fleet_from_membership(
    db_session: AsyncSession,
    membership_id: UUID,
    fleet_id: UUID,
    *,
    principal: Principal,
) -> None:
    """Take a fleet away from a membership (FL-10).

    Taking the last fleet away lifts the limit: the membership then covers the
    whole organization again.

    Args:
        db_session: Database session owned by the entry boundary.
        membership_id: The limited membership.
        fleet_id: The fleet to take away.
        principal: The caller, recorded as ``unassigned_by``.

    Raises:
        FleetUserAssignmentMembershipNotFoundError: Unknown membership, or one
            out of the caller's data reach.
        FleetUserAssignmentNotFoundError: The membership does not hold the
            fleet now.

    Side Effects:
        Stamps ``unassigned_at`` / ``unassigned_by``; does not commit.
    """
    await _resolve_membership_in_reach(db_session, membership_id, principal)
    assignment_record = await fleet_repository.find_open_assignment(
        db_session, fleet_id, membership_id
    )
    if assignment_record is None:
        raise FleetUserAssignmentNotFoundError(
            f"Membership '{membership_id}' does not hold fleet '{fleet_id}'"
        )
    await fleet_repository.close_assignment(
        db_session,
        assignment_record,
        unassigned_at=utc_now(),
        unassigned_by=principal.user_id,
    )


async def list_assignments_by_membership(
    db_session: AsyncSession,
    membership_id: UUID,
    *,
    principal: Principal,
    include_closed: bool = False,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> FleetUserAssignmentListResponse:
    """List the fleets a membership is limited to (FL-10).

    Args:
        db_session: Current database session.
        membership_id: The membership.
        principal: The caller (data scope).
        include_closed: Also list assignments that were taken away.
        page: Page number, starting from 1.
        page_size: Maximum number of assignments per page.

    Returns:
        Paginated assignments, newest first; an empty open list means the
        membership is not limited.

    Raises:
        FleetUserAssignmentMembershipNotFoundError: Unknown membership, or one
            out of the caller's data reach.
    """
    membership_reference = await _resolve_membership_in_reach(
        db_session, membership_id, principal
    )
    page_window = normalize_page_window(page, page_size)
    assignment_records = await fleet_repository.list_assignments(
        db_session,
        membership_id=membership_id,
        include_closed=include_closed,
        offset=page_window.offset,
        limit=page_window.page_size,
    )
    total = await fleet_repository.count_assignments(
        db_session, membership_id=membership_id, include_closed=include_closed
    )
    return FleetUserAssignmentListResponse(
        items=[
            await build_assignment_response(
                db_session,
                assignment_record,
                membership_reference=membership_reference,
            )
            for assignment_record in assignment_records
        ],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def list_assignments_by_fleet(
    db_session: AsyncSession,
    fleet_id: UUID,
    *,
    principal: Principal,
    include_closed: bool = False,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> FleetUserAssignmentListResponse:
    """List the memberships limited to a fleet (FL-10).

    Only rows that name this fleet itself; members who reach it through an
    assigned parent are not listed.

    Args:
        db_session: Current database session.
        fleet_id: The fleet.
        principal: The caller (data scope).
        include_closed: Also list assignments that were taken away.
        page: Page number, starting from 1.
        page_size: Maximum number of assignments per page.

    Returns:
        Paginated assignments, newest first.

    Raises:
        FleetNotFoundError: Unknown or deleted fleet, or one out of reach.
    """
    fleet_record = await fleet_repository.get_by_id(
        db_session, fleet_id, organization_id=principal.data_scope
    )
    if fleet_record is None:
        raise FleetNotFoundError(f"Fleet with id '{fleet_id}' not found")
    page_window = normalize_page_window(page, page_size)
    assignment_records = await fleet_repository.list_assignments(
        db_session,
        fleet_id=fleet_id,
        include_closed=include_closed,
        offset=page_window.offset,
        limit=page_window.page_size,
    )
    total = await fleet_repository.count_assignments(
        db_session, fleet_id=fleet_id, include_closed=include_closed
    )
    return FleetUserAssignmentListResponse(
        items=[
            await build_assignment_response(db_session, assignment_record)
            for assignment_record in assignment_records
        ],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )
