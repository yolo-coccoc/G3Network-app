"""Business service and public contract of the fleet domain.

This module holds the business rules for fleet records and fleet-vehicle
membership. Other domains may only call the public `resolve_*` functions to
obtain internal DTOs, and must never receive the ORM model or HTTP response
schema of the fleet domain. This domain depends on the `vehicles` domain's
public service to resolve a VIN into a vehicle and to enrich a response with
a vehicle's VIN/license plate/status - the same one-directional edge shape
already established by `telematics -> vehicles` and `drivers -> vehicles`.
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.vehicles.service as vehicle_service
from app.domains.fleet import repository as fleet_repository
from app.domains.fleet.exceptions import (
    FleetConflictError,
    FleetMembershipConflictError,
    FleetMembershipNotFoundError,
    FleetNotFoundError,
    FleetVehicleNotFoundError,
)
from app.domains.fleet.models import FleetModel, FleetVehicleMembershipModel
from app.domains.fleet.schemas import (
    FleetCreateRequest,
    FleetListResponse,
    FleetMembershipHistoryResponse,
    FleetMembershipResponse,
    FleetResponse,
    FleetUpdateRequest,
    FleetVehicleAddRequest,
    FleetVehicleListResponse,
    FleetVehicleResponse,
)
from app.domains.fleet.types import FleetReference, FleetStatus
from app.libs.common.config import settings


def to_fleet_reference(fleet_record: FleetModel) -> FleetReference:
    """Convert an ORM record into a minimal DTO for other domains.

    Args:
        fleet_record: An active fleet record.

    Returns:
        DTO containing the internal ID and name of the fleet.
    """
    return FleetReference(
        fleet_id=fleet_record.fleet_id,
        name=fleet_record.name,
    )


async def resolve_fleet_reference_by_id(
    db_session: AsyncSession,
    fleet_id: UUID,
) -> FleetReference | None:
    """Find an active fleet by ID and return its internal DTO.

    Args:
        db_session: Database session owned by the entry boundary.
        fleet_id: Internal ID of the fleet.

    Returns:
        `FleetReference` if the fleet is found; otherwise `None`.

    Side Effects:
        Performs a read-only query only; does not commit or rollback.
    """
    fleet_record = await fleet_repository.get_by_id(db_session, fleet_id)
    return to_fleet_reference(fleet_record) if fleet_record else None


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
        membership_id=membership_record.membership_id,
        fleet_id=membership_record.fleet_id,
        vehicle_id=membership_record.vehicle_id,
        vehicle_vin=vehicle_vin,
        joined_at=membership_record.joined_at,
        left_at=membership_record.left_at,
    )


async def build_fleet_response(
    db_session: AsyncSession, fleet_record: FleetModel
) -> FleetResponse:
    """Build a fleet response enriched with its current vehicle count.

    Args:
        db_session: Current database session.
        fleet_record: Fleet ORM record already queried, created, or
            updated by the caller.

    Returns:
        Response data with `vehicle_count` populated from the fleet's
        open memberships.

    Side Effects:
        One count query against this domain's own membership table -
        read-only; does not commit or rollback.
    """
    vehicle_count = await fleet_repository.count_active_memberships_by_fleet(
        db_session, fleet_record.fleet_id
    )
    return FleetResponse.model_validate(
        {**fleet_record.__dict__, "vehicle_count": vehicle_count}
    )


async def create_fleet(
    db_session: AsyncSession,
    fleet_create_request: FleetCreateRequest,
) -> FleetResponse:
    """Create a new fleet after verifying that the fleet code is unique.

    Args:
        db_session: Database session owned by the entry boundary.
        fleet_create_request: Request data that has passed Pydantic validation.

    Returns:
        Response for the newly created fleet.

    Raises:
        FleetConflictError: When the fleet code already exists.
    """
    existing_fleet = await fleet_repository.find_by_fleet_code(
        db_session, fleet_create_request.fleet_code
    )
    if existing_fleet:
        raise FleetConflictError(
            f"Fleet with code '{fleet_create_request.fleet_code}' already exists"
        )

    try:
        fleet_record = await fleet_repository.insert(
            db_session,
            fleet_create_request.model_dump(),
        )
    except IntegrityError as error:
        raise FleetConflictError("Fleet code already exists") from error

    return await build_fleet_response(db_session, fleet_record)


async def get_fleet(
    db_session: AsyncSession,
    fleet_id: UUID,
) -> FleetResponse:
    """Get an active fleet by ID.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.

    Returns:
        Response for the fleet.

    Raises:
        FleetNotFoundError: When the fleet does not exist or has been soft-deleted.
    """
    fleet_record = await fleet_repository.get_by_id(db_session, fleet_id)
    if not fleet_record:
        raise FleetNotFoundError(f"Fleet with id '{fleet_id}' not found")

    return await build_fleet_response(db_session, fleet_record)


async def list_fleets(
    db_session: AsyncSession,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
    status_filter: FleetStatus | None = None,
) -> FleetListResponse:
    """Get a paginated list of active fleets.

    Args:
        db_session: Current database session.
        page: Page number, starting from 1.
        page_size: Maximum number of fleets per page.
        status_filter: Status filter, if any.

    Returns:
        Paginated fleet list response.
    """
    page = max(page, settings.API_DEFAULT_PAGE)
    page_size = min(max(page_size, 1), settings.API_MAX_PAGE_SIZE)
    skip = (page - 1) * page_size

    fleet_records = await fleet_repository.list_all(
        db_session, skip, page_size, status_filter
    )
    total = await fleet_repository.count(db_session, status_filter)

    return FleetListResponse(
        items=[
            await build_fleet_response(db_session, fleet_record)
            for fleet_record in fleet_records
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


async def update_fleet(
    db_session: AsyncSession,
    fleet_id: UUID,
    fleet_update_request: FleetUpdateRequest,
) -> FleetResponse:
    """Partially update a fleet after checking the unique fleet code.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.
        fleet_update_request: Field data to update.

    Returns:
        Response for the updated fleet.

    Raises:
        FleetNotFoundError: When the fleet does not exist or has been soft-deleted.
        FleetConflictError: When the new fleet code is already in use.
    """
    fleet_record = await fleet_repository.get_by_id(db_session, fleet_id)
    if not fleet_record:
        raise FleetNotFoundError(f"Fleet with id '{fleet_id}' not found")

    if (
        fleet_update_request.fleet_code
        and fleet_update_request.fleet_code != fleet_record.fleet_code
    ):
        existing_fleet = await fleet_repository.find_by_fleet_code(
            db_session, fleet_update_request.fleet_code
        )
        if existing_fleet:
            raise FleetConflictError(
                f"Fleet with code '{fleet_update_request.fleet_code}' already exists"
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
            db_session, fleet_id, update_values
        )
    except IntegrityError as error:
        raise FleetConflictError("Fleet code already exists") from error

    if updated_fleet_record is None:
        raise FleetNotFoundError(f"Fleet with id '{fleet_id}' not found")

    return await build_fleet_response(db_session, updated_fleet_record)


async def soft_delete_fleet(
    db_session: AsyncSession,
    fleet_id: UUID,
) -> dict[str, str]:
    """Soft-delete a fleet, closing any active memberships first.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.

    Returns:
        Success deletion message.

    Raises:
        FleetNotFoundError: When the fleet does not exist or has been soft-deleted.

    Side Effects:
        Closes every open membership of this fleet (if any) in the same
        transaction as the soft delete, so a deleted fleet never holds a
        vehicle hostage against the active-membership partial unique index.
        One `close_membership` call per member vehicle - no batching, per
        this repo's no-premature-batching convention.
    """
    active_memberships = await fleet_repository.list_all_active_memberships_by_fleet(
        db_session, fleet_id
    )
    for membership_record in active_memberships:
        await fleet_repository.close_membership(
            db_session, membership_record, left_at=datetime.now(timezone.utc)
        )

    fleet_record = await fleet_repository.soft_delete(db_session, fleet_id)
    if not fleet_record:
        raise FleetNotFoundError(f"Fleet with id '{fleet_id}' not found")

    return {"message": "Fleet deleted successfully"}


async def add_vehicle_to_fleet(
    db_session: AsyncSession,
    fleet_id: UUID,
    fleet_vehicle_add_request: FleetVehicleAddRequest,
) -> FleetMembershipResponse:
    """Add a vehicle to a fleet by VIN.

    Rule:
        The target vehicle must not already be actively assigned to a
        *different* fleet (raises rather than silently stealing it).
        Adding a vehicle already actively in *this* fleet is a no-op that
        returns the existing membership.

    Args:
        db_session: Database session owned by the entry boundary.
        fleet_id: Internal ID of the fleet.
        fleet_vehicle_add_request: The vehicle to add, by VIN.

    Returns:
        The resulting open membership.

    Raises:
        FleetNotFoundError: When the fleet does not exist.
        FleetVehicleNotFoundError: When the VIN does not resolve to a vehicle.
        FleetMembershipConflictError: When the vehicle is already actively
            in a different fleet.

    Side Effects:
        Inserts a new membership row; does not commit or rollback on its own.
    """
    fleet_record = await fleet_repository.get_by_id(db_session, fleet_id)
    if fleet_record is None:
        raise FleetNotFoundError(f"Fleet with id '{fleet_id}' not found")

    vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_vin(
        db_session, fleet_vehicle_add_request.vehicle_vin
    )
    if vehicle_reference is None:
        raise FleetVehicleNotFoundError(
            f"Vehicle with VIN '{fleet_vehicle_add_request.vehicle_vin}' not found"
        )

    existing_membership = await fleet_repository.get_active_membership_by_vehicle(
        db_session, vehicle_reference.vehicle_id
    )
    if existing_membership is not None:
        if existing_membership.fleet_id == fleet_id:
            return to_membership_response(existing_membership, vehicle_reference.vin)
        raise FleetMembershipConflictError(
            f"Vehicle with VIN '{fleet_vehicle_add_request.vehicle_vin}' is "
            "already assigned to another fleet"
        )

    try:
        membership_record = await fleet_repository.insert_membership(
            db_session,
            fleet_id=fleet_id,
            vehicle_id=vehicle_reference.vehicle_id,
            joined_at=datetime.now(timezone.utc),
        )
    except IntegrityError as error:
        raise FleetMembershipConflictError(
            "Vehicle already has an active fleet membership"
        ) from error

    return to_membership_response(membership_record, vehicle_reference.vin)


async def remove_vehicle_from_fleet(
    db_session: AsyncSession,
    fleet_id: UUID,
    vehicle_id: UUID,
) -> None:
    """Close a vehicle's active membership in a fleet.

    Args:
        db_session: Database session owned by the entry boundary.
        fleet_id: Internal ID of the fleet.
        vehicle_id: Internal ID of the vehicle to remove.

    Raises:
        FleetNotFoundError: When the fleet does not exist.
        FleetMembershipNotFoundError: When the vehicle has no active
            membership in this fleet.
    """
    fleet_record = await fleet_repository.get_by_id(db_session, fleet_id)
    if fleet_record is None:
        raise FleetNotFoundError(f"Fleet with id '{fleet_id}' not found")

    active_membership = await fleet_repository.get_active_membership_by_vehicle(
        db_session, vehicle_id
    )
    if active_membership is None or active_membership.fleet_id != fleet_id:
        raise FleetMembershipNotFoundError(
            f"Vehicle with id '{vehicle_id}' has no active membership in "
            f"fleet '{fleet_id}'"
        )

    await fleet_repository.close_membership(
        db_session, active_membership, left_at=datetime.now(timezone.utc)
    )


async def list_fleet_vehicles(
    db_session: AsyncSession,
    fleet_id: UUID,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> FleetVehicleListResponse:
    """Get a fleet's paginated current vehicle list (F-E1).

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.
        page: Page number, starting from 1.
        page_size: Maximum number of vehicles per page.

    Returns:
        Paginated list of vehicles currently in the fleet.

    Raises:
        FleetNotFoundError: When the fleet does not exist.

    Side Effects:
        Resolves each vehicle's VIN/license plate/status with one
        per-row call to the vehicles domain's public service - no
        batching, per this repo's no-premature-batching convention.
        A membership whose vehicle no longer resolves (soft-deleted since)
        is silently skipped rather than raised, since it's a listing, not
        a lookup of one specific vehicle.
    """
    fleet_record = await fleet_repository.get_by_id(db_session, fleet_id)
    if fleet_record is None:
        raise FleetNotFoundError(f"Fleet with id '{fleet_id}' not found")

    page = max(page, settings.API_DEFAULT_PAGE)
    page_size = min(max(page_size, 1), settings.API_MAX_PAGE_SIZE)
    skip = (page - 1) * page_size

    membership_records = await fleet_repository.list_active_memberships_by_fleet(
        db_session, fleet_id, offset=skip, limit=page_size
    )
    total = await fleet_repository.count_active_memberships_by_fleet(
        db_session, fleet_id
    )

    items = []
    for membership_record in membership_records:
        vehicle_summary = await vehicle_service.resolve_vehicle_summary_by_id(
            db_session, membership_record.vehicle_id
        )
        if vehicle_summary is None:
            continue
        items.append(
            FleetVehicleResponse(
                vehicle_id=vehicle_summary.vehicle_id,
                vin=vehicle_summary.vin,
                license_plate=vehicle_summary.license_plate,
                status=vehicle_summary.status,
                joined_at=membership_record.joined_at,
            )
        )

    return FleetVehicleListResponse(
        items=items, total=total, page=page, page_size=page_size
    )


async def list_fleet_membership_history(
    db_session: AsyncSession,
    fleet_id: UUID,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> FleetMembershipHistoryResponse:
    """Get a fleet's paginated membership history, newest first.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.
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
    fleet_record = await fleet_repository.get_by_id(db_session, fleet_id)
    if fleet_record is None:
        raise FleetNotFoundError(f"Fleet with id '{fleet_id}' not found")

    page = max(page, settings.API_DEFAULT_PAGE)
    page_size = min(max(page_size, 1), settings.API_MAX_PAGE_SIZE)
    skip = (page - 1) * page_size

    membership_records = await fleet_repository.list_memberships_by_fleet(
        db_session, fleet_id, offset=skip, limit=page_size
    )
    total = await fleet_repository.count_memberships_by_fleet(db_session, fleet_id)

    items = []
    for membership_record in membership_records:
        vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_id(
            db_session, membership_record.vehicle_id
        )
        vehicle_vin = vehicle_reference.vin if vehicle_reference else None
        items.append(to_membership_response(membership_record, vehicle_vin))

    return FleetMembershipHistoryResponse(
        items=items, total=total, page=page, page_size=page_size
    )
