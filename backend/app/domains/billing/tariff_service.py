"""Tariffs and their immutable versions (PAY-09, BL-08, BL-09).

A tariff belongs to the organization that owns the locations it prices (our
internal organization for the public network, a customer for its own private
chargers) and prices one location or, with no location, is the owner's
default. The prices are immutable versions: publishing is the only way to
change a price, and the version in force is the newest whose ``effective_from``
has passed. The price a driver is quoted at the scan is frozen on the session's
bill (``bill_service``), so a later version never changes an old receipt.

Access: a caller reads and writes the tariffs of their own organization;
internal staff reach every organization (DM-24). Who may write at all is the
router's role gate. ``resolve_tariff_for_station`` is the cross-domain read the
QR scan uses; it runs for a system caller and applies no scope.

Limitations: a tariff cannot be deleted (sessions point to its versions
forever); per-customer and per-charger prices are later (BL-08).
"""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.billing.pricing as pricing
import app.domains.billing.repository as billing_repository
import app.domains.charging_stations.service as charging_stations_service
import app.domains.identity.service as identity_service
from app.domains.billing.exceptions import (
    NoTariffInForceError,
    TariffConflictError,
    TariffInputError,
    TariffNotFoundError,
    TariffStationNotFoundError,
)
from app.domains.billing.models import TariffModel, TariffVersionModel
from app.domains.billing.schemas import (
    TariffCreateRequest,
    TariffListResponse,
    TariffQuoteResponse,
    TariffResponse,
    TariffUpdateRequest,
    TariffVersionPublishRequest,
    TariffVersionResponse,
)
from app.domains.billing.types import TariffQuote, TariffStatus
from app.domains.identity.types import Principal
from app.libs.common.clock import utc_now
from app.libs.common.pagination import normalize_page_window
from app.libs.db.history import set_change_context

# A client clock a little behind ours may send "now" as a moment already past.
_EFFECTIVE_FROM_SKEW = timedelta(seconds=60)
_CURRENCY_VND = "VND"


def to_tariff_version_response(
    version_record: TariffVersionModel,
) -> TariffVersionResponse:
    """Convert a version row into its response (pure mapping).

    Args:
        version_record: The version.

    Returns:
        The response; prices are whole dong.
    """
    return TariffVersionResponse(
        tariff_version_id=version_record.tariff_version_id,
        tariff_id=version_record.tariff_id,
        version_no=version_record.version_no,
        effective_from=version_record.effective_from,
        price_per_kwh=int(version_record.price_per_kwh),
        vat_rate_percent=version_record.vat_rate_percent,
        time_periods=version_record.time_periods,
        change_reason=version_record.change_reason,
        created_by=version_record.created_by,
        created_at=version_record.created_at,
    )


async def build_tariff_response(
    db: AsyncSession, tariff_record: TariffModel
) -> TariffResponse:
    """Build a tariff response with the version in force now.

    Args:
        db: The async session owned by the entry boundary.
        tariff_record: The tariff.

    Returns:
        The response.

    Side Effects:
        One read query for the current version.
    """
    version_record = await billing_repository.find_tariff_version_in_force(
        db, tariff_record.tariff_id, utc_now()
    )
    return TariffResponse(
        tariff_id=tariff_record.tariff_id,
        organization_id=tariff_record.organization_id,
        location_id=tariff_record.location_id,
        name=tariff_record.name,
        currency=tariff_record.currency,
        status=TariffStatus(tariff_record.status),
        status_reason=tariff_record.status_reason,
        current_version=(
            None
            if version_record is None
            else to_tariff_version_response(version_record)
        ),
        created_at=tariff_record.created_at,
        updated_at=tariff_record.updated_at,
    )


async def _get_tariff_record(
    db: AsyncSession,
    tariff_id: UUID,
    principal: Principal,
    *,
    for_update: bool = False,
) -> TariffModel:
    """Find a tariff inside the caller's reach.

    Args:
        db: The async session owned by the entry boundary.
        tariff_id: UUID of the tariff.
        principal: The caller; another organization's tariff does not exist
            for them.
        for_update: Lock the row.

    Returns:
        The tariff.

    Raises:
        TariffNotFoundError: Unknown or out of reach.
    """
    tariff_record = await billing_repository.get_tariff_by_id(
        db, tariff_id, organization_id=principal.data_scope, for_update=for_update
    )
    if tariff_record is None:
        raise TariffNotFoundError(f"Tariff '{tariff_id}' was not found")
    return tariff_record


async def _insert_unique_active_tariff(
    db: AsyncSession, values: dict[str, object]
) -> TariffModel:
    """Insert a tariff, turning the unique-index clash into a conflict.

    Args:
        db: The async session owned by the entry boundary.
        values: Column values.

    Returns:
        The new tariff.

    Raises:
        TariffConflictError: Another ACTIVE tariff took the owner/location.
    """
    try:
        async with db.begin_nested():
            return await billing_repository.insert_tariff(db, values)
    except IntegrityError as error:
        raise TariffConflictError(
            "An active tariff already exists for this owner and location"
        ) from error


async def create_tariff(
    db: AsyncSession,
    tariff_create_request: TariffCreateRequest,
    *,
    principal: Principal,
) -> TariffResponse:
    """Create an ACTIVE tariff (BL-08); its prices come with the first version.

    Rule:
        1. The owner is the caller's organization; internal staff may name
           another existing one.
        2. A named location must exist and belong to the owner.
        3. At most one ACTIVE tariff per owner and location (one default per
           owner).

    Args:
        db: The async session owned by the entry boundary.
        tariff_create_request: The request.
        principal: The caller.

    Returns:
        The tariff, without a version yet.

    Raises:
        OrganizationNotFoundError: The named owner does not exist or is out of
            reach (404).
        TariffNotFoundError: The location does not exist (404).
        TariffInputError: The location belongs to another organization (400).
        TariffConflictError: An ACTIVE tariff already covers it (409).
    """
    owner_id = await identity_service.resolve_organization_for_new_record(
        db, principal, tariff_create_request.organization_id
    )
    location_id = tariff_create_request.location_id
    if location_id is not None:
        location = await charging_stations_service.resolve_location_owner_reference(
            db, location_id
        )
        if location is None:
            raise TariffNotFoundError(
                f"Charging location '{location_id}' was not found"
            )
        if location.organization_id != owner_id:
            raise TariffInputError("The location belongs to another organization")
    if await billing_repository.find_active_tariff(db, owner_id, location_id):
        raise TariffConflictError(
            "An active tariff already exists for this owner and location"
        )
    tariff_record = await _insert_unique_active_tariff(
        db,
        {
            "organization_id": owner_id,
            "location_id": location_id,
            "name": tariff_create_request.name.strip(),
            "currency": tariff_create_request.currency,
            "status": TariffStatus.ACTIVE.value,
        },
    )
    return await build_tariff_response(db, tariff_record)


async def get_tariff(
    db: AsyncSession, tariff_id: UUID, *, principal: Principal
) -> TariffResponse:
    """Get a tariff with its current version.

    Args:
        db: The async session owned by the entry boundary.
        tariff_id: UUID of the tariff.
        principal: The caller.

    Returns:
        The tariff.

    Raises:
        TariffNotFoundError: Unknown or out of reach (404).
    """
    return await build_tariff_response(
        db, await _get_tariff_record(db, tariff_id, principal)
    )


async def list_tariffs(
    db: AsyncSession,
    *,
    principal: Principal,
    page: int,
    page_size: int,
    organization_id: UUID | None,
    location_id: UUID | None,
    status: TariffStatus | None,
) -> TariffListResponse:
    """List tariffs inside the caller's reach.

    Args:
        db: The async session owned by the entry boundary.
        principal: The caller.
        page: Page number.
        page_size: Rows per page.
        organization_id: Owner filter; only internal staff can see beyond their
            own organization, others are limited to it whatever they ask.
        location_id: Only the tariff of this location.
        status: Only this status.

    Returns:
        A page of tariffs, newest first.
    """
    window = normalize_page_window(page, page_size)
    scope = principal.data_scope if principal.data_scope else organization_id
    rows, total = await billing_repository.list_tariffs(
        db,
        organization_id=scope,
        location_id=location_id,
        status=None if status is None else status.value,
        offset=window.offset,
        limit=window.page_size,
    )
    return TariffListResponse(
        items=[await build_tariff_response(db, row) for row in rows],
        total=total,
        page=window.page,
        page_size=window.page_size,
    )


async def update_tariff(
    db: AsyncSession,
    tariff_id: UUID,
    tariff_update_request: TariffUpdateRequest,
    *,
    principal: Principal,
) -> TariffResponse:
    """Rename a tariff (a tracked change).

    Args:
        db: The async session owned by the entry boundary.
        tariff_id: UUID of the tariff.
        tariff_update_request: The new name.
        principal: The caller.

    Returns:
        The tariff.

    Raises:
        TariffNotFoundError: Unknown or out of reach (404).
    """
    tariff_record = await _get_tariff_record(db, tariff_id, principal)
    await set_change_context(
        db, changed_by=principal.user_id, change_reason="Tariff renamed"
    )
    tariff_record.name = tariff_update_request.name.strip()
    await db.flush()
    return await build_tariff_response(db, tariff_record)


async def retire_tariff(
    db: AsyncSession, tariff_id: UUID, reason: str, *, principal: Principal
) -> TariffResponse:
    """Retire a tariff: INACTIVE with the reason (DM-19).

    Args:
        db: The async session owned by the entry boundary.
        tariff_id: UUID of the tariff.
        reason: Why it is retired.
        principal: The caller.

    Returns:
        The tariff.

    Raises:
        TariffNotFoundError: Unknown or out of reach (404).
        TariffConflictError: It is already INACTIVE (409).
    """
    tariff_record = await _get_tariff_record(db, tariff_id, principal)
    if tariff_record.status == TariffStatus.INACTIVE.value:
        raise TariffConflictError("The tariff is already retired")
    await set_change_context(
        db, changed_by=principal.user_id, change_reason=reason.strip()
    )
    tariff_record.status = TariffStatus.INACTIVE.value
    tariff_record.status_reason = reason.strip()
    await db.flush()
    return await build_tariff_response(db, tariff_record)


async def reactivate_tariff(
    db: AsyncSession, tariff_id: UUID, reason: str, *, principal: Principal
) -> TariffResponse:
    """Put a retired tariff back in use (ACTIVE with the reason).

    Args:
        db: The async session owned by the entry boundary.
        tariff_id: UUID of the tariff.
        reason: Why it is used again.
        principal: The caller.

    Returns:
        The tariff.

    Raises:
        TariffNotFoundError: Unknown or out of reach (404).
        TariffConflictError: It is already ACTIVE, or another ACTIVE tariff
            holds its owner and location (409).
    """
    tariff_record = await _get_tariff_record(db, tariff_id, principal)
    if tariff_record.status == TariffStatus.ACTIVE.value:
        raise TariffConflictError("The tariff is already active")
    if await billing_repository.find_active_tariff(
        db, tariff_record.organization_id, tariff_record.location_id
    ):
        raise TariffConflictError(
            "Another active tariff holds this owner and location; retire it first"
        )
    await set_change_context(
        db, changed_by=principal.user_id, change_reason=reason.strip()
    )
    tariff_record.status = TariffStatus.ACTIVE.value
    tariff_record.status_reason = reason.strip()
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as error:
        raise TariffConflictError(
            "Another active tariff holds this owner and location; retire it first"
        ) from error
    return await build_tariff_response(db, tariff_record)


async def publish_tariff_version(
    db: AsyncSession,
    tariff_id: UUID,
    publish_request: TariffVersionPublishRequest,
    *,
    principal: Principal,
) -> TariffVersionResponse:
    """Publish the next immutable version of a tariff (BL-09).

    Rule:
        1. The tariff is locked, so two publishes cannot take the same number.
        2. A retired tariff takes no version (reactivate it first).
        3. ``effective_from`` is now or later (a moment already past by less
           than the clock-skew allowance is taken as now) and later than the
           previous version's start, so the numbers and the dates agree.
        4. Time-of-use periods must be well-formed and must not overlap.

    Args:
        db: The async session owned by the entry boundary.
        tariff_id: UUID of the tariff.
        publish_request: The prices and the reason.
        principal: The caller; becomes ``created_by``.

    Returns:
        The new version.

    Raises:
        TariffNotFoundError: Unknown or out of reach (404).
        TariffConflictError: The tariff is retired (409).
        TariffInputError: A date or a period is invalid (400).
    """
    tariff_record = await _get_tariff_record(db, tariff_id, principal, for_update=True)
    if tariff_record.status != TariffStatus.ACTIVE.value:
        raise TariffConflictError("A retired tariff takes no new version")
    now = utc_now()
    effective_from = publish_request.effective_from or now
    if effective_from.tzinfo is None:
        raise TariffInputError("effective_from must carry a timezone")
    if effective_from < now - _EFFECTIVE_FROM_SKEW:
        raise TariffInputError("effective_from cannot be in the past")
    effective_from = max(effective_from, now)
    latest = await billing_repository.find_latest_tariff_version(db, tariff_id)
    if latest is not None and effective_from <= latest.effective_from:
        raise TariffInputError(
            "effective_from must be later than the start of the previous version"
        )
    time_periods = pricing.normalize_time_periods(
        None
        if publish_request.time_periods is None
        else [
            period.model_dump(by_alias=True) for period in publish_request.time_periods
        ]
    )
    version_record = await billing_repository.insert_tariff_version(
        db,
        {
            "tariff_id": tariff_id,
            "version_no": 1 if latest is None else latest.version_no + 1,
            "effective_from": effective_from,
            "price_per_kwh": Decimal(publish_request.price_per_kwh),
            "time_periods": time_periods,
            "vat_rate_percent": publish_request.vat_rate_percent,
            "change_reason": publish_request.change_reason.strip(),
            "created_by": principal.user_id,
        },
    )
    return to_tariff_version_response(version_record)


async def list_tariff_versions(
    db: AsyncSession, tariff_id: UUID, *, principal: Principal
) -> list[TariffVersionResponse]:
    """List every version of a tariff, newest number first.

    Args:
        db: The async session owned by the entry boundary.
        tariff_id: UUID of the tariff.
        principal: The caller.

    Returns:
        The versions.

    Raises:
        TariffNotFoundError: Unknown or out of reach (404).
    """
    await _get_tariff_record(db, tariff_id, principal)
    return [
        to_tariff_version_response(version)
        for version in await billing_repository.list_tariff_versions(db, tariff_id)
    ]


async def resolve_tariff_for_station(
    db: AsyncSession, station_id: UUID, at: datetime
) -> TariffQuote:
    """Find the price in force at a charger at a moment (BL-08, BL-09).

    The price at a location is its own ACTIVE tariff, otherwise the owner's
    default. A tariff with no version in force yet counts as absent. The price
    is the one of the time-of-use period covering the moment (Vietnam time),
    else the version's normal price. System caller: no data scope is applied.

    Args:
        db: The async session owned by the entry boundary.
        station_id: The charger.
        at: The moment; must carry a timezone.

    Returns:
        The quote.

    Raises:
        TariffStationNotFoundError: The charger does not exist (404).
        NoTariffInForceError: No tariff prices the charger now (409).
    """
    reference = await charging_stations_service.resolve_station_location_reference(
        db, station_id
    )
    if reference is None:
        raise TariffStationNotFoundError(f"Charger '{station_id}' was not found")
    for location_id in (reference.location_id, None):
        tariff_record = await billing_repository.find_active_tariff(
            db, reference.organization_id, location_id
        )
        if tariff_record is None:
            continue
        version_record = await billing_repository.find_tariff_version_in_force(
            db, tariff_record.tariff_id, at
        )
        if version_record is None:
            continue
        return TariffQuote(
            tariff_id=tariff_record.tariff_id,
            tariff_version_id=version_record.tariff_version_id,
            version_no=version_record.version_no,
            tariff_name=tariff_record.name,
            organization_id=tariff_record.organization_id,
            currency=tariff_record.currency,
            price_per_kwh=pricing.price_at(
                version_record.price_per_kwh, version_record.time_periods, at
            ),
            normal_price_per_kwh=version_record.price_per_kwh,
            vat_rate_percent=version_record.vat_rate_percent,
            time_periods=version_record.time_periods,
            at=at,
        )
    raise NoTariffInForceError("NO_TARIFF: no price is set for this charger")


def to_tariff_quote_response(quote: TariffQuote) -> TariffQuoteResponse:
    """Convert a quote into its response (pure mapping).

    Args:
        quote: The quote.

    Returns:
        The response; the with-VAT price is rounded to whole dong.
    """
    price_with_vat = pricing.round_to_whole_dong(
        quote.price_per_kwh * (Decimal(100) + quote.vat_rate_percent) / Decimal(100)
    )
    return TariffQuoteResponse(
        tariff_id=quote.tariff_id,
        tariff_version_id=quote.tariff_version_id,
        version_no=quote.version_no,
        tariff_name=quote.tariff_name,
        currency=quote.currency,
        at=quote.at,
        price_per_kwh=int(quote.price_per_kwh),
        price_per_kwh_with_vat=int(price_with_vat),
        normal_price_per_kwh=int(quote.normal_price_per_kwh),
        vat_rate_percent=quote.vat_rate_percent,
        time_periods=quote.time_periods,
    )


async def get_station_price(
    db: AsyncSession, station_id: UUID, at: datetime | None, *, principal: Principal
) -> TariffQuoteResponse:
    """Tell a caller the price of a charger they may see (PAY-09).

    Args:
        db: The async session owned by the entry boundary.
        station_id: The charger.
        at: The moment; now when omitted. Must carry a timezone.
        principal: The caller; the charger must be visible to them.

    Returns:
        The quote.

    Raises:
        ChargingStationNotFoundError: The charger is unknown or not visible (404).
        TariffInputError: ``at`` has no timezone (400).
        NoTariffInForceError: No tariff prices the charger (409).
    """
    moment = at or utc_now()
    if moment.tzinfo is None:
        raise TariffInputError("'at' must carry a timezone")
    await charging_stations_service.get_charging_station(
        db, station_id, principal=principal
    )
    return to_tariff_quote_response(
        await resolve_tariff_for_station(db, station_id, moment)
    )
