"""Trucks in a fleet: add, move, remove, list and history (FLT-02, FL-17).

A truck belongs to at most one fleet at a time (an open membership row);
adding it to another fleet is refused unless the request asks to move it,
and a fleet-limited manager reaches only the trucks and fleets of their own
limit. Also the fleet's current truck list with its filters and the
membership history. Internal module: the domain's router calls it. Runs in
the caller's transaction and never commits.
"""

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.fleet.access as fleet_access
import app.domains.fleet.repository as fleet_repository
import app.domains.vehicles.service as vehicle_service
from app.domains.fleet.exceptions import (
    FleetMembershipConflictError,
    FleetMembershipNotFoundError,
    FleetVehicleNotFoundError,
    FleetVehicleOrganizationMismatchError,
)
from app.domains.fleet.models import (
    FleetVehicleMembershipModel,
)
from app.domains.fleet.schemas import (
    FleetMembershipHistoryResponse,
    FleetMembershipResponse,
    FleetVehicleAddRequest,
    FleetVehicleListResponse,
    FleetVehicleResponse,
)
from app.domains.identity.types import Principal
from app.domains.vehicles.types import VehicleStatus, VehicleSummary
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.pagination import normalize_page_window


def to_membership_response(
    membership_record: FleetVehicleMembershipModel, vehicle_vin: str | None
) -> FleetMembershipResponse:
    """Convert a membership ORM record into a response, given its vehicle's VIN.

    Pure mapping only, no I/O - the caller resolves `vehicle_vin` via the
    vehicles domain's public service beforehand.

    Args:
        membership_record: Membership record already queried or created.
        vehicle_vin: VIN of the member vehicle, or `None` if it no longer
            resolves (the vehicle was soft-deleted since).

    Returns:
        Response data for the membership.
    """
    return FleetMembershipResponse(
        fleet_vehicle_membership_id=membership_record.fleet_vehicle_membership_id,
        fleet_id=membership_record.fleet_id,
        vehicle_id=membership_record.vehicle_id,
        vehicle_vin=vehicle_vin,
        added_at=membership_record.added_at,
        removed_at=membership_record.removed_at,
    )


async def add_vehicle_to_fleet(
    db_session: AsyncSession,
    fleet_id: UUID,
    fleet_vehicle_add_request: FleetVehicleAddRequest,
    *,
    principal: Principal,
) -> FleetMembershipResponse:
    """Add a vehicle to a fleet by VIN.

    Rule:
        The target vehicle must not already be actively assigned to a
        *different* fleet (raises rather than silently stealing it), unless
        the request asks to ``move`` it: then its membership there is closed
        and the new one opened in the same transaction. A fleet-limited
        manager reaches only the trucks of their own fleets, so they can move
        a truck between those and nowhere else (FL-10, bug-fix D4).
        Adding a vehicle already actively in *this* fleet is a no-op that
        returns the existing membership.

    Args:
        db_session: Database session owned by the entry boundary.
        fleet_id: Internal ID of the fleet.
        fleet_vehicle_add_request: The vehicle to add, by VIN.
        principal: The caller, recorded as ``added_by``; a fleet or vehicle of
            another organization is not found unless the caller is internal.

    Returns:
        The resulting open membership.

    Raises:
        FleetNotFoundError: When the fleet does not exist.
        FleetVehicleNotFoundError: When the VIN does not resolve to a vehicle.
        FleetVehicleOrganizationMismatchError: When the vehicle belongs to
            another organization than the fleet (FL-09).
        FleetMembershipConflictError: When the vehicle is already actively
            in a different fleet and no move was asked.

    Side Effects:
        Inserts a new membership row (and closes the old one on a move, with
        the caller as ``removed_by``); does not commit or rollback on its own.
    """
    fleet_record = await fleet_access.get_fleet_record(db_session, fleet_id, principal)

    vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_vin(
        db_session, fleet_vehicle_add_request.vehicle_vin
    )
    if vehicle_reference is None or not principal.can_access_organization(
        vehicle_reference.organization_id
    ):
        raise FleetVehicleNotFoundError(
            f"Vehicle with VIN '{fleet_vehicle_add_request.vehicle_vin}' not found"
        )

    # A manager limited to some fleets sees only the trucks in them: adding any
    # other truck (one in no fleet) would widen their own reach (FL-10, RV-AS1).
    visible_vehicle_ids = await fleet_access.resolve_principal_visible_vehicle_ids(
        db_session, principal
    )
    if (
        visible_vehicle_ids is not None
        and vehicle_reference.vehicle_id not in visible_vehicle_ids
    ):
        raise FleetVehicleNotFoundError(
            f"Vehicle with VIN '{fleet_vehicle_add_request.vehicle_vin}' not found"
        )

    # A fleet holds only its own organization's vehicles (FL-09).
    if vehicle_reference.organization_id != fleet_record.organization_id:
        raise FleetVehicleOrganizationMismatchError(
            f"Vehicle with VIN '{fleet_vehicle_add_request.vehicle_vin}' belongs "
            "to another organization than the fleet"
        )

    existing_membership = await fleet_repository.find_active_membership_by_vehicle(
        db_session, vehicle_reference.vehicle_id
    )
    now = utc_now()
    if existing_membership is not None:
        if existing_membership.fleet_id == fleet_id:
            return to_membership_response(existing_membership, vehicle_reference.vin)
        if not fleet_vehicle_add_request.move:
            raise FleetMembershipConflictError(
                f"Vehicle with VIN '{fleet_vehicle_add_request.vehicle_vin}' is "
                "already assigned to another fleet"
            )
        # The source fleet is the vehicle's organization's (FL-09), so it is in
        # the caller's reach; a limited manager got here only because the truck
        # is visible to them, i.e. it sits in one of their own fleets.
        await fleet_repository.close_membership(
            db_session,
            existing_membership,
            removed_at=now,
            removed_by=principal.user_id,
        )

    try:
        membership_record = await fleet_repository.insert_membership(
            db_session,
            fleet_id=fleet_id,
            vehicle_id=vehicle_reference.vehicle_id,
            added_at=now,
            added_by=principal.user_id,
        )
    except IntegrityError as error:
        raise FleetMembershipConflictError(
            "Vehicle already has an active fleet membership"
        ) from error

    return to_membership_response(membership_record, vehicle_reference.vin)


async def remove_vehicle_from_fleet(
    db_session: AsyncSession,
    fleet_id: UUID,
    vehicle_vin: str,
    *,
    principal: Principal,
) -> None:
    """Close a vehicle's active membership in a fleet, by VIN.

    Identifies the vehicle by VIN, the same way `add_vehicle_to_fleet` does.

    Args:
        db_session: Database session owned by the entry boundary.
        fleet_id: Internal ID of the fleet.
        vehicle_vin: VIN of the vehicle to remove.
        principal: The caller, recorded as ``removed_by``; a fleet of another
            organization is not found unless the caller is internal.

    Raises:
        FleetNotFoundError: When the fleet does not exist.
        FleetVehicleNotFoundError: When no active vehicle has this VIN.
        FleetMembershipNotFoundError: When the vehicle has no active
            membership in this fleet.

    Side Effects:
        Calls the vehicles domain's public service to resolve the VIN.
    """
    await fleet_access.get_fleet_record(db_session, fleet_id, principal)

    vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_vin(
        db_session, vehicle_vin
    )
    if vehicle_reference is None or not principal.can_access_organization(
        vehicle_reference.organization_id
    ):
        # A truck of another organization looks like a missing one (RV-AS5).
        raise FleetVehicleNotFoundError(f"Vehicle with VIN '{vehicle_vin}' not found")

    active_membership = await fleet_repository.find_active_membership_by_vehicle(
        db_session, vehicle_reference.vehicle_id
    )
    if active_membership is None or active_membership.fleet_id != fleet_id:
        raise FleetMembershipNotFoundError(
            f"Vehicle with VIN '{vehicle_vin}' has no active membership in "
            f"fleet '{fleet_id}'"
        )

    await fleet_repository.close_membership(
        db_session,
        active_membership,
        removed_at=utc_now(),
        removed_by=principal.user_id,
    )


async def close_fleet_membership(
    db_session: AsyncSession,
    fleet_id: UUID,
    fleet_vehicle_membership_id: UUID,
    *,
    principal: Principal,
) -> None:
    """Close an open membership of a fleet by its ID (D8, #84).

    The only way to release a membership whose vehicle was soft-deleted
    after joining: the VIN-based `remove_vehicle_from_fleet` can no longer
    resolve such a vehicle, so its open row would otherwise keep counting
    toward the fleet forever.

    Args:
        db_session: Database session owned by the entry boundary.
        fleet_id: Internal ID of the fleet.
        fleet_vehicle_membership_id: Internal ID of the membership to close.
        principal: The caller, recorded as ``removed_by``; a fleet of another
            organization is not found unless the caller is internal.

    Raises:
        FleetNotFoundError: When the fleet does not exist.
        FleetMembershipNotFoundError: When the membership is unknown,
            belongs to another fleet, or is already closed.

    Side Effects:
        Stamps the membership's ``removed_at``; does not commit or rollback.
    """
    await fleet_access.get_fleet_record(db_session, fleet_id, principal)

    membership_record = await fleet_repository.get_membership_by_id(
        db_session, fleet_vehicle_membership_id
    )
    if (
        membership_record is None
        or membership_record.fleet_id != fleet_id
        or membership_record.removed_at is not None
    ):
        raise FleetMembershipNotFoundError(
            f"Membership '{fleet_vehicle_membership_id}' is not an open "
            f"membership of fleet '{fleet_id}'"
        )

    await fleet_repository.close_membership(
        db_session,
        membership_record,
        removed_at=utc_now(),
        removed_by=principal.user_id,
    )


def to_fleet_vehicle_response(
    membership_record: FleetVehicleMembershipModel,
    vehicle_summary: VehicleSummary | None,
) -> FleetVehicleResponse:
    """Convert an open membership and its vehicle's summary into a list row.

    Pure mapping only, no I/O.

    Args:
        membership_record: The open membership.
        vehicle_summary: The member vehicle's summary, or ``None`` when the
            vehicle no longer resolves (soft-deleted after joining).

    Returns:
        The row, with ``vin``/``license_plate``/``status`` set to ``None``
        when there is no summary.
    """
    return FleetVehicleResponse(
        vehicle_id=membership_record.vehicle_id,
        vin=vehicle_summary.vin if vehicle_summary else None,
        license_plate=vehicle_summary.license_plate if vehicle_summary else None,
        status=vehicle_summary.status if vehicle_summary else None,
        added_at=membership_record.added_at,
    )


def _matches_vehicle_filters(
    vehicle_summary: VehicleSummary,
    *,
    status_filter: VehicleStatus | None,
    search_text: str | None,
) -> bool:
    """Check a member vehicle against the fleet-vehicle list filters.

    Args:
        vehicle_summary: The member vehicle's summary.
        status_filter: Required vehicle lifecycle status, if any.
        search_text: Case-insensitive substring of the VIN or license
            plate, if any.

    Returns:
        True when the vehicle passes every given filter.
    """
    if status_filter is not None and vehicle_summary.status != status_filter:
        return False
    if search_text:
        needle = search_text.casefold()
        if (
            needle not in vehicle_summary.vin.casefold()
            and needle not in vehicle_summary.license_plate.casefold()
        ):
            return False
    return True


async def list_fleet_vehicles(
    db_session: AsyncSession,
    fleet_id: UUID,
    *,
    principal: Principal,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
    status_filter: VehicleStatus | None = None,
    search_text: str | None = None,
) -> FleetVehicleListResponse:
    """Get a fleet's paginated current vehicle list, newest member first (F-E1).

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.
        principal: The caller; a fleet of another organization is not found
            unless the caller is internal.
        page: Page number, starting from 1.
        page_size: Maximum number of vehicles per page.
        status_filter: Only vehicles in this lifecycle status, if given.
        search_text: Only vehicles whose VIN or license plate contains this
            text (case-insensitive), if given.

    Returns:
        Paginated list of vehicles currently in the fleet.

    Raises:
        FleetNotFoundError: When the fleet does not exist.

    Side Effects:
        Resolves each vehicle's VIN/license plate/status with one per-row
        call to the vehicles domain's public service - no batching, per this
        repo's no-premature-batching convention. Without a filter, only the
        requested page of memberships is loaded, and a membership whose
        vehicle no longer resolves (soft-deleted since) is still listed with
        ``vin``/``license_plate``/``status`` set to ``None``, so ``total``
        matches the rows a client can page through. With a filter, the
        vehicle fields live in another domain, so every open membership is
        resolved, filtered and then paged in memory; an unresolvable
        vehicle can match no filter and is left out.
    """
    await fleet_access.get_fleet_record(db_session, fleet_id, principal)

    page_window = normalize_page_window(page, page_size)

    if status_filter is None and not search_text:
        membership_records = await fleet_repository.list_active_memberships_by_fleet(
            db_session,
            fleet_id,
            offset=page_window.offset,
            limit=page_window.page_size,
        )
        total = await fleet_repository.count_active_memberships_by_fleet(
            db_session, fleet_id
        )
        fleet_vehicle_responses = []
        for membership_record in membership_records:
            vehicle_summary = await vehicle_service.resolve_vehicle_summary_by_id(
                db_session, membership_record.vehicle_id
            )
            fleet_vehicle_responses.append(
                to_fleet_vehicle_response(membership_record, vehicle_summary)
            )
        return FleetVehicleListResponse(
            items=fleet_vehicle_responses,
            total=total,
            page=page_window.page,
            page_size=page_window.page_size,
        )

    all_membership_records = (
        await fleet_repository.list_all_active_memberships_by_fleet(
            db_session, fleet_id
        )
    )
    # Same order as the unfiltered, SQL-paged branch: newest member first.
    all_membership_records.sort(
        key=lambda membership_record: membership_record.added_at, reverse=True
    )
    matching_responses = []
    for membership_record in all_membership_records:
        vehicle_summary = await vehicle_service.resolve_vehicle_summary_by_id(
            db_session, membership_record.vehicle_id
        )
        if vehicle_summary is None or not _matches_vehicle_filters(
            vehicle_summary, status_filter=status_filter, search_text=search_text
        ):
            continue
        matching_responses.append(
            to_fleet_vehicle_response(membership_record, vehicle_summary)
        )

    page_end = page_window.offset + page_window.page_size
    return FleetVehicleListResponse(
        items=matching_responses[page_window.offset : page_end],
        total=len(matching_responses),
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def list_fleet_membership_history(
    db_session: AsyncSession,
    fleet_id: UUID,
    *,
    principal: Principal,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> FleetMembershipHistoryResponse:
    """Get a fleet's paginated membership history, newest first.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.
        principal: The caller; a fleet of another organization is not found
            unless the caller is internal.
        page: Page number, starting from 1.
        page_size: Maximum number of memberships per page.

    Returns:
        Paginated membership history, including closed memberships.

    Raises:
        FleetNotFoundError: When the fleet does not exist.

    Side Effects:
        Resolves each membership's vehicle VIN with one per-row call to
        the vehicles domain's public service - no batching, per this
        repo's no-premature-batching convention.
    """
    await fleet_access.get_fleet_record(db_session, fleet_id, principal)

    page_window = normalize_page_window(page, page_size)

    membership_records = await fleet_repository.list_memberships_by_fleet(
        db_session, fleet_id, offset=page_window.offset, limit=page_window.page_size
    )
    total = await fleet_repository.count_memberships_by_fleet(db_session, fleet_id)

    membership_responses = []
    for membership_record in membership_records:
        vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_id(
            db_session, membership_record.vehicle_id
        )
        vehicle_vin = vehicle_reference.vin if vehicle_reference else None
        membership_responses.append(
            to_membership_response(membership_record, vehicle_vin)
        )

    return FleetMembershipHistoryResponse(
        items=membership_responses,
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )
