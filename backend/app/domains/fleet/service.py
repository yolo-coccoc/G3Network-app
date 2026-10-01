"""Business service and public contract of the fleet domain.

This module holds the business rules for fleet records, fleet-vehicle
membership and fleet geofences (F-E1, F-A5). Other domains may only call the
public cross-domain functions at the end of this module
(`list_active_member_vehicle_ids`, `find_current_fleet_id_by_vehicle`,
`list_geofences_containing`), which take and return primitives or frozen
DTOs - never the ORM model or HTTP response schema of the fleet domain. This
domain depends on the `vehicles` domain's public service to resolve a VIN
into a vehicle and to enrich a response with a vehicle's VIN/license
plate/status - the same one-directional edge shape already established by
`telematics -> vehicles` and `drivers -> vehicles`.
"""

from uuid import UUID

from geoalchemy2.elements import WKBElement
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Polygon
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.fleet.repository as fleet_repository
import app.domains.vehicles.service as vehicle_service
from app.domains.fleet.exceptions import (
    FleetConflictError,
    FleetMembershipConflictError,
    FleetMembershipNotFoundError,
    FleetNotFoundError,
    FleetVehicleNotFoundError,
    GeofenceNotFoundError,
)
from app.domains.fleet.models import (
    FleetModel,
    FleetVehicleMembershipModel,
    GeofenceModel,
)
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
    GeofenceCreateRequest,
    GeofenceListResponse,
    GeofencePolygonGeoJson,
    GeofenceResponse,
    GeofenceUpdateRequest,
)
from app.domains.fleet.types import FleetStatus, GeofenceReference
from app.domains.vehicles.types import VehicleStatus, VehicleSummary
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.geo import coordinates_to_location
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
    return FleetResponse(
        fleet_id=fleet_record.fleet_id,
        fleet_code=fleet_record.fleet_code,
        name=fleet_record.name,
        status=fleet_record.status,
        vehicle_count=vehicle_count,
        created_at=fleet_record.created_at,
        updated_at=fleet_record.updated_at,
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
    *,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
    status_filter: FleetStatus | None = None,
    search_text: str | None = None,
    vehicle_vin: str | None = None,
) -> FleetListResponse:
    """Get a paginated list of active fleets.

    Args:
        db_session: Current database session.
        page: Page number, starting from 1.
        page_size: Maximum number of fleets per page.
        status_filter: Status filter, if any.
        search_text: Case-insensitive substring of the fleet name or fleet
            code, if any.
        vehicle_vin: Only the fleet this vehicle is currently a member of
            ("which fleet is this vehicle in"), if given. A VIN that doesn't
            resolve to an active vehicle yields an empty page, not an error:
            it is a filter, like the others.

    Returns:
        Paginated fleet list response.

    Side Effects:
        Calls the vehicles domain's public service to resolve
        ``vehicle_vin``. Read-only; does not commit or rollback.
    """
    page_window = normalize_page_window(page, page_size)

    vehicle_id: UUID | None = None
    if vehicle_vin is not None:
        vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_vin(
            db_session, vehicle_vin
        )
        if vehicle_reference is None:
            return FleetListResponse(
                items=[],
                total=0,
                page=page_window.page,
                page_size=page_window.page_size,
            )
        vehicle_id = vehicle_reference.vehicle_id

    fleet_records = await fleet_repository.list_all(
        db_session,
        offset=page_window.offset,
        limit=page_window.page_size,
        status_filter=status_filter,
        search_text=search_text,
        vehicle_id=vehicle_id,
    )
    total = await fleet_repository.count(
        db_session,
        status_filter=status_filter,
        search_text=search_text,
        vehicle_id=vehicle_id,
    )

    return FleetListResponse(
        items=[
            await build_fleet_response(db_session, fleet_record)
            for fleet_record in fleet_records
        ],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
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
            db_session, membership_record, left_at=utc_now()
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

    existing_membership = await fleet_repository.find_active_membership_by_vehicle(
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
            joined_at=utc_now(),
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
) -> None:
    """Close a vehicle's active membership in a fleet, by VIN.

    Identifies the vehicle by VIN, the same way `add_vehicle_to_fleet` does.

    Args:
        db_session: Database session owned by the entry boundary.
        fleet_id: Internal ID of the fleet.
        vehicle_vin: VIN of the vehicle to remove.

    Raises:
        FleetNotFoundError: When the fleet does not exist.
        FleetVehicleNotFoundError: When no active vehicle has this VIN.
        FleetMembershipNotFoundError: When the vehicle has no active
            membership in this fleet.

    Side Effects:
        Calls the vehicles domain's public service to resolve the VIN.
    """
    fleet_record = await fleet_repository.get_by_id(db_session, fleet_id)
    if fleet_record is None:
        raise FleetNotFoundError(f"Fleet with id '{fleet_id}' not found")

    vehicle_reference = await vehicle_service.resolve_vehicle_reference_by_vin(
        db_session, vehicle_vin
    )
    if vehicle_reference is None:
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
        db_session, active_membership, left_at=utc_now()
    )


async def close_fleet_membership(
    db_session: AsyncSession,
    fleet_id: UUID,
    membership_id: UUID,
) -> None:
    """Close an open membership of a fleet by its ID (D8, #84).

    The only way to release a membership whose vehicle was soft-deleted
    after joining: the VIN-based `remove_vehicle_from_fleet` can no longer
    resolve such a vehicle, so its open row would otherwise keep counting
    toward the fleet forever.

    Args:
        db_session: Database session owned by the entry boundary.
        fleet_id: Internal ID of the fleet.
        membership_id: Internal ID of the membership to close.

    Raises:
        FleetNotFoundError: When the fleet does not exist.
        FleetMembershipNotFoundError: When the membership is unknown,
            belongs to another fleet, or is already closed.

    Side Effects:
        Stamps the membership's ``left_at``; does not commit or rollback.
    """
    fleet_record = await fleet_repository.get_by_id(db_session, fleet_id)
    if fleet_record is None:
        raise FleetNotFoundError(f"Fleet with id '{fleet_id}' not found")

    membership_record = await fleet_repository.get_membership_by_id(
        db_session, membership_id
    )
    if (
        membership_record is None
        or membership_record.fleet_id != fleet_id
        or membership_record.left_at is not None
    ):
        raise FleetMembershipNotFoundError(
            f"Membership '{membership_id}' is not an open membership of "
            f"fleet '{fleet_id}'"
        )

    await fleet_repository.close_membership(
        db_session, membership_record, left_at=utc_now()
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
        joined_at=membership_record.joined_at,
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
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
    status_filter: VehicleStatus | None = None,
    search_text: str | None = None,
) -> FleetVehicleListResponse:
    """Get a fleet's paginated current vehicle list, newest member first (F-E1).

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.
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
    fleet_record = await fleet_repository.get_by_id(db_session, fleet_id)
    if fleet_record is None:
        raise FleetNotFoundError(f"Fleet with id '{fleet_id}' not found")

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
        key=lambda membership_record: membership_record.joined_at, reverse=True
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


def to_geofence_boundary(boundary_geojson: GeofencePolygonGeoJson) -> WKBElement:
    """Convert a validated GeoJSON polygon into a PostGIS geography polygon.

    Pure mapping only, no I/O. The ring keeps its given order.

    Args:
        boundary_geojson: A polygon the request schema already validated
            (one closed, non-self-crossing ring of at least 4 positions).

    Returns:
        A WKB polygon with SRID 4326, ready to persist.
    """
    return from_shape(Polygon(boundary_geojson.coordinates[0]), srid=4326)


def to_geofence_polygon_geojson(boundary: object) -> GeofencePolygonGeoJson:
    """Convert a stored geography polygon back into the API's GeoJSON shape.

    Pure mapping only, no I/O.

    Args:
        boundary: The ``geofences.boundary`` value read back from the ORM.

    Returns:
        The polygon with its single exterior ring, in the stored order.

    Raises:
        TypeError: If ``boundary`` is not a WKB polygon - never the case for
            a row flushed to or loaded from the database, whose column only
            admits polygons.
    """
    if not isinstance(boundary, WKBElement):
        raise TypeError(f"expected a WKB polygon, got {type(boundary).__name__}")
    polygon = to_shape(boundary)
    if not isinstance(polygon, Polygon):
        raise TypeError(f"expected a polygon, got {polygon.geom_type}")
    ring = [
        (float(position[0]), float(position[1])) for position in polygon.exterior.coords
    ]
    return GeofencePolygonGeoJson(coordinates=[ring])


def to_geofence_response(geofence_record: GeofenceModel) -> GeofenceResponse:
    """Convert a geofence ORM record into an HTTP response.

    Pure mapping only, no I/O.

    Args:
        geofence_record: A geofence record created, loaded or updated by the
            repository.

    Returns:
        Response data, with the boundary in the API's GeoJSON shape.
    """
    return GeofenceResponse(
        geofence_id=geofence_record.geofence_id,
        fleet_id=geofence_record.fleet_id,
        name=geofence_record.name,
        boundary=to_geofence_polygon_geojson(geofence_record.boundary),
        created_at=geofence_record.created_at,
        updated_at=geofence_record.updated_at,
    )


async def _get_fleet_record(db_session: AsyncSession, fleet_id: UUID) -> FleetModel:
    """Load a live fleet, or raise if it doesn't exist.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.

    Returns:
        The fleet record.

    Raises:
        FleetNotFoundError: When the fleet does not exist or was soft-deleted.
    """
    fleet_record = await fleet_repository.get_by_id(db_session, fleet_id)
    if fleet_record is None:
        raise FleetNotFoundError(f"Fleet with id '{fleet_id}' not found")
    return fleet_record


async def _get_fleet_geofence_record(
    db_session: AsyncSession, fleet_id: UUID, geofence_id: UUID
) -> GeofenceModel:
    """Load a live geofence of a live fleet, or raise.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet the geofence must belong to.
        geofence_id: Internal ID of the geofence.

    Returns:
        The geofence record.

    Raises:
        FleetNotFoundError: When the fleet does not exist.
        GeofenceNotFoundError: When the geofence does not exist, was
            soft-deleted, or belongs to another fleet.
    """
    await _get_fleet_record(db_session, fleet_id)
    geofence_record = await fleet_repository.get_geofence_by_id(db_session, geofence_id)
    if geofence_record is None or geofence_record.fleet_id != fleet_id:
        raise GeofenceNotFoundError(
            f"Geofence '{geofence_id}' not found in fleet '{fleet_id}'"
        )
    return geofence_record


async def create_geofence(
    db_session: AsyncSession,
    fleet_id: UUID,
    geofence_create_request: GeofenceCreateRequest,
) -> GeofenceResponse:
    """Create a geofence for a fleet (F-A5, D6).

    The geofence applies to every vehicle that is currently a member of the
    fleet; entry/exit detection itself belongs to the telemetry domain.

    Args:
        db_session: Database session owned by the entry boundary.
        fleet_id: Internal ID of the owning fleet.
        geofence_create_request: Name and boundary, already validated.

    Returns:
        Response for the new geofence.

    Raises:
        FleetNotFoundError: When the fleet does not exist.

    Side Effects:
        Inserts one ``geofences`` row (flushed, not committed).
    """
    await _get_fleet_record(db_session, fleet_id)
    geofence_record = await fleet_repository.insert_geofence(
        db_session,
        fleet_id=fleet_id,
        name=geofence_create_request.name,
        boundary=to_geofence_boundary(geofence_create_request.boundary),
    )
    return to_geofence_response(geofence_record)


async def list_geofences(
    db_session: AsyncSession,
    fleet_id: UUID,
    *,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> GeofenceListResponse:
    """Get a fleet's paginated live geofences, newest first (F-A5).

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.
        page: Page number, starting from 1.
        page_size: Maximum number of geofences per page.

    Returns:
        Paginated geofence list response.

    Raises:
        FleetNotFoundError: When the fleet does not exist.
    """
    await _get_fleet_record(db_session, fleet_id)
    page_window = normalize_page_window(page, page_size)

    geofence_records = await fleet_repository.list_geofences_by_fleet(
        db_session, fleet_id, offset=page_window.offset, limit=page_window.page_size
    )
    total = await fleet_repository.count_geofences(db_session, fleet_id)

    return GeofenceListResponse(
        items=[
            to_geofence_response(geofence_record)
            for geofence_record in geofence_records
        ],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def get_geofence(
    db_session: AsyncSession, fleet_id: UUID, geofence_id: UUID
) -> GeofenceResponse:
    """Get one live geofence of a fleet (F-A5).

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.
        geofence_id: Internal ID of the geofence.

    Returns:
        Response for the geofence.

    Raises:
        FleetNotFoundError: When the fleet does not exist.
        GeofenceNotFoundError: When the geofence is not a live geofence of
            this fleet.
    """
    geofence_record = await _get_fleet_geofence_record(
        db_session, fleet_id, geofence_id
    )
    return to_geofence_response(geofence_record)


async def update_geofence(
    db_session: AsyncSession,
    fleet_id: UUID,
    geofence_id: UUID,
    geofence_update_request: GeofenceUpdateRequest,
) -> GeofenceResponse:
    """Partially update a geofence's name and/or boundary (F-A5).

    A field not sent, or sent as ``null``, is left unchanged (the backend's
    PATCH convention).

    Args:
        db_session: Database session owned by the entry boundary.
        fleet_id: Internal ID of the fleet.
        geofence_id: Internal ID of the geofence.
        geofence_update_request: Fields to change, already validated.

    Returns:
        Response for the geofence after the update.

    Raises:
        FleetNotFoundError: When the fleet does not exist.
        GeofenceNotFoundError: When the geofence is not a live geofence of
            this fleet.

    Side Effects:
        Updates the row and flushes; does not commit.
    """
    geofence_record = await _get_fleet_geofence_record(
        db_session, fleet_id, geofence_id
    )
    update_values: dict[str, object] = {}
    if geofence_update_request.name is not None:
        update_values["name"] = geofence_update_request.name
    if geofence_update_request.boundary is not None:
        update_values["boundary"] = to_geofence_boundary(
            geofence_update_request.boundary
        )
    if not update_values:
        return to_geofence_response(geofence_record)

    updated_geofence_record = await fleet_repository.update_geofence_fields(
        db_session, geofence_record, update_values
    )
    return to_geofence_response(updated_geofence_record)


async def soft_delete_geofence(
    db_session: AsyncSession, fleet_id: UUID, geofence_id: UUID
) -> dict[str, str]:
    """Soft-delete a geofence of a fleet (F-A5).

    Args:
        db_session: Database session owned by the entry boundary.
        fleet_id: Internal ID of the fleet.
        geofence_id: Internal ID of the geofence.

    Returns:
        Success deletion message.

    Raises:
        FleetNotFoundError: When the fleet does not exist.
        GeofenceNotFoundError: When the geofence is not a live geofence of
            this fleet (including one already deleted).

    Side Effects:
        Stamps ``deleted_at``; does not commit. A deleted geofence is no
        longer returned by ``list_geofences_containing``.
    """
    geofence_record = await _get_fleet_geofence_record(
        db_session, fleet_id, geofence_id
    )
    await fleet_repository.soft_delete_geofence(db_session, geofence_record)
    return {"message": "Geofence deleted successfully"}


async def list_active_member_vehicle_ids(
    db: AsyncSession, fleet_id: UUID
) -> list[UUID]:
    """List the vehicles currently in a fleet. Public cross-domain entry point.

    Used by the telemetry domain's fleet-wide views and by the telematics
    domain's fleet-wide config push.

    Args:
        db: Session owned by the caller's entry boundary.
        fleet_id: Internal ID of the fleet.

    Returns:
        IDs of the vehicles with an open membership, oldest member first.
        A vehicle soft-deleted after joining is still included (its
        membership is still open); the caller resolves each ID through the
        vehicles domain if it needs a live vehicle.

    Raises:
        FleetNotFoundError: When the fleet does not exist or was
            soft-deleted.

    Side Effects:
        Read-only; does not commit or rollback.
    """
    await _get_fleet_record(db, fleet_id)
    membership_records = await fleet_repository.list_all_active_memberships_by_fleet(
        db, fleet_id
    )
    membership_records.sort(key=lambda membership_record: membership_record.joined_at)
    return [membership_record.vehicle_id for membership_record in membership_records]


async def find_current_fleet_id_by_vehicle(
    db: AsyncSession, vehicle_id: UUID
) -> UUID | None:
    """Find the fleet a vehicle currently belongs to. Public cross-domain entry point.

    Args:
        db: Session owned by the caller's entry boundary.
        vehicle_id: Internal ID of the vehicle.

    Returns:
        The fleet of the vehicle's open membership, or ``None`` when the
        vehicle is in no fleet. A soft-deleted fleet never shows up here:
        deleting a fleet closes its memberships in the same transaction.

    Side Effects:
        Read-only; does not commit or rollback.
    """
    membership_record = await fleet_repository.find_active_membership_by_vehicle(
        db, vehicle_id
    )
    return membership_record.fleet_id if membership_record is not None else None


async def list_geofences_containing(
    db: AsyncSession, fleet_id: UUID, latitude: float, longitude: float
) -> list[GeofenceReference]:
    """List a fleet's live geofences that cover a point. Public cross-domain entry point.

    Used by the telemetry domain to detect geofence entry and exit (F-A5).
    The fleet is not checked: an unknown or deleted fleet simply has no
    live geofence that matches.

    Args:
        db: Session owned by the caller's entry boundary.
        fleet_id: Internal ID of the fleet.
        latitude: Latitude of the point, in decimal degrees (WGS84).
        longitude: Longitude of the point, in decimal degrees (WGS84).

    Returns:
        References to the matching geofences, oldest first; a point on a
        boundary counts as inside (``ST_Covers``).

    Side Effects:
        Read-only; does not commit or rollback.
    """
    location = coordinates_to_location(latitude, longitude)
    if location is None:
        return []
    geofence_records = await fleet_repository.list_geofences_covering_point(
        db, fleet_id, location
    )
    return [
        GeofenceReference(
            geofence_id=geofence_record.geofence_id, name=geofence_record.name
        )
        for geofence_record in geofence_records
    ]
