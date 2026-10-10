"""Public service of the charging_stations domain.

Holds the location CRUD with soft-delete and the access grants of private
locations (CS-09, CS-10, CS-13), charger / EVSE / connector CRUD with
soft-delete (F-C1), the directory reads (charger list/detail with the derived
``is_online`` and ``available_connector_count``), the station status view
(whole charger plus every gun, F-C2), the nearest-available-station lookup other
domains call (F-A2, used by ``telemetry``), the driver-facing nearby search
(F-D1), the all-stations energy report (F-C5, which asks ``charging_sessions``
for each station's total), the command channel the API (and ``charging_sessions``
for a remote start) writes to (CS-20, PR-16), and the read of the latest
configuration a charger reported. This is the only module another domain may
import; the OCPP gateway's own writes live in the internal
``ocpp_state_service.py``.

"Available" means: the charger is ``ACTIVE`` and not deleted, stands at an
``ACTIVE`` location the caller may see and has at least one connector whose
last reported status is ``Available``; ``is_online`` is not required.

Access (ACC-15, CS-10, CS-13): every HTTP-facing function takes the caller's
`Principal`. A location, and the chargers, EVSEs and guns below it, can be
*managed* (changed, commanded, granted) only by the owner organization and by
internal staff, and *viewed* also when the location is public or the caller's
organization holds a live grant on it. Anything else answers "not found". The
system callers (`find_nearest_operational_station` for an alert) see public
locations only.

Pre-provisioning invariants are enforced here: a location must exist before a
charger, a charger before an EVSE, an EVSE before a connector, and topology
identities are never reused, even after the old record was soft-deleted.
Functions run inside the caller's transaction (FastAPI's ``get_db`` for HTTP,
the ingestion worker's for ``find_nearest_operational_station``) and never
commit or roll back.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.service as charging_sessions_service
import app.domains.charging_stations.ocpp_state_repository as ocpp_state_repository
import app.domains.charging_stations.repository as charging_stations_repository
import app.domains.identity.service as identity_service
from app.domains.charging_stations.exceptions import (
    ChargingConnectorNotFoundError,
    ChargingEvseNotFoundError,
    ChargingLocationAccessConflictError,
    ChargingLocationAccessNotFoundError,
    ChargingLocationNotFoundError,
    ChargingStationCommandInputError,
    ChargingStationNotFoundError,
    ChargingStationReportRangeError,
    ChargingTopologyConflictError,
)
from app.domains.charging_stations.models import (
    ChargingConnectorModel,
    ChargingConnectorStateModel,
    ChargingEvseModel,
    ChargingLocationAccessModel,
    ChargingLocationModel,
    ChargingStationCommandModel,
    ChargingStationModel,
    ChargingStationStateModel,
)
from app.domains.charging_stations.schemas import (
    ChargingConnectorCreateRequest,
    ChargingConnectorListResponse,
    ChargingConnectorResponse,
    ChargingConnectorUpdateRequest,
    ChargingEvseCreateRequest,
    ChargingEvseListResponse,
    ChargingEvseResponse,
    ChargingEvseUpdateRequest,
    ChargingLocationAccessCreateRequest,
    ChargingLocationAccessListResponse,
    ChargingLocationAccessResponse,
    ChargingLocationAccessRevokeRequest,
    ChargingLocationCreateRequest,
    ChargingLocationListResponse,
    ChargingLocationResponse,
    ChargingLocationUpdateRequest,
    ChargingResourceDeleteResponse,
    ChargingStationCommandCreateRequest,
    ChargingStationCommandListResponse,
    ChargingStationCommandResponse,
    ChargingStationConfigurationEntryResponse,
    ChargingStationConfigurationResponse,
    ChargingStationConnectorStatusResponse,
    ChargingStationCreateRequest,
    ChargingStationEnergyTotalListResponse,
    ChargingStationEnergyTotalResponse,
    ChargingStationListResponse,
    ChargingStationResponse,
    ChargingStationStatusResponse,
    ChargingStationUpdateRequest,
    NearbyChargingStationListResponse,
    NearbyChargingStationResponse,
)
from app.domains.charging_stations.types import (
    ChargingConnectorStatus,
    ChargingResourceStatus,
    ConnectorStandard,
    LocationViewer,
    NearestChargingStationReference,
    StationCommandOutcome,
    StationCommandReference,
    StationCommandType,
)
from app.domains.identity.exceptions import OrganizationNotFoundError
from app.domains.identity.types import Principal
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.geo import coordinates_to_location, location_to_coordinates
from app.libs.common.pagination import normalize_page_window


def _viewer_of(principal: Principal) -> LocationViewer:
    """Build the location visibility rule of a caller (CS-10, CS-13).

    Args:
        principal: The caller.

    Returns:
        The caller's organization, and whether they see every location.
    """
    return LocationViewer(
        organization_id=principal.organization_id, sees_all=principal.is_internal
    )


async def _can_reach_location(
    db: AsyncSession,
    location: ChargingLocationModel,
    principal: Principal,
    *,
    view: bool,
) -> bool:
    """Apply the access rule to one location.

    Args:
        db: Async session owned by the HTTP boundary.
        location: The location.
        principal: The caller.
        view: `True` for a read a driver may do (a public location, or one the
            caller's organization has a live grant on, also counts); `False`
            for managing it (owner organization and internal staff only).

    Returns:
        Whether the caller may act on the location.
    """
    if principal.can_access_organization(location.organization_id):
        return True
    if not view:
        return False
    if location.is_public:
        return True
    grant = await charging_stations_repository.get_live_location_access(
        db, location.location_id, principal.organization_id
    )
    return grant is not None


async def _get_location_in_reach(
    db: AsyncSession, location_id: UUID, principal: Principal, *, view: bool = False
) -> ChargingLocationModel:
    """Load an active location the caller may act on, or raise "not found".

    Args:
        db: Async session owned by the HTTP boundary.
        location_id: UUID of the location.
        principal: The caller.
        view: See `_can_reach_location`.

    Returns:
        The location.

    Raises:
        ChargingLocationNotFoundError: It does not exist, was deleted or is out
            of the caller's reach.
    """
    location = await charging_stations_repository.get_location_by_id(db, location_id)
    if location is None or not await _can_reach_location(
        db, location, principal, view=view
    ):
        raise ChargingLocationNotFoundError(f"Location '{location_id}' not found")
    return location


async def _get_station_in_reach(
    db: AsyncSession, station_id: UUID, principal: Principal, *, view: bool = False
) -> ChargingStationModel:
    """Load an active charger the caller may act on, or raise "not found".

    A charger reads its owner through its location (CS-10).

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: UUID of the charger.
        principal: The caller.
        view: See `_can_reach_location`.

    Returns:
        The charger.

    Raises:
        ChargingStationNotFoundError: It does not exist, was deleted or is out
            of the caller's reach.
    """
    station = await charging_stations_repository.get_station_by_id(db, station_id)
    if station is not None:
        location = await charging_stations_repository.get_location_by_id(
            db, station.location_id, include_deleted=True
        )
        if location is not None and await _can_reach_location(
            db, location, principal, view=view
        ):
            return station
    raise ChargingStationNotFoundError(f"Station '{station_id}' not found")


async def _get_evse_in_reach(
    db: AsyncSession, evse_id: UUID, principal: Principal, *, view: bool = False
) -> ChargingEvseModel:
    """Load an active EVSE whose charger the caller may act on, or raise.

    Args:
        db: Async session owned by the HTTP boundary.
        evse_id: UUID of the EVSE.
        principal: The caller.
        view: See `_can_reach_location`.

    Returns:
        The EVSE.

    Raises:
        ChargingEvseNotFoundError: It does not exist, was deleted or is out of
            the caller's reach.
    """
    evse = await charging_stations_repository.get_evse_by_id(db, evse_id)
    if evse is not None:
        try:
            await _get_station_in_reach(db, evse.station_id, principal, view=view)
        except ChargingStationNotFoundError:
            pass
        else:
            return evse
    raise ChargingEvseNotFoundError(f"EVSE '{evse_id}' not found")


async def _get_connector_in_reach(
    db: AsyncSession, connector_id: UUID, principal: Principal, *, view: bool = False
) -> ChargingConnectorModel:
    """Load an active gun whose charger the caller may act on, or raise.

    Args:
        db: Async session owned by the HTTP boundary.
        connector_id: UUID of the gun.
        principal: The caller.
        view: See `_can_reach_location`.

    Returns:
        The connector.

    Raises:
        ChargingConnectorNotFoundError: It does not exist, was deleted or is
            out of the caller's reach.
    """
    connector = await charging_stations_repository.get_connector_by_id(db, connector_id)
    if connector is not None:
        try:
            await _get_evse_in_reach(db, connector.evse_id, principal, view=view)
        except ChargingEvseNotFoundError:
            pass
        else:
            return connector
    raise ChargingConnectorNotFoundError(f"Connector '{connector_id}' not found")


# Short names a driver app or portal may send for a plug standard (CS-17); the
# stored value is always the OCPI name.
_CONNECTOR_STANDARD_ALIASES = {
    "CCS2": ConnectorStandard.IEC_62196_T2_COMBO,
    "CCS1": ConnectorStandard.IEC_62196_T1_COMBO,
    "GBT": ConnectorStandard.GBT_DC,
    "GB/T": ConnectorStandard.GBT_DC,
}


def _to_optional_float(value: Decimal | None) -> float | None:
    """Convert a nullable ``Numeric`` column value into the float the API uses.

    Args:
        value: The stored decimal (e.g. ``max_power_kw``), or ``None``.

    Returns:
        ``float(value)``, or ``None`` when no value is stored.
    """
    return float(value) if value is not None else None


def _is_station_online(
    state: ChargingStationStateModel | None, checked_at: datetime
) -> bool:
    """Derive whether a charger is online at a given time.

    Args:
        state: The charger's state row carrying ``last_seen_at``, or ``None``.
        checked_at: Reference time of the check.

    Returns:
        ``True`` if the latest frame of any kind arrived within
        ``CHARGING_OFFLINE_TIMEOUT_SECONDS`` of ``checked_at``; ``False`` for
        a station that has never connected.
    """
    return (
        state is not None
        and state.last_seen_at is not None
        and (checked_at - state.last_seen_at)
        <= timedelta(seconds=settings.CHARGING_OFFLINE_TIMEOUT_SECONDS)
    )


def normalize_connector_standard(value: str) -> str:
    """Turn a plug standard sent by a client into the stored OCPI name.

    Args:
        value: An OCPI name (``IEC_62196_T2_COMBO``) or a short name
            (``CCS2``, ``GBT``), in any case.

    Returns:
        The OCPI name when ``value`` is a known short name or OCPI name;
        otherwise ``value`` unchanged (it then matches no connector).
    """
    key = value.strip().upper()
    alias = _CONNECTOR_STANDARD_ALIASES.get(key)
    return alias.value if alias is not None else key


def to_charging_location_response(
    location: ChargingLocationModel,
) -> ChargingLocationResponse:
    """Build a location response from the ORM model.

    Args:
        location: Location ORM object queried or created by the repository.

    Returns:
        The response schema, with the map pin split into latitude and longitude.
    """
    latitude, longitude = location_to_coordinates(location.coordinates)
    return ChargingLocationResponse(
        location_id=location.location_id,
        organization_id=location.organization_id,
        display_name=location.display_name,
        address=location.address,
        latitude=latitude,
        longitude=longitude,
        is_public=location.is_public,
        status=ChargingResourceStatus(location.status),
        status_reason=location.status_reason,
        created_at=location.created_at,
        updated_at=location.updated_at,
        deleted_at=location.deleted_at,
    )


def to_charging_station_response(
    station: ChargingStationModel,
    location: ChargingLocationModel,
    state: ChargingStationStateModel | None,
    *,
    connector_count: int,
    available_connector_count: int,
    now: datetime | None = None,
) -> ChargingStationResponse:
    """Build a station response from the profile, its location and its state.

    Pure mapping only - the connector counts are computed by the caller (via
    repository queries) rather than here, since a pure mapper must never do
    I/O. The only outside input is the clock, needed to derive ``is_online``;
    it can be injected through ``now`` (tests do).

    Args:
        station: Station ORM object queried or created by the repository.
        location: The location the station stands at.
        state: The station's state row, or ``None`` (then nothing is reported).
        connector_count: Number of active connectors across the station's
            active EVSEs, already computed by the caller.
        available_connector_count: How many of them last reported
            ``Available``, already computed by the caller.
        now: Reference time for the online check; defaults to the current
            UTC time.

    Returns:
        Response schema including the profile, the owner read through the
        location, connector counts, the charger's reported info, and
        ``is_online`` (``last_seen_at`` within
        ``CHARGING_OFFLINE_TIMEOUT_SECONDS``; ``False`` if never seen).
    """
    checked_at = now if now is not None else utc_now()
    latitude, longitude = location_to_coordinates(location.coordinates)
    charger_status = (
        ChargingConnectorStatus(state.charger_status)
        if state is not None and state.charger_status is not None
        else None
    )
    return ChargingStationResponse(
        station_id=station.station_id,
        location_id=station.location_id,
        organization_id=location.organization_id,
        location_display_name=location.display_name,
        latitude=latitude,
        longitude=longitude,
        ocpp_identity=station.ocpp_identity,
        registered_serial_number=station.registered_serial_number,
        physical_reference=station.physical_reference,
        max_power_kw=_to_optional_float(station.max_power_kw),
        status=ChargingResourceStatus(station.status),
        status_reason=station.status_reason,
        connector_count=connector_count,
        available_connector_count=available_connector_count,
        ocpp_protocol_version=state.ocpp_protocol_version if state else None,
        vendor=state.vendor if state else None,
        model=state.model if state else None,
        serial_number=state.serial_number if state else None,
        firmware_version=state.firmware_version if state else None,
        last_boot_at=state.last_boot_at if state else None,
        last_seen_at=state.last_seen_at if state else None,
        charger_status=charger_status,
        charger_status_updated_at=(state.charger_status_updated_at if state else None),
        charger_error_code=state.charger_error_code if state else None,
        charger_vendor_error_code=state.charger_vendor_error_code if state else None,
        is_online=_is_station_online(state, checked_at),
        created_at=station.created_at,
        updated_at=station.updated_at,
        deleted_at=station.deleted_at,
    )


def to_charging_evse_response(evse: ChargingEvseModel) -> ChargingEvseResponse:
    """Build an EVSE response from the ORM model.

    Args:
        evse: EVSE ORM object queried or created by the repository.

    Returns:
        Response schema corresponding to the EVSE.
    """
    return ChargingEvseResponse.model_validate(evse)


def to_charging_connector_response(
    connector: ChargingConnectorModel,
    state: ChargingConnectorStateModel | None,
) -> ChargingConnectorResponse:
    """Build a connector response from the profile and the reported state.

    Args:
        connector: Connector ORM object queried or created by the
            repository.
        state: The connector's state row, or ``None`` (then no report).

    Returns:
        Response schema corresponding to the connector.
    """
    reported_status = (
        ChargingConnectorStatus(state.status)
        if state is not None and state.status is not None
        else None
    )
    return ChargingConnectorResponse(
        connector_id=connector.connector_id,
        evse_id=connector.evse_id,
        ocpp_connector_id=connector.ocpp_connector_id,
        standard=ConnectorStandard(connector.standard),
        max_power_kw=float(connector.max_power_kw),
        max_voltage_v=connector.max_voltage_v,
        max_current_a=connector.max_current_a,
        status=reported_status,
        status_updated_at=state.status_updated_at if state else None,
        error_code=state.error_code if state else None,
        vendor_error_code=state.vendor_error_code if state else None,
        status_info=state.status_info if state else None,
        created_at=connector.created_at,
        updated_at=connector.updated_at,
        deleted_at=connector.deleted_at,
    )


async def _build_charging_station_response(
    db: AsyncSession, station: ChargingStationModel
) -> ChargingStationResponse:
    """Load a station's location and state, count its connectors, build its response.

    Args:
        db: Async session owned by the caller's entry boundary.
        station: Station ORM object already loaded by the caller.

    Returns:
        The station response with ``connector_count`` and
        ``available_connector_count`` filled in.

    Raises:
        ChargingLocationNotFoundError: If the station's location is missing
            (cannot happen under the foreign key).

    Side Effects:
        Runs the location and state reads plus two count queries; does not
        commit or roll back.
    """
    location = await charging_stations_repository.get_location_by_id(
        db, station.location_id, include_deleted=True
    )
    if location is None:
        raise ChargingLocationNotFoundError(
            f"Location '{station.location_id}' not found"
        )
    state = await charging_stations_repository.get_station_state(db, station.station_id)
    connector_count = await charging_stations_repository.count_connectors_by_station_id(
        db, station.station_id
    )
    available_connector_count = (
        await charging_stations_repository.count_available_connectors_by_station_id(
            db, station.station_id
        )
    )
    return to_charging_station_response(
        station,
        location,
        state,
        connector_count=connector_count,
        available_connector_count=available_connector_count,
    )


async def _build_charging_connector_response(
    db: AsyncSession, connector: ChargingConnectorModel
) -> ChargingConnectorResponse:
    """Load a connector's reported state and build its response.

    Args:
        db: Async session owned by the caller's entry boundary.
        connector: Connector ORM object already loaded by the caller.

    Returns:
        The connector response.
    """
    state = await charging_stations_repository.get_connector_state(
        db, connector.connector_id
    )
    return to_charging_connector_response(connector, state)


def _clean_update_values(requested_fields: Mapping[str, object]) -> dict[str, object]:
    """Drop ``None`` fields per the backend's PATCH convention.

    Args:
        requested_fields: Mapping from ``model_dump(exclude_unset=True)``.

    Returns:
        Mapping containing only the fields with values to update.

    Note:
        This domain has no dedicated contract yet for clearing a nullable
        value with ``null``; therefore ``None`` is treated as "do not
        update".
    """
    return {
        field_name: value
        for field_name, value in requested_fields.items()
        if value is not None
    }


def _status_update_values(update_data: dict[str, object], default_reason: str) -> str:
    """Normalize a status change in ``update_data`` and pick the change reason.

    A status of ``ACTIVE`` clears the reason (it is only kept while out of
    service); a person-typed ``status_reason`` becomes the history reason.

    Args:
        update_data: The cleaned PATCH values; changed in place (the status
            becomes its string value, and ``status_reason`` is cleared on
            ``ACTIVE``).
        default_reason: History reason when the person typed none.

    Returns:
        The reason to record in the change history.
    """
    status = update_data.get("status")
    if isinstance(status, ChargingResourceStatus):
        update_data["status"] = status.value
        if status is ChargingResourceStatus.ACTIVE:
            update_data["status_reason"] = None
    reason = update_data.get("status_reason")
    return reason if isinstance(reason, str) else default_reason


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
    return to_charging_location_response(location)


async def list_charging_locations(
    db: AsyncSession,
    *,
    principal: Principal,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> ChargingLocationListResponse:
    """List the active locations the caller owns (all of them for internal staff).

    Args:
        db: Async session owned by the HTTP boundary.
        principal: The caller (data scope).
        page: Page number starting at one.
        page_size: Page size, clamped according to settings.

    Returns:
        The locations and pagination metadata.
    """
    page_window = normalize_page_window(page, page_size)
    locations = await charging_stations_repository.list_charging_locations(
        db,
        offset=page_window.offset,
        limit=page_window.page_size,
        organization_id=principal.data_scope,
    )
    total = await charging_stations_repository.count_locations(
        db, organization_id=principal.data_scope
    )
    return ChargingLocationListResponse(
        items=[to_charging_location_response(location) for location in locations],
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
    location = await _get_location_in_reach(db, location_id, principal, view=True)
    return to_charging_location_response(location)


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
    location = await _get_location_in_reach(db, location_id, principal)
    update_data = _clean_update_values(
        location_update_request.model_dump(
            exclude_unset=True, exclude={"latitude", "longitude"}
        )
    )
    if location_update_request.latitude is not None:
        update_data["coordinates"] = coordinates_to_location(
            location_update_request.latitude, location_update_request.longitude
        )
    change_reason = _status_update_values(update_data, "Charging location edited")
    if not update_data:
        return to_charging_location_response(location)
    updated = await charging_stations_repository.update_charging_location(
        db,
        location_id,
        update_data,
        change_reason=change_reason,
        changed_by=principal.user_id,
    )
    if updated is None:
        raise ChargingLocationNotFoundError(f"Location '{location_id}' not found")
    return to_charging_location_response(updated)


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
    await _get_location_in_reach(db, location_id, principal)
    if not await charging_stations_repository.soft_delete_location(
        db, location_id, status_reason=status_reason, changed_by=principal.user_id
    ):
        raise ChargingLocationNotFoundError(f"Location '{location_id}' not found")
    return ChargingResourceDeleteResponse(message="Charging location soft-deleted")


# --- Access grants -----------------------------------------------------------


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
    location = await _get_location_in_reach(db, location_id, principal)
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
    await _get_location_in_reach(db, location_id, principal)
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
    await _get_location_in_reach(db, location_id, principal)
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


# --- Stations ----------------------------------------------------------------


async def create_charging_station(
    db: AsyncSession,
    station_create_request: ChargingStationCreateRequest,
    *,
    principal: Principal,
) -> ChargingStationResponse:
    """Create a new charger at a location after checking its unique values.

    Args:
        db: Async session owned by the HTTP boundary.
        station_create_request: Station data, already Pydantic-validated.
        principal: The caller; the location must be one they may manage.

    Returns:
        The newly created station response.

    Raises:
        ChargingLocationNotFoundError: If the location is not active or is
            out of the caller's reach.
        ChargingTopologyConflictError: If the identity already exists, even
            if soft-deleted, or the registered serial is used by a charger
            not deleted.
    """
    await _get_location_in_reach(db, station_create_request.location_id, principal)
    if await charging_stations_repository.get_station_by_identity(
        db, station_create_request.ocpp_identity
    ):
        raise ChargingTopologyConflictError(
            f"OCPP identity '{station_create_request.ocpp_identity}' already exists"
        )
    if await charging_stations_repository.find_live_station_by_registered_serial(
        db, station_create_request.registered_serial_number
    ):
        raise ChargingTopologyConflictError(
            "Registered serial number "
            f"'{station_create_request.registered_serial_number}' already exists"
        )
    max_power_kw = (
        Decimal(str(station_create_request.max_power_kw))
        if station_create_request.max_power_kw is not None
        else None
    )
    try:
        station = await charging_stations_repository.create_charging_station(
            db,
            location_id=station_create_request.location_id,
            ocpp_identity=station_create_request.ocpp_identity,
            registered_serial_number=station_create_request.registered_serial_number,
            physical_reference=station_create_request.physical_reference,
            max_power_kw=max_power_kw,
        )
    except IntegrityError as error:
        raise ChargingTopologyConflictError(
            "Station OCPP identity or registered serial already exists"
        ) from error
    # A brand-new station has no EVSEs/connectors yet - no count query needed.
    location = await charging_stations_repository.get_location_by_id(
        db, station.location_id
    )
    assert location is not None, "the location was just checked"
    state = await charging_stations_repository.get_station_state(db, station.station_id)
    return to_charging_station_response(
        station, location, state, connector_count=0, available_connector_count=0
    )


async def list_charging_stations(
    db: AsyncSession,
    *,
    principal: Principal,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> ChargingStationListResponse:
    """List the active stations at the caller's locations (all for internal staff).

    Args:
        db: Async session owned by the HTTP boundary.
        principal: The caller (data scope, through the chargers' locations).
        page: Page number starting at one; lower values are clamped to the
            default.
        page_size: Page size, clamped according to settings.

    Returns:
        List of stations and pagination metadata.

    Side Effects:
        Performs two read queries plus the location, state and two
        connector-count queries per station on the page (no batching yet -
        deliberately deferred until throughput needs it, see
        ``docs/decisions/deferred.md``); does not commit or rollback.
    """
    page_window = normalize_page_window(page, page_size)
    stations = await charging_stations_repository.list_charging_stations(
        db,
        offset=page_window.offset,
        limit=page_window.page_size,
        organization_id=principal.data_scope,
    )
    total = await charging_stations_repository.count_stations(
        db, organization_id=principal.data_scope
    )
    items = [
        await _build_charging_station_response(db, station) for station in stations
    ]
    return ChargingStationListResponse(
        items=items,
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def get_charging_station(
    db: AsyncSession, station_id: UUID, *, principal: Principal
) -> ChargingStationResponse:
    """Get an active station the caller may see by internal UUID.

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: UUID of the station to query.
        principal: The caller; a charger at a private location is visible to
            its owner, to organizations with a live grant and to internal
            staff.

    Returns:
        The active station response.

    Raises:
        ChargingStationNotFoundError: If the station does not exist or was
            soft-deleted.
    """
    station = await _get_station_in_reach(db, station_id, principal, view=True)
    return await _build_charging_station_response(db, station)


async def get_charging_station_status(
    db: AsyncSession, station_id: UUID, *, principal: Principal
) -> ChargingStationStatusResponse:
    """Get the whole charger's status and every gun's status of a station (F-C2).

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: UUID of the station.
        principal: The caller (view access, see the module docstring).

    Returns:
        The whole-charger status fields of the station and one entry per
        active connector, ordered by OCPP EVSE number, then connector number.
        Statuses are returned as last reported, even if the charger is
        offline.

    Raises:
        ChargingStationNotFoundError: If the station does not exist or was
            soft-deleted.

    Side Effects:
        Performs three read queries; does not commit or roll back.
    """
    station = await _get_station_in_reach(db, station_id, principal, view=True)
    state = await charging_stations_repository.get_station_state(db, station_id)
    connectors = await charging_stations_repository.list_connectors_by_station_id(
        db, station_id
    )
    return ChargingStationStatusResponse(
        station_id=station.station_id,
        charger_status=(
            ChargingConnectorStatus(state.charger_status)
            if state is not None and state.charger_status is not None
            else None
        ),
        charger_status_updated_at=state.charger_status_updated_at if state else None,
        charger_error_code=state.charger_error_code if state else None,
        charger_vendor_error_code=state.charger_vendor_error_code if state else None,
        connectors=[
            ChargingStationConnectorStatusResponse(
                connector_id=connector.connector_id,
                evse_id=connector.evse_id,
                ocpp_evse_id=ocpp_evse_id,
                ocpp_connector_id=connector.ocpp_connector_id,
                status=(
                    ChargingConnectorStatus(connector_state.status)
                    if connector_state is not None and connector_state.status
                    else None
                ),
                status_updated_at=(
                    connector_state.status_updated_at if connector_state else None
                ),
                error_code=connector_state.error_code if connector_state else None,
                vendor_error_code=(
                    connector_state.vendor_error_code if connector_state else None
                ),
                status_info=connector_state.status_info if connector_state else None,
            )
            for connector, ocpp_evse_id, connector_state in connectors
        ],
    )


async def find_nearest_operational_station(
    db: AsyncSession, *, latitude: float, longitude: float
) -> NearestChargingStationReference | None:
    """Find the nearest available station to a point. Public entry point for F-A2.

    A station qualifies when the charger is ``ACTIVE`` and not deleted, its
    location is ``ACTIVE``, public and has a position, and at least one
    connector last reported ``Available``. The charger's ``is_online`` is
    deliberately not consulted (D3): a connector's last reported status is the
    availability signal.

    Args:
        db: Async session owned by the caller's entry boundary (e.g. the
            telemetry ingestion worker's transaction).
        latitude: GPS latitude in decimal degrees of the query point.
        longitude: GPS longitude in decimal degrees of the query point.

    Returns:
        A minimal reference DTO for the nearest qualifying station - never the
        ORM model - or ``None`` if none qualifies. Its name is the location's.
    """
    query_point = coordinates_to_location(latitude, longitude)
    assert query_point is not None, "latitude/longitude are both required here"
    match = await charging_stations_repository.find_nearest_station_by_location(
        db, query_point
    )
    if match is None:
        return None
    station, location, distance_meters = match
    station_latitude, station_longitude = location_to_coordinates(location.coordinates)
    assert station_latitude is not None and station_longitude is not None, (
        "a location always has coordinates"
    )
    return NearestChargingStationReference(
        station_id=station.station_id,
        display_name=location.display_name,
        latitude=station_latitude,
        longitude=station_longitude,
        distance_km=distance_meters / 1000,
    )


def to_nearby_charging_station_response(
    station: ChargingStationModel,
    location: ChargingLocationModel,
    state: ChargingStationStateModel | None,
    *,
    connector_count: int,
    available_connector_count: int,
    distance_km: float,
    now: datetime | None = None,
) -> NearbyChargingStationResponse:
    """Build a driver-facing nearby-station response (F-D1).

    Pure mapping only - the connector counts and distance are computed by
    the caller, since a pure mapper must never do I/O. The clock (for
    ``is_online``) can be injected through ``now``.

    Args:
        station: Station ORM object queried by the repository.
        location: The location the station stands at.
        state: The station's state row, or ``None``.
        connector_count: Number of active connectors, already computed by
            the caller.
        available_connector_count: How many of them last reported
            ``Available``, already computed by the caller.
        distance_km: Distance from the query point, already computed by
            the caller.
        now: Reference time for the online check; defaults to the current
            UTC time.

    Returns:
        Driver-facing response schema, without the internal/admin fields
        ``ChargingStationResponse`` carries.
    """
    checked_at = now if now is not None else utc_now()
    latitude, longitude = location_to_coordinates(location.coordinates)
    return NearbyChargingStationResponse(
        station_id=station.station_id,
        location_id=station.location_id,
        display_name=location.display_name,
        physical_reference=station.physical_reference,
        latitude=latitude,
        longitude=longitude,
        max_power_kw=_to_optional_float(station.max_power_kw),
        status=ChargingResourceStatus(station.status),
        connector_count=connector_count,
        available_connector_count=available_connector_count,
        is_online=_is_station_online(state, checked_at),
        distance_km=distance_km,
    )


async def list_nearby_charging_stations(
    db: AsyncSession,
    *,
    principal: Principal,
    latitude: float,
    longitude: float,
    radius_km: float,
    connector_standard: str | None = None,
    min_power_kw: float | None = None,
    is_operational_only: bool = True,
    is_available_only: bool = False,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> NearbyChargingStationListResponse:
    """Find stations within a radius of a point, filtered and paginated (F-D1).

    Generalizes ``find_nearest_operational_station`` (F-A2) from "1
    nearest" to "N within a radius, filtered by plug standard/power,
    nearest first". Only chargers at ``ACTIVE`` locations the caller may see
    are found: public ones, the caller's organization's own, those it holds
    a live grant on, and every location for internal staff (CS-10, CS-13).

    Args:
        db: Async session owned by the HTTP boundary.
        principal: The caller (decides which private locations show).
        latitude: GPS latitude in decimal degrees of the query point.
        longitude: GPS longitude in decimal degrees of the query point.
        radius_km: Search radius in km; clamped to
            ``(0, settings.CHARGING_STATIONS_NEARBY_MAX_RADIUS_KM]``.
        connector_standard: Plug standard (OCPI name or a short name such as
            ``CCS2``): the charger needs an in-service gun of it. ``None`` to
            not filter.
        min_power_kw: Minimum charger ``max_power_kw``, or ``None``.
        is_operational_only: Whether to only return ``ACTIVE`` chargers.
        is_available_only: Whether to only return available stations:
            ``ACTIVE`` and at least one connector whose last reported status
            is ``Available`` - the same rule as F-A2; ``is_online`` is not
            required. Implies ``is_operational_only``.
        page: Page number starting at one; lower values are clamped to the
            default.
        page_size: Page size, clamped according to settings.

    Returns:
        Matching stations nearest-first, and pagination metadata.

    Side Effects:
        Performs two read queries plus the state and two connector-count
        queries per station on the page - the same deliberate, deferred N+1 as
        ``list_charging_stations`` (``docs/decisions/deferred.md`` item 34);
        does not commit or rollback.
    """
    # radius_km > 0 is enforced by the router's Query validation; clamp only
    # the upper bound here so a non-HTTP caller can't request an unbounded
    # PostGIS scan, and floor negative/zero input rather than passing it
    # straight to ST_DWithin.
    radius_km = min(
        max(radius_km, 0.001), settings.CHARGING_STATIONS_NEARBY_MAX_RADIUS_KM
    )
    page_window = normalize_page_window(page, page_size)

    query_point = coordinates_to_location(latitude, longitude)
    assert query_point is not None, "latitude/longitude are both required here"
    radius_meters = radius_km * 1000
    min_power_decimal = Decimal(str(min_power_kw)) if min_power_kw is not None else None
    standard = (
        normalize_connector_standard(connector_standard)
        if connector_standard is not None
        else None
    )

    matches = await charging_stations_repository.list_nearby_stations(
        db,
        point=query_point,
        radius_meters=radius_meters,
        connector_standard=standard,
        min_power_kw=min_power_decimal,
        is_operational_only=is_operational_only,
        is_available_only=is_available_only,
        offset=page_window.offset,
        limit=page_window.page_size,
        viewer=_viewer_of(principal),
    )
    total = await charging_stations_repository.count_nearby_stations(
        db,
        point=query_point,
        radius_meters=radius_meters,
        connector_standard=standard,
        min_power_kw=min_power_decimal,
        is_operational_only=is_operational_only,
        is_available_only=is_available_only,
        viewer=_viewer_of(principal),
    )
    items = []
    for station, location, distance_meters in matches:
        state = await charging_stations_repository.get_station_state(
            db, station.station_id
        )
        connector_count = (
            await charging_stations_repository.count_connectors_by_station_id(
                db, station.station_id
            )
        )
        available_connector_count = (
            await charging_stations_repository.count_available_connectors_by_station_id(
                db, station.station_id
            )
        )
        items.append(
            to_nearby_charging_station_response(
                station,
                location,
                state,
                connector_count=connector_count,
                available_connector_count=available_connector_count,
                distance_km=distance_meters / 1000,
            )
        )
    return NearbyChargingStationListResponse(
        items=items,
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def update_charging_station(
    db: AsyncSession,
    station_id: UUID,
    station_update_request: ChargingStationUpdateRequest,
    *,
    principal: Principal,
) -> ChargingStationResponse:
    """PATCH a station and check for identity conflicts before flushing.

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: UUID of the station to update.
        station_update_request: PATCH fields, already Pydantic-validated.
        principal: The caller; only the owner organization and internal staff
            may change a charger, and a new location must be one they manage.

    Returns:
        The updated station response.

    Raises:
        ChargingStationNotFoundError: If the station does not exist or was
            deleted.
        ChargingLocationNotFoundError: If the new location is not active.
        ChargingTopologyConflictError: If the new identity or serial is
            already in use.
    """
    station = await _get_station_in_reach(db, station_id, principal)
    if (
        station_update_request.ocpp_identity is not None
        and station_update_request.ocpp_identity != station.ocpp_identity
        and await charging_stations_repository.get_station_by_identity(
            db, station_update_request.ocpp_identity
        )
    ):
        raise ChargingTopologyConflictError(
            f"OCPP identity '{station_update_request.ocpp_identity}' already exists"
        )
    if (
        station_update_request.registered_serial_number is not None
        and station_update_request.registered_serial_number
        != station.registered_serial_number
        and await charging_stations_repository.find_live_station_by_registered_serial(
            db, station_update_request.registered_serial_number
        )
    ):
        raise ChargingTopologyConflictError(
            "Registered serial number "
            f"'{station_update_request.registered_serial_number}' already exists"
        )
    if station_update_request.location_id is not None:
        await _get_location_in_reach(db, station_update_request.location_id, principal)

    update_data = _clean_update_values(
        station_update_request.model_dump(exclude_unset=True, exclude={"max_power_kw"})
    )
    # max_power_kw needs a float->Decimal conversion, handled separately from
    # the generic _clean_update_values pass above.
    if station_update_request.max_power_kw is not None:
        update_data["max_power_kw"] = Decimal(str(station_update_request.max_power_kw))
    change_reason = _status_update_values(update_data, "Charging station edited")
    if not update_data:
        return await _build_charging_station_response(db, station)
    try:
        updated = await charging_stations_repository.update_charging_station(
            db,
            station_id,
            update_data,
            change_reason=change_reason,
            changed_by=principal.user_id,
        )
    except IntegrityError as error:
        raise ChargingTopologyConflictError(
            "Station OCPP identity or registered serial already exists"
        ) from error
    if updated is None:
        raise ChargingStationNotFoundError(f"Station '{station_id}' not found")
    return await _build_charging_station_response(db, updated)


async def soft_delete_charging_station(
    db: AsyncSession,
    station_id: UUID,
    *,
    principal: Principal,
    status_reason: str = "Charging station removed",
) -> ChargingResourceDeleteResponse:
    """Soft-delete a station and its child topology within the same transaction.

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: UUID of the station to soft-delete.
        principal: The caller; only the owner organization and internal staff.
        status_reason: Why the charger left the system.

    Returns:
        Confirmation message for the soft-delete.

    Raises:
        ChargingStationNotFoundError: If the station does not exist or was
            deleted.

    Side Effects:
        Marks the station and its EVSEs ``INACTIVE`` with the reason and sets
        ``deleted_at`` on them and on its connectors; does not physically
        delete records and does not commit on its own.
    """
    await _get_station_in_reach(db, station_id, principal)
    if not await charging_stations_repository.soft_delete_station(
        db, station_id, status_reason=status_reason, changed_by=principal.user_id
    ):
        raise ChargingStationNotFoundError(f"Station '{station_id}' not found")
    return ChargingResourceDeleteResponse(message="Charging station soft-deleted")


# --- EVSEs -------------------------------------------------------------------


async def create_charging_evse(
    db: AsyncSession,
    station_id: UUID,
    evse_create_request: ChargingEvseCreateRequest,
    *,
    principal: Principal,
) -> ChargingEvseResponse:
    """Create an EVSE only if the parent station is active and the identities are unused.

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: UUID of the parent station.
        evse_create_request: EVSE identity, already validated.
        principal: The caller; only the owner organization and internal staff.

    Returns:
        The newly created EVSE response.

    Raises:
        ChargingStationNotFoundError: If the parent station is not active.
        ChargingTopologyConflictError: If the EVSE identity or public ID
            already exists.
    """
    await _get_station_in_reach(db, station_id, principal)
    if await charging_stations_repository.get_evse_by_identity(
        db, station_id, evse_create_request.ocpp_evse_id
    ):
        raise ChargingTopologyConflictError(
            f"EVSE ID '{evse_create_request.ocpp_evse_id}' already exists in station"
        )
    if await charging_stations_repository.find_live_evse_by_emi3_id(
        db, evse_create_request.emi3_evse_id
    ):
        raise ChargingTopologyConflictError(
            f"Public EVSE ID '{evse_create_request.emi3_evse_id}' already exists"
        )
    try:
        evse = await charging_stations_repository.create_charging_evse(
            db,
            station_id=station_id,
            ocpp_evse_id=evse_create_request.ocpp_evse_id,
            emi3_evse_id=evse_create_request.emi3_evse_id,
        )
    except IntegrityError as error:
        raise ChargingTopologyConflictError(
            "EVSE identity or public ID already exists"
        ) from error
    return to_charging_evse_response(evse)


async def list_charging_evses(
    db: AsyncSession,
    station_id: UUID,
    *,
    principal: Principal,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> ChargingEvseListResponse:
    """List active EVSEs belonging to the parent station.

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: UUID of the parent station.
        principal: The caller (view access, see the module docstring).
        page: Page number starting at one.
        page_size: Page size, bounded by settings.

    Returns:
        List of EVSEs and pagination metadata.

    Raises:
        ChargingStationNotFoundError: If the parent station is not active.
    """
    await _get_station_in_reach(db, station_id, principal, view=True)
    page_window = normalize_page_window(page, page_size)
    evses = await charging_stations_repository.list_charging_evses(
        db,
        station_id=station_id,
        offset=page_window.offset,
        limit=page_window.page_size,
    )
    total = await charging_stations_repository.count_evses(db, station_id)
    return ChargingEvseListResponse(
        items=[to_charging_evse_response(evse) for evse in evses],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def get_charging_evse(
    db: AsyncSession, evse_id: UUID, *, principal: Principal
) -> ChargingEvseResponse:
    """Get an active EVSE by internal UUID.

    Args:
        db: Async session owned by the HTTP boundary.
        evse_id: UUID of the EVSE to query.
        principal: The caller (view access, see the module docstring).

    Returns:
        The active EVSE response.

    Raises:
        ChargingEvseNotFoundError: If the EVSE does not exist or was
            deleted.
    """
    evse = await _get_evse_in_reach(db, evse_id, principal, view=True)
    return to_charging_evse_response(evse)


async def update_charging_evse(
    db: AsyncSession,
    evse_id: UUID,
    evse_update_request: ChargingEvseUpdateRequest,
    *,
    principal: Principal,
) -> ChargingEvseResponse:
    """PATCH an EVSE while keeping its identities unique.

    Args:
        db: Async session owned by the HTTP boundary.
        evse_id: UUID of the EVSE to update.
        evse_update_request: PATCH fields, already validated.
        principal: The caller; only the owner organization and internal staff.

    Returns:
        The updated EVSE response.

    Raises:
        ChargingEvseNotFoundError: If the EVSE is not active.
        ChargingTopologyConflictError: If the new identity conflicts within
            the station, or the public ID is in use.
    """
    evse = await _get_evse_in_reach(db, evse_id, principal)
    if (
        evse_update_request.ocpp_evse_id is not None
        and evse_update_request.ocpp_evse_id != evse.ocpp_evse_id
        and await charging_stations_repository.get_evse_by_identity(
            db, evse.station_id, evse_update_request.ocpp_evse_id
        )
    ):
        raise ChargingTopologyConflictError(
            f"EVSE ID '{evse_update_request.ocpp_evse_id}' already exists in station"
        )
    if (
        evse_update_request.emi3_evse_id is not None
        and evse_update_request.emi3_evse_id != evse.emi3_evse_id
        and await charging_stations_repository.find_live_evse_by_emi3_id(
            db, evse_update_request.emi3_evse_id
        )
    ):
        raise ChargingTopologyConflictError(
            f"Public EVSE ID '{evse_update_request.emi3_evse_id}' already exists"
        )
    update_data = _clean_update_values(
        evse_update_request.model_dump(exclude_unset=True)
    )
    change_reason = _status_update_values(update_data, "Charging EVSE edited")
    if not update_data:
        return to_charging_evse_response(evse)
    try:
        updated = await charging_stations_repository.update_charging_evse(
            db,
            evse_id,
            update_data,
            change_reason=change_reason,
            changed_by=principal.user_id,
        )
    except IntegrityError as error:
        raise ChargingTopologyConflictError(
            "EVSE identity or public ID already exists"
        ) from error
    if updated is None:
        raise ChargingEvseNotFoundError(f"EVSE '{evse_id}' not found")
    return to_charging_evse_response(updated)


async def soft_delete_charging_evse(
    db: AsyncSession,
    evse_id: UUID,
    *,
    principal: Principal,
    status_reason: str = "Charging EVSE removed",
) -> ChargingResourceDeleteResponse:
    """Soft-delete an EVSE and its child connectors.

    Args:
        db: Async session owned by the HTTP boundary.
        evse_id: UUID of the EVSE to soft-delete.
        principal: The caller; only the owner organization and internal staff.
        status_reason: Why the EVSE left the system.

    Returns:
        Confirmation message for the soft-delete.

    Raises:
        ChargingEvseNotFoundError: If the EVSE is not active.

    Side Effects:
        Marks the EVSE and its child connectors; does not physically delete
        and does not commit.
    """
    await _get_evse_in_reach(db, evse_id, principal)
    if not await charging_stations_repository.soft_delete_evse(
        db, evse_id, status_reason=status_reason, changed_by=principal.user_id
    ):
        raise ChargingEvseNotFoundError(f"EVSE '{evse_id}' not found")
    return ChargingResourceDeleteResponse(message="EVSE soft-deleted")


# --- Connectors --------------------------------------------------------------


async def create_charging_connector(
    db: AsyncSession,
    evse_id: UUID,
    connector_create_request: ChargingConnectorCreateRequest,
    *,
    principal: Principal,
) -> ChargingConnectorResponse:
    """Create a connector only if the parent EVSE is active and the identity is unused.

    Args:
        db: Async session owned by the HTTP boundary.
        evse_id: UUID of the parent EVSE.
        connector_create_request: Connector identity, plug and power, validated.
        principal: The caller; only the owner organization and internal staff.

    Returns:
        The newly created connector response.

    Raises:
        ChargingEvseNotFoundError: If the parent EVSE is not active.
        ChargingTopologyConflictError: If the connector identity already
            exists.
    """
    await _get_evse_in_reach(db, evse_id, principal)
    if await charging_stations_repository.get_connector_by_identity(
        db, evse_id, connector_create_request.ocpp_connector_id
    ):
        raise ChargingTopologyConflictError(
            f"Connector ID '{connector_create_request.ocpp_connector_id}' "
            "already exists in EVSE"
        )
    try:
        connector = await charging_stations_repository.create_charging_connector(
            db,
            evse_id=evse_id,
            ocpp_connector_id=connector_create_request.ocpp_connector_id,
            standard=connector_create_request.standard.value,
            max_power_kw=Decimal(str(connector_create_request.max_power_kw)),
            max_voltage_v=connector_create_request.max_voltage_v,
            max_current_a=connector_create_request.max_current_a,
        )
    except IntegrityError as error:
        raise ChargingTopologyConflictError(
            "Connector identity already exists in EVSE"
        ) from error
    return await _build_charging_connector_response(db, connector)


async def list_charging_connectors(
    db: AsyncSession,
    evse_id: UUID,
    *,
    principal: Principal,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> ChargingConnectorListResponse:
    """List active connectors belonging to the parent EVSE.

    Args:
        db: Async session owned by the HTTP boundary.
        evse_id: UUID of the parent EVSE.
        principal: The caller (view access, see the module docstring).
        page: Page number starting at one.
        page_size: Page size, bounded by settings.

    Returns:
        List of connectors and pagination metadata.

    Raises:
        ChargingEvseNotFoundError: If the parent EVSE is not active.
    """
    await _get_evse_in_reach(db, evse_id, principal, view=True)
    page_window = normalize_page_window(page, page_size)
    connectors = await charging_stations_repository.list_charging_connectors(
        db,
        evse_id=evse_id,
        offset=page_window.offset,
        limit=page_window.page_size,
    )
    total = await charging_stations_repository.count_connectors(db, evse_id)
    return ChargingConnectorListResponse(
        items=[
            await _build_charging_connector_response(db, connector)
            for connector in connectors
        ],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def get_charging_connector(
    db: AsyncSession, connector_id: UUID, *, principal: Principal
) -> ChargingConnectorResponse:
    """Get an active connector by internal UUID.

    Args:
        db: Async session owned by the HTTP boundary.
        connector_id: UUID of the connector to query.
        principal: The caller (view access, see the module docstring).

    Returns:
        The active connector response.

    Raises:
        ChargingConnectorNotFoundError: If the connector does not exist or
            was deleted.
    """
    connector = await _get_connector_in_reach(db, connector_id, principal, view=True)
    return await _build_charging_connector_response(db, connector)


async def update_charging_connector(
    db: AsyncSession,
    connector_id: UUID,
    connector_update_request: ChargingConnectorUpdateRequest,
    *,
    principal: Principal,
) -> ChargingConnectorResponse:
    """PATCH a connector while keeping its identity unique within the parent EVSE.

    Args:
        db: Async session owned by the HTTP boundary.
        connector_id: UUID of the connector to update.
        connector_update_request: PATCH fields, already validated.
        principal: The caller; only the owner organization and internal staff.

    Returns:
        The updated connector response.

    Raises:
        ChargingConnectorNotFoundError: If the connector is not active.
        ChargingTopologyConflictError: If the new identity conflicts within
            the EVSE.
    """
    connector = await _get_connector_in_reach(db, connector_id, principal)
    if (
        connector_update_request.ocpp_connector_id is not None
        and connector_update_request.ocpp_connector_id != connector.ocpp_connector_id
        and await charging_stations_repository.get_connector_by_identity(
            db, connector.evse_id, connector_update_request.ocpp_connector_id
        )
    ):
        raise ChargingTopologyConflictError(
            f"Connector ID '{connector_update_request.ocpp_connector_id}' "
            "already exists in EVSE"
        )
    update_data = _clean_update_values(
        connector_update_request.model_dump(
            exclude_unset=True, exclude={"standard", "max_power_kw"}
        )
    )
    if connector_update_request.standard is not None:
        update_data["standard"] = connector_update_request.standard.value
    if connector_update_request.max_power_kw is not None:
        update_data["max_power_kw"] = Decimal(
            str(connector_update_request.max_power_kw)
        )
    if not update_data:
        return await _build_charging_connector_response(db, connector)
    try:
        updated = await charging_stations_repository.update_charging_connector(
            db,
            connector_id,
            update_data,
            change_reason="Charging connector edited",
            changed_by=principal.user_id,
        )
    except IntegrityError as error:
        raise ChargingTopologyConflictError(
            "Connector identity already exists in EVSE"
        ) from error
    if updated is None:
        raise ChargingConnectorNotFoundError(f"Connector '{connector_id}' not found")
    return await _build_charging_connector_response(db, updated)


async def soft_delete_charging_connector(
    db: AsyncSession, connector_id: UUID, *, principal: Principal
) -> ChargingResourceDeleteResponse:
    """Soft-delete a connector.

    Args:
        db: Async session owned by the HTTP boundary.
        connector_id: UUID of the connector to soft-delete.
        principal: The caller; only the owner organization and internal staff.

    Returns:
        Confirmation message for the soft-delete.

    Raises:
        ChargingConnectorNotFoundError: If the connector is not active.

    Side Effects:
        Marks ``deleted_at`` within the current transaction; does not commit
        on its own.
    """
    await _get_connector_in_reach(db, connector_id, principal)
    if not await charging_stations_repository.soft_delete_connector(
        db,
        connector_id,
        change_reason="Charging connector removed",
        changed_by=principal.user_id,
    ):
        raise ChargingConnectorNotFoundError(f"Connector '{connector_id}' not found")
    return ChargingResourceDeleteResponse(message="Connector soft-deleted")


# --- Commands ----------------------------------------------------------------


def _validate_command_parameters(
    command_type: StationCommandType,
    *,
    evse_id: UUID | None,
    session_id: UUID | None,
    parameters: dict[str, object] | None,
) -> dict[str, object] | None:
    """Check that a command carries what its type needs and fill the defaults.

    Args:
        command_type: What to ask.
        evse_id: The gun targeted, if any.
        session_id: The session the command is about, if any.
        parameters: The caller's parameters, if any.

    Returns:
        The parameters to store (defaults added), ``None`` if there are none.

    Raises:
        ChargingStationCommandInputError: If a required link or parameter is
            missing.
    """
    values: dict[str, object] = dict(parameters or {})
    if command_type is StationCommandType.REMOTE_START:
        if session_id is None and "id_token" not in values:
            raise ChargingStationCommandInputError(
                "REMOTE_START needs a session (its token) or an id_token parameter"
            )
        # The 2.0.1 remoteStartId is kept on the row (CS-20); a 1.6J charger
        # ignores it.
        values.setdefault("remote_start_id", uuid4().int % 2**31)
    elif command_type is StationCommandType.REMOTE_STOP:
        if session_id is None:
            raise ChargingStationCommandInputError("REMOTE_STOP needs a session")
    elif command_type is StationCommandType.UNLOCK_CONNECTOR:
        if evse_id is None:
            raise ChargingStationCommandInputError("UNLOCK_CONNECTOR needs an EVSE")
    elif command_type is StationCommandType.RESET:
        values.setdefault("reset_type", "Soft")
    elif command_type is StationCommandType.CHANGE_AVAILABILITY:
        values.setdefault("availability", "Inoperative")
    elif command_type is StationCommandType.CHANGE_CONFIGURATION:
        if "key" not in values or "value" not in values:
            raise ChargingStationCommandInputError(
                "CHANGE_CONFIGURATION needs key and value parameters"
            )
    elif command_type is StationCommandType.TRIGGER_MESSAGE:
        if "requested_message" not in values:
            raise ChargingStationCommandInputError(
                "TRIGGER_MESSAGE needs a requested_message parameter"
            )
    return values or None


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
    """Queue a command for a charger: the OCPP gateway sends it (PR-16).

    Public entry point for other domains (a remote start for a QR charge is
    queued by ``charging_sessions``, WP8). The command is written ``PENDING``
    with no message ID; the gateway process holding the charger's connection
    claims it, sends the OCPP call and writes the answer back. A charger that
    is not connected in time ends as ``NOT_SENT``.

    Args:
        db: Async session owned by the caller's entry boundary.
        station_id: The charger the command goes to.
        command_type: What to ask.
        evse_id: The gun it targets, ``None`` for the whole charger.
        session_id: The session a remote start begins or a remote stop ends.
        parameters: What to send besides the links (see
            ``ChargingStationCommandCreateRequest``); validated per type.
        requested_by: User who asked, ``None`` when the system sends it.
        reason: Why, typed by the operator for a manual command.

    Returns:
        A reference to the queued command.

    Raises:
        ChargingStationNotFoundError: If the charger is not active.
        ChargingStationCommandInputError: If a link or parameter the type needs
            is missing, the EVSE is not the charger's, or a referenced row does
            not exist.
    """
    if await charging_stations_repository.get_station_by_id(db, station_id) is None:
        raise ChargingStationNotFoundError(f"Station '{station_id}' not found")
    if evse_id is not None:
        evse = await charging_stations_repository.get_evse_by_id(db, evse_id)
        if evse is None or evse.station_id != station_id:
            raise ChargingStationCommandInputError(
                f"EVSE '{evse_id}' does not belong to the station"
            )
    stored_parameters = _validate_command_parameters(
        command_type,
        evse_id=evse_id,
        session_id=session_id,
        parameters=parameters,
    )
    try:
        command = await ocpp_state_repository.insert_station_command(
            db,
            station_id=station_id,
            command_type=command_type,
            evse_id=evse_id,
            session_id=session_id,
            parameters=stored_parameters,
            requested_by=requested_by,
            reason=reason,
        )
    except IntegrityError as error:
        raise ChargingStationCommandInputError(
            "The session or the requesting user does not exist"
        ) from error
    return StationCommandReference(
        command_id=command.command_id,
        station_id=command.station_id,
        command_type=StationCommandType(command.command_type),
        outcome=StationCommandOutcome(command.outcome),
    )


async def create_charging_station_command(
    db: AsyncSession,
    station_id: UUID,
    command_create_request: ChargingStationCommandCreateRequest,
    *,
    principal: Principal,
) -> ChargingStationCommandResponse:
    """Queue a command for a charger from the API and return it.

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: The charger the command goes to.
        command_create_request: The command, already Pydantic-validated.
        principal: The caller, recorded as ``requested_by``; only the owner
            organization and internal staff may command a charger.

    Returns:
        The queued command (``PENDING``, no message ID yet).

    Raises:
        ChargingStationNotFoundError: If the charger is not active or is out
            of the caller's reach.
        ChargingStationCommandInputError: See ``queue_station_command``.
    """
    await _get_station_in_reach(db, station_id, principal)
    reference = await queue_station_command(
        db,
        station_id=station_id,
        command_type=command_create_request.command_type,
        evse_id=command_create_request.evse_id,
        session_id=command_create_request.session_id,
        parameters=command_create_request.parameters,
        requested_by=principal.user_id,
        reason=command_create_request.reason,
    )
    command = await ocpp_state_repository.get_station_command_by_id(
        db, reference.command_id
    )
    assert command is not None, "the command was just inserted"
    return to_charging_station_command_response(command)


def to_charging_station_command_response(
    command: ChargingStationCommandModel,
) -> ChargingStationCommandResponse:
    """Build a command response from the ORM model.

    Args:
        command: Command ORM object queried or created by the repository.

    Returns:
        Response schema corresponding to the command.
    """
    return ChargingStationCommandResponse.model_validate(command)


async def get_charging_station_command(
    db: AsyncSession, station_id: UUID, command_id: UUID, *, principal: Principal
) -> ChargingStationCommandResponse:
    """Get one command of a charger (the answer is polled here, PR-16).

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: The charger.
        command_id: The command.
        principal: The caller (manage access to the charger).

    Returns:
        The command with its current outcome.

    Raises:
        ChargingStationNotFoundError: If the command does not exist, belongs
            to another charger or the charger is out of the caller's reach.
    """
    await _get_station_in_reach(db, station_id, principal)
    command = await ocpp_state_repository.get_station_command_by_id(db, command_id)
    if command is None or command.station_id != station_id:
        raise ChargingStationNotFoundError(
            f"Command '{command_id}' not found for the station"
        )
    return to_charging_station_command_response(command)


async def list_charging_station_commands(
    db: AsyncSession,
    station_id: UUID,
    *,
    principal: Principal,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> ChargingStationCommandListResponse:
    """List a charger's commands, newest first (STN-10).

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: The charger.
        principal: The caller (manage access to the charger).
        page: Page number starting at one.
        page_size: Page size, bounded by settings.

    Returns:
        The commands and pagination metadata.

    Raises:
        ChargingStationNotFoundError: If the charger is not active.
    """
    await _get_station_in_reach(db, station_id, principal)
    page_window = normalize_page_window(page, page_size)
    commands = await ocpp_state_repository.list_station_commands(
        db,
        station_id=station_id,
        offset=page_window.offset,
        limit=page_window.page_size,
    )
    total = await ocpp_state_repository.count_station_commands(db, station_id)
    return ChargingStationCommandListResponse(
        items=[to_charging_station_command_response(command) for command in commands],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def get_latest_station_configuration(
    db: AsyncSession, station_id: UUID, *, principal: Principal
) -> ChargingStationConfigurationResponse:
    """Get the latest configuration a charger reported.

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: UUID of the station.
        principal: The caller (manage access to the charger).

    Returns:
        The newest complete snapshot's settings sorted by name, or an empty
        response with ``null`` capture fields if the charger has not reported
        yet.

    Raises:
        ChargingStationNotFoundError: If the station does not exist or was
            soft-deleted.

    Side Effects:
        Performs up to three read queries; does not commit.
    """
    await _get_station_in_reach(db, station_id, principal)
    latest = await ocpp_state_repository.get_latest_configuration_capture(
        db, station_id
    )
    if latest is None:
        return ChargingStationConfigurationResponse(
            station_id=station_id, capture_id=None, captured_at=None, items=[]
        )
    capture_id, captured_at = latest
    rows = await ocpp_state_repository.list_configuration_entries_by_capture_id(
        db, capture_id
    )
    return ChargingStationConfigurationResponse(
        station_id=station_id,
        capture_id=capture_id,
        captured_at=captured_at,
        items=[
            ChargingStationConfigurationEntryResponse.model_validate(row)
            for row in rows
        ],
    )


def _normalize_report_bound(value: datetime, field_name: str) -> datetime:
    """Require a timezone-aware report bound and convert it to UTC.

    Args:
        value: The bound from the request.
        field_name: Name used in the error message.

    Returns:
        ``value`` in UTC.

    Raises:
        ChargingStationReportRangeError: If ``value`` has no timezone.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ChargingStationReportRangeError(f"{field_name} must have a timezone")
    return value.astimezone(timezone.utc)


async def list_station_energy_totals(
    db: AsyncSession, *, principal: Principal, start_time: datetime, end_time: datetime
) -> ChargingStationEnergyTotalListResponse:
    """Energy sold per station within a window, for every active station (F-C5).

    Rule:
        Same semantics as the per-station summary in ``charging_sessions``
        (completed sessions whose ``ended_at`` falls within the inclusive
        window, summing ``energy_delivered_wh``), applied to every
        non-deleted station; a station with no session is listed at zero.
        Items are ranked by energy, highest first, ties by display name.
        This domain orchestrates because it owns the station directory;
        ``charging_sessions`` never calls back into it.

    Args:
        db: Async session owned by the HTTP boundary.
        principal: The caller; only chargers at the caller's own locations are
            listed unless the caller is internal.
        start_time: Inclusive lower bound on ``ended_at``; must carry a
            timezone.
        end_time: Inclusive upper bound on ``ended_at``; must carry a
            timezone and be after ``start_time``.

    Returns:
        Per-station totals plus the grand total, in kWh.

    Raises:
        ChargingStationReportRangeError: If a bound lacks a timezone or
            ``end_time`` is not after ``start_time``.

    Side Effects:
        Performs one station query plus one energy query per station
        (deliberately unbatched, like the directory's per-station counts);
        does not commit or roll back.
    """
    normalized_start = _normalize_report_bound(start_time, "start_time")
    normalized_end = _normalize_report_bound(end_time, "end_time")
    if normalized_end <= normalized_start:
        raise ChargingStationReportRangeError("end_time must be after start_time")
    stations = await charging_stations_repository.list_active_stations_with_location(db)
    items: list[ChargingStationEnergyTotalResponse] = []
    for station, location in stations:
        if not principal.can_access_organization(location.organization_id):
            continue
        energy_total = await charging_sessions_service.resolve_station_energy_total(
            db,
            station_id=station.station_id,
            start_time=normalized_start,
            end_time=normalized_end,
        )
        items.append(
            ChargingStationEnergyTotalResponse(
                station_id=station.station_id,
                display_name=(
                    location.display_name
                    if station.physical_reference is None
                    else f"{location.display_name} - {station.physical_reference}"
                ),
                total_energy_kwh=float(energy_total.total_energy_wh / Decimal(1000)),
                session_count=energy_total.session_count,
            )
        )
    # The repository already orders by display name; a stable sort on the
    # energy alone keeps that order among equal totals.
    items.sort(key=lambda item: item.total_energy_kwh, reverse=True)
    return ChargingStationEnergyTotalListResponse(
        start_time=normalized_start,
        end_time=normalized_end,
        total_energy_kwh=sum(item.total_energy_kwh for item in items),
        session_count=sum(item.session_count for item in items),
        items=items,
    )
