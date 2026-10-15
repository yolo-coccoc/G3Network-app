"""Public service of the charging_stations domain: what other domains call.

The domain's logic lives in area modules (``location_service``,
``station_service``, ``search_service``, ``evse_service``,
``command_service``, with the shared ``access`` and ``mappers``; CV-20,
backend-coding-conventions.md §7.4); the router calls them directly. This
module is the only one another domain may import. It holds the lookups other
domains need (a charger's or location's owner, the scan checks and the
session place of the QR charge) and thin wrappers for the three area
functions called from outside: ``get_charging_station`` (billing),
``find_nearest_operational_station`` (telemetry) and
``queue_station_command`` (the QR charge flow). The OCPP gateway's own writes
live in the internal ``ocpp_state_service.py``.

Access (ACC-15, CS-10, CS-13): a function that takes the caller's
`Principal` answers "not found" for anything outside their reach (see
``access.py``). Functions run inside the caller's transaction and never
commit or roll back.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_stations.access as station_access
import app.domains.charging_stations.command_service as command_service
import app.domains.charging_stations.mappers as station_mappers
import app.domains.charging_stations.repository as charging_stations_repository
import app.domains.charging_stations.search_service as search_service
import app.domains.charging_stations.station_service as station_service
from app.domains.charging_stations.exceptions import (
    ChargingLocationAccessDeniedError,
    ChargingStationNotFoundError,
    ChargingStationOfflineError,
    ChargingStationUnavailableError,
)
from app.domains.charging_stations.schemas import (
    ChargingStationResponse,
)
from app.domains.charging_stations.types import (
    ChargingResourceStatus,
    LocationOwnerReference,
    NearestChargingStationReference,
    ScanTargetReference,
    SessionPlaceReference,
    StationCommandReference,
    StationCommandType,
    StationLocationReference,
)
from app.domains.identity.types import Principal
from app.libs.common.clock import utc_now


async def resolve_station_owner_organization_id(
    db: AsyncSession, station_id: UUID
) -> UUID | None:
    """Tell which organization owns a charger now (public, for warranties).

    A warranty has no organization of its own (DM-24): it follows its object.
    A charger reads its owner through its location (CS-10).

    Args:
        db: Async session owned by the caller's entry boundary.
        station_id: UUID of the charger.

    Returns:
        The owning organization of the charger's location, or `None` when the
        charger does not exist or was soft-deleted.

    Side Effects:
        Read-only queries; does not commit or roll back.
    """
    station = await charging_stations_repository.get_station_by_id(db, station_id)
    if station is None:
        return None
    location = await charging_stations_repository.get_location_by_id(
        db, station.location_id, include_deleted=True
    )
    return location.organization_id if location is not None else None


async def resolve_station_location_reference(
    db: AsyncSession, station_id: UUID
) -> StationLocationReference | None:
    """Find a charger's location and that location's owner (public, billing).

    Billing resolves the tariff in force for a charger from its location and
    the location's owner (BL-08). A soft-deleted location still answers, so a
    session already started keeps a price.

    Args:
        db: Async session owned by the caller's entry boundary.
        station_id: UUID of the charger.

    Returns:
        The reference, or `None` when the charger does not exist or was
        soft-deleted.

    Side Effects:
        Read-only queries; does not commit or roll back.
    """
    station = await charging_stations_repository.get_station_by_id(db, station_id)
    if station is None:
        return None
    location = await charging_stations_repository.get_location_by_id(
        db, station.location_id, include_deleted=True
    )
    if location is None:
        return None
    return StationLocationReference(
        station_id=station.station_id,
        location_id=location.location_id,
        organization_id=location.organization_id,
    )


async def resolve_location_owner_reference(
    db: AsyncSession, location_id: UUID
) -> LocationOwnerReference | None:
    """Find a charging location and its owning organization (public, billing).

    Args:
        db: Async session owned by the caller's entry boundary.
        location_id: UUID of the location.

    Returns:
        The reference, or `None` when the location does not exist or was
        soft-deleted.

    Side Effects:
        Read-only query; does not commit or roll back.
    """
    location = await charging_stations_repository.get_location_by_id(db, location_id)
    if location is None:
        return None
    return LocationOwnerReference(
        location_id=location.location_id,
        organization_id=location.organization_id,
        display_name=location.display_name,
    )


async def resolve_scan_target(
    db: AsyncSession,
    *,
    charger_code: str,
    gun_number: int | None,
    principal: Principal,
) -> ScanTargetReference:
    """Find and check the charger a driver scanned, before a charge starts (CHG-01).

    The QR content is vendor-open (CO-14), so the code is matched against the
    charger's OCPP identity first and its registered serial number second. The
    checks run in this order, so the caller sees the first problem:

    1. the charger exists and was not deleted (not found);
    2. its location is visible to the caller: public, owned by the caller's
       organization, or granted to it (CS-10, CS-13); otherwise a refusal;
    3. the charger and its location are ``ACTIVE`` and, when a gun is named,
       the gun's EVSE is ``ACTIVE`` and has a plug (unavailable);
    4. the charger is connected now (offline).

    Args:
        db: Async session owned by the HTTP boundary.
        charger_code: What the QR carries: the charger's OCPP identity (or its
            registered serial number).
        gun_number: The gun named by the scan (its OCPP EVSE number), ``None``
            when the driver picks the gun on the charger's screen.
        principal: The caller whose visibility applies.

    Returns:
        The charger, its location and, when named, the gun and its plug.

    Raises:
        ChargingStationNotFoundError: No such charger, or the gun does not
            exist on it.
        ChargingLocationAccessDeniedError: The location is private and not
            granted to the caller's organization.
        ChargingStationUnavailableError: The charger, its location or the gun
            is out of service.
        ChargingStationOfflineError: The charger is not connected now.

    Side Effects:
        Read-only queries; does not commit or roll back.
    """
    code = charger_code.strip()
    station = await charging_stations_repository.get_station_by_identity(
        db, code, include_deleted=False
    )
    if station is None:
        station = (
            await charging_stations_repository.find_live_station_by_registered_serial(
                db, code
            )
        )
    if station is None:
        raise ChargingStationNotFoundError("No charger matches this code")
    location = await charging_stations_repository.get_location_by_id(
        db, station.location_id
    )
    if location is None:
        raise ChargingStationNotFoundError("No charger matches this code")
    if not await station_access.can_reach_location(db, location, principal, view=True):
        raise ChargingLocationAccessDeniedError(
            "This charger is at a private location your organization may not use"
        )
    if (
        station.status != ChargingResourceStatus.ACTIVE.value
        or location.status != ChargingResourceStatus.ACTIVE.value
    ):
        raise ChargingStationUnavailableError("This charger is out of service")
    state = await charging_stations_repository.get_station_state(db, station.station_id)
    if not station_mappers.is_station_online(state, utc_now()):
        raise ChargingStationOfflineError("This charger is not connected right now")
    evse_id: UUID | None = None
    connector_id: UUID | None = None
    if gun_number is not None:
        evse = await charging_stations_repository.get_evse_by_identity(
            db, station.station_id, gun_number, include_deleted=False
        )
        if evse is None:
            raise ChargingStationNotFoundError(
                f"The charger has no gun number {gun_number}"
            )
        if evse.status != ChargingResourceStatus.ACTIVE.value:
            raise ChargingStationUnavailableError(
                f"Gun number {gun_number} is out of service"
            )
        plugs = await charging_stations_repository.list_charging_connectors(
            db, evse_id=evse.evse_id, offset=0, limit=1
        )
        if not plugs:
            raise ChargingStationUnavailableError(
                f"Gun number {gun_number} has no plug set up"
            )
        evse_id = evse.evse_id
        connector_id = plugs[0].connector_id
    connector_ids = tuple(
        plug.connector_id
        for plug, _evse_number, _state in (
            await charging_stations_repository.list_connectors_by_station_id(
                db, station.station_id
            )
        )
    )
    return ScanTargetReference(
        station_id=station.station_id,
        location_id=location.location_id,
        location_name=location.display_name,
        evse_id=evse_id,
        connector_id=connector_id,
        gun_number=gun_number,
        connector_ids=connector_ids,
    )


async def resolve_session_place_reference(
    db: AsyncSession, station_id: UUID, evse_id: UUID | None
) -> SessionPlaceReference | None:
    """Describe where a session happened, for its receipt (CHG-03). Cross-domain.

    The caller has already checked that the session is in the caller's reach,
    so nothing is filtered here.

    Args:
        db: Async session owned by the caller's entry boundary.
        station_id: The session's charger.
        evse_id: The session's gun, ``None`` when it never started.

    Returns:
        The place, or ``None`` when the charger was soft-deleted since.

    Side Effects:
        Read-only queries; does not commit or roll back.
    """
    station = await charging_stations_repository.get_station_by_id(db, station_id)
    if station is None:
        return None
    location = await charging_stations_repository.get_location_by_id(
        db, station.location_id, include_deleted=True
    )
    if location is None:
        return None
    gun_number: int | None = None
    connector_standard: str | None = None
    if evse_id is not None:
        evse = await charging_stations_repository.get_evse_by_id(db, evse_id)
        if evse is not None:
            gun_number = evse.ocpp_evse_id
            plugs = await charging_stations_repository.list_charging_connectors(
                db, evse_id=evse.evse_id, offset=0, limit=1
            )
            connector_standard = plugs[0].standard if plugs else None
    return SessionPlaceReference(
        station_id=station.station_id,
        location_id=location.location_id,
        location_name=location.display_name,
        location_address=location.address,
        physical_reference=station.physical_reference,
        gun_number=gun_number,
        connector_standard=connector_standard,
    )


async def get_charging_station(
    db: AsyncSession, station_id: UUID, *, principal: Principal
) -> ChargingStationResponse:
    """Get an active station the caller may see (billing checks a price read, PAY-09).

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: UUID of the station to query.
        principal: The caller; a charger at a private location is visible to
            its owner, to organizations with a live grant and to internal
            staff.

    Returns:
        The active station response.

    Raises:
        ChargingStationNotFoundError: If the station does not exist, was
            soft-deleted or is out of the caller's reach.
    """
    return await station_service.get_charging_station(
        db, station_id, principal=principal
    )


async def find_nearest_operational_station(
    db: AsyncSession, *, latitude: float, longitude: float
) -> NearestChargingStationReference | None:
    """Find the nearest available public station to a point (F-A2, telemetry alerts).

    Args:
        db: Async session owned by the caller's entry boundary.
        latitude: GPS latitude in decimal degrees of the query point.
        longitude: GPS longitude in decimal degrees of the query point.

    Returns:
        A reference to the nearest qualifying station, or ``None`` if none
        qualifies (rules in ``search_service``).
    """
    return await search_service.find_nearest_operational_station(
        db, latitude=latitude, longitude=longitude
    )


async def queue_station_command(
    db: AsyncSession,
    *,
    station_id: UUID,
    command_type: StationCommandType,
    evse_id: UUID | None = None,
    session_id: UUID | None = None,
    parameters: dict[str, object] | None = None,
    requested_by: UUID | None = None,
    reason: str | None = None,
) -> StationCommandReference:
    """Queue a command for a charger; the OCPP gateway sends it (PR-16, the QR charge).

    Args:
        db: Async session owned by the caller's entry boundary.
        station_id: The charger the command goes to.
        command_type: What to ask.
        evse_id: The gun it targets, ``None`` for the whole charger.
        session_id: The session a remote start begins or a remote stop ends.
        parameters: What to send besides the links; validated per type.
        requested_by: User who asked, ``None`` when the system sends it.
        reason: Why, typed by the operator for a manual command.

    Returns:
        A reference to the queued command.

    Raises:
        ChargingStationNotFoundError: If the charger is not active.
        ChargingStationCommandInputError: If a link or parameter the type
            needs is missing, the EVSE is not the charger's, or a referenced
            row does not exist.
    """
    return await command_service.queue_station_command(
        db,
        station_id=station_id,
        command_type=command_type,
        evse_id=evse_id,
        session_id=session_id,
        parameters=parameters,
        requested_by=requested_by,
        reason=reason,
    )
