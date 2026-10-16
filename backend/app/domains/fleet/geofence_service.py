"""Geofences of a fleet: create, list, read, update, soft-delete (F-A5, FL-11).

A geofence is a polygon (``geography(POLYGON, 4326)``) owned by one fleet;
containment uses ``ST_Covers``. The entry / exit alerts are raised by
``telemetry/geofencing.py`` through ``service.list_geofences_containing``.
Internal module: the domain's router calls it. Runs in the caller's
transaction and never commits.
"""

from uuid import UUID

from geoalchemy2.elements import WKBElement
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Polygon
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.fleet.access as fleet_access
import app.domains.fleet.repository as fleet_repository
from app.domains.fleet.exceptions import (
    GeofenceNotFoundError,
)
from app.domains.fleet.models import (
    GeofenceModel,
)
from app.domains.fleet.schemas import (
    GeofenceCreateRequest,
    GeofenceListResponse,
    GeofencePolygonGeoJson,
    GeofenceResponse,
    GeofenceUpdateRequest,
)
from app.domains.identity.types import Principal
from app.libs.common.config import settings
from app.libs.common.pagination import normalize_page_window


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


async def _get_fleet_geofence_record(
    db_session: AsyncSession,
    fleet_id: UUID,
    geofence_id: UUID,
    principal: Principal,
) -> GeofenceModel:
    """Load a live geofence of a live fleet, or raise.

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet the geofence must belong to.
        geofence_id: Internal ID of the geofence.
        principal: The caller (data scope).

    Returns:
        The geofence record.

    Raises:
        FleetNotFoundError: When the fleet does not exist.
        GeofenceNotFoundError: When the geofence does not exist, was
            soft-deleted, or belongs to another fleet.
    """
    await fleet_access.get_fleet_record(db_session, fleet_id, principal)
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
    *,
    principal: Principal,
) -> GeofenceResponse:
    """Create a geofence for a fleet (F-A5, D6).

    The geofence applies to every vehicle that is currently a member of the
    fleet; entry/exit detection itself belongs to the telemetry domain.

    Args:
        db_session: Database session owned by the entry boundary.
        fleet_id: Internal ID of the owning fleet.
        geofence_create_request: Name and boundary, already validated.
        principal: The caller (data scope).

    Returns:
        Response for the new geofence.

    Raises:
        FleetNotFoundError: When the fleet does not exist.

    Side Effects:
        Inserts one ``geofences`` row (flushed, not committed).
    """
    await fleet_access.get_fleet_record(db_session, fleet_id, principal)
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
    principal: Principal,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> GeofenceListResponse:
    """Get a fleet's paginated live geofences, newest first (F-A5).

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.
        principal: The caller (data scope).
        page: Page number, starting from 1.
        page_size: Maximum number of geofences per page.

    Returns:
        Paginated geofence list response.

    Raises:
        FleetNotFoundError: When the fleet does not exist.
    """
    await fleet_access.get_fleet_record(db_session, fleet_id, principal)
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
    db_session: AsyncSession,
    fleet_id: UUID,
    geofence_id: UUID,
    *,
    principal: Principal,
) -> GeofenceResponse:
    """Get one live geofence of a fleet (F-A5).

    Args:
        db_session: Current database session.
        fleet_id: Internal ID of the fleet.
        geofence_id: Internal ID of the geofence.
        principal: The caller (data scope).

    Returns:
        Response for the geofence.

    Raises:
        FleetNotFoundError: When the fleet does not exist.
        GeofenceNotFoundError: When the geofence is not a live geofence of
            this fleet.
    """
    geofence_record = await _get_fleet_geofence_record(
        db_session, fleet_id, geofence_id, principal
    )
    return to_geofence_response(geofence_record)


async def update_geofence(
    db_session: AsyncSession,
    fleet_id: UUID,
    geofence_id: UUID,
    geofence_update_request: GeofenceUpdateRequest,
    *,
    principal: Principal,
) -> GeofenceResponse:
    """Partially update a geofence's name and/or boundary (F-A5).

    A field not sent, or sent as ``null``, is left unchanged (the backend's
    PATCH convention).

    Args:
        db_session: Database session owned by the entry boundary.
        fleet_id: Internal ID of the fleet.
        geofence_id: Internal ID of the geofence.
        geofence_update_request: Fields to change, already validated.
        principal: The caller (data scope).

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
        db_session, fleet_id, geofence_id, principal
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
    db_session: AsyncSession,
    fleet_id: UUID,
    geofence_id: UUID,
    *,
    principal: Principal,
) -> dict[str, str]:
    """Soft-delete a geofence of a fleet (F-A5).

    Args:
        db_session: Database session owned by the entry boundary.
        fleet_id: Internal ID of the fleet.
        geofence_id: Internal ID of the geofence.
        principal: The caller (data scope).

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
        db_session, fleet_id, geofence_id, principal
    )
    await fleet_repository.soft_delete_geofence(db_session, geofence_record)
    return {"message": "Geofence deleted successfully"}
