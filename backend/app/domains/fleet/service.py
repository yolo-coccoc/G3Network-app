"""Business service and public contract of the fleet domain.

This module holds the business rules for fleet records, the fleet tree,
fleet-vehicle membership and fleet geofences (F-E1, F-A5), and the fleet
limit of a fleet-level user (FL-10). Other domains may only call the public
cross-domain functions at the end of this module
(`list_active_member_vehicle_ids`, `list_descendant_fleet_ids`,
`resolve_visible_fleet_ids`, `resolve_visible_vehicle_ids`,
`find_current_fleet_id_by_vehicle`, `list_geofences_containing`), which take
and return primitives or frozen DTOs - never the ORM model or HTTP response
schema of the fleet domain. The assignment endpoints (who is limited to which
fleet) live in `assignment_service.py`, internal to this domain. This
domain depends on the `vehicles` domain's public service to resolve a VIN
into a vehicle and to enrich a response with a vehicle's VIN/license
plate/status - the same one-directional edge shape already established by
`telematics -> vehicles` and `drivers -> vehicles`.

Where the code lives (CV-20, CV-23): this module holds the fleets and their
tree, and what other domains call (member trucks, the current fleet of a
truck, geofences containing a point, the sold-truck and membership-end
hooks, and wrappers for the two visibility functions). Trucks in a fleet are
in ``vehicle_membership_service.py``, geofences in ``geofence_service.py``,
member fleet limits in ``assignment_service.py``, and the reach and
fleet-limit rules every area uses in ``access.py``.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.fleet.access as fleet_access
import app.domains.fleet.repository as fleet_repository
import app.domains.identity.service as identity_service
import app.domains.vehicles.service as vehicle_service
from app.domains.fleet.exceptions import (
    FleetConflictError,
    FleetHasSubFleetsError,
    FleetHierarchyLoopError,
    FleetNotFoundError,
    FleetParentNotFoundError,
    FleetParentOrganizationMismatchError,
)
from app.domains.fleet.models import (
    FleetModel,
)
from app.domains.fleet.schemas import (
    FleetCreateRequest,
    FleetListResponse,
    FleetResponse,
    FleetTreeNodeResponse,
    FleetTreeResponse,
    FleetUpdateRequest,
)
from app.domains.fleet.types import GeofenceReference
from app.domains.identity.exceptions import AccessDeniedError
from app.domains.identity.types import MembershipEndKind, Principal
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.geo import coordinates_to_location
from app.libs.common.pagination import normalize_page_window


async def build_fleet_response(
    db_session: AsyncSession,
    fleet_record: FleetModel,
    *,
    include_descendants: bool = False,
) -> FleetResponse:
    """Build a fleet response enriched with its current vehicle count.

    Args:
        db_session: Current database session.
        fleet_record: Fleet ORM record already queried, created, or
            updated by the caller.
        include_descendants: Count the trucks of every fleet below this one
            too (the roll-up of FLT-01), not only the trucks directly in it.

    Returns:
        Response data with `vehicle_count` populated from the fleet's
        open memberships.

    Side Effects:
        One count query against this domain's own membership table, or, with
        `include_descendants`, a walk down the tree and one query for the
        trucks of the whole subtree - read-only; does not commit or rollback.
    """
    if include_descendants:
        subtree_fleet_ids = frozenset(
            [
                fleet_record.fleet_id,
                *await fleet_access.list_descendant_fleet_ids(
                    db_session, fleet_record.fleet_id
                ),
            ]
        )
        vehicle_count = len(
            await fleet_repository.list_active_vehicle_ids_by_fleet_ids(
                db_session, subtree_fleet_ids
            )
        )
    else:
        vehicle_count = await fleet_repository.count_active_memberships_by_fleet(
            db_session, fleet_record.fleet_id
        )
    return FleetResponse(
        fleet_id=fleet_record.fleet_id,
        organization_id=fleet_record.organization_id,
        fleet_code=fleet_record.fleet_code,
        name=fleet_record.name,
        parent_fleet_id=fleet_record.parent_fleet_id,
        vehicle_count=vehicle_count,
        created_at=fleet_record.created_at,
        updated_at=fleet_record.updated_at,
    )


async def _ensure_valid_parent_fleet(
    db_session: AsyncSession,
    parent_fleet_id: UUID,
    *,
    organization_id: UUID,
    moved_fleet_id: UUID | None,
) -> None:
    """Check that a fleet may sit under `parent_fleet_id` (FL-02, FL-08).

    The parent must be a live fleet of the same organization, and when an existing fleet is moved,
    the parent must be neither that fleet nor any fleet below it, or the
    tree would become a loop. Walks up from the new parent one fleet at a
    time (one query per level, no batching); fleet trees are a few levels
    deep.

    Two concurrent moves (A under B and B under A) can each pass this check
    and still form a loop: the walk keeps a visited set so it always ends,
    and preventing the race needs row locks, not worth it for an
    administrator's rare edit.

    Args:
        db_session: Current database session.
        parent_fleet_id: Internal ID of the proposed parent fleet.
        organization_id: Organization of the fleet that is created or moved;
            the parent must belong to it.
        moved_fleet_id: Internal ID of the fleet being moved, or `None`
            when a new fleet is created (it cannot be anyone's ancestor).

    Raises:
        FleetParentNotFoundError: When the parent does not exist or was
            soft-deleted.
        FleetParentOrganizationMismatchError: When the parent belongs to
            another organization.
        FleetHierarchyLoopError: When the parent is the moved fleet itself
            or one of its sub-fleets.

    Side Effects:
        Read-only; does not commit or rollback.
    """
    parent_fleet_record = await fleet_repository.get_by_id(db_session, parent_fleet_id)
    if parent_fleet_record is None:
        raise FleetParentNotFoundError(
            f"Parent fleet with id '{parent_fleet_id}' not found"
        )
    if parent_fleet_record.organization_id != organization_id:
        raise FleetParentOrganizationMismatchError(
            f"Parent fleet '{parent_fleet_id}' belongs to another organization"
        )
    if moved_fleet_id is None:
        return

    visited_fleet_ids: set[UUID] = set()
    ancestor_record: FleetModel | None = parent_fleet_record
    while (
        ancestor_record is not None
        and ancestor_record.fleet_id not in visited_fleet_ids
    ):
        if ancestor_record.fleet_id == moved_fleet_id:
            raise FleetHierarchyLoopError(
                f"Fleet '{moved_fleet_id}' cannot be moved under itself or "
                "under one of its own sub-fleets"
            )
        visited_fleet_ids.add(ancestor_record.fleet_id)
        if ancestor_record.parent_fleet_id is None:
            return
        ancestor_record = await fleet_repository.get_by_id(
            db_session, ancestor_record.parent_fleet_id
        )


async def create_fleet(
    db_session: AsyncSession,
    fleet_create_request: FleetCreateRequest,
    *,
    principal: Principal,
) -> FleetResponse:
    """Create a new fleet, optionally under a parent fleet.

    Rules: a given fleet code must not be used by another live fleet of the
    same organization, and a given parent must be a live fleet of the same
    organization (FL-02, FL-08). The request schema already guarantees a
    name, a code, or both (FL-08).

    Args:
        db_session: Database session owned by the entry boundary.
        fleet_create_request: Request data that has passed Pydantic validation.
        principal: The caller; the fleet belongs to the caller's organization
            unless internal staff name another one in the request.

    Returns:
        Response for the newly created fleet.

    Raises:
        FleetConflictError: When the fleet code already exists.
        FleetParentNotFoundError: When the parent fleet does not exist.
        FleetParentOrganizationMismatchError: When the parent belongs to
            another organization.
        OrganizationNotFoundError: When the named organization does not exist
            or is out of the caller's reach.
        AccessDeniedError: When a caller limited to some fleets (FL-10)
            creates a top-level fleet.
    """
    organization_id = await identity_service.resolve_organization_for_new_record(
        db_session, principal, fleet_create_request.organization_id
    )
    visible_fleet_ids = await fleet_access.resolve_principal_visible_fleet_ids(
        db_session, principal
    )
    if visible_fleet_ids is not None and (
        fleet_create_request.parent_fleet_id not in visible_fleet_ids
    ):
        # A top-level fleet would sit outside the caller's limit (FL-10).
        if fleet_create_request.parent_fleet_id is None:
            raise AccessDeniedError(
                "A manager limited to some fleets creates a fleet under one of them"
            )
        raise FleetParentNotFoundError(
            f"Parent fleet with id '{fleet_create_request.parent_fleet_id}' not found"
        )
    if fleet_create_request.fleet_code is not None:
        existing_fleet = await fleet_repository.find_by_fleet_code(
            db_session,
            organization_id,
            fleet_create_request.fleet_code,
        )
        if existing_fleet:
            raise FleetConflictError(
                f"Fleet with code '{fleet_create_request.fleet_code}' already exists"
            )
    if fleet_create_request.parent_fleet_id is not None:
        await _ensure_valid_parent_fleet(
            db_session,
            fleet_create_request.parent_fleet_id,
            organization_id=organization_id,
            moved_fleet_id=None,
        )

    try:
        fleet_record = await fleet_repository.insert(
            db_session,
            {
                **fleet_create_request.model_dump(exclude={"organization_id"}),
                "organization_id": organization_id,
            },
        )
    except IntegrityError as error:
        raise FleetConflictError("Fleet code already exists") from error

    return await build_fleet_response(db_session, fleet_record)


async def get_fleet(
    db_session: AsyncSession,
    fleet_id: UUID,
    *,
    principal: Principal,
    include_descendants: bool = False,
) -> FleetResponse:
    """Get an active fleet by ID inside the caller's data reach.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.
        principal: The caller; a fleet of another organization (or outside
            the caller's fleet limit, FL-10) is not found unless the caller
            is internal.
        include_descendants: Count the trucks of the sub-fleets too.

    Returns:
        Response for the fleet.

    Raises:
        FleetNotFoundError: When the fleet does not exist or has been soft-deleted.
    """
    fleet_record = await fleet_access.get_fleet_record(db_session, fleet_id, principal)
    return await build_fleet_response(
        db_session, fleet_record, include_descendants=include_descendants
    )


async def list_fleets(
    db_session: AsyncSession,
    *,
    principal: Principal,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
    search_text: str | None = None,
    vehicle_vin: str | None = None,
    parent_fleet_id: UUID | None = None,
    include_descendants: bool = False,
) -> FleetListResponse:
    """Get a paginated list of active fleets.

    Args:
        db_session: Current database session.
        principal: The caller; only fleets of the caller's organization are
            listed unless the caller is internal.
        page: Page number, starting from 1.
        page_size: Maximum number of fleets per page.
        search_text: Case-insensitive substring of the fleet name or fleet
            code, if any.
        vehicle_vin: Only the fleet this vehicle is currently a member of
            ("which fleet is this vehicle in"), if given. A VIN that doesn't
            resolve to an active vehicle yields an empty page, not an error:
            it is a filter, like the others.
        parent_fleet_id: Only the fleets directly under this fleet (all the
            fleets below it with `include_descendants`), if given.
        include_descendants: With `parent_fleet_id`, list every fleet below it,
            not only its children; in every case a fleet's `vehicle_count`
            then counts the trucks of its sub-fleets too (FLT-01 roll-up).

    Returns:
        Paginated fleet list response.

    Side Effects:
        Calls the vehicles domain's public service to resolve
        ``vehicle_vin``. Read-only; does not commit or rollback.
    """
    page_window = normalize_page_window(page, page_size)

    # The fleets this caller may see (FL-10), narrowed to the part of the tree
    # under `parent_fleet_id` when given.
    fleet_ids = await fleet_access.resolve_principal_visible_fleet_ids(
        db_session, principal
    )
    if parent_fleet_id is not None:
        await fleet_access.get_fleet_record(db_session, parent_fleet_id, principal)
        below_parent_ids = frozenset(
            await fleet_access.list_descendant_fleet_ids(db_session, parent_fleet_id)
            if include_descendants
            else await fleet_repository.list_child_fleet_ids(
                db_session, parent_fleet_id
            )
        )
        fleet_ids = (
            below_parent_ids if fleet_ids is None else below_parent_ids & fleet_ids
        )

    vehicle_id: UUID | None = None
    if vehicle_vin is not None:
        vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_vin(
            db_session, vehicle_vin
        )
        if vehicle_reference is None:
            return FleetListResponse(
                items=[],
                total=0,
                page=page_window.page,
                page_size=page_window.page_size,
            )
        vehicle_id = vehicle_reference.vehicle_id

    fleet_records = await fleet_repository.list_all(
        db_session,
        offset=page_window.offset,
        limit=page_window.page_size,
        search_text=search_text,
        vehicle_id=vehicle_id,
        organization_id=principal.data_scope,
        fleet_ids=fleet_ids,
    )
    total = await fleet_repository.count(
        db_session,
        search_text=search_text,
        vehicle_id=vehicle_id,
        organization_id=principal.data_scope,
        fleet_ids=fleet_ids,
    )

    return FleetListResponse(
        items=[
            await build_fleet_response(
                db_session, fleet_record, include_descendants=include_descendants
            )
            for fleet_record in fleet_records
        ],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def get_fleet_tree(
    db_session: AsyncSession,
    *,
    principal: Principal,
    organization_id: UUID | None = None,
) -> FleetTreeResponse:
    """Get the nested fleet tree of an organization (FLT-01).

    A caller limited to some fleets (FL-10) gets only the fleets they may see:
    the tops of their assigned subtrees become the roots. Each node carries
    the trucks directly in it and the trucks of its whole subtree.

    Args:
        db_session: Current database session.
        principal: The caller.
        organization_id: The organization to show; internal staff only
            (defaults to the caller's own organization).

    Returns:
        The tree: root fleets with their children, oldest first.

    Raises:
        OrganizationNotFoundError: When the named organization does not exist
            or is out of the caller's reach.

    Side Effects:
        Read-only; one query for the organization's fleets and one count per
        fleet (no batching).
    """
    tree_organization_id = await identity_service.resolve_organization_for_new_record(
        db_session, principal, organization_id
    )
    fleet_records = await fleet_repository.list_by_organization(
        db_session, tree_organization_id
    )
    visible_fleet_ids = await fleet_access.resolve_principal_visible_fleet_ids(
        db_session, principal
    )
    if visible_fleet_ids is not None:
        fleet_records = [
            fleet_record
            for fleet_record in fleet_records
            if fleet_record.fleet_id in visible_fleet_ids
        ]

    fleet_records_by_id = {
        fleet_record.fleet_id: fleet_record for fleet_record in fleet_records
    }
    child_records_by_parent_id: dict[UUID, list[FleetModel]] = {}
    root_records: list[FleetModel] = []
    for fleet_record in fleet_records:
        if fleet_record.parent_fleet_id in fleet_records_by_id:
            child_records_by_parent_id.setdefault(
                fleet_record.parent_fleet_id, []
            ).append(fleet_record)
        else:
            root_records.append(fleet_record)

    own_vehicle_counts = {
        fleet_record.fleet_id: await fleet_repository.count_active_memberships_by_fleet(
            db_session, fleet_record.fleet_id
        )
        for fleet_record in fleet_records
    }
    return FleetTreeResponse(
        organization_id=tree_organization_id,
        items=[
            build_fleet_tree_node(
                root_record, child_records_by_parent_id, own_vehicle_counts, set()
            )
            for root_record in root_records
        ],
        total_fleets=len(fleet_records),
    )


def build_fleet_tree_node(
    fleet_record: FleetModel,
    child_records_by_parent_id: dict[UUID, list[FleetModel]],
    own_vehicle_counts: dict[UUID, int],
    visited_fleet_ids: set[UUID],
) -> FleetTreeNodeResponse:
    """Assemble one tree node and, recursively, the fleets below it.

    Pure mapping only, no I/O. The visited set keeps a damaged loop in the
    data from recursing forever.

    Args:
        fleet_record: The fleet of this node.
        child_records_by_parent_id: The live fleets of the tree grouped by
            their parent's ID.
        own_vehicle_counts: Trucks directly in each fleet, by fleet ID.
        visited_fleet_ids: Fleets already placed in the tree.

    Returns:
        The node with its roll-up count and children.
    """
    visited_fleet_ids.add(fleet_record.fleet_id)
    child_nodes = [
        build_fleet_tree_node(
            child_record,
            child_records_by_parent_id,
            own_vehicle_counts,
            visited_fleet_ids,
        )
        for child_record in child_records_by_parent_id.get(fleet_record.fleet_id, [])
        if child_record.fleet_id not in visited_fleet_ids
    ]
    own_vehicle_count = own_vehicle_counts.get(fleet_record.fleet_id, 0)
    return FleetTreeNodeResponse(
        fleet_id=fleet_record.fleet_id,
        organization_id=fleet_record.organization_id,
        fleet_code=fleet_record.fleet_code,
        name=fleet_record.name,
        parent_fleet_id=fleet_record.parent_fleet_id,
        vehicle_count=own_vehicle_count,
        vehicle_count_with_descendants=own_vehicle_count
        + sum(child.vehicle_count_with_descendants for child in child_nodes),
        children=child_nodes,
        created_at=fleet_record.created_at,
        updated_at=fleet_record.updated_at,
    )


async def update_fleet(
    db_session: AsyncSession,
    fleet_id: UUID,
    fleet_update_request: FleetUpdateRequest,
    *,
    principal: Principal,
) -> FleetResponse:
    """Partially update a fleet: rename it, change its code, or move it.

    Rules: a new fleet code must not be used by another live fleet of the same
    organization; a new parent must be a live fleet of the same organization
    that is neither this fleet nor one of its
    sub-fleets (FL-02). Moving a fleet moves its whole subtree with it.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.
        fleet_update_request: Field data to update.
        principal: The caller; a fleet of another organization is not found
            unless the caller is internal.

    Returns:
        Response for the updated fleet.

    Raises:
        FleetNotFoundError: When the fleet does not exist or has been soft-deleted.
        FleetConflictError: When the new fleet code is already in use.
        FleetParentNotFoundError: When the new parent fleet does not exist.
        FleetParentOrganizationMismatchError: When the new parent belongs to
            another organization.
        FleetHierarchyLoopError: When the new parent is this fleet or one of
            its sub-fleets.
    """
    fleet_record = await fleet_access.get_fleet_record(db_session, fleet_id, principal)

    if (
        fleet_update_request.fleet_code
        and fleet_update_request.fleet_code != fleet_record.fleet_code
    ):
        existing_fleet = await fleet_repository.find_by_fleet_code(
            db_session, fleet_record.organization_id, fleet_update_request.fleet_code
        )
        if existing_fleet:
            raise FleetConflictError(
                f"Fleet with code '{fleet_update_request.fleet_code}' already exists"
            )

    if (
        fleet_update_request.parent_fleet_id is not None
        and fleet_update_request.parent_fleet_id != fleet_record.parent_fleet_id
    ):
        visible_fleet_ids = await fleet_access.resolve_principal_visible_fleet_ids(
            db_session, principal
        )
        if (
            visible_fleet_ids is not None
            and fleet_update_request.parent_fleet_id not in visible_fleet_ids
        ):
            # A caller limited to some fleets cannot move a fleet out of
            # (or into somewhere outside) their own part of the tree (FL-10).
            raise FleetParentNotFoundError(
                f"Parent fleet with id '{fleet_update_request.parent_fleet_id}' "
                "not found"
            )
        await _ensure_valid_parent_fleet(
            db_session,
            fleet_update_request.parent_fleet_id,
            organization_id=fleet_record.organization_id,
            moved_fleet_id=fleet_id,
        )

    update_values = {
        field_name: value
        for field_name, value in fleet_update_request.model_dump(
            exclude_unset=True
        ).items()
        if value is not None
    }
    if not update_values:
        return await build_fleet_response(db_session, fleet_record)

    try:
        updated_fleet_record = await fleet_repository.update_fields(
            db_session, fleet_id, update_values, changed_by=principal.user_id
        )
    except IntegrityError as error:
        raise FleetConflictError("Fleet code already exists") from error

    if updated_fleet_record is None:
        raise FleetNotFoundError(f"Fleet with id '{fleet_id}' not found")

    return await build_fleet_response(db_session, updated_fleet_record)


async def soft_delete_fleet(
    db_session: AsyncSession,
    fleet_id: UUID,
    *,
    principal: Principal,
    reason: str | None = None,
) -> dict[str, str]:
    """Soft-delete a fleet, closing any active memberships first.

    A fleet that still has live sub-fleets is refused (FL-08): they must be
    moved or deleted first, so no live fleet ever sits under a deleted one.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.
        principal: The caller; a fleet of another organization is not found
            unless the caller is internal.
        reason: Why the fleet is deleted, kept in the fleet's history; a fixed
            text when omitted.

    Returns:
        Success deletion message.

    Raises:
        FleetNotFoundError: When the fleet does not exist or has been soft-deleted.
        FleetHasSubFleetsError: When live fleets still sit under this one.

    Raises (besides the above):
        AccessDeniedError: When a caller limited to some fleets (FL-10) deletes
            a fleet that is given to someone, which would end that limit.

    Side Effects:
        Closes every open membership of this fleet (if any) in the same
        transaction as the soft delete, so a deleted fleet never holds a
        vehicle hostage against the active-membership partial unique index.
        One `close_membership` call per member vehicle - no batching, per
        this repo's no-premature-batching convention. Also ends every open
        user assignment pointing at the fleet (``unassigned_by`` NULL: the
        system ended it, FL-10); a member whose last assignment ended is no
        longer limited.
    """
    await fleet_access.get_fleet_record(db_session, fleet_id, principal)
    if await fleet_repository.count_child_fleets(db_session, fleet_id) > 0:
        raise FleetHasSubFleetsError(
            f"Fleet '{fleet_id}' still has sub-fleets; move or delete them first"
        )
    open_assignments = await fleet_repository.list_open_assignments_by_fleet(
        db_session, fleet_id
    )
    if open_assignments and principal.is_fleet_limited:
        # Deleting an assigned fleet ends the limit of whoever holds it; only
        # the organization administrator may lift a limit (FLT-03).
        raise AccessDeniedError(
            "A fleet given to a user can be deleted only by the organization "
            "administrator"
        )

    active_memberships = await fleet_repository.list_all_active_memberships_by_fleet(
        db_session, fleet_id
    )
    for membership_record in active_memberships:
        await fleet_repository.close_membership(
            db_session,
            membership_record,
            removed_at=utc_now(),
            removed_by=principal.user_id,
        )
    for assignment_record in open_assignments:
        await fleet_repository.close_assignment(
            db_session,
            assignment_record,
            unassigned_at=utc_now(),
            unassigned_by=None,
        )

    fleet_record = await fleet_repository.soft_delete(
        db_session,
        fleet_id,
        changed_by=principal.user_id,
        change_reason=reason or fleet_repository.FLEET_DELETED_REASON,
    )
    if not fleet_record:
        raise FleetNotFoundError(f"Fleet with id '{fleet_id}' not found")

    return {"message": "Fleet deleted successfully"}


async def list_active_member_vehicle_ids(
    db: AsyncSession,
    fleet_id: UUID,
    *,
    organization_id: UUID | None = None,
    include_descendants: bool = False,
    principal: Principal | None = None,
) -> list[UUID]:
    """List the vehicles currently in a fleet. Public cross-domain entry point.

    Used by the telemetry domain's fleet-wide views and by the telematics
    domain's fleet-wide config push and device-health list.

    Args:
        db: Session owned by the caller's entry boundary.
        fleet_id: Internal ID of the fleet.
        organization_id: Data scope of an HTTP caller: a fleet of another
            organization is then not found. `None` (internal staff, system
            callers) means no restriction.
        include_descendants: Also list the trucks of every fleet below this
            one (reports roll up to parents, FLT-01).
        principal: The HTTP caller. When given, its data scope replaces
            `organization_id` and its fleet limit (FL-10) applies: a fleet
            outside the limit is not found.

    Returns:
        IDs of the vehicles with an open membership, oldest member first.
        A vehicle soft-deleted after joining is still included (its
        membership is still open); the caller resolves each ID through the
        vehicles domain if it needs a live vehicle.

    Raises:
        FleetNotFoundError: When the fleet does not exist, was
            soft-deleted or is outside the caller's reach.

    Side Effects:
        Read-only; does not commit or rollback.
    """
    if principal is not None:
        organization_id = principal.data_scope
    fleet_record = await fleet_repository.get_by_id(
        db, fleet_id, organization_id=organization_id
    )
    if fleet_record is None:
        raise FleetNotFoundError(f"Fleet with id '{fleet_id}' not found")
    if principal is not None:
        visible_fleet_ids = await fleet_access.resolve_principal_visible_fleet_ids(
            db, principal
        )
        if visible_fleet_ids is not None and fleet_id not in visible_fleet_ids:
            raise FleetNotFoundError(f"Fleet with id '{fleet_id}' not found")
    if include_descendants:
        subtree_fleet_ids = frozenset(
            [fleet_id, *await fleet_access.list_descendant_fleet_ids(db, fleet_id)]
        )
        return await fleet_repository.list_active_vehicle_ids_by_fleet_ids(
            db, subtree_fleet_ids
        )
    membership_records = await fleet_repository.list_all_active_memberships_by_fleet(
        db, fleet_id
    )
    membership_records.sort(key=lambda membership_record: membership_record.added_at)
    return [membership_record.vehicle_id for membership_record in membership_records]


async def find_current_fleet_id_by_vehicle(
    db: AsyncSession, vehicle_id: UUID
) -> UUID | None:
    """Find the fleet a vehicle currently belongs to. Public cross-domain entry point.

    Args:
        db: Session owned by the caller's entry boundary.
        vehicle_id: Internal ID of the vehicle.

    Returns:
        The fleet of the vehicle's open membership, or ``None`` when the
        vehicle is in no fleet. A soft-deleted fleet never shows up here:
        deleting a fleet closes its memberships in the same transaction.

    Side Effects:
        Read-only; does not commit or rollback.
    """
    membership_record = await fleet_repository.find_active_membership_by_vehicle(
        db, vehicle_id
    )
    return membership_record.fleet_id if membership_record is not None else None


async def list_geofences_containing(
    db: AsyncSession, fleet_id: UUID, *, latitude: float, longitude: float
) -> list[GeofenceReference]:
    """List a fleet's live geofences that cover a point. Public cross-domain entry point.

    Used by the telemetry domain to detect geofence entry and exit (F-A5).
    The fleet is not checked: an unknown or deleted fleet simply has no
    live geofence that matches.

    Args:
        db: Session owned by the caller's entry boundary.
        fleet_id: Internal ID of the fleet.
        latitude: Latitude of the point, in decimal degrees (WGS84).
        longitude: Longitude of the point, in decimal degrees (WGS84).

    Returns:
        References to the matching geofences, oldest first; a point on a
        boundary counts as inside (``ST_Covers``).

    Side Effects:
        Read-only; does not commit or rollback.
    """
    location = coordinates_to_location(latitude, longitude)
    if location is None:
        return []
    geofence_records = await fleet_repository.list_geofences_covering_point(
        db, fleet_id, location
    )
    return [
        GeofenceReference(
            geofence_id=geofence_record.geofence_id, name=geofence_record.name
        )
        for geofence_record in geofence_records
    ]


async def close_membership_of_sold_vehicle(
    db: AsyncSession, vehicle_id: UUID, *, removed_at: datetime
) -> UUID | None:
    """Close a truck's open fleet membership because the truck was sold (VH-12).

    Public cross-domain entry point, called by the ownership-transfer
    orchestration (``app/api/vehicle_transfer.py``); the vehicles domain
    cannot call ``fleet`` itself (FL-01). The system closes the period, so
    ``removed_by`` stays ``NULL`` (FL-09).

    Args:
        db: Session owned by the caller's entry boundary.
        vehicle_id: Internal ID of the sold truck.
        removed_at: Effective date of the sale.

    Returns:
        The ID of the fleet the truck left, or `None` when it was in no fleet.

    Side Effects:
        Stamps the membership's ``removed_at``; does not commit or roll back.
    """
    membership_record = await fleet_repository.find_active_membership_by_vehicle(
        db, vehicle_id
    )
    if membership_record is None:
        return None
    await fleet_repository.close_membership(
        db,
        membership_record,
        # A sale dated before the truck joined must not give a negative period.
        removed_at=max(removed_at, membership_record.added_at),
        removed_by=None,
    )
    return membership_record.fleet_id


async def handle_membership_end(
    db_session: AsyncSession,
    *,
    membership_id: UUID,
    kind: MembershipEndKind,
    acting_user_id: UUID,
    reason: str,
) -> None:
    """Close a membership's fleet limits when the person leaves (hardening D6).

    Registered with the identity domain's membership-end hook by
    ``app/api/membership_end_hooks.py``, so it runs inside the same
    transaction as the removal or the leave.

    Rules:
        An ENDED membership has every open ``fleet_user_assignments`` row
        closed, with the acting user as ``unassigned_by``. A LOCK leaves them
        open: no open row means "the whole organization", so closing them on
        a lock would widen the person's reach when the lock is lifted, and a
        locked membership cannot act anyway.

    Args:
        db_session: Session owned by the entry boundary.
        membership_id: The membership that ended or was locked.
        kind: Whether it ended or was locked.
        acting_user_id: Who did it.
        reason: Why; not stored (an assignment has no reason column, and the
            membership keeps it as its own ``status_reason``).

    Side Effects:
        Updates the open assignments in the caller's transaction; does
        nothing for a lock or a membership with no assignment.
    """
    if kind is not MembershipEndKind.ENDED:
        return
    open_assignments = await fleet_repository.list_open_assignments_by_membership(
        db_session, membership_id
    )
    ended_at = utc_now()
    for assignment_record in open_assignments:
        await fleet_repository.close_assignment(
            db_session,
            assignment_record,
            unassigned_at=ended_at,
            unassigned_by=acting_user_id,
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
    return await fleet_access.resolve_principal_visible_vehicle_ids(db, principal)


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
    return await fleet_access.resolve_visible_vehicle_ids(
        db, principal_membership_id, organization_id
    )
