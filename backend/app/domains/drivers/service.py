"""Business service and public contract of the drivers domain.

This module holds the business rules for driver records and driver-vehicle
assignment. Other domains may only call the public `resolve_*` functions to
obtain internal DTOs, and must never receive the ORM model or HTTP response
schema of the drivers domain. This domain depends on the `vehicles` domain's
public service to resolve a VIN into a vehicle and to enrich a response with
a vehicle's VIN - the same one-directional edge shape already established by
`telematics -> vehicles`.
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.vehicles.service as vehicle_service
from app.domains.drivers import repository as driver_repository
from app.domains.drivers.exceptions import (
    DriverAssignmentConflictError,
    DriverAssignmentNotFoundError,
    DriverConflictError,
    DriverNotFoundError,
    DriverVehicleNotFoundError,
)
from app.domains.drivers.models import DriverModel, DriverVehicleAssignmentModel
from app.domains.drivers.schemas import (
    DriverAssignmentHistoryResponse,
    DriverCreateRequest,
    DriverListResponse,
    DriverResponse,
    DriverUpdateRequest,
    DriverVehicleAssignmentResponse,
    DriverVehicleAssignRequest,
)
from app.domains.drivers.types import DriverReference, DriverStatus
from app.libs.common.config import settings


def to_driver_reference(driver_record: DriverModel) -> DriverReference:
    """Convert an ORM record into a minimal DTO for other domains.

    Args:
        driver_record: An active driver record.

    Returns:
        DTO containing the internal ID and full name of the driver.
    """
    return DriverReference(
        driver_id=driver_record.driver_id,
        full_name=driver_record.full_name,
    )


async def resolve_driver_reference_by_id(
    db_session: AsyncSession,
    driver_id: UUID,
) -> DriverReference | None:
    """Find an active driver by ID and return its internal DTO.

    Args:
        db_session: Database session owned by the entry boundary.
        driver_id: Internal ID of the driver.

    Returns:
        `DriverReference` if the driver is found; otherwise `None`.

    Side Effects:
        Performs a read-only query only; does not commit or rollback.
    """
    driver_record = await driver_repository.get_by_id(db_session, driver_id)
    return to_driver_reference(driver_record) if driver_record else None


def to_assignment_response(
    assignment_record: DriverVehicleAssignmentModel, vehicle_vin: str | None
) -> DriverVehicleAssignmentResponse:
    """Convert an assignment ORM record into a response, given its vehicle's VIN.

    Pure mapping only, no I/O - the caller resolves `vehicle_vin` via the
    vehicles domain's public service beforehand.

    Args:
        assignment_record: Assignment record already queried or created.
        vehicle_vin: VIN of the assigned vehicle, or `None` if it no
            longer resolves (the vehicle was soft-deleted since).

    Returns:
        Response data for the assignment.
    """
    return DriverVehicleAssignmentResponse(
        assignment_id=assignment_record.assignment_id,
        driver_id=assignment_record.driver_id,
        vehicle_id=assignment_record.vehicle_id,
        vehicle_vin=vehicle_vin,
        assigned_at=assignment_record.assigned_at,
        unassigned_at=assignment_record.unassigned_at,
    )


async def build_driver_response(
    db_session: AsyncSession, driver_record: DriverModel
) -> DriverResponse:
    """Build a driver response enriched with the currently assigned vehicle.

    Args:
        db_session: Current database session.
        driver_record: Driver ORM record already queried, created, or
            updated by the caller (with all columns populated - i.e.
            freshly refreshed from the database, not a transient instance
            missing unset columns).

    Returns:
        Response data with `current_vehicle_id`/`current_vehicle_vin`
        populated from the driver's open assignment, or both `None` if
        the driver is currently unassigned.

    Side Effects:
        Looks up the driver's open assignment and, if one exists, calls
        the vehicles domain's public service to resolve its VIN - the
        only way to enrich a cross-domain fact without importing
        `vehicles.repository`/`models` directly. Read-only; does not
        commit or rollback.
    """
    active_assignment = await driver_repository.get_active_assignment_by_driver(
        db_session, driver_record.driver_id
    )
    current_vehicle_id: UUID | None = None
    current_vehicle_vin: str | None = None
    if active_assignment is not None:
        vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_id(
            db_session, active_assignment.vehicle_id
        )
        current_vehicle_id = active_assignment.vehicle_id
        current_vehicle_vin = vehicle_reference.vin if vehicle_reference else None
    return DriverResponse.model_validate(
        {
            **driver_record.__dict__,
            "current_vehicle_id": current_vehicle_id,
            "current_vehicle_vin": current_vehicle_vin,
        }
    )


async def create_driver(
    db_session: AsyncSession,
    driver_create_request: DriverCreateRequest,
) -> DriverResponse:
    """Create a new driver after verifying that the phone/license are unique.

    Args:
        db_session: Database session owned by the entry boundary.
        driver_create_request: Request data that has passed Pydantic validation.

    Returns:
        Response for the newly created driver.

    Raises:
        DriverConflictError: When the phone number or license number
            already exists.
    """
    existing_driver_by_phone = await driver_repository.find_by_phone_number(
        db_session,
        driver_create_request.phone_number,
    )
    if existing_driver_by_phone:
        raise DriverConflictError(
            f"Driver with phone number '{driver_create_request.phone_number}' "
            "already exists"
        )

    existing_driver_by_license = await driver_repository.find_by_license_number(
        db_session,
        driver_create_request.license_number,
    )
    if existing_driver_by_license:
        raise DriverConflictError(
            f"Driver with license number '{driver_create_request.license_number}' "
            "already exists"
        )

    try:
        driver_record = await driver_repository.insert(
            db_session,
            driver_create_request.model_dump(),
        )
    except IntegrityError as error:
        raise DriverConflictError(
            "Driver phone number or license number already exists"
        ) from error

    return await build_driver_response(db_session, driver_record)


async def get_driver(
    db_session: AsyncSession,
    driver_id: UUID,
) -> DriverResponse:
    """Get an active driver by ID.

    Args:
        db_session: Current database session.
        driver_id: Internal ID of the driver.

    Returns:
        Response for the driver.

    Raises:
        DriverNotFoundError: When the driver does not exist or has been soft-deleted.
    """
    driver_record = await driver_repository.get_by_id(db_session, driver_id)
    if not driver_record:
        raise DriverNotFoundError(f"Driver with id '{driver_id}' not found")

    return await build_driver_response(db_session, driver_record)


async def list_drivers(
    db_session: AsyncSession,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
    status_filter: DriverStatus | None = None,
) -> DriverListResponse:
    """Get a paginated list of active drivers.

    Args:
        db_session: Current database session.
        page: Page number, starting from 1.
        page_size: Maximum number of drivers per page.
        status_filter: Status filter, if any.

    Returns:
        Paginated driver list response.
    """
    page = max(page, settings.API_DEFAULT_PAGE)
    if page_size < 1:
        page_size = settings.API_DEFAULT_PAGE_SIZE
    elif page_size > settings.API_MAX_PAGE_SIZE:
        page_size = settings.API_MAX_PAGE_SIZE
    skip = (page - 1) * page_size

    driver_records = await driver_repository.list_all(
        db_session,
        skip,
        page_size,
        status_filter,
    )
    total = await driver_repository.count(db_session, status_filter)

    return DriverListResponse(
        items=[
            await build_driver_response(db_session, driver_record)
            for driver_record in driver_records
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


async def update_driver(
    db_session: AsyncSession,
    driver_id: UUID,
    driver_update_request: DriverUpdateRequest,
) -> DriverResponse:
    """Partially update a driver after checking the unique fields.

    Args:
        db_session: Current database session.
        driver_id: Internal ID of the driver.
        driver_update_request: Field data to update.

    Returns:
        Response for the updated driver.

    Raises:
        DriverNotFoundError: When the driver does not exist or has been soft-deleted.
        DriverConflictError: When the new phone number or license number
            is already in use.
    """
    driver_record = await driver_repository.get_by_id(db_session, driver_id)
    if not driver_record:
        raise DriverNotFoundError(f"Driver with id '{driver_id}' not found")

    if (
        driver_update_request.phone_number
        and driver_update_request.phone_number != driver_record.phone_number
    ):
        existing_driver = await driver_repository.find_by_phone_number(
            db_session,
            driver_update_request.phone_number,
        )
        if existing_driver:
            raise DriverConflictError(
                f"Driver with phone number '{driver_update_request.phone_number}' "
                "already exists"
            )

    if (
        driver_update_request.license_number
        and driver_update_request.license_number != driver_record.license_number
    ):
        existing_driver = await driver_repository.find_by_license_number(
            db_session,
            driver_update_request.license_number,
        )
        if existing_driver:
            raise DriverConflictError(
                f"Driver with license number '{driver_update_request.license_number}' "
                "already exists"
            )

    update_values = {
        field_name: value
        for field_name, value in driver_update_request.model_dump(
            exclude_unset=True
        ).items()
        if value is not None
    }
    if not update_values:
        return await build_driver_response(db_session, driver_record)

    try:
        updated_driver_record = await driver_repository.update_fields(
            db_session,
            driver_id,
            update_values,
        )
    except IntegrityError as error:
        raise DriverConflictError(
            "Driver phone number or license number already exists"
        ) from error

    if updated_driver_record is None:
        raise DriverNotFoundError(f"Driver with id '{driver_id}' not found")

    return await build_driver_response(db_session, updated_driver_record)


async def soft_delete_driver(
    db_session: AsyncSession,
    driver_id: UUID,
) -> dict[str, str]:
    """Soft-delete a driver, closing any active assignment first.

    Args:
        db_session: Current database session.
        driver_id: Internal ID of the driver.

    Returns:
        Success deletion message.

    Raises:
        DriverNotFoundError: When the driver does not exist or has been soft-deleted.

    Side Effects:
        Closes the driver's open assignment (if any) in the same
        transaction as the soft delete, so a deleted driver never holds a
        vehicle hostage against the active-assignment partial unique index.
    """
    active_assignment = await driver_repository.get_active_assignment_by_driver(
        db_session, driver_id
    )
    if active_assignment is not None:
        await driver_repository.close_assignment(
            db_session, active_assignment, unassigned_at=datetime.now(timezone.utc)
        )

    driver_record = await driver_repository.soft_delete(db_session, driver_id)
    if not driver_record:
        raise DriverNotFoundError(f"Driver with id '{driver_id}' not found")

    return {"message": "Driver deleted successfully"}


async def assign_vehicle_to_driver(
    db_session: AsyncSession,
    driver_id: UUID,
    driver_vehicle_assign_request: DriverVehicleAssignRequest,
) -> DriverVehicleAssignmentResponse:
    """Assign a vehicle to a driver, smoothly reassigning if needed.

    Rule:
        The target vehicle must not already be actively assigned to a
        *different* driver (raises rather than silently stealing it). If
        the driver already has a *different* active vehicle, that
        assignment is closed automatically before the new one opens - a
        single call reassigns, unlike the two-step "unassign, then
        assign" the existing `telematics -> vehicles` pattern requires.
        Assigning the vehicle the driver is already actively assigned to
        is a no-op that returns the existing assignment.

    Args:
        db_session: Database session owned by the entry boundary.
        driver_id: Internal ID of the driver.
        driver_vehicle_assign_request: The vehicle to assign, by VIN.

    Returns:
        The resulting open assignment.

    Raises:
        DriverNotFoundError: When the driver does not exist.
        DriverVehicleNotFoundError: When the VIN does not resolve to a vehicle.
        DriverAssignmentConflictError: When the vehicle is already
            actively assigned to a different driver.

    Side Effects:
        May close the driver's previous assignment and insert a new one
        in the same transaction; does not commit or rollback on its own.
    """
    driver_record = await driver_repository.get_by_id(db_session, driver_id)
    if driver_record is None:
        raise DriverNotFoundError(f"Driver with id '{driver_id}' not found")

    vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_vin(
        db_session, driver_vehicle_assign_request.vehicle_vin
    )
    if vehicle_reference is None:
        raise DriverVehicleNotFoundError(
            f"Vehicle with VIN '{driver_vehicle_assign_request.vehicle_vin}' not found"
        )

    vehicle_active_assignment = (
        await driver_repository.get_active_assignment_by_vehicle(
            db_session, vehicle_reference.vehicle_id
        )
    )
    if vehicle_active_assignment is not None:
        if vehicle_active_assignment.driver_id == driver_id:
            return to_assignment_response(
                vehicle_active_assignment, vehicle_reference.vin
            )
        raise DriverAssignmentConflictError(
            f"Vehicle with VIN '{driver_vehicle_assign_request.vehicle_vin}' is "
            "already assigned to another driver"
        )

    driver_active_assignment = await driver_repository.get_active_assignment_by_driver(
        db_session, driver_id
    )
    if driver_active_assignment is not None:
        await driver_repository.close_assignment(
            db_session,
            driver_active_assignment,
            unassigned_at=datetime.now(timezone.utc),
        )

    try:
        assignment_record = await driver_repository.insert_assignment(
            db_session,
            driver_id=driver_id,
            vehicle_id=vehicle_reference.vehicle_id,
            assigned_at=datetime.now(timezone.utc),
        )
    except IntegrityError as error:
        raise DriverAssignmentConflictError(
            "Vehicle or driver already has an active assignment"
        ) from error

    return to_assignment_response(assignment_record, vehicle_reference.vin)


async def unassign_vehicle_from_driver(
    db_session: AsyncSession,
    driver_id: UUID,
) -> None:
    """Close a driver's active assignment.

    Args:
        db_session: Database session owned by the entry boundary.
        driver_id: Internal ID of the driver.

    Raises:
        DriverNotFoundError: When the driver does not exist.
        DriverAssignmentNotFoundError: When the driver has no active assignment.
    """
    driver_record = await driver_repository.get_by_id(db_session, driver_id)
    if driver_record is None:
        raise DriverNotFoundError(f"Driver with id '{driver_id}' not found")

    active_assignment = await driver_repository.get_active_assignment_by_driver(
        db_session, driver_id
    )
    if active_assignment is None:
        raise DriverAssignmentNotFoundError(
            f"Driver with id '{driver_id}' has no active assignment"
        )

    await driver_repository.close_assignment(
        db_session, active_assignment, unassigned_at=datetime.now(timezone.utc)
    )


async def list_driver_assignment_history(
    db_session: AsyncSession,
    driver_id: UUID,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> DriverAssignmentHistoryResponse:
    """Get a driver's paginated assignment history, newest first.

    Args:
        db_session: Current database session.
        driver_id: Internal ID of the driver.
        page: Page number, starting from 1.
        page_size: Maximum number of assignments per page.

    Returns:
        Paginated assignment history, including closed assignments.

    Raises:
        DriverNotFoundError: When the driver does not exist.

    Side Effects:
        Resolves each assignment's vehicle VIN with one per-row call to
        the vehicles domain's public service - no batching, per this
        repo's no-premature-batching convention.
    """
    driver_record = await driver_repository.get_by_id(db_session, driver_id)
    if driver_record is None:
        raise DriverNotFoundError(f"Driver with id '{driver_id}' not found")

    page = max(page, settings.API_DEFAULT_PAGE)
    page_size = min(max(page_size, 1), settings.API_MAX_PAGE_SIZE)
    skip = (page - 1) * page_size

    assignment_records = await driver_repository.list_assignments_by_driver(
        db_session, driver_id, offset=skip, limit=page_size
    )
    total = await driver_repository.count_assignments_by_driver(db_session, driver_id)

    items = []
    for assignment_record in assignment_records:
        vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_id(
            db_session, assignment_record.vehicle_id
        )
        vehicle_vin = vehicle_reference.vin if vehicle_reference else None
        items.append(to_assignment_response(assignment_record, vehicle_vin))

    return DriverAssignmentHistoryResponse(
        items=items, total=total, page=page, page_size=page_size
    )
