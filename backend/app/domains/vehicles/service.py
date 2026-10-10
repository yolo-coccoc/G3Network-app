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

from datetime import datetime, timedelta
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
    VehicleTransferInvalidError,
)
from app.domains.vehicles.models import VehicleModel, VehicleModelModel
from app.domains.vehicles.schemas import (
    VehicleCreateRequest,
    VehicleListResponse,
    VehicleModelCreateRequest,
    VehicleModelListResponse,
    VehicleModelResponse,
    VehicleModelUpdateRequest,
    VehicleOwnershipPeriodListResponse,
    VehicleOwnershipPeriodResponse,
    VehicleOwnershipTransferRequest,
    VehicleResponse,
    VehicleUpdateRequest,
)
from app.domains.vehicles.types import (
    VehicleOwnershipTransferResult,
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
VEHICLE_MODEL_EDITED_REASON = "Vehicle model edited"
VEHICLE_MODEL_DELETED_REASON = "Vehicle model removed from the catalog"

# A handover date typed on a client may run a little ahead of the server clock;
# a transfer effective later than this margin is refused (VH-12).
TRANSFER_CLOCK_SKEW = timedelta(minutes=5)


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


async def resolve_vehicle_reference_by_code(
    db_session: AsyncSession,
    vehicle_code: str,
) -> VehicleReference | None:
    """Find an active vehicle by the code printed on it: its VIN or its plate.

    The content of the QR code is still open (deferred.md 94), so a scanned
    code may be either value. The VIN is tried first; a plate is matched as
    typed, then upper-cased.

    Args:
        db_session: Database session owned by the entry boundary.
        vehicle_code: A VIN or a licence plate.

    Returns:
        `VehicleReference` if exactly such a vehicle is found; otherwise `None`.

    Side Effects:
        Performs read-only queries only; does not commit or rollback.
    """
    code = vehicle_code.strip()
    vehicle_record = await vehicle_repository.find_by_vin(db_session, code)
    if vehicle_record is None:
        vehicle_record = await vehicle_repository.find_by_license_plate(
            db_session, code
        )
    if vehicle_record is None and code != code.upper():
        vehicle_record = await vehicle_repository.find_by_license_plate(
            db_session, code.upper()
        )
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


async def list_vehicle_summaries(
    db_session: AsyncSession,
    *,
    organization_id: UUID | None,
    offset: int,
    limit: int,
) -> list[VehicleSummary]:
    """List live vehicles as display DTOs, for another domain's own listing.

    The activation list of ``telemetry`` (VEH-05) walks the trucks of a data
    scope page by page; the vehicles domain still owns the table.

    Args:
        db_session: Database session owned by the entry boundary.
        organization_id: Data scope (the caller's organization); `None` means
            every organization.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        Summaries of live (not soft-deleted) vehicles, newest first.

    Side Effects:
        Performs a read-only query only; does not commit or rollback.
    """
    vehicle_records = await vehicle_repository.list_all(
        db_session, offset=offset, limit=limit, organization_id=organization_id
    )
    return [to_vehicle_summary(vehicle_record) for vehicle_record in vehicle_records]


async def resolve_first_handover_at(
    db_session: AsyncSession, vehicle_id: UUID
) -> datetime | None:
    """Tell when a truck first went to an owner (its handover date, VH-06).

    Read through the ownership periods view (DM-22), so a later sale does not
    move the date: it is the start of the oldest period.

    Args:
        db_session: Database session owned by the entry boundary.
        vehicle_id: Internal ID of the vehicle.

    Returns:
        The start of the first ownership period, or `None` if the view has no
        period for the truck.

    Side Effects:
        Performs a read-only query only; does not commit or rollback.
    """
    period_rows = await vehicle_repository.list_ownership_periods(
        db_session, vehicle_id
    )
    return period_rows[0][1] if period_rows else None


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
    search: str | None = None,
    vehicle_model_id: UUID | None = None,
    owner_organization_id: UUID | None = None,
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
        search: Plate or VIN fragment, if any (case-insensitive).
        vehicle_model_id: Only trucks of this catalog model, if given.
        owner_organization_id: Only trucks owned by this organization, if
            given (for a customer it can only narrow their own trucks).

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
        search=search,
        vehicle_model_id=vehicle_model_id,
        owner_organization_id=owner_organization_id,
    )
    total = await vehicle_repository.count(
        db_session,
        status_filter=status_filter,
        organization_id=principal.data_scope,
        search=search,
        vehicle_model_id=vehicle_model_id,
        owner_organization_id=owner_organization_id,
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


async def transfer_vehicle_ownership(
    db_session: AsyncSession,
    vehicle_id: UUID,
    vehicle_ownership_transfer_request: VehicleOwnershipTransferRequest,
    *,
    principal: Principal,
) -> VehicleOwnershipTransferResult:
    """Hand a truck to a new owning organization (VH-12, DM-22).

    Only the truck's own row changes here: ``organization_id`` and
    ``acquired_at`` are replaced and the old values stay in ``vehicle_history``
    with the caller as actor and the typed reason, so the view
    ``vehicle_ownership_periods`` shows the new period. The rest of VH-12
    (the seller's fleet memberships, the open driving session, a battery the
    seller owns) belongs to other domains and is done by the orchestration in
    ``app/api/vehicle_transfer.py`` in the same transaction, because
    ``vehicles`` must not call ``fleet``, ``drivers`` or ``batteries``
    (one-way edges, FL-01, VH-13).

    Rules:
        The new owner must exist and differ from the current owner. The
        effective date defaults to now, may not be in the future (beyond
        ``TRANSFER_CLOCK_SKEW``) and must be after the date the current owner
        took the truck, so every period has a positive length.

    Args:
        db_session: Current database session.
        vehicle_id: Internal ID of the vehicle.
        vehicle_ownership_transfer_request: New owner, effective date, reason.
        principal: The caller (internal staff); a truck outside their reach is
            not found.

    Returns:
        What changed, for the orchestrating caller.

    Raises:
        VehicleNotFoundError: The vehicle does not exist or is soft-deleted.
        OrganizationNotFoundError: The new owner does not exist.
        VehicleTransferInvalidError: A rule above is broken.

    Side Effects:
        Flushes one UPDATE of the vehicle; does not commit.
    """
    vehicle_record = await vehicle_repository.get_by_id(
        db_session, vehicle_id, organization_id=principal.data_scope
    )
    if vehicle_record is None:
        raise VehicleNotFoundError(f"Vehicle with id '{vehicle_id}' not found")

    new_organization_id = await identity_service.resolve_organization_for_new_record(
        db_session, principal, vehicle_ownership_transfer_request.organization_id
    )
    if new_organization_id == vehicle_record.organization_id:
        raise VehicleTransferInvalidError("The organization already owns this vehicle")
    now = utc_now()
    effective_at = vehicle_ownership_transfer_request.acquired_at or now
    if effective_at > now + TRANSFER_CLOCK_SKEW:
        raise VehicleTransferInvalidError("The transfer date cannot be in the future")
    if effective_at <= vehicle_record.acquired_at:
        raise VehicleTransferInvalidError(
            "The transfer date must be after the date the current owner took "
            "the vehicle"
        )

    previous_organization_id = vehicle_record.organization_id
    await vehicle_repository.update_fields(
        db_session,
        vehicle_id,
        {"organization_id": new_organization_id, "acquired_at": effective_at},
        change_reason=vehicle_ownership_transfer_request.reason,
        changed_by=principal.user_id,
        organization_id=principal.data_scope,
    )
    return VehicleOwnershipTransferResult(
        vehicle_id=vehicle_id,
        previous_organization_id=previous_organization_id,
        organization_id=new_organization_id,
        acquired_at=effective_at,
    )


async def list_vehicle_ownership_periods(
    db_session: AsyncSession,
    vehicle_id: UUID,
    *,
    principal: Principal,
) -> VehicleOwnershipPeriodListResponse:
    """List the periods in which organizations owned a truck (VH-10).

    Read through the view ``vehicle_ownership_periods``. Internal staff see
    every period; anyone else sees only their own organization's periods
    (the truck must be theirs now).

    Args:
        db_session: Current database session.
        vehicle_id: Internal ID of the vehicle.
        principal: The caller.

    Returns:
        The periods, oldest first; the owner now has ``owned_until`` null.

    Raises:
        VehicleNotFoundError: The vehicle does not exist, is soft-deleted or
            is out of the caller's reach.
    """
    vehicle_record = await vehicle_repository.get_by_id(
        db_session, vehicle_id, organization_id=principal.data_scope
    )
    if vehicle_record is None:
        raise VehicleNotFoundError(f"Vehicle with id '{vehicle_id}' not found")
    period_rows = await vehicle_repository.list_ownership_periods(
        db_session, vehicle_id, organization_id=principal.data_scope
    )
    return VehicleOwnershipPeriodListResponse(
        items=[
            VehicleOwnershipPeriodResponse(
                organization_id=period_organization_id,
                owned_from=owned_from,
                owned_until=owned_until,
            )
            for period_organization_id, owned_from, owned_until in period_rows
        ]
    )


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
    search: str | None = None,
    make: str | None = None,
) -> VehicleModelListResponse:
    """Get a paginated list of the catalog's vehicle models.

    Args:
        db_session: Current database session.
        page: Page number, starting from 1; clamped by `normalize_page_window`.
        page_size: Maximum number of models per page.
        search: Make or model-name fragment, if any.
        make: Exact manufacturer, if any.

    Returns:
        Paginated list response carrying the normalized page and page size.

    Side Effects:
        Two read-only queries (the page, then the total count).
    """
    page_window = normalize_page_window(page, page_size)
    vehicle_model_records = await vehicle_repository.list_vehicle_models(
        db_session,
        offset=page_window.offset,
        limit=page_window.page_size,
        search=search,
        make=make,
    )
    total = await vehicle_repository.count_vehicle_models(
        db_session, search=search, make=make
    )
    return VehicleModelListResponse(
        items=[
            to_vehicle_model_response(vehicle_model_record)
            for vehicle_model_record in vehicle_model_records
        ],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def update_vehicle_model(
    db_session: AsyncSession,
    vehicle_model_id: UUID,
    vehicle_model_update_request: VehicleModelUpdateRequest,
    *,
    principal: Principal,
) -> VehicleModelResponse:
    """Partially update a catalog model (VH-15, VEH-03).

    Figures are entered once confirmed and may be corrected later; trucks of
    the model read the new figures at once (reports use the model's nominal
    capacity).

    Args:
        db_session: Current database session.
        vehicle_model_id: Internal ID of the model.
        vehicle_model_update_request: Fields to change; null means unchanged.
        principal: The caller (internal staff), recorded as the history actor.

    Returns:
        The updated model.

    Raises:
        VehicleModelNotFoundError: The model does not exist or was removed.
        VehicleModelConflictError: Another live model has the new make and name.

    Side Effects:
        Flushes the UPDATE; the change is recorded in the model's history.
    """
    vehicle_model_record = await vehicle_repository.get_vehicle_model_by_id(
        db_session, vehicle_model_id
    )
    if vehicle_model_record is None:
        raise VehicleModelNotFoundError(f"Vehicle model '{vehicle_model_id}' not found")

    update_values = {
        field_name: value
        for field_name, value in vehicle_model_update_request.model_dump(
            exclude_unset=True
        ).items()
        if value is not None
    }
    if not update_values:
        return to_vehicle_model_response(vehicle_model_record)
    capacity_kwh = update_values.get("nominal_battery_capacity_kwh")
    if capacity_kwh is not None:
        update_values["nominal_battery_capacity_kwh"] = Decimal(str(capacity_kwh))

    new_make = update_values.get("make", vehicle_model_record.make)
    new_model_name = update_values.get("model_name", vehicle_model_record.model_name)
    if (new_make, new_model_name) != (
        vehicle_model_record.make,
        vehicle_model_record.model_name,
    ) and await vehicle_repository.find_vehicle_model_by_make_and_name(
        db_session, new_make, new_model_name
    ):
        raise VehicleModelConflictError(
            f"Vehicle model '{new_make} {new_model_name}' already exists"
        )
    try:
        updated_record = await vehicle_repository.update_vehicle_model_fields(
            db_session,
            vehicle_model_id,
            update_values,
            change_reason=VEHICLE_MODEL_EDITED_REASON,
            changed_by=principal.user_id,
        )
    except IntegrityError as error:
        raise VehicleModelConflictError(
            "Vehicle model make and name already exist"
        ) from error
    if updated_record is None:
        raise VehicleModelNotFoundError(f"Vehicle model '{vehicle_model_id}' not found")
    return to_vehicle_model_response(updated_record)


async def soft_delete_vehicle_model(
    db_session: AsyncSession,
    vehicle_model_id: UUID,
    *,
    principal: Principal,
) -> None:
    """Remove a model from the catalog (soft delete).

    Trucks that already point to the model keep it and read its figures
    (``include_deleted``); only new trucks can no longer choose it.

    Args:
        db_session: Current database session.
        vehicle_model_id: Internal ID of the model.
        principal: The caller (internal staff), recorded as the history actor.

    Raises:
        VehicleModelNotFoundError: The model does not exist or was removed.

    Side Effects:
        Stamps ``deleted_at``; the change is recorded in the model's history.
    """
    removed_record = await vehicle_repository.update_vehicle_model_fields(
        db_session,
        vehicle_model_id,
        {"deleted_at": utc_now()},
        change_reason=VEHICLE_MODEL_DELETED_REASON,
        changed_by=principal.user_id,
    )
    if removed_record is None:
        raise VehicleModelNotFoundError(f"Vehicle model '{vehicle_model_id}' not found")
