"""Business service and public contract of the warranties domain (WAR-01).

A warranty covers exactly one truck, battery, T-Box or charger and follows its
object (VH-18, VH-19). This module holds the rules: the covered object must
exist, the ``limits`` keys must be those the object allows, a period ends on
or after it starts, a live warranty of the same type does not overlap another
for the same object, a VOIDED warranty is final, and "expired" is computed,
never stored (DM-25). Nothing here is called by other domains. None of the
functions commit or roll back - the caller's entry boundary owns the
transaction.

Access: the router-facing functions take the caller's `Principal` and pass
``principal.data_scope`` to the repository, which keeps a warranty visible
only when the caller's organization owns the covered object (DM-24); the
object's owner is asked of the object's own domain.
"""

from datetime import date, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.batteries.service as battery_service
import app.domains.charging_stations.service as charging_station_service
import app.domains.telematics.service as telematics_service
import app.domains.vehicles.service as vehicle_service
import app.domains.warranties.repository as warranty_repository
from app.domains.identity.types import Principal
from app.domains.warranties.exceptions import (
    WarrantyConflictError,
    WarrantyLimitsInvalidError,
    WarrantyNotFoundError,
    WarrantyObjectNotFoundError,
    WarrantyPeriodInvalidError,
)
from app.domains.warranties.models import WarrantyModel
from app.domains.warranties.schemas import (
    WarrantyCreateRequest,
    WarrantyListResponse,
    WarrantyResponse,
    WarrantyUpdateRequest,
)
from app.domains.warranties.types import (
    WARRANTY_LIMIT_KEYS,
    WarrantyObjectKind,
    WarrantyStatus,
    WarrantyType,
)
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.pagination import normalize_page_window

# Fixed history reasons of routine actions; the acting user is the caller.
WARRANTY_EDITED_REASON = "Warranty details edited"
WARRANTY_DELETED_REASON = "Warranty entered by mistake"

# Column of ``warranties`` that holds the link of each kind of object.
_LINK_COLUMN: dict[WarrantyObjectKind, str] = {
    WarrantyObjectKind.VEHICLE: "vehicle_id",
    WarrantyObjectKind.BATTERY: "battery_id",
    WarrantyObjectKind.TELEMATIC: "telematic_id",
    WarrantyObjectKind.STATION: "station_id",
}


def _today() -> date:
    """Return today's calendar day in the report time zone.

    Returns:
        The current date in ``settings.APP_REPORT_TIMEZONE`` ("expired" is a
        calendar-day question, like every other day-based figure).
    """
    return utc_now().astimezone(ZoneInfo(settings.APP_REPORT_TIMEZONE)).date()


def kind_of_warranty(warranty_record: WarrantyModel) -> WarrantyObjectKind:
    """Tell what a warranty covers: the one link that is set.

    Args:
        warranty_record: A warranty record.

    Returns:
        The kind of the covered object.
    """
    for object_kind, link_column in _LINK_COLUMN.items():
        if getattr(warranty_record, link_column) is not None:
            return object_kind
    raise ValueError("A warranty must have exactly one link set")


def to_warranty_response(warranty_record: WarrantyModel) -> WarrantyResponse:
    """Convert a warranty ORM record into an HTTP API response.

    Pure mapping plus the computed expiry (a calendar-day comparison, no I/O).

    Args:
        warranty_record: Record queried or created by the repository.

    Returns:
        The response; ``is_expired`` is true when ``ends_on`` has passed.
    """
    warranty_response = WarrantyResponse.model_validate(warranty_record)
    warranty_response.is_expired = warranty_record.ends_on < _today()
    return warranty_response


def validate_limits(
    object_kind: WarrantyObjectKind, limits: dict[str, float] | None
) -> dict[str, float] | None:
    """Check the ``limits`` keys against the covered object (VH-18, VH-19).

    Args:
        object_kind: What the warranty covers.
        limits: The requested limits, or `None`.

    Returns:
        The limits to store, or `None` when there are none (an empty object
        counts as none).

    Raises:
        WarrantyLimitsInvalidError: A key is not allowed for this kind of
            object, or a reading is negative.
    """
    if not limits:
        return None
    allowed_keys = WARRANTY_LIMIT_KEYS[object_kind]
    unknown_keys = sorted(set(limits) - allowed_keys)
    if unknown_keys:
        raise WarrantyLimitsInvalidError(
            f"Limit keys {unknown_keys} are not allowed for a "
            f"{object_kind.value.lower()} warranty; allowed: {sorted(allowed_keys)}"
        )
    if any(reading < 0 for reading in limits.values()):
        raise WarrantyLimitsInvalidError("A limit reading cannot be negative")
    return dict(limits)


async def _resolve_object_owner_organization_id(
    db_session: AsyncSession, object_kind: WarrantyObjectKind, object_id: UUID
) -> UUID | None:
    """Ask the object's own domain who owns the covered object.

    Args:
        db_session: Current database session.
        object_kind: What the warranty covers.
        object_id: Internal ID of the object.

    Returns:
        The owning organization, or `None` when the object does not exist.
    """
    if object_kind is WarrantyObjectKind.VEHICLE:
        vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_id(
            db_session, object_id
        )
        return vehicle_reference.organization_id if vehicle_reference else None
    if object_kind is WarrantyObjectKind.BATTERY:
        return await battery_service.resolve_battery_owner_organization_id(
            db_session, object_id
        )
    if object_kind is WarrantyObjectKind.TELEMATIC:
        return await telematics_service.resolve_telematic_owner_organization_id(
            db_session, object_id
        )
    return await charging_station_service.resolve_station_owner_organization_id(
        db_session, object_id
    )


async def _get_warranty_record(
    db_session: AsyncSession, warranty_id: UUID, principal: Principal
) -> WarrantyModel:
    """Load a live warranty inside the caller's data reach.

    Args:
        db_session: Current database session.
        warranty_id: Internal ID of the warranty.
        principal: The caller.

    Returns:
        The warranty record.

    Raises:
        WarrantyNotFoundError: Missing, entered by mistake, or its object
            belongs to an organization the caller cannot reach.
    """
    warranty_record = await warranty_repository.get_by_id(
        db_session, warranty_id, organization_id=principal.data_scope
    )
    if warranty_record is None:
        raise WarrantyNotFoundError(f"Warranty with id '{warranty_id}' not found")
    return warranty_record


async def _ensure_no_overlap(
    db_session: AsyncSession,
    object_kind: WarrantyObjectKind,
    object_id: UUID,
    warranty_type: str,
    starts_on: date,
    ends_on: date,
    *,
    excluding_warranty_id: UUID | None = None,
) -> None:
    """Refuse a period that overlaps a live warranty of the same object and type.

    Args:
        db_session: Current database session.
        object_kind: What the warranty covers.
        object_id: Internal ID of the object.
        warranty_type: ``STANDARD`` / ``EXTENDED``.
        starts_on: First day of coverage.
        ends_on: Last day of coverage.
        excluding_warranty_id: The warranty being edited, to ignore.

    Raises:
        WarrantyConflictError: Another ACTIVE warranty of the same type
            covers the object in an overlapping period.
    """
    overlapping = await warranty_repository.find_overlapping(
        db_session,
        {_LINK_COLUMN[object_kind]: object_id},
        warranty_type,
        starts_on,
        ends_on,
        excluding_warranty_id=excluding_warranty_id,
    )
    if overlapping is not None:
        raise WarrantyConflictError(
            f"Warranty '{overlapping.warranty_id}' of the same type already "
            "covers this object in an overlapping period"
        )


async def create_warranty(
    db_session: AsyncSession,
    warranty_create_request: WarrantyCreateRequest,
    *,
    principal: Principal,
) -> WarrantyResponse:
    """Enter a warranty of one truck, battery, T-Box or charger.

    Args:
        db_session: Database session owned by the entry boundary.
        warranty_create_request: Validated request (exactly one object).
        principal: The caller.

    Returns:
        Response for the new, ACTIVE warranty.

    Raises:
        WarrantyObjectNotFoundError: The object does not exist or is out of
            the caller's reach.
        WarrantyPeriodInvalidError: ``ends_on`` is before ``starts_on``.
        WarrantyLimitsInvalidError: A limits key is not allowed for the object.
        WarrantyConflictError: An overlapping warranty of the same type exists.

    Side Effects:
        Inserts and flushes the row; does not commit.
    """
    link_values = {
        object_kind: getattr(warranty_create_request, link_column)
        for object_kind, link_column in _LINK_COLUMN.items()
    }
    object_kind, object_id = next(
        (kind, value) for kind, value in link_values.items() if value is not None
    )
    owner_organization_id = await _resolve_object_owner_organization_id(
        db_session, object_kind, object_id
    )
    if owner_organization_id is None or not principal.can_access_organization(
        owner_organization_id
    ):
        raise WarrantyObjectNotFoundError(
            f"The {object_kind.value.lower()} '{object_id}' was not found"
        )
    if warranty_create_request.ends_on < warranty_create_request.starts_on:
        raise WarrantyPeriodInvalidError("The coverage cannot end before it starts")
    limits = validate_limits(object_kind, warranty_create_request.limits)
    await _ensure_no_overlap(
        db_session,
        object_kind,
        object_id,
        warranty_create_request.warranty_type.value,
        warranty_create_request.starts_on,
        warranty_create_request.ends_on,
    )
    insert_values: dict[str, object] = warranty_create_request.model_dump()
    insert_values["warranty_type"] = warranty_create_request.warranty_type.value
    insert_values["limits"] = limits
    insert_values["status"] = WarrantyStatus.ACTIVE.value
    try:
        warranty_record = await warranty_repository.insert(db_session, insert_values)
    except IntegrityError as error:
        raise WarrantyObjectNotFoundError(
            f"The {object_kind.value.lower()} '{object_id}' was not found"
        ) from error
    return to_warranty_response(warranty_record)


async def get_warranty(
    db_session: AsyncSession, warranty_id: UUID, *, principal: Principal
) -> WarrantyResponse:
    """Get a warranty by ID inside the caller's data reach.

    Args:
        db_session: Current database session.
        warranty_id: Internal ID of the warranty.
        principal: The caller.

    Returns:
        Response for the warranty.

    Raises:
        WarrantyNotFoundError: Missing, entered by mistake or out of reach.
    """
    return to_warranty_response(
        await _get_warranty_record(db_session, warranty_id, principal)
    )


async def list_warranties(
    db_session: AsyncSession,
    *,
    principal: Principal,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
    vehicle_id: UUID | None = None,
    battery_id: UUID | None = None,
    telematic_id: UUID | None = None,
    station_id: UUID | None = None,
    status_filter: WarrantyStatus | None = None,
    warranty_type: WarrantyType | None = None,
    expiring_within_days: int | None = None,
    owner_organization_id: UUID | None = None,
) -> WarrantyListResponse:
    """Get a paginated list of warranties, the ones ending soonest first.

    Args:
        db_session: Current database session.
        principal: The caller; only warranties of objects the caller's
            organization owns are listed unless the caller is internal.
        page: Page number, starting from 1.
        page_size: Maximum number of warranties per page.
        vehicle_id: Only warranties of this truck, if given.
        battery_id: Only warranties of this battery, if given.
        telematic_id: Only warranties of this T-Box, if given.
        station_id: Only warranties of this charger, if given.
        status_filter: Status filter, if any.
        warranty_type: Type filter, if any.
        expiring_within_days: Only ACTIVE warranties whose coverage ends from
            today up to this many days ahead (an expiring-soon list).
        owner_organization_id: Only warranties of objects this organization
            owns, if given.

    Returns:
        Paginated list response.

    Side Effects:
        Two read-only queries (the page, then the total count).
    """
    page_window = normalize_page_window(page, page_size)
    status_value = status_filter.value if status_filter is not None else None
    ending_between: tuple[date, date] | None = None
    if expiring_within_days is not None:
        today = _today()
        ending_between = (today, today + timedelta(days=expiring_within_days))
        status_value = WarrantyStatus.ACTIVE.value
    warranty_type_value = warranty_type.value if warranty_type is not None else None
    warranty_records = await warranty_repository.list_all(
        db_session,
        offset=page_window.offset,
        limit=page_window.page_size,
        organization_id=principal.data_scope,
        vehicle_id=vehicle_id,
        battery_id=battery_id,
        telematic_id=telematic_id,
        station_id=station_id,
        status=status_value,
        warranty_type=warranty_type_value,
        ending_between=ending_between,
        owner_organization_id=owner_organization_id,
    )
    total = await warranty_repository.count(
        db_session,
        organization_id=principal.data_scope,
        vehicle_id=vehicle_id,
        battery_id=battery_id,
        telematic_id=telematic_id,
        station_id=station_id,
        status=status_value,
        warranty_type=warranty_type_value,
        ending_between=ending_between,
        owner_organization_id=owner_organization_id,
    )
    return WarrantyListResponse(
        items=[
            to_warranty_response(warranty_record)
            for warranty_record in warranty_records
        ],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def update_warranty(
    db_session: AsyncSession,
    warranty_id: UUID,
    warranty_update_request: WarrantyUpdateRequest,
    *,
    principal: Principal,
) -> WarrantyResponse:
    """Partially update the terms of an ACTIVE warranty.

    Args:
        db_session: Current database session.
        warranty_id: Internal ID of the warranty.
        warranty_update_request: Fields to change; null means unchanged.
        principal: The caller.

    Returns:
        Response for the updated warranty.

    Raises:
        WarrantyNotFoundError: Missing, entered by mistake or out of reach.
        WarrantyConflictError: The warranty is VOIDED, or the new period
            overlaps another warranty of the same type.
        WarrantyPeriodInvalidError: The new period ends before it starts.
        WarrantyLimitsInvalidError: A limits key is not allowed for the object.

    Side Effects:
        The change is recorded in the warranty's history with the caller as
        actor.
    """
    warranty_record = await _get_warranty_record(db_session, warranty_id, principal)
    if warranty_record.status == WarrantyStatus.VOIDED.value:
        raise WarrantyConflictError(f"Warranty '{warranty_id}' is voided")

    update_values: dict[str, object] = {
        field_name: value
        for field_name, value in warranty_update_request.model_dump(
            exclude_unset=True
        ).items()
        if value is not None
    }
    if not update_values:
        return to_warranty_response(warranty_record)
    object_kind = kind_of_warranty(warranty_record)
    if warranty_update_request.warranty_type is not None:
        update_values["warranty_type"] = warranty_update_request.warranty_type.value
    if warranty_update_request.limits is not None:
        update_values["limits"] = validate_limits(
            object_kind, warranty_update_request.limits
        )
    starts_on = warranty_update_request.starts_on or warranty_record.starts_on
    ends_on = warranty_update_request.ends_on or warranty_record.ends_on
    if ends_on < starts_on:
        raise WarrantyPeriodInvalidError("The coverage cannot end before it starts")
    warranty_type = str(
        update_values.get("warranty_type", warranty_record.warranty_type)
    )
    if (
        starts_on != warranty_record.starts_on
        or ends_on != warranty_record.ends_on
        or warranty_type != warranty_record.warranty_type
    ):
        await _ensure_no_overlap(
            db_session,
            object_kind,
            getattr(warranty_record, _LINK_COLUMN[object_kind]),
            warranty_type,
            starts_on,
            ends_on,
            excluding_warranty_id=warranty_id,
        )
    updated_record = await warranty_repository.update_fields(
        db_session,
        warranty_id,
        update_values,
        change_reason=WARRANTY_EDITED_REASON,
        changed_by=principal.user_id,
        organization_id=principal.data_scope,
    )
    if updated_record is None:
        raise WarrantyNotFoundError(f"Warranty with id '{warranty_id}' not found")
    return to_warranty_response(updated_record)


async def void_warranty(
    db_session: AsyncSession,
    warranty_id: UUID,
    *,
    principal: Principal,
    reason: str,
) -> WarrantyResponse:
    """Void a warranty, with the reason (DM-25: VOIDED by our warranty team).

    A voided warranty stays in the system for the record and cannot be
    edited, voided again or brought back.

    Args:
        db_session: Current database session.
        warranty_id: Internal ID of the warranty.
        principal: The caller.
        reason: Why the warranty is voided; it is the status reason and the
            history's change reason.

    Returns:
        Response for the voided warranty.

    Raises:
        WarrantyNotFoundError: Missing, entered by mistake or out of reach.
        WarrantyConflictError: The warranty is already VOIDED.
    """
    warranty_record = await _get_warranty_record(db_session, warranty_id, principal)
    if warranty_record.status == WarrantyStatus.VOIDED.value:
        raise WarrantyConflictError(f"Warranty '{warranty_id}' is already voided")
    updated_record = await warranty_repository.update_fields(
        db_session,
        warranty_id,
        {"status": WarrantyStatus.VOIDED.value, "status_reason": reason},
        change_reason=reason,
        changed_by=principal.user_id,
        organization_id=principal.data_scope,
    )
    if updated_record is None:
        raise WarrantyNotFoundError(f"Warranty with id '{warranty_id}' not found")
    return to_warranty_response(updated_record)


async def soft_delete_warranty(
    db_session: AsyncSession,
    warranty_id: UUID,
    *,
    principal: Principal,
    reason: str | None = None,
) -> None:
    """Soft-delete a warranty that was entered by mistake (DM-25).

    Rule:
        A deleted warranty is also VOIDED with a reason
        (``ck_warranties_deleted_voided``). An ordinary end of coverage is
        not a delete: it is expiry (computed) or a void.

    Args:
        db_session: Current database session.
        warranty_id: Internal ID of the warranty.
        principal: The caller.
        reason: Why it is removed; a fixed text when omitted.

    Raises:
        WarrantyNotFoundError: Missing, already removed or out of reach.
    """
    delete_reason = reason or WARRANTY_DELETED_REASON
    warranty_record = await warranty_repository.update_fields(
        db_session,
        warranty_id,
        {
            "status": WarrantyStatus.VOIDED.value,
            "status_reason": delete_reason,
            "deleted_at": utc_now(),
        },
        change_reason=delete_reason,
        changed_by=principal.user_id,
        organization_id=principal.data_scope,
    )
    if warranty_record is None:
        raise WarrantyNotFoundError(f"Warranty with id '{warranty_id}' not found")
