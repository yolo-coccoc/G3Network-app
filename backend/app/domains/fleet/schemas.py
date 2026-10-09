"""Pydantic schemas for the fleet domain HTTP API.

Geofence boundaries (F-A5) cross the API as a GeoJSON ``Polygon`` object
with exactly one linear ring of ``[longitude, latitude]`` positions; holes
and multi-polygons are not supported.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator
from shapely.geometry import Polygon

from app.domains.vehicles.types import VehicleStatus
from app.libs.common.config import settings

# Three distinct corners plus the closing position that repeats the first.
GEOFENCE_MIN_RING_POSITIONS = 4

# A GeoJSON position: longitude first, then latitude, both range-checked.
GeofencePosition = tuple[
    Annotated[float, Field(ge=-180, le=180)],
    Annotated[float, Field(ge=-90, le=90)],
]


class _FleetInputFields(BaseModel):
    """Fields shared by the create and response fleet schemas.

    ``name`` and ``fleet_code`` are both optional, but a fleet always has at
    least one of them (FL-08): checked on create, and guaranteed by the
    ``ck_fleets_name_or_code`` check constraint.
    """

    organization_id: UUID = Field(
        ..., description="Organization that owns the fleet (FL-08)"
    )
    fleet_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=50,
        description="Fleet code, unique within the organization",
    )
    name: str | None = Field(
        default=None, min_length=1, max_length=100, description="Fleet name"
    )
    parent_fleet_id: UUID | None = Field(
        default=None,
        description="Fleet this one sits under; null for a top-level fleet",
    )


class FleetCreateRequest(_FleetInputFields):
    """HTTP request data for creating a new fleet.

    ``organization_id`` is a required field until authentication (WP2) lets
    the API take the caller's organization from the login instead.
    """

    @model_validator(mode="after")
    def _require_name_or_code(self) -> "FleetCreateRequest":
        """Reject a fleet with neither a name nor a code (FL-08).

        Returns:
            The validated request.

        Raises:
            ValueError: When both ``name`` and ``fleet_code`` are missing.
        """
        if self.name is None and self.fleet_code is None:
            raise ValueError("a fleet needs a name, a fleet code, or both")
        return self


class FleetUpdateRequest(BaseModel):
    """HTTP request data for partially updating (renaming or moving) a fleet.

    A field that is not sent, or sent as ``null``, is left unchanged, so an
    update can neither clear the name or code nor move a fleet back to the
    top level (deferred.md 98).
    """

    fleet_code: str | None = Field(
        default=None, min_length=1, max_length=50, description="Natural fleet code"
    )
    name: str | None = Field(
        default=None, min_length=1, max_length=100, description="Fleet name"
    )
    parent_fleet_id: UUID | None = Field(
        default=None, description="Move the fleet under this fleet"
    )


class FleetResponse(_FleetInputFields):
    """Fleet data returned via the HTTP API.

    Attributes:
        fleet_id: Internal ID of the fleet.
        vehicle_count: Number of vehicles currently in the fleet (open
            memberships only), enriched by the service.
        created_at: Creation time.
        updated_at: Last update time.
    """

    fleet_id: UUID = Field(..., description="Fleet ID (internal)")
    vehicle_count: int = Field(..., ge=0, description="Number of member vehicles")
    created_at: datetime = Field(..., description="Creation time")
    updated_at: datetime = Field(..., description="Last update time")


class FleetListResponse(BaseModel):
    """Paginated fleet list data returned via the HTTP API."""

    items: list[FleetResponse] = Field(..., description="List of fleets")
    total: int = Field(..., ge=0, description="Total number of fleets")
    page: int = Field(..., ge=1, description="Current page")
    page_size: int = Field(
        ..., ge=1, le=settings.API_MAX_PAGE_SIZE, description="Number of items per page"
    )


class FleetVehicleAddRequest(BaseModel):
    """HTTP request data for adding a vehicle to a fleet."""

    vehicle_vin: str = Field(
        ..., min_length=17, max_length=17, description="VIN of the vehicle to add"
    )


class FleetVehicleResponse(BaseModel):
    """One vehicle in a fleet's current member list (F-E1).

    Attributes:
        vehicle_id: Internal ID of the vehicle.
        vin: VIN of the vehicle; ``None`` when the vehicle was soft-deleted
            after joining (the membership is still open and still counted
            in ``total``).
        license_plate: License plate of the vehicle; ``None`` in the same
            case.
        status: Vehicle lifecycle status - the only status this backend
            can honestly report; no online/offline signal exists yet.
            ``None`` in the same case.
        added_at: When this vehicle was added to the fleet.
    """

    vehicle_id: UUID
    vin: str | None
    license_plate: str | None
    status: VehicleStatus | None
    added_at: datetime


class FleetVehicleListResponse(BaseModel):
    """A fleet's paginated current vehicle list (F-E1)."""

    items: list[FleetVehicleResponse] = Field(
        ..., description="Vehicles currently in the fleet"
    )
    total: int = Field(..., ge=0, description="Total number of member vehicles")
    page: int = Field(..., ge=1, description="Current page")
    page_size: int = Field(
        ..., ge=1, le=settings.API_MAX_PAGE_SIZE, description="Number of items per page"
    )


class FleetMembershipResponse(BaseModel):
    """One fleet-vehicle membership record, open or closed.

    Attributes:
        fleet_vehicle_membership_id: Internal ID of the membership.
        fleet_id: The owning fleet.
        vehicle_id: The member vehicle.
        vehicle_vin: VIN of the member vehicle, enriched by the service.
            `None` only for a historical membership whose vehicle has
            since been soft-deleted and no longer resolves.
        added_at: When the vehicle was added to the fleet.
        removed_at: When it was removed, `None` while still a member.
    """

    fleet_vehicle_membership_id: UUID
    fleet_id: UUID
    vehicle_id: UUID
    vehicle_vin: str | None
    added_at: datetime
    removed_at: datetime | None


class FleetMembershipHistoryResponse(BaseModel):
    """A fleet's paginated membership history, newest first."""

    items: list[FleetMembershipResponse] = Field(
        ..., description="Membership history, newest first"
    )
    total: int = Field(..., ge=0, description="Total number of memberships")
    page: int = Field(..., ge=1, description="Current page")
    page_size: int = Field(
        ..., ge=1, le=settings.API_MAX_PAGE_SIZE, description="Number of items per page"
    )


def _describe_ring_problem(ring: Sequence[tuple[float, float]]) -> str | None:
    """Explain why a linear ring cannot be a geofence boundary, if it can't.

    Args:
        ring: ``(longitude, latitude)`` positions, already range-checked.

    Returns:
        A human-readable reason, or ``None`` when the ring has at least
        ``GEOFENCE_MIN_RING_POSITIONS`` positions, is closed (the last
        position equals the first) and encloses an area without crossing
        itself.
    """
    if len(ring) < GEOFENCE_MIN_RING_POSITIONS:
        return (
            f"a polygon ring needs at least {GEOFENCE_MIN_RING_POSITIONS} "
            "positions (three corners plus the closing position)"
        )
    if tuple(ring[0]) != tuple(ring[-1]):
        return "the polygon ring must be closed: the last position must equal the first"
    if not Polygon(ring).is_valid:
        return "the polygon ring must enclose an area and must not cross itself"
    return None


class GeofencePolygonGeoJson(BaseModel):
    """A geofence boundary as a GeoJSON ``Polygon`` with a single ring (F-A5).

    Used both in requests and responses. A geography polygon covers the
    smaller of the two areas its ring bounds, so the ring may run either
    way; the stored order is returned unchanged.

    Attributes:
        type: Always ``"Polygon"``.
        coordinates: Exactly one closed linear ring of ``[longitude,
            latitude]`` positions.
    """

    type: Literal["Polygon"] = Field(default="Polygon", description="GeoJSON type")
    coordinates: list[list[GeofencePosition]] = Field(
        ...,
        min_length=1,
        max_length=1,
        description=(
            "Exactly one closed linear ring of [longitude, latitude] positions "
            "(at least 4, the last equal to the first); holes are not supported"
        ),
    )

    @model_validator(mode="after")
    def _require_valid_ring(self) -> "GeofencePolygonGeoJson":
        """Reject a ring that is too short, not closed, or self-crossing.

        Returns:
            The validated polygon.

        Raises:
            ValueError: With the reason from ``_describe_ring_problem``.
        """
        ring_problem = _describe_ring_problem(self.coordinates[0])
        if ring_problem is not None:
            raise ValueError(ring_problem)
        return self


class GeofenceCreateRequest(BaseModel):
    """HTTP request data for creating a fleet geofence (F-A5)."""

    name: str = Field(..., min_length=1, max_length=100, description="Geofence name")
    boundary: GeofencePolygonGeoJson = Field(..., description="The geofence area")


class GeofenceUpdateRequest(BaseModel):
    """HTTP request data for partially updating a geofence (F-A5).

    A field that is not sent, or sent as ``null``, is left unchanged.
    """

    name: str | None = Field(
        None, min_length=1, max_length=100, description="Geofence name"
    )
    boundary: GeofencePolygonGeoJson | None = Field(
        None, description="The new geofence area"
    )


class GeofenceResponse(BaseModel):
    """A fleet geofence returned via the HTTP API (F-A5).

    Attributes:
        geofence_id: Internal ID of the geofence.
        fleet_id: The fleet whose member vehicles the area applies to.
        name: Geofence name, shown in alerts.
        boundary: The area, in the same GeoJSON shape as the request.
        created_at: Creation time.
        updated_at: Last update time.
    """

    geofence_id: UUID
    fleet_id: UUID
    name: str
    boundary: GeofencePolygonGeoJson
    created_at: datetime
    updated_at: datetime


class GeofenceListResponse(BaseModel):
    """A fleet's paginated live geofences, newest first (F-A5)."""

    items: list[GeofenceResponse] = Field(..., description="Geofences, newest first")
    total: int = Field(..., ge=0, description="Total number of live geofences")
    page: int = Field(..., ge=1, description="Current page")
    page_size: int = Field(
        ..., ge=1, le=settings.API_MAX_PAGE_SIZE, description="Number of items per page"
    )
