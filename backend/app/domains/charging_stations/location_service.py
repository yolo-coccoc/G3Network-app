"""Locations and their access grants (CS-09, CS-10, CS-13, STN-01).

Create, list, read, update and soft-delete charging locations (the place a
charger stands, owned by one organization, public or private), and grant,
list and revoke the access of another organization to a private location.
Internal module: the domain's router calls it; other domains use
``service.py``. Runs in the caller's transaction and never commits.
"""

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_stations.access as station_access
import app.domains.charging_stations.mappers as station_mappers
import app.domains.charging_stations.repository as charging_stations_repository
import app.domains.identity.service as identity_service
from app.domains.charging_stations.exceptions import (
    ChargingLocationAccessConflictError,
    ChargingLocationAccessNotFoundError,
    ChargingLocationNotFoundError,
    ChargingTopologyConflictError,
)
from app.domains.charging_stations.models import (
    ChargingLocationAccessModel,
)
from app.domains.charging_stations.schemas import (
    ChargingLocationAccessCreateRequest,
    ChargingLocationAccessListResponse,
    ChargingLocationAccessResponse,
    ChargingLocationAccessRevokeRequest,
    ChargingLocationCreateRequest,
    ChargingLocationListResponse,
    ChargingLocationResponse,
    ChargingLocationUpdateRequest,
    ChargingResourceDeleteResponse,
)
from app.domains.charging_stations.types import (
    ChargingResourceStatus,
    StationListScope,
)
from app.domains.identity.exceptions import OrganizationNotFoundError
from app.domains.identity.types import Principal
from app.libs.common.config import settings
from app.libs.common.geo import coordinates_to_location
from app.libs.common.pagination import normalize_page_window

# Fixed ``revoke_reason`` texts of the grants the system closes on its own
# (``revoked_by`` stays NULL): one whose last valid day has passed, and the
# live grants of a location that is soft-deleted (CS-13).
_EXPIRED_GRANT_REVOKE_REASON = "Access expired"
_DELETED_LOCATION_GRANT_REVOKE_REASON = "Location left the system"

# --- Locations ---------------------------------------------------------------


async def create_charging_location(
    db: AsyncSession,
    location_create_request: ChargingLocationCreateRequest,
    *,
    principal: Principal,
) -> ChargingLocationResponse:
    """Create a location owned by the caller's organization (CS-10).

    Args:
        db: Async session owned by the HTTP boundary.
        location_create_request: Location data, already Pydantic-validated.
        principal: The caller; internal staff may name another owner
            organization in the request.

    Returns:
        The newly created location.

    Raises:
        OrganizationNotFoundError: The named owner organization does not exist
            or is out of the caller's reach.
        ChargingTopologyConflictError: On a database integrity failure.
    """
    owner_organization_id = await identity_service.resolve_organization_for_new_record(
        db, principal, location_create_request.organization_id
    )
    coordinates = coordinates_to_location(
        location_create_request.latitude, location_create_request.longitude
    )
    assert coordinates is not None, "latitude/longitude are both required here"
    try:
        location = await charging_stations_repository.create_charging_location(
            db,
            organization_id=owner_organization_id,
            display_name=location_create_request.display_name,
            address=location_create_request.address,
            coordinates=coordinates,
            is_public=location_create_request.is_public,
        )
    except IntegrityError as error:
        raise ChargingTopologyConflictError("Location could not be created") from error
    return station_mappers.to_charging_location_response(location)


async def list_charging_locations(
    db: AsyncSession,
    *,
    principal: Principal,
    scope: StationListScope = StationListScope.MANAGED,
    owner_organization_id: UUID | None = None,
    is_public: bool | None = None,
    status: ChargingResourceStatus | None = None,
    search_text: str | None = None,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> ChargingLocationListResponse:
    """List active locations the caller manages, or all they may see (STN-01, STN-12).

    Args:
        db: Async session owned by the HTTP boundary.
        principal: The caller (data scope).
        scope: ``MANAGED`` (default): the ones owned by the caller's
            organization, all for internal staff. ``VISIBLE``: also the public
            ones and the ones the caller's organization holds a grant on (the
            map data of CS-28).
        owner_organization_id: Only locations of this owner organization.
        is_public: Only public (`True`) or private (`False`) locations.
        status: Only locations with this status.
        search_text: Case-insensitive fragment of the name or address.
        page: Page number starting at one.
        page_size: Page size, clamped according to settings.

    Returns:
        The locations and pagination metadata.
    """
    page_window = normalize_page_window(page, page_size)
    organization_id, viewer = station_access.list_reach(principal, scope)
    status_value = status.value if status is not None else None
    search = search_text.strip() if search_text else None
    locations = await charging_stations_repository.list_charging_locations(
        db,
        offset=page_window.offset,
        limit=page_window.page_size,
        organization_id=organization_id,
        viewer=viewer,
        owner_organization_id=owner_organization_id,
        is_public=is_public,
        status=status_value,
        search_text=search,
    )
    total = await charging_stations_repository.count_locations(
        db,
        organization_id=organization_id,
        viewer=viewer,
        owner_organization_id=owner_organization_id,
        is_public=is_public,
        status=status_value,
        search_text=search,
    )
    return ChargingLocationListResponse(
        items=[
            station_mappers.to_charging_location_response(location)
            for location in locations
        ],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def get_charging_location(
    db: AsyncSession, location_id: UUID, *, principal: Principal
) -> ChargingLocationResponse:
    """Get an active location the caller may see by internal UUID.

    Args:
        db: Async session owned by the HTTP boundary.
        location_id: UUID of the location.
        principal: The caller; a private location is visible to its owner,
            to organizations with a live grant and to internal staff.

    Returns:
        The active location.

    Raises:
        ChargingLocationNotFoundError: If it does not exist or was deleted.
    """
    location = await station_access.get_location_in_reach(
        db, location_id, principal, view=True
    )
    return station_mappers.to_charging_location_response(location)


async def update_charging_location(
    db: AsyncSession,
    location_id: UUID,
    location_update_request: ChargingLocationUpdateRequest,
    *,
    principal: Principal,
) -> ChargingLocationResponse:
    """PATCH a location.

    Args:
        db: Async session owned by the HTTP boundary.
        location_id: UUID of the location to update.
        location_update_request: PATCH fields, already Pydantic-validated.
        principal: The caller; only the owner organization and internal staff
            may change a location.

    Returns:
        The updated location.

    Raises:
        ChargingLocationNotFoundError: If it does not exist or was deleted.
    """
    location = await station_access.get_location_in_reach(db, location_id, principal)
    update_data = station_mappers.clean_update_values(
        location_update_request.model_dump(
            exclude_unset=True, exclude={"latitude", "longitude"}
        )
    )
    if location_update_request.latitude is not None:
        update_data["coordinates"] = coordinates_to_location(
            location_update_request.latitude, location_update_request.longitude
        )
    update_data, change_reason = station_mappers.normalize_status_update(
        update_data, "Charging location edited"
    )
    if not update_data:
        return station_mappers.to_charging_location_response(location)
    updated = await charging_stations_repository.update_charging_location(
        db,
        location_id,
        update_data,
        change_reason=change_reason,
        changed_by=principal.user_id,
    )
    if updated is None:
        raise ChargingLocationNotFoundError(f"Location '{location_id}' not found")
    return station_mappers.to_charging_location_response(updated)


async def soft_delete_charging_location(
    db: AsyncSession,
    location_id: UUID,
    *,
    principal: Principal,
    status_reason: str = "Charging location removed",
) -> ChargingResourceDeleteResponse:
    """Soft-delete a location with its chargers, EVSEs, connectors and grants.

    Args:
        db: Async session owned by the HTTP boundary.
        location_id: UUID of the location to soft-delete.
        principal: The caller; only the owner organization and internal staff
            may delete a location.
        status_reason: Why the location left the system.

    Returns:
        Confirmation message for the soft-delete.

    Raises:
        ChargingLocationNotFoundError: If it does not exist or was deleted.
    """
    await station_access.get_location_in_reach(db, location_id, principal)
    for (
        station_id
    ) in await charging_stations_repository.list_station_ids_by_location_id(
        db, location_id
    ):
        await station_access.ensure_no_active_session(
            db,
            await station_access.list_station_connector_ids(db, station_id),
            "A charging session is in progress at this location",
        )
    if not await charging_stations_repository.soft_delete_location(
        db,
        location_id,
        status_reason=status_reason,
        grant_revoke_reason=_DELETED_LOCATION_GRANT_REVOKE_REASON,
        changed_by=principal.user_id,
    ):
        raise ChargingLocationNotFoundError(f"Location '{location_id}' not found")
    return ChargingResourceDeleteResponse(message="Charging location soft-deleted")


# --- Access grants -----------------------------------------------------------


async def _close_expired_grants(db: AsyncSession, location_id: UUID) -> None:
    """Close a location's live grants whose last valid day has passed (CS-13).

    A grant is valid through its ``valid_until`` day in the report time zone,
    so it has expired once that day is before today there.

    Args:
        db: Async session owned by the HTTP boundary.
        location_id: The location whose grants are tidied.

    Side Effects:
        Closes the expired rows with a fixed reason and flushes; no commit.
    """
    await charging_stations_repository.close_expired_location_access(
        db,
        valid_before=station_access.today_in_report_zone(),
        revoke_reason=_EXPIRED_GRANT_REVOKE_REASON,
        location_id=location_id,
    )


async def grant_charging_location_access(
    db: AsyncSession,
    location_id: UUID,
    access_create_request: ChargingLocationAccessCreateRequest,
    *,
    principal: Principal,
) -> ChargingLocationAccessResponse:
    """Let another organization charge at a location (CS-10, CS-13).

    Rule:
        A grant to the owner itself is refused, and so is a second live grant
        to the same organization. Grants may be made on a public location (they
        have no effect there and apply again if it turns private).

    Args:
        db: Async session owned by the HTTP boundary.
        location_id: UUID of the location.
        access_create_request: The grantee and the end date.
        principal: The caller, recorded as ``granted_by``; only the owner
            organization and internal staff may grant access.

    Returns:
        The new grant.

    Raises:
        ChargingLocationNotFoundError: If the location is not active or is
            out of the caller's reach.
        OrganizationNotFoundError: If the grantee organization does not exist.
        ChargingLocationAccessConflictError: If the grantee is the owner or a
            live grant exists.
    """
    location = await station_access.get_location_in_reach(db, location_id, principal)
    # An expired grant would still block a new one (one live row per pair).
    await _close_expired_grants(db, location_id)
    if access_create_request.allowed_organization_id == location.organization_id:
        raise ChargingLocationAccessConflictError(
            "The owner organization already may charge at its own location"
        )
    if (
        await identity_service.find_organization_reference(
            db, access_create_request.allowed_organization_id
        )
        is None
    ):
        raise OrganizationNotFoundError(
            f"Organization '{access_create_request.allowed_organization_id}' not found"
        )
    if await charging_stations_repository.get_live_location_access(
        db, location_id, access_create_request.allowed_organization_id
    ):
        raise ChargingLocationAccessConflictError(
            "The organization already has access to this location"
        )
    try:
        access = await charging_stations_repository.create_location_access(
            db,
            location_id=location_id,
            allowed_organization_id=access_create_request.allowed_organization_id,
            granted_by=principal.user_id,
            valid_until=access_create_request.valid_until,
        )
    except IntegrityError as error:
        raise ChargingLocationAccessConflictError(
            "The access already exists"
        ) from error
    return ChargingLocationAccessResponse.model_validate(access)


async def list_charging_location_access(
    db: AsyncSession, location_id: UUID, *, principal: Principal
) -> ChargingLocationAccessListResponse:
    """List the live grants of a location.

    Args:
        db: Async session owned by the HTTP boundary.
        location_id: UUID of the location.
        principal: The caller; only the owner organization and internal staff
            see who was granted access.

    Returns:
        The grants that are not revoked.

    Raises:
        ChargingLocationNotFoundError: If the location is not active.
    """
    await station_access.get_location_in_reach(db, location_id, principal)
    await _close_expired_grants(db, location_id)
    grants = await charging_stations_repository.list_live_location_access(
        db, location_id
    )
    return ChargingLocationAccessListResponse(
        items=[ChargingLocationAccessResponse.model_validate(grant) for grant in grants]
    )


async def revoke_charging_location_access(
    db: AsyncSession,
    location_id: UUID,
    access_id: UUID,
    access_revoke_request: ChargingLocationAccessRevokeRequest,
    *,
    principal: Principal,
) -> ChargingLocationAccessResponse:
    """Close a grant (the row is kept, so a new grant is possible, CS-13).

    Args:
        db: Async session owned by the HTTP boundary.
        location_id: UUID of the location.
        access_id: UUID of the grant.
        access_revoke_request: Why the access ends.
        principal: The caller, recorded as ``revoked_by``; only the owner
            organization and internal staff may revoke.

    Returns:
        The closed grant.

    Raises:
        ChargingLocationNotFoundError: If the location is out of reach.
        ChargingLocationAccessNotFoundError: If the location has no such grant.
        ChargingLocationAccessConflictError: If the grant is already closed.
    """
    await station_access.get_location_in_reach(db, location_id, principal)
    access: (
        ChargingLocationAccessModel | None
    ) = await charging_stations_repository.get_location_access_by_id(
        db, location_id, access_id
    )
    if access is None:
        raise ChargingLocationAccessNotFoundError(f"Access '{access_id}' not found")
    if access.revoked_at is not None:
        raise ChargingLocationAccessConflictError("The access is already revoked")
    revoked = await charging_stations_repository.revoke_location_access(
        db,
        access,
        revoked_by=principal.user_id,
        revoke_reason=access_revoke_request.revoke_reason,
    )
    return ChargingLocationAccessResponse.model_validate(revoked)
