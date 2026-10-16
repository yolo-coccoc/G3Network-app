"""Reach checks of the charging_stations domain (ACC-15, CS-10, CS-13).

Shared by the area services: which locations a caller may manage or view,
and loaders that return a location, charger, EVSE or gun only when it is in
the caller's reach (otherwise "not found"). A location is *managed* by its
owner organization and internal staff, and *viewed* also when it is public or
the caller's organization holds a live grant. Also the guard every area uses
before changing a gun, a charger or a location: no charge may be running on
the guns concerned. Internal module: other domains use ``service.py``.
"""

from datetime import date
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.charging_sessions.service as charging_sessions_service
import app.domains.charging_stations.repository as charging_stations_repository
from app.domains.charging_stations.exceptions import (
    ChargingConnectorNotFoundError,
    ChargingEvseNotFoundError,
    ChargingLocationNotFoundError,
    ChargingStationNotFoundError,
    ChargingTopologyConflictError,
)
from app.domains.charging_stations.models import (
    ChargingConnectorModel,
    ChargingEvseModel,
    ChargingLocationModel,
    ChargingStationModel,
)
from app.domains.charging_stations.types import (
    LocationViewer,
    StationListScope,
)
from app.domains.identity.types import Principal
from app.libs.common.clock import utc_now
from app.libs.common.config import settings


def today_in_report_zone() -> date:
    """Tell today's calendar date in the report time zone (Vietnam time, CS-13).

    Returns:
        The date a grant's ``valid_until`` is compared with.
    """
    return utc_now().astimezone(ZoneInfo(settings.APP_REPORT_TIMEZONE)).date()


def list_reach(
    principal: Principal, scope: StationListScope
) -> tuple[UUID | None, LocationViewer | None]:
    """Pick the repository arguments of a list for the caller and the scope (CS-28).

    Args:
        principal: The caller.
        scope: ``MANAGED`` for what they manage, ``VISIBLE`` for everything
            they may see.

    Returns:
        ``(organization_id, viewer)`` for the repository's list and count
        functions: the manage scope or the view scope, never both.
    """
    if scope is StationListScope.VISIBLE:
        return None, viewer_of(principal)
    return principal.data_scope, None


def viewer_of(principal: Principal) -> LocationViewer:
    """Build the location visibility rule of a caller (CS-10, CS-13).

    Args:
        principal: The caller.

    Returns:
        The caller's organization, whether they see every location, and the
        day a grant must still be valid on (today in the report time zone).
    """
    return LocationViewer(
        organization_id=principal.organization_id,
        sees_all=principal.is_internal,
        grants_valid_on=today_in_report_zone(),
    )


async def can_reach_location(
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
    return grant is not None and (
        grant.valid_until is None or grant.valid_until >= today_in_report_zone()
    )


async def get_location_in_reach(
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
    if location is None or not await can_reach_location(
        db, location, principal, view=view
    ):
        raise ChargingLocationNotFoundError(f"Location '{location_id}' not found")
    return location


async def get_station_in_reach(
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
        if location is not None and await can_reach_location(
            db, location, principal, view=view
        ):
            return station
    raise ChargingStationNotFoundError(f"Station '{station_id}' not found")


async def get_evse_in_reach(
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
            await get_station_in_reach(db, evse.station_id, principal, view=view)
        except ChargingStationNotFoundError:
            pass
        else:
            return evse
    raise ChargingEvseNotFoundError(f"EVSE '{evse_id}' not found")


async def get_connector_in_reach(
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
            await get_evse_in_reach(db, connector.evse_id, principal, view=view)
        except ChargingEvseNotFoundError:
            pass
        else:
            return connector
    raise ChargingConnectorNotFoundError(f"Connector '{connector_id}' not found")


async def ensure_no_active_session(
    db: AsyncSession, connector_ids: list[UUID], message: str
) -> None:
    """Refuse an action while a charge is running on any of the given guns.

    Args:
        db: Async session owned by the caller's entry boundary.
        connector_ids: The guns to check.
        message: What to say when one of them is charging.

    Raises:
        ChargingTopologyConflictError: A gun has an ``ACTIVE`` session.
    """
    for connector_id in connector_ids:
        if await charging_sessions_service.has_active_session_on_connector(
            db, connector_id
        ):
            raise ChargingTopologyConflictError(message)


async def list_evse_connector_ids(db: AsyncSession, evse_id: UUID) -> list[UUID]:
    """List the active guns of an EVSE (a handful per EVSE).

    Args:
        db: Async session owned by the caller's entry boundary.
        evse_id: The EVSE.

    Returns:
        The connector IDs.
    """
    connectors = await charging_stations_repository.list_charging_connectors(
        db, evse_id=evse_id, offset=0, limit=100
    )
    return [connector.connector_id for connector in connectors]


async def list_station_connector_ids(db: AsyncSession, station_id: UUID) -> list[UUID]:
    """List the active guns of a charger.

    Args:
        db: Async session owned by the caller's entry boundary.
        station_id: The charger.

    Returns:
        The connector IDs.
    """
    connectors = await charging_stations_repository.list_connectors_by_station_id(
        db, station_id
    )
    return [connector.connector_id for connector, _evse_number, _state in connectors]
