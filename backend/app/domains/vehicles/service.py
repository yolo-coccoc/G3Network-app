"""Business service and public contract of the vehicles domain.

This module holds the business rules for the vehicle record and the vehicle
model catalog. Other domains may only call the `resolve_*` functions (which
return internal DTOs); they never receive the ORM model or HTTP response schema
of the vehicles domain. None of the functions commit or roll back - the
caller's entry boundary owns the transaction.

Access (ACC-15): the router-facing functions take the caller's `Principal`
and pass `principal.data_scope` to the repository, so a vehicle of another
organization is "not found" unless the caller is internal staff; a new vehicle
is owned by the caller's organization unless staff name another one. The
cross-domain `resolve_*` functions are unscoped system lookups.
"""

from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.identity.service as identity_service
import app.domains.vehicles.repository as vehicle_repository
from app.domains.identity.types import Principal
from app.domains.vehicles.exceptions import (
    VehicleConflictError,
    VehicleModelConflictError,
    VehicleModelNotFoundError,
    VehicleNotFoundError,
)
from app.domains.vehicles.models import VehicleModel, VehicleModelModel
from app.domains.vehicles.schemas import (
    VehicleCreateRequest,
    VehicleListResponse,
    VehicleModelCreateRequest,
    VehicleModelListResponse,
    VehicleModelResponse,
    VehicleResponse,
    VehicleUpdateRequest,
)
from app.domains.vehicles.types import (
    VehicleReference,
    VehicleStatus,
    VehicleSummary,
)
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.pagination import normalize_page_window

# Fixed history reasons of routine actions; the acting user is the caller.
VEHICLE_EDITED_REASON = "Vehicle details edited"
VEHICLE_DELETED_REASON = "Vehicle deleted"


def to_vehicle_response(vehicle_record: VehicleModel) -> VehicleResponse:
    """Convert a vehicle ORM record into an HTTP API response.

    Args:
        vehicle_record: Vehicle record queried or created by the repository.

    Returns:
        Response data corresponding to the vehicle record.
    """
    return VehicleResponse.model_validate(vehicle_record)


async def build_vehicle_reference(
    db_session: AsyncSession, vehicle_record: VehicleModel
) -> VehicleReference:
    """Build the minimal DTO for other domains, including the pack capacity.

    The capacity is the nominal battery capacity of the vehicle's model (the
    installed battery's design capacity takes precedence once the batteries
    domain can be read, VH-16). Reads the model even when it was removed from
    the catalog, because the vehicle keeps pointing to it.

    Args:
        db_session: Current database session.
        vehicle_record: An active vehicle record.

    Returns:
        DTO containing the internal ID, VIN, owning organization and nominal
        battery capacity of the vehicle.

    Side Effects:
        One read-only query for the vehicle model.
    """
    vehicle_model_record = await vehicle_repository.get_vehicle_model_by_id(
        db_session, vehicle_record.vehicle_model_id, include_deleted=True
    )
    battery_capacity_kwh = (
        float(vehicle_model_record.nominal_battery_capacity_kwh)
        if vehicle_model_record is not None
        and vehicle_model_record.nominal_battery_capacity_kwh is not None
        else None
    )
    return VehicleReference(
        vehicle_id=vehicle_record.vehicle_id,
        vin=vehicle_record.vin,
        organization_id=vehicle_record.organization_id,
        battery_capacity_kwh=battery_capacity_kwh,
    )


async def resolve_vehicle_reference_by_vin(
    db_session: AsyncSession,
    vin: str,
) -> VehicleReference | None:
    """Find an active vehicle by VIN and return its internal DTO.

    Args:
        db_session: Database session owned by the entry boundary.
        vin: VIN (chassis number) to look up.

    Returns:
        `VehicleReference` if the vehicle is found; otherwise `None`.

    Side Effects:
        Performs read-only queries only; does not commit or rollback.
    """
    vehicle_record = await vehicle_repository.find_by_vin(db_session, vin)
    if vehicle_record is None:
        return None
    return await build_vehicle_reference(db_session, vehicle_record)


async def resolve_vehicle_reference_by_id(
    db_session: AsyncSession,
    vehicle_id: UUID,
) -> VehicleReference | None:
    """Find an active vehicle by ID and return its internal DTO.

    Args:
        db_session: Database session owned by the entry boundary.
        vehicle_id: Internal ID of the vehicle.

    Returns:
        `VehicleReference` if the vehicle is found; otherwise `None`.

    Side Effects:
        Performs read-only queries only; does not commit or rollback.
    """
    vehicle_record = await vehicle_repository.get_by_id(db_session, vehicle_id)
    if vehicle_record is None:
        return None
    return await build_vehicle_reference(db_session, vehicle_record)


def to_vehicle_summary(vehicle_record: VehicleModel) -> VehicleSummary:
    """Convert an ORM record into a display-oriented DTO for other domains.

    Args:
        vehicle_record: An active vehicle record.

    Returns:
        DTO containing the internal ID, VIN, license plate, and status of
        the vehicle.
    """
    return VehicleSummary(
        vehicle_id=vehicle_record.vehicle_id,
        vin=vehicle_record.vin,
        license_plate=vehicle_record.license_plate,
        status=vehicle_record.status,
    )


async def resolve_vehicle_summary_by_id(
    db_session: AsyncSession,
    vehicle_id: UUID,
) -> VehicleSummary | None:
    """Find an active vehicle by ID and return its display-oriented DTO.

    Args:
        db_session: Database session owned by the entry boundary.
        vehicle_id: Internal ID of the vehicle.

    Returns:
        `VehicleSummary` if the vehicle is found; otherwise `None`.

    Side Effects:
        Performs a read-only query only; does not commit or rollback.
    """
    vehicle_record = await vehicle_repository.get_by_id(db_session, vehicle_id)
    return to_vehicle_summary(vehicle_record) if vehicle_record else None


async def create_vehicle(
    db_session: AsyncSession,
    vehicle_create_request: VehicleCreateRequest,
    *,
    principal: Principal,
) -> VehicleResponse:
    """Create a new vehicle after verifying its model and unique fields.

    Args:
        db_session: Database session owned by the entry boundary.
        vehicle_create_request: Request data that has passed Pydantic validation.
        principal: The caller; the truck is owned by the caller's organization
            unless internal staff name another one in the request.

    Returns:
        Response for the newly created vehicle.

    Raises:
        VehicleModelNotFoundError: When the vehicle model is not in the catalog.
        VehicleConflictError: When the VIN or license plate is already used by
            a vehicle still in the system.
        OrganizationNotFoundError: The named organization does not exist or is
            out of the caller's reach.

    Side Effects:
        Inserts and flushes the vehicle; does not commit. ``acquired_at``
        defaults to the current time (the handover date is not known to the
        caller yet).
    """
    if (
        await vehicle_repository.get_vehicle_model_by_id(
            db_session, vehicle_create_request.vehicle_model_id
        )
        is None
    ):
        raise VehicleModelNotFoundError(
            f"Vehicle model '{vehicle_create_request.vehicle_model_id}' not found"
        )

    existing_vehicle_by_plate = await vehicle_repository.find_by_license_plate(
        db_session,
        vehicle_create_request.license_plate,
    )
    if existing_vehicle_by_plate:
        raise VehicleConflictError(
            f"Vehicle with license plate "
            f"'{vehicle_create_request.license_plate}' already exists"
        )

    existing_vehicle_by_vin = await vehicle_repository.find_by_vin(
        db_session,
        vehicle_create_request.vin,
    )
    if existing_vehicle_by_vin:
        raise VehicleConflictError(
            f"Vehicle with VIN '{vehicle_create_request.vin}' already exists"
        )

    insert_values = vehicle_create_request.model_dump()
    insert_values[
        "organization_id"
    ] = await identity_service.resolve_organization_for_new_record(
        db_session, principal, vehicle_create_request.organization_id
    )
    if insert_values["acquired_at"] is None:
        insert_values["acquired_at"] = utc_now()
    try:
        vehicle_record = await vehicle_repository.insert(db_session, insert_values)
    except IntegrityError as error:
        raise VehicleConflictError(
            "Vehicle license plate or VIN already exists"
        ) from error

    return to_vehicle_response(vehicle_record)


async def get_vehicle(
    db_session: AsyncSession,
    vehicle_id: UUID,
    *,
    principal: Principal,
) -> VehicleResponse:
    """Get an active vehicle by ID inside the caller's data reach.

    Args:
        db_session: Current database session.
        vehicle_id: Internal ID of the vehicle.
        principal: The caller; a vehicle of another organization is not found
            unless the caller is internal.

    Returns:
        Response for the vehicle.

    Raises:
        VehicleNotFoundError: When the vehicle does not exist or has been soft-deleted.
    """
    vehicle_record = await vehicle_repository.get_by_id(
        db_session, vehicle_id, organization_id=principal.data_scope
    )
    if not vehicle_record:
        raise VehicleNotFoundError(f"Vehicle with id '{vehicle_id}' not found")

    return to_vehicle_response(vehicle_record)


async def list_vehicles(
    db_session: AsyncSession,
    *,
    principal: Principal,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
    status_filter: VehicleStatus | None = None,
) -> VehicleListResponse:
    """Get a paginated list of vehicles that are not soft-deleted.

    Args:
        db_session: Current database session.
        principal: The caller; only vehicles of the caller's organization are
            listed unless the caller is internal.
        page: Page number, starting from 1; clamped by
            `normalize_page_window`.
        page_size: Maximum number of vehicles per page; clamped to
            `1..API_MAX_PAGE_SIZE`.
        status_filter: Service status filter, if any.

    Returns:
        Paginated vehicle list response carrying the normalized page and
        page size.

    Side Effects:
        Two read-only queries (the page, then the total count).
    """
    page_window = normalize_page_window(page, page_size)
    vehicle_records = await vehicle_repository.list_all(
        db_session,
        offset=page_window.offset,
        limit=page_window.page_size,
        status_filter=status_filter,
        organization_id=principal.data_scope,
    )
    total = await vehicle_repository.count(
        db_session,
        status_filter=status_filter,
        organization_id=principal.data_scope,
    )

    return VehicleListResponse(
        items=[
            to_vehicle_response(vehicle_record) for vehicle_record in vehicle_records
        ],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def update_vehicle(
    db_session: AsyncSession,
    vehicle_id: UUID,
    vehicle_update_request: VehicleUpdateRequest,
    *,
    principal: Principal,
) -> VehicleResponse:
    """Partially update a vehicle after checking the unique fields.

    Args:
        db_session: Current database session.
        vehicle_id: Internal ID of the vehicle.
        vehicle_update_request: Field data to update.
        principal: The caller; a vehicle of another organization is not found
            unless the caller is internal.

    Returns:
        Response for the updated vehicle.

    Raises:
        VehicleNotFoundError: When the vehicle does not exist or has been soft-deleted.
        VehicleModelNotFoundError: When a new vehicle model is not in the catalog.
        VehicleConflictError: When the new VIN or license plate is already in use.

    Side Effects:
        The change is recorded in the vehicle's history with the caller as
        actor and the request's ``status_reason`` (a typed reason for a
        status decision) or else ``VEHICLE_EDITED_REASON``. Does not commit.
    """
    vehicle_record = await vehicle_repository.get_by_id(
        db_session, vehicle_id, organization_id=principal.data_scope
    )
    if not vehicle_record:
        raise VehicleNotFoundError(f"Vehicle with id '{vehicle_id}' not found")

    if (
        vehicle_update_request.vehicle_model_id
        and vehicle_update_request.vehicle_model_id != vehicle_record.vehicle_model_id
        and await vehicle_repository.get_vehicle_model_by_id(
            db_session, vehicle_update_request.vehicle_model_id
        )
        is None
    ):
        raise VehicleModelNotFoundError(
            f"Vehicle model '{vehicle_update_request.vehicle_model_id}' not found"
        )

    if (
        vehicle_update_request.license_plate
        and vehicle_update_request.license_plate != vehicle_record.license_plate
    ):
        existing_vehicle = await vehicle_repository.find_by_license_plate(
            db_session,
            vehicle_update_request.license_plate,
        )
        if existing_vehicle:
            raise VehicleConflictError(
                f"Vehicle with license plate "
                f"'{vehicle_update_request.license_plate}' already exists"
            )

    if vehicle_update_request.vin and vehicle_update_request.vin != vehicle_record.vin:
        existing_vehicle = await vehicle_repository.find_by_vin(
            db_session,
            vehicle_update_request.vin,
        )
        if existing_vehicle:
            raise VehicleConflictError(
                f"Vehicle with VIN '{vehicle_update_request.vin}' already exists"
            )

    update_values = {
        field_name: value
        for field_name, value in vehicle_update_request.model_dump(
            exclude_unset=True
        ).items()
        if value is not None
    }
    if not update_values:
        return to_vehicle_response(vehicle_record)

    try:
        updated_vehicle_record = await vehicle_repository.update_fields(
            db_session,
            vehicle_id,
            update_values,
            change_reason=update_values.get("status_reason", VEHICLE_EDITED_REASON),
            changed_by=principal.user_id,
            organization_id=principal.data_scope,
        )
    except IntegrityError as error:
        raise VehicleConflictError(
            "Vehicle license plate or VIN already exists"
        ) from error

    if updated_vehicle_record is None:
        raise VehicleNotFoundError(f"Vehicle with id '{vehicle_id}' not found")

    return to_vehicle_response(updated_vehicle_record)


async def soft_delete_vehicle(
    db_session: AsyncSession,
    vehicle_id: UUID,
    *,
    principal: Principal,
    reason: str | None = None,
) -> None:
    """Soft-delete a vehicle: it leaves the system and becomes INACTIVE.

    Rule:
        A soft-deleted vehicle is also set INACTIVE with a reason (DM-25,
        enforced by ``ck_vehicles_deleted_inactive``), so a row read later
        never claims a deleted vehicle is still ACTIVE.

    Args:
        db_session: Current database session.
        vehicle_id: Internal ID of the vehicle.
        principal: The caller; a vehicle of another organization is not found
            unless the caller is internal.
        reason: Why the vehicle leaves the system; defaults to
            ``VEHICLE_DELETED_REASON``.

    Raises:
        VehicleNotFoundError: When the vehicle does not exist or has been soft-deleted.

    Side Effects:
        Writes ``deleted_at``, ``status`` and ``status_reason`` in one flushed
        UPDATE (history reason = the reason, actor = the caller); does not
        commit.
    """
    delete_reason = reason or VEHICLE_DELETED_REASON
    vehicle_record = await vehicle_repository.update_fields(
        db_session,
        vehicle_id,
        {
            "status": VehicleStatus.INACTIVE,
            "status_reason": delete_reason,
            "deleted_at": utc_now(),
        },
        change_reason=delete_reason,
        changed_by=principal.user_id,
        organization_id=principal.data_scope,
    )
    if not vehicle_record:
        raise VehicleNotFoundError(f"Vehicle with id '{vehicle_id}' not found")


def to_vehicle_model_response(
    vehicle_model_record: VehicleModelModel,
) -> VehicleModelResponse:
    """Convert a vehicle model ORM record into an HTTP API response.

    Args:
        vehicle_model_record: Record queried or created by the repository.

    Returns:
        Response data corresponding to the record.
    """
    return VehicleModelResponse.model_validate(vehicle_model_record)


async def create_vehicle_model(
    db_session: AsyncSession,
    vehicle_model_create_request: VehicleModelCreateRequest,
) -> VehicleModelResponse:
    """Add a truck model to the catalog (VH-15).

    Args:
        db_session: Database session owned by the entry boundary.
        vehicle_model_create_request: Validated request data.

    Returns:
        Response for the new vehicle model.

    Raises:
        VehicleModelConflictError: When a live model has the same make and name.

    Side Effects:
        Inserts and flushes the row; does not commit.
    """
    if await vehicle_repository.find_vehicle_model_by_make_and_name(
        db_session,
        vehicle_model_create_request.make,
        vehicle_model_create_request.model_name,
    ):
        raise VehicleModelConflictError(
            f"Vehicle model '{vehicle_model_create_request.make} "
            f"{vehicle_model_create_request.model_name}' already exists"
        )
    insert_values = vehicle_model_create_request.model_dump()
    capacity_kwh = insert_values["nominal_battery_capacity_kwh"]
    if capacity_kwh is not None:
        # Numeric columns take Decimal; going through str keeps 282.0 exact.
        insert_values["nominal_battery_capacity_kwh"] = Decimal(str(capacity_kwh))
    try:
        vehicle_model_record = await vehicle_repository.insert_vehicle_model(
            db_session, insert_values
        )
    except IntegrityError as error:
        raise VehicleModelConflictError(
            "Vehicle model make and name already exist"
        ) from error
    return to_vehicle_model_response(vehicle_model_record)


async def get_vehicle_model(
    db_session: AsyncSession,
    vehicle_model_id: UUID,
) -> VehicleModelResponse:
    """Get a vehicle model from the catalog by ID.

    Args:
        db_session: Current database session.
        vehicle_model_id: Internal ID of the vehicle model.

    Returns:
        Response for the vehicle model.

    Raises:
        VehicleModelNotFoundError: When it does not exist or was removed.
    """
    vehicle_model_record = await vehicle_repository.get_vehicle_model_by_id(
        db_session, vehicle_model_id
    )
    if vehicle_model_record is None:
        raise VehicleModelNotFoundError(f"Vehicle model '{vehicle_model_id}' not found")
    return to_vehicle_model_response(vehicle_model_record)


async def list_vehicle_models(
    db_session: AsyncSession,
    *,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> VehicleModelListResponse:
    """Get a paginated list of the catalog's vehicle models.

    Args:
        db_session: Current database session.
        page: Page number, starting from 1; clamped by `normalize_page_window`.
        page_size: Maximum number of models per page.

    Returns:
        Paginated list response carrying the normalized page and page size.

    Side Effects:
        Two read-only queries (the page, then the total count).
    """
    page_window = normalize_page_window(page, page_size)
    vehicle_model_records = await vehicle_repository.list_vehicle_models(
        db_session, offset=page_window.offset, limit=page_window.page_size
    )
    total = await vehicle_repository.count_vehicle_models(db_session)
    return VehicleModelListResponse(
        items=[
            to_vehicle_model_response(vehicle_model_record)
            for vehicle_model_record in vehicle_model_records
        ],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )
