"""Chargers: CRUD, status, connection, OCPP log, configuration, energy report.

Charger create / list / read / update / soft-delete (F-C1, STN-01, STN-12),
the status view of a charger and its guns (F-C2) and the network status board
(STN-04), the connection facts (STN-03), the staff-only OCPP message log, the
latest configuration a charger reported, and the all-stations energy report
(F-C5, which asks ``charging_sessions`` for each station's total). Internal
module: the domain's router calls it; ``service.py`` wraps
``get_charging_station`` for other domains. Runs in the caller's transaction
and never commits.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.service as charging_sessions_service
import app.domains.charging_stations.access as station_access
import app.domains.charging_stations.mappers as station_mappers
import app.domains.charging_stations.ocpp_state_repository as ocpp_state_repository
import app.domains.charging_stations.repository as charging_stations_repository
from app.domains.charging_stations.exceptions import (
    ChargingStationNotFoundError,
    ChargingStationReportRangeError,
    ChargingTopologyConflictError,
)
from app.domains.charging_stations.models import (
    ChargingConnectorModel,
    ChargingConnectorStateModel,
    ChargingOcppMessageModel,
)
from app.domains.charging_stations.schemas import (
    ChargingOcppMessageListResponse,
    ChargingOcppMessageResponse,
    ChargingResourceDeleteResponse,
    ChargingStationConfigurationEntryResponse,
    ChargingStationConfigurationResponse,
    ChargingStationConnectionResponse,
    ChargingStationConnectorStatusResponse,
    ChargingStationCreateRequest,
    ChargingStationEnergyTotalListResponse,
    ChargingStationEnergyTotalResponse,
    ChargingStationListResponse,
    ChargingStationResponse,
    ChargingStationStatusResponse,
    ChargingStationStatusSummaryListResponse,
    ChargingStationStatusSummaryResponse,
    ChargingStationUpdateRequest,
)
from app.domains.charging_stations.types import (
    ChargingConnectorStatus,
    ChargingResourceStatus,
    ConnectorStandard,
    OcppMessageDirection,
    StationListScope,
)
from app.domains.identity.exceptions import AccessDeniedError
from app.domains.identity.types import Principal
from app.libs.common.clock import utc_now
from app.libs.common.config import settings
from app.libs.common.pagination import normalize_page_window

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
    await station_access.get_location_in_reach(
        db, station_create_request.location_id, principal
    )
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
    return station_mappers.to_charging_station_response(
        station, location, state, connector_count=0, available_connector_count=0
    )


async def list_charging_stations(
    db: AsyncSession,
    *,
    principal: Principal,
    scope: StationListScope = StationListScope.MANAGED,
    owner_organization_id: UUID | None = None,
    location_id: UUID | None = None,
    is_public: bool | None = None,
    status: ChargingResourceStatus | None = None,
    search_text: str | None = None,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> ChargingStationListResponse:
    """List active stations the caller manages, or all they may see (STN-01, STN-12).

    Args:
        db: Async session owned by the HTTP boundary.
        principal: The caller (data scope, through the chargers' locations).
        scope: ``MANAGED`` (default) or ``VISIBLE``, see `list_charging_locations`.
        owner_organization_id: Only chargers at locations of this owner.
        location_id: Only chargers at this location.
        is_public: Only chargers at public (`True`) or private locations.
        status: Only chargers with this status.
        search_text: Case-insensitive fragment of the label, OCPP identity, or
            the location's name or address.
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
    organization_id, viewer = station_access.list_reach(principal, scope)
    status_value = status.value if status is not None else None
    search = search_text.strip() if search_text else None
    stations = await charging_stations_repository.list_charging_stations(
        db,
        offset=page_window.offset,
        limit=page_window.page_size,
        organization_id=organization_id,
        viewer=viewer,
        owner_organization_id=owner_organization_id,
        location_id=location_id,
        is_public=is_public,
        status=status_value,
        search_text=search,
    )
    total = await charging_stations_repository.count_stations(
        db,
        organization_id=organization_id,
        viewer=viewer,
        owner_organization_id=owner_organization_id,
        location_id=location_id,
        is_public=is_public,
        status=status_value,
        search_text=search,
    )
    items = [
        await station_mappers.build_charging_station_response(db, station)
        for station in stations
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
    station = await station_access.get_station_in_reach(
        db, station_id, principal, view=True
    )
    return await station_mappers.build_charging_station_response(db, station)


_UNKNOWN_STATUS = "Unknown"


def _summarize_connector_statuses(
    connectors: list[
        tuple[ChargingConnectorModel, int, ChargingConnectorStateModel | None]
    ],
) -> tuple[int, int, dict[str, int]]:
    """Count a charger's guns, the available ones and the guns per status (STN-04).

    Args:
        connectors: The ``(connector, ocpp_evse_id, state)`` triples of the
            charger, as the repository lists them.

    Returns:
        ``(connector_count, available_connector_count, status_counts)``; a
        gun that never reported is counted under ``Unknown``. Derived at read
        time, nothing is stored.
    """
    status_counts: dict[str, int] = {}
    for _connector, _ocpp_evse_id, connector_state in connectors:
        reported = (
            connector_state.status
            if connector_state is not None and connector_state.status
            else _UNKNOWN_STATUS
        )
        status_counts[reported] = status_counts.get(reported, 0) + 1
    return (
        len(connectors),
        status_counts.get(ChargingConnectorStatus.AVAILABLE.value, 0),
        status_counts,
    )


async def get_charging_station_status(
    db: AsyncSession, station_id: UUID, *, principal: Principal
) -> ChargingStationStatusResponse:
    """Get the whole charger's status and every gun's status of a station (STN-04).

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: UUID of the station.
        principal: The caller (view access, see the module docstring).

    Returns:
        The whole-charger status fields of the station, the derived counts and
        one entry per active connector, ordered by OCPP EVSE number, then
        connector number. Statuses are returned as last reported, even if the
        charger is offline; then ``is_online`` is false and every gun is
        flagged ``is_status_stale``, so a client shows "unknown".

    Raises:
        ChargingStationNotFoundError: If the station does not exist or was
            soft-deleted.

    Side Effects:
        Performs three read queries; does not commit or roll back.
    """
    station = await station_access.get_station_in_reach(
        db, station_id, principal, view=True
    )
    state = await charging_stations_repository.get_station_state(db, station_id)
    connectors = await charging_stations_repository.list_connectors_by_station_id(
        db, station_id
    )
    is_online = station_mappers.is_station_online(state, utc_now())
    connector_count, available_connector_count, status_counts = (
        _summarize_connector_statuses(connectors)
    )
    return ChargingStationStatusResponse(
        station_id=station.station_id,
        is_online=is_online,
        last_seen_at=state.last_seen_at if state else None,
        charger_status=(
            ChargingConnectorStatus(state.charger_status)
            if state is not None and state.charger_status is not None
            else None
        ),
        charger_status_updated_at=state.charger_status_updated_at if state else None,
        charger_error_code=state.charger_error_code if state else None,
        charger_vendor_error_code=state.charger_vendor_error_code if state else None,
        connector_count=connector_count,
        available_connector_count=available_connector_count,
        status_counts=status_counts,
        connectors=[
            ChargingStationConnectorStatusResponse(
                connector_id=connector.connector_id,
                evse_id=connector.evse_id,
                ocpp_evse_id=ocpp_evse_id,
                ocpp_connector_id=connector.ocpp_connector_id,
                standard=ConnectorStandard(connector.standard),
                max_power_kw=float(connector.max_power_kw),
                status=(
                    ChargingConnectorStatus(connector_state.status)
                    if connector_state is not None and connector_state.status
                    else None
                ),
                status_updated_at=(
                    connector_state.status_updated_at if connector_state else None
                ),
                is_status_stale=not is_online,
                error_code=connector_state.error_code if connector_state else None,
                vendor_error_code=(
                    connector_state.vendor_error_code if connector_state else None
                ),
                status_info=connector_state.status_info if connector_state else None,
            )
            for connector, ocpp_evse_id, connector_state in connectors
        ],
    )


async def list_charging_station_status_summaries(
    db: AsyncSession,
    *,
    principal: Principal,
    scope: StationListScope = StationListScope.VISIBLE,
    owner_organization_id: UUID | None = None,
    location_id: UUID | None = None,
    search_text: str | None = None,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> ChargingStationStatusSummaryListResponse:
    """List the chargers the caller may see with their gun counts (STN-04 board).

    Args:
        db: Async session owned by the HTTP boundary.
        principal: The caller.
        scope: ``VISIBLE`` by default (the board of everything the caller may
            see); ``MANAGED`` for their own chargers only.
        owner_organization_id: Only chargers at locations of this owner.
        location_id: Only chargers at this location.
        search_text: Case-insensitive fragment, see `list_charging_stations`.
        page: Page number starting at one.
        page_size: Page size, clamped according to settings.

    Returns:
        One summary row per charger and pagination metadata.

    Side Effects:
        One location, state and gun-list query per charger on the page (no
        batching, deferred.md 34); does not commit or roll back.
    """
    page_window = normalize_page_window(page, page_size)
    organization_id, viewer = station_access.list_reach(principal, scope)
    search = search_text.strip() if search_text else None
    stations = await charging_stations_repository.list_charging_stations(
        db,
        offset=page_window.offset,
        limit=page_window.page_size,
        organization_id=organization_id,
        viewer=viewer,
        owner_organization_id=owner_organization_id,
        location_id=location_id,
        search_text=search,
    )
    total = await charging_stations_repository.count_stations(
        db,
        organization_id=organization_id,
        viewer=viewer,
        owner_organization_id=owner_organization_id,
        location_id=location_id,
        search_text=search,
    )
    checked_at = utc_now()
    items: list[ChargingStationStatusSummaryResponse] = []
    for station in stations:
        location = await charging_stations_repository.get_location_by_id(
            db, station.location_id, include_deleted=True
        )
        assert location is not None, "the foreign key guarantees the location"
        state = await charging_stations_repository.get_station_state(
            db, station.station_id
        )
        connectors = await charging_stations_repository.list_connectors_by_station_id(
            db, station.station_id
        )
        connector_count, available_connector_count, status_counts = (
            _summarize_connector_statuses(connectors)
        )
        items.append(
            ChargingStationStatusSummaryResponse(
                station_id=station.station_id,
                location_id=station.location_id,
                location_display_name=location.display_name,
                physical_reference=station.physical_reference,
                is_online=station_mappers.is_station_online(state, checked_at),
                last_seen_at=state.last_seen_at if state else None,
                charger_status=(
                    ChargingConnectorStatus(state.charger_status)
                    if state is not None and state.charger_status is not None
                    else None
                ),
                connector_count=connector_count,
                available_connector_count=available_connector_count,
                status_counts=status_counts,
            )
        )
    return ChargingStationStatusSummaryListResponse(
        items=items,
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


async def get_charging_station_connection(
    db: AsyncSession, station_id: UUID, *, principal: Principal
) -> ChargingStationConnectionResponse:
    """Get what the gateway knows about a charger's connection (STN-03).

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: UUID of the station.
        principal: The caller (manage access: it shows the OCPP identity and
            serial numbers, which a driver who may only view the charger
            never needs).

    Returns:
        Liveness (``last_seen_at`` and the derived ``is_online``), protocol
        version and the data of the latest boot; all empty before the first
        connection.

    Raises:
        ChargingStationNotFoundError: If the station does not exist, was
            deleted or is out of the caller's reach.
    """
    station = await station_access.get_station_in_reach(db, station_id, principal)
    state = await charging_stations_repository.get_station_state(db, station_id)
    reported_serial = state.serial_number if state else None
    return ChargingStationConnectionResponse(
        station_id=station.station_id,
        ocpp_identity=station.ocpp_identity,
        is_online=station_mappers.is_station_online(state, utc_now()),
        offline_after_seconds=settings.CHARGING_OFFLINE_TIMEOUT_SECONDS,
        last_seen_at=state.last_seen_at if state else None,
        last_boot_at=state.last_boot_at if state else None,
        ocpp_protocol_version=state.ocpp_protocol_version if state else None,
        vendor=state.vendor if state else None,
        model=state.model if state else None,
        serial_number=reported_serial,
        registered_serial_number=station.registered_serial_number,
        is_serial_matching=(
            None
            if reported_serial is None
            else reported_serial == station.registered_serial_number
        ),
        firmware_version=state.firmware_version if state else None,
    )


async def list_charging_station_ocpp_messages(
    db: AsyncSession,
    station_id: UUID,
    *,
    principal: Principal,
    action: str | None = None,
    direction: OcppMessageDirection | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    include_raw_frame: bool = False,
    page: int = settings.API_DEFAULT_PAGE,
    page_size: int = settings.API_DEFAULT_PAGE_SIZE,
) -> ChargingOcppMessageListResponse:
    """Read a charger's OCPP message log, newest first (STN-03, minimal STN-15).

    Rule:
        Only internal staff read the log. The text of a frame can hold RFID
        ``idTag`` values, so it is left out unless the caller asks for it with
        ``include_raw_frame`` (and the router then writes an audit row); a
        customer never reaches this function, whatever they ask.

    Args:
        db: Async session owned by the HTTP boundary.
        station_id: UUID of the station.
        principal: The caller; must be internal staff.
        action: Only requests of this OCPP action (case as OCPP writes it).
        direction: Only frames in this direction.
        occurred_from: Inclusive lower bound on the time; must carry a timezone.
        occurred_to: Exclusive upper bound on the time; must carry a timezone.
        include_raw_frame: Whether to return the frame text.
        page: Page number starting at one.
        page_size: Page size, clamped according to settings.

    Returns:
        The frames and pagination metadata.

    Raises:
        AccessDeniedError: The caller is not internal staff.
        ChargingStationNotFoundError: The station does not exist or was deleted.
        ChargingStationReportRangeError: A time bound has no timezone.
    """
    if not principal.is_internal:
        raise AccessDeniedError("The OCPP message log is for our own staff")
    await station_access.get_station_in_reach(db, station_id, principal)
    lower = (
        _normalize_report_bound(occurred_from, "occurred_from")
        if occurred_from is not None
        else None
    )
    upper = (
        _normalize_report_bound(occurred_to, "occurred_to")
        if occurred_to is not None
        else None
    )
    page_window = normalize_page_window(page, page_size)
    messages = await ocpp_state_repository.list_ocpp_messages(
        db,
        station_id=station_id,
        offset=page_window.offset,
        limit=page_window.page_size,
        action=action,
        direction=direction,
        occurred_from=lower,
        occurred_to=upper,
    )
    total = await ocpp_state_repository.count_ocpp_messages(
        db,
        station_id=station_id,
        action=action,
        direction=direction,
        occurred_from=lower,
        occurred_to=upper,
    )
    return ChargingOcppMessageListResponse(
        items=[
            to_charging_ocpp_message_response(message, include_raw_frame)
            for message in messages
        ],
        total=total,
        page=page_window.page,
        page_size=page_window.page_size,
    )


def to_charging_ocpp_message_response(
    message: ChargingOcppMessageModel, include_raw_frame: bool
) -> ChargingOcppMessageResponse:
    """Build a message-log response, with or without the frame text.

    Args:
        message: Log row queried by the repository.
        include_raw_frame: Whether to copy the frame text into the response.

    Returns:
        The response; ``raw_frame`` is `None` unless asked for.
    """
    return ChargingOcppMessageResponse(
        message_id=message.message_id,
        occurred_at=message.occurred_at,
        direction=message.direction,
        ocpp_subprotocol=message.ocpp_subprotocol,
        action=message.action,
        ocpp_message_id=message.ocpp_message_id,
        raw_frame=message.raw_frame if include_raw_frame else None,
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
    station = await station_access.get_station_in_reach(db, station_id, principal)
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
        await station_access.get_location_in_reach(
            db, station_update_request.location_id, principal
        )

    update_data = station_mappers.clean_update_values(
        station_update_request.model_dump(exclude_unset=True, exclude={"max_power_kw"})
    )
    # max_power_kw needs a float->Decimal conversion, handled separately from
    # the generic _clean_update_values pass above.
    if station_update_request.max_power_kw is not None:
        update_data["max_power_kw"] = Decimal(str(station_update_request.max_power_kw))
    change_reason = station_mappers.resolve_status_change_reason(
        update_data, "Charging station edited"
    )
    if not update_data:
        return await station_mappers.build_charging_station_response(db, station)
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
    return await station_mappers.build_charging_station_response(db, updated)


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
    await station_access.get_station_in_reach(db, station_id, principal)
    await station_access.ensure_no_active_session(
        db,
        await station_access.list_station_connector_ids(db, station_id),
        "A charging session is in progress on this charger",
    )
    if not await charging_stations_repository.soft_delete_station(
        db, station_id, status_reason=status_reason, changed_by=principal.user_id
    ):
        raise ChargingStationNotFoundError(f"Station '{station_id}' not found")
    return ChargingResourceDeleteResponse(message="Charging station soft-deleted")


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
    await station_access.get_station_in_reach(db, station_id, principal)
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
