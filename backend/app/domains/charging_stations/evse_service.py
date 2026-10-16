"""EVSEs and connectors (guns) of a charger (F-C1).

Create, list, read, update and soft-delete EVSEs and their connectors. A
charger must exist before an EVSE and an EVSE before a connector, and
topology identities are never reused, even after a soft delete. Internal
module: the domain's router calls it. Runs in the caller's transaction and
never commits.
"""

from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_stations.access as station_access
import app.domains.charging_stations.mappers as station_mappers
import app.domains.charging_stations.repository as charging_stations_repository
from app.domains.charging_stations.exceptions import (
    ChargingConnectorNotFoundError,
    ChargingEvseNotFoundError,
    ChargingTopologyConflictError,
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
    ChargingResourceDeleteResponse,
)
from app.domains.identity.types import Principal
from app.libs.common.config import settings
from app.libs.common.pagination import normalize_page_window

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
    await station_access.get_station_in_reach(db, station_id, principal)
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
    return station_mappers.to_charging_evse_response(evse)


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
    await station_access.get_station_in_reach(db, station_id, principal, view=True)
    page_window = normalize_page_window(page, page_size)
    evses = await charging_stations_repository.list_charging_evses(
        db,
        station_id=station_id,
        offset=page_window.offset,
        limit=page_window.page_size,
    )
    total = await charging_stations_repository.count_evses(db, station_id)
    return ChargingEvseListResponse(
        items=[station_mappers.to_charging_evse_response(evse) for evse in evses],
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
    evse = await station_access.get_evse_in_reach(db, evse_id, principal, view=True)
    return station_mappers.to_charging_evse_response(evse)


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
    evse = await station_access.get_evse_in_reach(db, evse_id, principal)
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
    update_data = station_mappers.clean_update_values(
        evse_update_request.model_dump(exclude_unset=True)
    )
    update_data, change_reason = station_mappers.normalize_status_update(
        update_data, "Charging EVSE edited"
    )
    if not update_data:
        return station_mappers.to_charging_evse_response(evse)
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
    return station_mappers.to_charging_evse_response(updated)


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
        ChargingTopologyConflictError: If a charge is running on one of its guns.

    Side Effects:
        Marks the EVSE and its child connectors; does not physically delete
        and does not commit.
    """
    await station_access.get_evse_in_reach(db, evse_id, principal)
    await station_access.ensure_no_active_session(
        db,
        await station_access.list_evse_connector_ids(db, evse_id),
        "A charging session is in progress on this EVSE",
    )
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
    await station_access.get_evse_in_reach(db, evse_id, principal)
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
    return await station_mappers.build_charging_connector_response(db, connector)


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
    await station_access.get_evse_in_reach(db, evse_id, principal, view=True)
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
            await station_mappers.build_charging_connector_response(db, connector)
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
    connector = await station_access.get_connector_in_reach(
        db, connector_id, principal, view=True
    )
    return await station_mappers.build_charging_connector_response(db, connector)


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
    connector = await station_access.get_connector_in_reach(db, connector_id, principal)
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
    update_data = station_mappers.clean_update_values(
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
        return await station_mappers.build_charging_connector_response(db, connector)
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
    return await station_mappers.build_charging_connector_response(db, updated)


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
    await station_access.get_connector_in_reach(db, connector_id, principal)
    await station_access.ensure_no_active_session(
        db, [connector_id], "A charging session is in progress on this connector"
    )
    if not await charging_stations_repository.soft_delete_connector(
        db,
        connector_id,
        change_reason="Charging connector removed",
        changed_by=principal.user_id,
    ):
        raise ChargingConnectorNotFoundError(f"Connector '{connector_id}' not found")
    return ChargingResourceDeleteResponse(message="Connector soft-deleted")
