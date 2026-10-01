"""Business service and public contract of the vehicles domain.

This module holds the business rules for the vehicle record. Other domains
may only call the `resolve_*` functions (which return internal DTOs) and the
F-F2 activation hooks `mark_device_assigned`/`mark_vehicle_activated`; they
never receive the ORM model or HTTP response schema of the vehicles domain.
None of the functions commit or roll back - the caller's entry boundary owns
the transaction.
"""

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.vehicles.repository as vehicle_repository
from app.domains.vehicles.exceptions import (
    VehicleConflictError,
    VehicleNotFoundError,
)
from app.domains.vehicles.models import VehicleModel
from app.domains.vehicles.schemas import (
    VehicleActivationSummaryResponse,
    VehicleCreateRequest,
    VehicleListResponse,
    VehicleResponse,
    VehicleUpdateRequest,
)
from app.domains.vehicles.types import (
    VehicleActivationStatus,
    VehicleReference,
    VehicleStatus,
    VehicleSummary,
)
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.pagination import normalize_page_window


def to_vehicle_response(vehicle_record: VehicleModel) -> VehicleResponse:
    """Convert a vehicle ORM record into an HTTP API response.

    Args:
        vehicle_record: Vehicle record queried or created by the repository.

    Returns:
        Response data corresponding to the vehicle record.
    """
    return VehicleResponse.model_validate(vehicle_record)


def to_vehicle_reference(vehicle_record: VehicleModel) -> VehicleReference:
    """Convert an ORM record into a minimal DTO for other domains.

    Args:
        vehicle_record: An active vehicle record.

    Returns:
        DTO containing the internal ID, VIN, and battery capacity of the
        vehicle.
    """
    return VehicleReference(
        vehicle_id=vehicle_record.vehicle_id,
        vin=vehicle_record.vin,
        battery_capacity_kwh=vehicle_record.battery_capacity_kwh,
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
        Performs a read-only query only; does not commit or rollback.
    """
    vehicle_record = await vehicle_repository.find_by_vin(db_session, vin)
    return to_vehicle_reference(vehicle_record) if vehicle_record else None


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
        Performs a read-only query only; does not commit or rollback.
    """
    vehicle_record = await vehicle_repository.get_by_id(db_session, vehicle_id)
    return to_vehicle_reference(vehicle_record) if vehicle_record else None


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


async def mark_device_assigned(db_session: AsyncSession, vehicle_id: UUID) -> None:
    """Advance a vehicle's activation status once a device is assigned. Public entry point for F-F2.

    Called by the `telematics` domain right after it resolves a telematic
    device onto this vehicle.

    Args:
        db_session: Database session owned by the caller's entry boundary
            (the HTTP boundary handling the telematic create/update).
        vehicle_id: Internal ID of the vehicle a device was just assigned to.

    Side Effects:
        No-op if the vehicle doesn't exist or is already past `PENDING` -
        this is a best-effort side channel, not a primary business
        operation, so it never raises back into the caller's flow.
    """
    vehicle_record = await vehicle_repository.get_by_id(db_session, vehicle_id)
    if (
        vehicle_record is None
        or vehicle_record.activation_status is not VehicleActivationStatus.PENDING
    ):
        return
    await vehicle_repository.update_fields(
        db_session,
        vehicle_id,
        {"activation_status": VehicleActivationStatus.DEVICE_ASSIGNED},
    )


async def mark_vehicle_activated(db_session: AsyncSession, vehicle_id: UUID) -> None:
    """Mark a vehicle activated once its first telemetry message arrives. Public entry point for F-F2.

    Called by `telemetry.process_message` on a vehicle's first-ever
    telemetry message - the strongest available signal that end-to-end
    data flow is confirmed.

    Args:
        db_session: Database session owned by the caller's entry boundary
            (the telemetry ingestion worker's transaction).
        vehicle_id: Internal ID of the vehicle that just reported telemetry
            for the first time.

    Side Effects:
        No-op if the vehicle doesn't exist or is already `ACTIVATED` -
        same best-effort, never-raises contract as `mark_device_assigned`.
    """
    vehicle_record = await vehicle_repository.get_by_id(db_session, vehicle_id)
    if (
        vehicle_record is None
        or vehicle_record.activation_status is VehicleActivationStatus.ACTIVATED
    ):
        return
    await vehicle_repository.update_fields(
        db_session,
        vehicle_id,
        {"activation_status": VehicleActivationStatus.ACTIVATED},
    )


async def get_vehicle_activation_summary(
    db_session: AsyncSession,
) -> VehicleActivationSummaryResponse:
    """Get the fleet-wide F-F2 activation success rate.

    Args:
        db_session: Current database session.

    Returns:
        Counts of vehicles that have at least started provisioning
        (`DEVICE_ASSIGNED` or `ACTIVATED`) versus fully confirmed
        (`ACTIVATED`), and the resulting success rate. `attempted_count ==
        0` reports `activation_rate_percent = None` rather than dividing
        by zero.

    Side Effects:
        Two separate count queries (one per activation status) rather than
        one grouped query - this repo's convention is the simple per-item
        version first, batched only once a benchmark shows a need.
    """
    device_assigned_count = await vehicle_repository.count_by_activation_status(
        db_session, VehicleActivationStatus.DEVICE_ASSIGNED
    )
    activated_count = await vehicle_repository.count_by_activation_status(
        db_session, VehicleActivationStatus.ACTIVATED
    )
    attempted_count = device_assigned_count + activated_count
    activation_rate_percent = (
        (activated_count / attempted_count) * 100 if attempted_count > 0 else None
    )
    return VehicleActivationSummaryResponse(
        attempted_count=attempted_count,
        activated_count=activated_count,
        activation_rate_percent=activation_rate_percent,
    )


async def create_vehicle(
    db_session: AsyncSession,
    vehicle_create_request: VehicleCreateRequest,
) -> VehicleResponse:
    """Create a new vehicle after verifying that the VIN and license plate are unique.

    Args:
        db_session: Database session owned by the entry boundary.
        vehicle_create_request: Request data that has passed Pydantic validation.

    Returns:
        Response for the newly created vehicle.

    Raises:
        VehicleConflictError: When the VIN or license plate already exists.
    """
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

    try:
        vehicle_record = await vehicle_repository.insert(
            db_session,
            vehicle_create_request.model_dump(),
        )
    except IntegrityError as error:
        raise VehicleConflictError(
            "Vehicle license plate or VIN already exists"
        ) from error

    return to_vehicle_response(vehicle_record)


async def get_vehicle(
    db_session: AsyncSession,
    vehicle_id: UUID,
) -> VehicleResponse:
    """Get an active vehicle by ID.

    Args:
        db_session: Current database session.
        vehicle_id: Internal ID of the vehicle.

    Returns:
        Response for the vehicle.

    Raises:
        VehicleNotFoundError: When the vehicle does not exist or has been soft-deleted.
    """
    vehicle_record = await vehicle_repository.get_by_id(db_session, vehicle_id)
    if not vehicle_record:
        raise VehicleNotFoundError(f"Vehicle with id '{vehicle_id}' not found")

    return to_vehicle_response(vehicle_record)


async def list_vehicles(
    db_session: AsyncSession,
    *,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
    status_filter: VehicleStatus | None = None,
) -> VehicleListResponse:
    """Get a paginated list of vehicles that are not soft-deleted.

    Args:
        db_session: Current database session.
        page: Page number, starting from 1; clamped by
            `normalize_page_window`.
        page_size: Maximum number of vehicles per page; clamped to
            `1..API_MAX_PAGE_SIZE`.
        status_filter: Status filter, if any.

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
    )
    total = await vehicle_repository.count(db_session, status_filter)

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
) -> VehicleResponse:
    """Partially update a vehicle after checking the unique fields.

    Args:
        db_session: Current database session.
        vehicle_id: Internal ID of the vehicle.
        vehicle_update_request: Field data to update.

    Returns:
        Response for the updated vehicle.

    Raises:
        VehicleNotFoundError: When the vehicle does not exist or has been soft-deleted.
        VehicleConflictError: When the new VIN or license plate is already in use.
    """
    vehicle_record = await vehicle_repository.get_by_id(db_session, vehicle_id)
    if not vehicle_record:
        raise VehicleNotFoundError(f"Vehicle with id '{vehicle_id}' not found")

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
) -> None:
    """Soft-delete a vehicle and decommission it.

    Rule:
        A soft-deleted vehicle is also moved to `DECOMMISSIONED`, so a row
        read later (history, audit) never claims a deleted vehicle is still
        `ACTIVE`.

    Args:
        db_session: Current database session.
        vehicle_id: Internal ID of the vehicle.

    Raises:
        VehicleNotFoundError: When the vehicle does not exist or has been soft-deleted.

    Side Effects:
        Writes `deleted_at` and `status` in one flushed UPDATE; does not
        commit.
    """
    vehicle_record = await vehicle_repository.update_fields(
        db_session,
        vehicle_id,
        {"status": VehicleStatus.DECOMMISSIONED, "deleted_at": utc_now()},
    )
    if not vehicle_record:
        raise VehicleNotFoundError(f"Vehicle with id '{vehicle_id}' not found")
