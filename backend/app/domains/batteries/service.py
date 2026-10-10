"""Business service and public contract of the batteries domain (BAT-01).

Holds the rules for the battery model catalog and for batteries as assets:
registration, edits, fitting to / removal from a truck (one pack per truck,
VH-16), ownership transfer and the soft delete (DM-25). Other domains may only
call the ``resolve_*`` function and ``transfer_installed_battery_with_vehicle``
(they take and return primitives); they never receive the ORM model or an HTTP
schema. None of the functions commit or roll back - the caller's entry
boundary owns the transaction.

Access: the router-facing functions take the caller's `Principal` and pass
``principal.data_scope`` to the repository, so a battery owned by another
organization is "not found" unless the caller is internal staff. A truck's
pack capacity (``resolve_installed_battery_capacity_kwh``) is an unscoped
system lookup.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.batteries.repository as battery_repository
import app.domains.identity.service as identity_service
import app.domains.vehicles.service as vehicle_service
from app.domains.batteries.exceptions import (
    BatteryConflictError,
    BatteryDateInvalidError,
    BatteryModelConflictError,
    BatteryModelNotFoundError,
    BatteryNotFoundError,
    BatteryVehicleNotFoundError,
)
from app.domains.batteries.models import BatteryModel, BatteryModelModel
from app.domains.batteries.schemas import (
    BatteryCreateRequest,
    BatteryInstallationPeriodListResponse,
    BatteryInstallationPeriodResponse,
    BatteryInstallRequest,
    BatteryListResponse,
    BatteryModelCreateRequest,
    BatteryModelListResponse,
    BatteryModelResponse,
    BatteryModelUpdateRequest,
    BatteryOwnershipTransferRequest,
    BatteryResponse,
    BatteryUpdateRequest,
)
from app.domains.batteries.types import BatteryStatus
from app.domains.identity.types import Principal
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.pagination import normalize_page_window

# Fixed history reasons of routine actions; the acting user is the caller.
BATTERY_MODEL_EDITED_REASON = "Battery model edited"
BATTERY_MODEL_DELETED_REASON = "Battery model removed from the catalog"
BATTERY_EDITED_REASON = "Battery details edited"
BATTERY_DELETED_REASON = "Battery deleted"
BATTERY_INSTALLED_REASON = "Battery installed in vehicle"
BATTERY_REMOVED_REASON = "Battery removed from vehicle"
BATTERY_SOLD_WITH_VEHICLE_REASON = "Battery moved to the buyer with its vehicle"

# A date typed on a client may run a little ahead of the server clock; a date
# later than this margin is refused.
DATE_CLOCK_SKEW = timedelta(minutes=5)


def to_battery_model_response(
    battery_model_record: BatteryModelModel,
) -> BatteryModelResponse:
    """Convert a battery model ORM record into an HTTP API response.

    Args:
        battery_model_record: Record queried or created by the repository.

    Returns:
        Response data corresponding to the record.
    """
    return BatteryModelResponse.model_validate(battery_model_record)


def to_battery_response(battery_record: BatteryModel) -> BatteryResponse:
    """Convert a battery ORM record into an HTTP API response.

    Args:
        battery_record: Record queried or created by the repository.

    Returns:
        Response data corresponding to the record.
    """
    return BatteryResponse.model_validate(battery_record)


def _to_decimal_fields(values: dict[str, object]) -> None:
    """Turn the float figures of a request into ``Decimal`` for numeric columns.

    Going through ``str`` keeps 282.5 exact instead of its binary float form.

    Args:
        values: Insert or update values; modified in place.
    """
    for field_name in ("design_capacity_kwh", "nominal_voltage_v"):
        figure = values.get(field_name)
        if figure is not None:
            values[field_name] = Decimal(str(figure))


# --- cross-domain contract --------------------------------------------------


async def resolve_installed_battery_capacity_kwh(
    db_session: AsyncSession, vehicle_id: UUID
) -> float | None:
    """Tell the design capacity of the pack fitted to a truck (VH-16).

    A truck's capacity is its installed battery's design capacity, else its
    model's nominal capacity; the caller applies the fallback.

    Args:
        db_session: Database session owned by the entry boundary.
        vehicle_id: Internal ID of the truck.

    Returns:
        The design capacity in kWh of the battery fitted now, or `None` when
        no battery is fitted or its model records no capacity.

    Side Effects:
        Read-only queries; does not commit or roll back. Reads the battery's
        model even when it was removed from the catalog.
    """
    battery_record = await battery_repository.find_by_vehicle_id(db_session, vehicle_id)
    if battery_record is None:
        return None
    battery_model_record = await battery_repository.get_battery_model_by_id(
        db_session, battery_record.battery_model_id, include_deleted=True
    )
    if battery_model_record is None or battery_model_record.design_capacity_kwh is None:
        return None
    return float(battery_model_record.design_capacity_kwh)


async def transfer_installed_battery_with_vehicle(
    db_session: AsyncSession,
    vehicle_id: UUID,
    *,
    from_organization_id: UUID,
    to_organization_id: UUID,
    acquired_at: datetime,
    changed_by: UUID | None,
) -> UUID | None:
    """Move the seller's battery to the buyer when its truck is sold (VH-12).

    A battery owned by someone else (for example leased from G3) stays with
    its owner; only a pack owned by the seller moves.

    Args:
        db_session: Database session owned by the entry boundary.
        vehicle_id: The truck that was sold.
        from_organization_id: The seller.
        to_organization_id: The buyer.
        acquired_at: Effective date of the truck transfer, used as the
            battery's new ``acquired_at``.
        changed_by: The acting user, recorded in the battery's history.

    Returns:
        The ID of the battery that moved, or `None` when the truck holds no
        battery or the battery belongs to another organization.

    Side Effects:
        One flushed UPDATE of the battery; does not commit.
    """
    battery_record = await battery_repository.find_by_vehicle_id(db_session, vehicle_id)
    if battery_record is None or battery_record.organization_id != from_organization_id:
        return None
    await battery_repository.update_fields(
        db_session,
        battery_record.battery_id,
        {"organization_id": to_organization_id, "acquired_at": acquired_at},
        change_reason=BATTERY_SOLD_WITH_VEHICLE_REASON,
        changed_by=changed_by,
    )
    return battery_record.battery_id


# --- battery models ---------------------------------------------------------


async def create_battery_model(
    db_session: AsyncSession,
    battery_model_create_request: BatteryModelCreateRequest,
) -> BatteryModelResponse:
    """Add a battery type to the catalog.

    Args:
        db_session: Database session owned by the entry boundary.
        battery_model_create_request: Validated request data.

    Returns:
        Response for the new battery model.

    Raises:
        BatteryModelConflictError: A live model has the same maker and name.

    Side Effects:
        Inserts and flushes the row; does not commit.
    """
    if await battery_repository.find_battery_model_by_manufacturer_and_name(
        db_session,
        battery_model_create_request.manufacturer,
        battery_model_create_request.model_name,
    ):
        raise BatteryModelConflictError(
            f"Battery model '{battery_model_create_request.manufacturer} "
            f"{battery_model_create_request.model_name}' already exists"
        )
    insert_values: dict[str, object] = battery_model_create_request.model_dump()
    insert_values["chemistry"] = battery_model_create_request.chemistry.value
    _to_decimal_fields(insert_values)
    try:
        battery_model_record = await battery_repository.insert_battery_model(
            db_session, insert_values
        )
    except IntegrityError as error:
        raise BatteryModelConflictError(
            "Battery model maker and name already exist"
        ) from error
    return to_battery_model_response(battery_model_record)


async def get_battery_model(
    db_session: AsyncSession, battery_model_id: UUID
) -> BatteryModelResponse:
    """Get a battery model from the catalog by ID.

    Args:
        db_session: Current database session.
        battery_model_id: Internal ID of the model.

    Returns:
        Response for the battery model.

    Raises:
        BatteryModelNotFoundError: It does not exist or was removed.
    """
    battery_model_record = await battery_repository.get_battery_model_by_id(
        db_session, battery_model_id
    )
    if battery_model_record is None:
        raise BatteryModelNotFoundError(f"Battery model '{battery_model_id}' not found")
    return to_battery_model_response(battery_model_record)


async def list_battery_models(
    db_session: AsyncSession,
    *,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
    search: str | None = None,
    chemistry: str | None = None,
) -> BatteryModelListResponse:
    """Get a paginated list of the catalog's battery models.

    Args:
        db_session: Current database session.
        page: Page number, starting from 1; clamped by `normalize_page_window`.
        page_size: Maximum number of models per page.
        search: Maker or model-name fragment, if any.
        chemistry: Exact chemistry value, if any.

    Returns:
        Paginated list response carrying the normalized page and page size.

    Side Effects:
        Two read-only queries (the page, then the total count).
    """
    page_window = normalize_page_window(page, page_size)
    battery_model_records = await battery_repository.list_battery_models(
        db_session,
        offset=page_window.offset,
        limit=page_window.page_size,
        search=search,
        chemistry=chemistry,
    )
    total = await battery_repository.count_battery_models(
        db_session, search=search, chemistry=chemistry
    )
    return BatteryModelListResponse(
        items=[
            to_battery_model_response(battery_model_record)
            for battery_model_record in battery_model_records
        ],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def update_battery_model(
    db_session: AsyncSession,
    battery_model_id: UUID,
    battery_model_update_request: BatteryModelUpdateRequest,
    *,
    principal: Principal,
) -> BatteryModelResponse:
    """Partially update a battery model.

    Args:
        db_session: Current database session.
        battery_model_id: Internal ID of the model.
        battery_model_update_request: Fields to change; null means unchanged.
        principal: The caller (internal staff), recorded as the history actor.

    Returns:
        The updated model.

    Raises:
        BatteryModelNotFoundError: The model does not exist or was removed.
        BatteryModelConflictError: Another live model has the new maker and name.

    Side Effects:
        Flushes the UPDATE; the change is recorded in the model's history.
    """
    battery_model_record = await battery_repository.get_battery_model_by_id(
        db_session, battery_model_id
    )
    if battery_model_record is None:
        raise BatteryModelNotFoundError(f"Battery model '{battery_model_id}' not found")
    update_values: dict[str, object] = {
        field_name: value
        for field_name, value in battery_model_update_request.model_dump(
            exclude_unset=True
        ).items()
        if value is not None
    }
    if not update_values:
        return to_battery_model_response(battery_model_record)
    if battery_model_update_request.chemistry is not None:
        update_values["chemistry"] = battery_model_update_request.chemistry.value
    _to_decimal_fields(update_values)

    new_manufacturer = str(
        update_values.get("manufacturer", battery_model_record.manufacturer)
    )
    new_model_name = str(
        update_values.get("model_name", battery_model_record.model_name)
    )
    if (new_manufacturer, new_model_name) != (
        battery_model_record.manufacturer,
        battery_model_record.model_name,
    ) and await battery_repository.find_battery_model_by_manufacturer_and_name(
        db_session, new_manufacturer, new_model_name
    ):
        raise BatteryModelConflictError(
            f"Battery model '{new_manufacturer} {new_model_name}' already exists"
        )
    try:
        updated_record = await battery_repository.update_battery_model_fields(
            db_session,
            battery_model_id,
            update_values,
            change_reason=BATTERY_MODEL_EDITED_REASON,
            changed_by=principal.user_id,
        )
    except IntegrityError as error:
        raise BatteryModelConflictError(
            "Battery model maker and name already exist"
        ) from error
    if updated_record is None:
        raise BatteryModelNotFoundError(f"Battery model '{battery_model_id}' not found")
    return to_battery_model_response(updated_record)


async def soft_delete_battery_model(
    db_session: AsyncSession, battery_model_id: UUID, *, principal: Principal
) -> None:
    """Remove a model from the catalog (soft delete).

    Batteries that already point to the model keep it; only new batteries can
    no longer choose it.

    Args:
        db_session: Current database session.
        battery_model_id: Internal ID of the model.
        principal: The caller (internal staff), recorded as the history actor.

    Raises:
        BatteryModelNotFoundError: The model does not exist or was removed.
    """
    removed_record = await battery_repository.update_battery_model_fields(
        db_session,
        battery_model_id,
        {"deleted_at": utc_now()},
        change_reason=BATTERY_MODEL_DELETED_REASON,
        changed_by=principal.user_id,
    )
    if removed_record is None:
        raise BatteryModelNotFoundError(f"Battery model '{battery_model_id}' not found")


# --- batteries --------------------------------------------------------------


async def create_battery(
    db_session: AsyncSession,
    battery_create_request: BatteryCreateRequest,
    *,
    principal: Principal,
) -> BatteryResponse:
    """Register a battery as an asset (BAT-01).

    Args:
        db_session: Database session owned by the entry boundary.
        battery_create_request: Validated request data.
        principal: The caller; the pack is owned by the caller's organization
            unless internal staff name another one.

    Returns:
        Response for the new battery (in stock, not fitted).

    Raises:
        BatteryModelNotFoundError: The model is not in the catalog.
        BatteryConflictError: The serial number is used by a live battery.
        OrganizationNotFoundError: The named owner does not exist.

    Side Effects:
        Inserts and flushes the battery; does not commit. ``acquired_at``
        defaults to now.
    """
    if (
        await battery_repository.get_battery_model_by_id(
            db_session, battery_create_request.battery_model_id
        )
        is None
    ):
        raise BatteryModelNotFoundError(
            f"Battery model '{battery_create_request.battery_model_id}' not found"
        )
    if await battery_repository.find_by_serial_number(
        db_session, battery_create_request.serial_number
    ):
        raise BatteryConflictError(
            f"Battery with serial number '{battery_create_request.serial_number}' "
            "already exists"
        )
    insert_values: dict[str, object] = battery_create_request.model_dump()
    insert_values["status"] = battery_create_request.status.value
    insert_values[
        "organization_id"
    ] = await identity_service.resolve_organization_for_new_record(
        db_session, principal, battery_create_request.organization_id
    )
    if insert_values["acquired_at"] is None:
        insert_values["acquired_at"] = utc_now()
    try:
        battery_record = await battery_repository.insert(db_session, insert_values)
    except IntegrityError as error:
        raise BatteryConflictError("Battery serial number already exists") from error
    return to_battery_response(battery_record)


async def _get_battery_record(
    db_session: AsyncSession, battery_id: UUID, principal: Principal
) -> BatteryModel:
    """Load a live battery inside the caller's data reach.

    Args:
        db_session: Current database session.
        battery_id: Internal ID of the battery.
        principal: The caller.

    Returns:
        The battery record.

    Raises:
        BatteryNotFoundError: Missing, removed or owned by another organization.
    """
    battery_record = await battery_repository.get_by_id(
        db_session, battery_id, organization_id=principal.data_scope
    )
    if battery_record is None:
        raise BatteryNotFoundError(f"Battery with id '{battery_id}' not found")
    return battery_record


async def get_battery(
    db_session: AsyncSession, battery_id: UUID, *, principal: Principal
) -> BatteryResponse:
    """Get a battery by ID inside the caller's data reach.

    Args:
        db_session: Current database session.
        battery_id: Internal ID of the battery.
        principal: The caller.

    Returns:
        Response for the battery.

    Raises:
        BatteryNotFoundError: Missing, removed or out of reach.
    """
    return to_battery_response(
        await _get_battery_record(db_session, battery_id, principal)
    )


async def list_batteries(
    db_session: AsyncSession,
    *,
    principal: Principal,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
    search: str | None = None,
    status_filter: BatteryStatus | None = None,
    battery_model_id: UUID | None = None,
    vehicle_id: UUID | None = None,
    is_installed: bool | None = None,
    owner_organization_id: UUID | None = None,
) -> BatteryListResponse:
    """Get a paginated list of batteries inside the caller's data reach.

    Args:
        db_session: Current database session.
        principal: The caller; only batteries of the caller's organization are
            listed unless the caller is internal.
        page: Page number, starting from 1.
        page_size: Maximum number of batteries per page.
        search: Serial-number fragment, if any.
        status_filter: Status filter, if any.
        battery_model_id: Only batteries of this model, if given.
        vehicle_id: Only the battery fitted to this truck, if given.
        is_installed: Fitted (True) or in stock (False), if given.
        owner_organization_id: Only batteries owned by this organization.

    Returns:
        Paginated list response.

    Side Effects:
        Two read-only queries (the page, then the total count).
    """
    page_window = normalize_page_window(page, page_size)
    status_value = status_filter.value if status_filter is not None else None
    battery_records = await battery_repository.list_all(
        db_session,
        offset=page_window.offset,
        limit=page_window.page_size,
        organization_id=principal.data_scope,
        search=search,
        status=status_value,
        battery_model_id=battery_model_id,
        vehicle_id=vehicle_id,
        is_installed=is_installed,
        owner_organization_id=owner_organization_id,
    )
    total = await battery_repository.count(
        db_session,
        organization_id=principal.data_scope,
        search=search,
        status=status_value,
        battery_model_id=battery_model_id,
        vehicle_id=vehicle_id,
        is_installed=is_installed,
        owner_organization_id=owner_organization_id,
    )
    return BatteryListResponse(
        items=[
            to_battery_response(battery_record) for battery_record in battery_records
        ],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def update_battery(
    db_session: AsyncSession,
    battery_id: UUID,
    battery_update_request: BatteryUpdateRequest,
    *,
    principal: Principal,
) -> BatteryResponse:
    """Partially update a battery's details or status.

    Args:
        db_session: Current database session.
        battery_id: Internal ID of the battery.
        battery_update_request: Fields to change; null means unchanged. Setting
            the status ACTIVE without a reason clears the stored reason.
        principal: The caller.

    Returns:
        Response for the updated battery.

    Raises:
        BatteryNotFoundError: Missing, removed or out of reach.
        BatteryModelNotFoundError: The new model is not in the catalog.
        BatteryConflictError: The new serial number is used by another battery.

    Side Effects:
        The change is recorded in the battery's history with the caller as
        actor and the typed ``status_reason`` or else a fixed text.
    """
    battery_record = await _get_battery_record(db_session, battery_id, principal)
    if (
        battery_update_request.battery_model_id is not None
        and battery_update_request.battery_model_id != battery_record.battery_model_id
        and await battery_repository.get_battery_model_by_id(
            db_session, battery_update_request.battery_model_id
        )
        is None
    ):
        raise BatteryModelNotFoundError(
            f"Battery model '{battery_update_request.battery_model_id}' not found"
        )
    if (
        battery_update_request.serial_number is not None
        and battery_update_request.serial_number != battery_record.serial_number
        and await battery_repository.find_by_serial_number(
            db_session, battery_update_request.serial_number
        )
    ):
        raise BatteryConflictError(
            f"Battery with serial number '{battery_update_request.serial_number}' "
            "already exists"
        )

    update_values: dict[str, object] = {
        field_name: value
        for field_name, value in battery_update_request.model_dump(
            exclude_unset=True
        ).items()
        if value is not None
    }
    if battery_update_request.status is not None:
        update_values["status"] = battery_update_request.status.value
        if (
            battery_update_request.status is BatteryStatus.ACTIVE
            and battery_update_request.status_reason is None
        ):
            update_values["status_reason"] = None
    if not update_values:
        return to_battery_response(battery_record)
    typed_reason = battery_update_request.status_reason
    try:
        updated_record = await battery_repository.update_fields(
            db_session,
            battery_id,
            update_values,
            change_reason=typed_reason or BATTERY_EDITED_REASON,
            changed_by=principal.user_id,
            organization_id=principal.data_scope,
        )
    except IntegrityError as error:
        raise BatteryConflictError("Battery serial number already exists") from error
    if updated_record is None:
        raise BatteryNotFoundError(f"Battery with id '{battery_id}' not found")
    return to_battery_response(updated_record)


async def soft_delete_battery(
    db_session: AsyncSession,
    battery_id: UUID,
    *,
    principal: Principal,
    reason: str | None = None,
) -> None:
    """Soft-delete a battery: it leaves the system and becomes INACTIVE.

    Rule:
        A deleted battery is also INACTIVE with a reason (DM-25,
        ``ck_batteries_deleted_inactive``) and, if it was fitted, is taken out
        of its truck in the same UPDATE, so the truck is free for another
        pack and the installation period ends.

    Args:
        db_session: Current database session.
        battery_id: Internal ID of the battery.
        principal: The caller.
        reason: Why the battery leaves the system; a fixed text when omitted.

    Raises:
        BatteryNotFoundError: Missing, removed or out of reach.
    """
    delete_reason = reason or BATTERY_DELETED_REASON
    battery_record = await battery_repository.update_fields(
        db_session,
        battery_id,
        {
            "status": BatteryStatus.INACTIVE.value,
            "status_reason": delete_reason,
            "deleted_at": utc_now(),
            "vehicle_id": None,
            "installed_at": None,
        },
        change_reason=delete_reason,
        changed_by=principal.user_id,
        organization_id=principal.data_scope,
    )
    if battery_record is None:
        raise BatteryNotFoundError(f"Battery with id '{battery_id}' not found")


async def install_battery(
    db_session: AsyncSession,
    battery_id: UUID,
    battery_install_request: BatteryInstallRequest,
    *,
    principal: Principal,
) -> BatteryResponse:
    """Fit a battery to a truck (VH-16: one pack per truck).

    Rules:
        The battery must be ACTIVE and not fitted anywhere; the truck must
        exist and hold no other pack; ``installed_at`` defaults to now and may
        not be in the future. The battery's owner and the truck's owner may
        differ.

    Args:
        db_session: Current database session.
        battery_id: Internal ID of the battery.
        battery_install_request: The truck, the fitting time, an optional reason.
        principal: The caller.

    Returns:
        Response for the fitted battery.

    Raises:
        BatteryNotFoundError: The battery is missing or out of reach.
        BatteryVehicleNotFoundError: The truck does not exist.
        BatteryConflictError: The battery is INACTIVE or already fitted, or the
            truck already holds a pack.
        BatteryDateInvalidError: ``installed_at`` is in the future.

    Side Effects:
        Flushes one UPDATE; the history keeps the earlier state, which closes
        the previous installation period in the view.
    """
    battery_record = await _get_battery_record(db_session, battery_id, principal)
    if battery_record.vehicle_id is not None:
        raise BatteryConflictError(
            f"Battery '{battery_id}' is already fitted to vehicle "
            f"'{battery_record.vehicle_id}'"
        )
    if battery_record.status != BatteryStatus.ACTIVE.value:
        raise BatteryConflictError(f"Battery '{battery_id}' is not ACTIVE")
    vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_id(
        db_session, battery_install_request.vehicle_id
    )
    if vehicle_reference is None:
        raise BatteryVehicleNotFoundError(
            f"Vehicle '{battery_install_request.vehicle_id}' not found"
        )
    if await battery_repository.find_by_vehicle_id(
        db_session, battery_install_request.vehicle_id
    ):
        raise BatteryConflictError(
            f"Vehicle '{battery_install_request.vehicle_id}' already holds a battery"
        )
    now = utc_now()
    installed_at = battery_install_request.installed_at or now
    if installed_at > now + DATE_CLOCK_SKEW:
        raise BatteryDateInvalidError("The installation time cannot be in the future")
    try:
        updated_record = await battery_repository.update_fields(
            db_session,
            battery_id,
            {
                "vehicle_id": battery_install_request.vehicle_id,
                "installed_at": installed_at,
            },
            change_reason=battery_install_request.reason or BATTERY_INSTALLED_REASON,
            changed_by=principal.user_id,
            organization_id=principal.data_scope,
        )
    except IntegrityError as error:
        raise BatteryConflictError(
            f"Vehicle '{battery_install_request.vehicle_id}' already holds a battery"
        ) from error
    if updated_record is None:
        raise BatteryNotFoundError(f"Battery with id '{battery_id}' not found")
    return to_battery_response(updated_record)


async def remove_battery_from_vehicle(
    db_session: AsyncSession,
    battery_id: UUID,
    *,
    principal: Principal,
    reason: str | None = None,
) -> BatteryResponse:
    """Take a battery out of its truck; it goes back to stock.

    Args:
        db_session: Current database session.
        battery_id: Internal ID of the battery.
        principal: The caller.
        reason: Why it is removed (kept in the history); a fixed text if omitted.

    Returns:
        Response for the battery, now in stock.

    Raises:
        BatteryNotFoundError: The battery is missing or out of reach.
        BatteryConflictError: The battery is not fitted to a truck.

    Side Effects:
        Flushes one UPDATE that clears ``vehicle_id`` and ``installed_at``;
        the installation period ends at the time of the change.
    """
    battery_record = await _get_battery_record(db_session, battery_id, principal)
    if battery_record.vehicle_id is None:
        raise BatteryConflictError(f"Battery '{battery_id}' is not fitted to a vehicle")
    updated_record = await battery_repository.update_fields(
        db_session,
        battery_id,
        {"vehicle_id": None, "installed_at": None},
        change_reason=reason or BATTERY_REMOVED_REASON,
        changed_by=principal.user_id,
        organization_id=principal.data_scope,
    )
    if updated_record is None:
        raise BatteryNotFoundError(f"Battery with id '{battery_id}' not found")
    return to_battery_response(updated_record)


async def transfer_battery_ownership(
    db_session: AsyncSession,
    battery_id: UUID,
    battery_ownership_transfer_request: BatteryOwnershipTransferRequest,
    *,
    principal: Principal,
) -> BatteryResponse:
    """Hand a battery to another organization (the pack alone, VH-16).

    Rules:
        The new owner must exist and differ from the current owner; the
        effective date defaults to now, may not be in the future and must be
        after the date the current owner took the pack. A fitted pack stays in
        its truck.

    Args:
        db_session: Current database session.
        battery_id: Internal ID of the battery.
        battery_ownership_transfer_request: New owner, date and reason.
        principal: The caller (internal staff).

    Returns:
        Response for the battery with its new owner.

    Raises:
        BatteryNotFoundError: The battery is missing or out of reach.
        OrganizationNotFoundError: The new owner does not exist.
        BatteryConflictError: The organization already owns the battery.
        BatteryDateInvalidError: The date breaks a rule.

    Side Effects:
        Flushes one UPDATE; the reason is the history's change reason.
    """
    battery_record = await _get_battery_record(db_session, battery_id, principal)
    new_organization_id = await identity_service.resolve_organization_for_new_record(
        db_session, principal, battery_ownership_transfer_request.organization_id
    )
    if new_organization_id == battery_record.organization_id:
        raise BatteryConflictError("The organization already owns this battery")
    now = utc_now()
    effective_at = battery_ownership_transfer_request.acquired_at or now
    if effective_at > now + DATE_CLOCK_SKEW:
        raise BatteryDateInvalidError("The transfer date cannot be in the future")
    if effective_at <= battery_record.acquired_at:
        raise BatteryDateInvalidError(
            "The transfer date must be after the date the current owner took "
            "the battery"
        )
    updated_record = await battery_repository.update_fields(
        db_session,
        battery_id,
        {"organization_id": new_organization_id, "acquired_at": effective_at},
        change_reason=battery_ownership_transfer_request.reason,
        changed_by=principal.user_id,
        organization_id=principal.data_scope,
    )
    if updated_record is None:
        raise BatteryNotFoundError(f"Battery with id '{battery_id}' not found")
    return to_battery_response(updated_record)


async def list_battery_installation_periods(
    db_session: AsyncSession, battery_id: UUID, *, principal: Principal
) -> BatteryInstallationPeriodListResponse:
    """List the trucks a battery has been fitted to (VH-16).

    Read through the view ``battery_installation_periods``.

    Args:
        db_session: Current database session.
        battery_id: Internal ID of the battery.
        principal: The caller.

    Returns:
        The periods, oldest first; the current one has ``installed_until`` null.

    Raises:
        BatteryNotFoundError: Missing, removed or out of reach.
    """
    await _get_battery_record(db_session, battery_id, principal)
    period_rows = await battery_repository.list_installation_periods(
        db_session, battery_id
    )
    return BatteryInstallationPeriodListResponse(
        items=[
            BatteryInstallationPeriodResponse(
                vehicle_id=period_vehicle_id,
                installed_from=installed_from,
                installed_until=installed_until,
            )
            for period_vehicle_id, installed_from, installed_until in period_rows
        ]
    )


async def resolve_battery_owner_organization_id(
    db_session: AsyncSession, battery_id: UUID
) -> UUID | None:
    """Tell which organization owns a battery now (public, for warranties).

    A warranty has no organization of its own (DM-24): it follows its object,
    so the warranties domain asks the object's domain who owns it.

    Args:
        db_session: Database session owned by the entry boundary.
        battery_id: Internal ID of the battery.

    Returns:
        The owning organization, or `None` when the battery does not exist or
        left the system.

    Side Effects:
        One read-only query.
    """
    battery_record = await battery_repository.get_by_id(db_session, battery_id)
    return battery_record.organization_id if battery_record else None
