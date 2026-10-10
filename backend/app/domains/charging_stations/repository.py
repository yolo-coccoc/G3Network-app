"""Asynchronous repository for charging locations, stations and their topology.

Holds the location / access-grant / station / EVSE / connector queries, the
identity lookups used for conflict checks and OCPP resolution, the connector
counts (all, and the ones whose reported status is ``Available``), the
station-wide connector listing (F-C2), and the PostGIS nearest/nearby searches
(F-A2, F-D1), whose "available" filter is the correlated
``_has_available_connector`` subquery. The queries for state a charger reports
about itself (frame log, state tables, commands, configuration captures) live in
``ocpp_state_repository.py``.

The repository only queries and flushes data; it never commits or rolls back.
Business checks (parent existence, identity conflicts, grant rules) belong to
the service. The soft-delete cascade (location -> stations -> EVSEs ->
connectors) is done here, inside one flush, because it is a pure consequence of
the parent row's ``deleted_at`` with no rule to decide. Every update of a
tracked row (``@tracked *``: location, station, EVSE, connector) first sets the
change context so the history trigger records the reason.
"""

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from geoalchemy2.elements import WKBElement
from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.charging_stations.models import (
    ChargingConnectorModel,
    ChargingConnectorStateModel,
    ChargingEvseModel,
    ChargingLocationAccessModel,
    ChargingLocationModel,
    ChargingStationModel,
    ChargingStationStateModel,
)
from app.domains.charging_stations.types import (
    ChargingConnectorStatus,
    ChargingResourceStatus,
)
from app.libs.common.clock import utc_now
from app.libs.db.history import set_change_context

_ACTIVE = ChargingResourceStatus.ACTIVE.value
_INACTIVE = ChargingResourceStatus.INACTIVE.value


# --- Locations ---------------------------------------------------------------


async def create_charging_location(
    db: AsyncSession,
    *,
    organization_id: UUID,
    display_name: str,
    address: str,
    coordinates: WKBElement,
    is_public: bool,
    status: ChargingResourceStatus = ChargingResourceStatus.ACTIVE,
    status_reason: str | None = None,
) -> ChargingLocationModel:
    """Create a location and flush so constraint violations surface in the transaction.

    Args:
        db: Async session owned by the entry boundary.
        organization_id: The owning organization.
        display_name: Name shown to drivers.
        address: One free-text address line.
        coordinates: Map pin as a PostGIS geography point.
        is_public: Whether anyone may charge there.
        status: Initial status (defaults to ``ACTIVE``).
        status_reason: Why the location has that status, nullable.

    Returns:
        The location that was just persisted.
    """
    location = ChargingLocationModel(
        organization_id=organization_id,
        display_name=display_name,
        address=address,
        coordinates=coordinates,
        is_public=is_public,
        status=status.value,
        status_reason=status_reason,
    )
    db.add(location)
    await db.flush()
    await db.refresh(location)
    return location


async def get_location_by_id(
    db: AsyncSession, location_id: UUID, *, include_deleted: bool = False
) -> ChargingLocationModel | None:
    """Find a location by internal ID (active only by default).

    Args:
        db: Current async session.
        location_id: Internal UUID.
        include_deleted: Whether to include a soft-deleted location.

    Returns:
        The matching location, or ``None``.
    """
    conditions: list[ColumnElement[bool]] = [
        ChargingLocationModel.location_id == location_id
    ]
    if not include_deleted:
        conditions.append(ChargingLocationModel.deleted_at.is_(None))
    result = await db.execute(select(ChargingLocationModel).where(*conditions))
    return result.scalar_one_or_none()


async def list_charging_locations(
    db: AsyncSession, *, offset: int, limit: int
) -> list[ChargingLocationModel]:
    """Get non-soft-deleted locations in a stable order.

    Args:
        db: Current async session.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        Active locations ordered by creation time descending.
    """
    result = await db.execute(
        select(ChargingLocationModel)
        .where(ChargingLocationModel.deleted_at.is_(None))
        .order_by(
            ChargingLocationModel.created_at.desc(),
            ChargingLocationModel.location_id.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_locations(db: AsyncSession) -> int:
    """Count active locations.

    Args:
        db: Current async session.

    Returns:
        Number of non-soft-deleted locations.
    """
    result = await db.execute(
        select(func.count(ChargingLocationModel.location_id)).where(
            ChargingLocationModel.deleted_at.is_(None)
        )
    )
    return int(result.scalar() or 0)


async def update_charging_location(
    db: AsyncSession,
    location_id: UUID,
    update_data: Mapping[str, object],
    *,
    change_reason: str,
) -> ChargingLocationModel | None:
    """Update an active location using fields already filtered by the service.

    Args:
        db: Current async session.
        location_id: UUID of the location to update.
        update_data: Mapping containing only fields allowed to be updated.
        change_reason: Why the row changes, recorded in the change history.

    Returns:
        The updated location, or ``None`` if it is no longer active.

    Side Effects:
        Sets the change context, assigns fields and flushes; does not commit.
    """
    location = await get_location_by_id(db, location_id)
    if location is None:
        return None
    await set_change_context(db, changed_by=None, change_reason=change_reason)
    for field_name, value in update_data.items():
        setattr(location, field_name, value)
    await db.flush()
    await db.refresh(location)
    return location


async def soft_delete_location(
    db: AsyncSession, location_id: UUID, *, status_reason: str
) -> bool:
    """Soft-delete a location with its chargers, EVSEs, connectors and grants.

    A deleted row is also ``INACTIVE`` with the reason (DM-25). The live
    access grants are closed by the system (``revoked_by`` stays ``NULL``).

    Args:
        db: Current async session.
        location_id: UUID of the location to soft-delete.
        status_reason: Why the location left the system.

    Returns:
        ``True`` if an active location existed and was marked.

    Side Effects:
        Updates the location and its children in one flush; does not commit.
    """
    location = await get_location_by_id(db, location_id)
    if location is None:
        return False
    now = utc_now()
    await set_change_context(db, changed_by=None, change_reason=status_reason)
    station_ids = list(
        (
            await db.execute(
                select(ChargingStationModel.station_id).where(
                    ChargingStationModel.location_id == location_id,
                    ChargingStationModel.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for station_id in station_ids:
        await _soft_delete_station_rows(db, station_id, status_reason, now)
    await db.execute(
        update(ChargingLocationAccessModel)
        .where(
            ChargingLocationAccessModel.location_id == location_id,
            ChargingLocationAccessModel.revoked_at.is_(None),
        )
        .values(revoked_at=now, revoke_reason="Location left the system")
    )
    location.status = _INACTIVE
    location.status_reason = status_reason
    location.deleted_at = now
    location.updated_at = now
    await db.flush()
    return True


# --- Location access grants --------------------------------------------------


async def create_location_access(
    db: AsyncSession,
    *,
    location_id: UUID,
    allowed_organization_id: UUID,
    granted_by: UUID,
    valid_until: date | None,
) -> ChargingLocationAccessModel:
    """Insert an access grant and flush so FK and uniqueness errors surface.

    Args:
        db: Async session owned by the entry boundary.
        location_id: The private location.
        allowed_organization_id: The grantee organization.
        granted_by: User who grants the access.
        valid_until: Last valid day (inclusive), or ``None`` for no end.

    Returns:
        The persisted grant.
    """
    access = ChargingLocationAccessModel(
        location_id=location_id,
        allowed_organization_id=allowed_organization_id,
        granted_by=granted_by,
        valid_until=valid_until,
    )
    db.add(access)
    await db.flush()
    await db.refresh(access)
    return access


async def get_live_location_access(
    db: AsyncSession, location_id: UUID, allowed_organization_id: UUID
) -> ChargingLocationAccessModel | None:
    """Find the live (not revoked) grant of an organization at a location.

    Args:
        db: Current async session.
        location_id: The location.
        allowed_organization_id: The grantee organization.

    Returns:
        The live grant, or ``None``.
    """
    result = await db.execute(
        select(ChargingLocationAccessModel).where(
            ChargingLocationAccessModel.location_id == location_id,
            ChargingLocationAccessModel.allowed_organization_id
            == allowed_organization_id,
            ChargingLocationAccessModel.revoked_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def get_location_access_by_id(
    db: AsyncSession, location_id: UUID, access_id: UUID
) -> ChargingLocationAccessModel | None:
    """Find a grant of a location by ID, revoked or not.

    Args:
        db: Current async session.
        location_id: The location the grant belongs to.
        access_id: The grant's ID.

    Returns:
        The grant, or ``None``.
    """
    result = await db.execute(
        select(ChargingLocationAccessModel).where(
            ChargingLocationAccessModel.location_id == location_id,
            ChargingLocationAccessModel.access_id == access_id,
        )
    )
    return result.scalar_one_or_none()


async def list_live_location_access(
    db: AsyncSession, location_id: UUID
) -> list[ChargingLocationAccessModel]:
    """List the live grants of a location, oldest first.

    Args:
        db: Current async session.
        location_id: The location.

    Returns:
        Grants that are not revoked.
    """
    result = await db.execute(
        select(ChargingLocationAccessModel)
        .where(
            ChargingLocationAccessModel.location_id == location_id,
            ChargingLocationAccessModel.revoked_at.is_(None),
        )
        .order_by(
            ChargingLocationAccessModel.granted_at.asc(),
            ChargingLocationAccessModel.access_id.asc(),
        )
    )
    return list(result.scalars().all())


async def revoke_location_access(
    db: AsyncSession,
    access: ChargingLocationAccessModel,
    *,
    revoked_by: UUID | None,
    revoke_reason: str,
) -> ChargingLocationAccessModel:
    """Close a grant (rows are only ever closed, never edited otherwise).

    Args:
        db: Current async session.
        access: A live grant loaded by the caller.
        revoked_by: Who revoked it, ``None`` when the system ended it.
        revoke_reason: Why it ended.

    Returns:
        The closed grant.

    Side Effects:
        Sets the three closing columns and flushes; does not commit.
    """
    access.revoked_at = utc_now()
    access.revoked_by = revoked_by
    access.revoke_reason = revoke_reason
    await db.flush()
    await db.refresh(access)
    return access


# --- Stations ----------------------------------------------------------------


async def create_charging_station(
    db: AsyncSession,
    *,
    location_id: UUID,
    ocpp_identity: str,
    registered_serial_number: str,
    physical_reference: str | None = None,
    max_power_kw: Decimal | None = None,
    status: ChargingResourceStatus = ChargingResourceStatus.ACTIVE,
    status_reason: str | None = None,
) -> ChargingStationModel:
    """Create a station and its (empty) state row, then flush.

    Args:
        db: Async session owned by the entry boundary.
        location_id: The location the charger stands at.
        ocpp_identity: Unique OCPP identity of the station.
        registered_serial_number: Serial read from the nameplate.
        physical_reference: Label printed on the unit, nullable.
        max_power_kw: Total output shared by the guns, nullable.
        status: Initial status (defaults to ``ACTIVE``).
        status_reason: Why the charger has that status, nullable.

    Returns:
        The station that was just persisted.
    """
    station = ChargingStationModel(
        location_id=location_id,
        ocpp_identity=ocpp_identity,
        registered_serial_number=registered_serial_number,
        physical_reference=physical_reference,
        max_power_kw=max_power_kw,
        status=status.value,
        status_reason=status_reason,
    )
    db.add(station)
    await db.flush()
    db.add(ChargingStationStateModel(station_id=station.station_id))
    await db.flush()
    await db.refresh(station)
    return station


async def get_station_by_id(
    db: AsyncSession, station_id: UUID
) -> ChargingStationModel | None:
    """Find an active (not soft-deleted) station by internal ID.

    Args:
        db: Current async session.
        station_id: Internal UUID.

    Returns:
        The matching active station, or ``None``.
    """
    result = await db.execute(
        select(ChargingStationModel).where(
            ChargingStationModel.station_id == station_id,
            ChargingStationModel.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def get_station_by_identity(
    db: AsyncSession, ocpp_identity: str, *, include_deleted: bool = True
) -> ChargingStationModel | None:
    """Find a station by OCPP identity.

    Args:
        db: Current async session.
        ocpp_identity: Business identity to look up.
        include_deleted: Whether to include soft-deleted records. Defaults to
            ``True`` so the service can detect that an identity must not be
            reused.

    Returns:
        The matching station, or ``None``.
    """
    conditions: list[ColumnElement[bool]] = [
        ChargingStationModel.ocpp_identity == ocpp_identity
    ]
    if not include_deleted:
        conditions.append(ChargingStationModel.deleted_at.is_(None))
    result = await db.execute(select(ChargingStationModel).where(*conditions))
    return result.scalar_one_or_none()


async def find_live_station_by_registered_serial(
    db: AsyncSession, registered_serial_number: str
) -> ChargingStationModel | None:
    """Find the station not deleted that carries a registered serial number.

    Args:
        db: Current async session.
        registered_serial_number: Serial read from the nameplate.

    Returns:
        The matching station, or ``None``.
    """
    result = await db.execute(
        select(ChargingStationModel).where(
            ChargingStationModel.registered_serial_number == registered_serial_number,
            ChargingStationModel.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def get_station_state(
    db: AsyncSession, station_id: UUID
) -> ChargingStationStateModel | None:
    """Read what a station reported about itself.

    Args:
        db: Current async session.
        station_id: Internal UUID of the station.

    Returns:
        The state row (created with the station), or ``None``.
    """
    return await db.get(ChargingStationStateModel, station_id)


async def list_charging_stations(
    db: AsyncSession,
    *,
    offset: int,
    limit: int,
) -> list[ChargingStationModel]:
    """Get non-soft-deleted stations in a stable order.

    Args:
        db: Current async session.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        List of active stations ordered by creation time descending.
    """
    result = await db.execute(
        select(ChargingStationModel)
        .where(ChargingStationModel.deleted_at.is_(None))
        .order_by(
            ChargingStationModel.created_at.desc(),
            ChargingStationModel.station_id.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_stations(
    db: AsyncSession,
) -> int:
    """Count active stations.

    Args:
        db: Current async session.

    Returns:
        Number of non-soft-deleted stations.
    """
    result = await db.execute(
        select(func.count(ChargingStationModel.station_id)).where(
            ChargingStationModel.deleted_at.is_(None)
        )
    )
    return int(result.scalar() or 0)


async def list_active_stations_with_location(
    db: AsyncSession,
) -> list[tuple[ChargingStationModel, ChargingLocationModel]]:
    """Get every non-soft-deleted station with its location, unpaginated.

    Used by the all-stations energy report (F-C5), which ranks every station
    and therefore cannot page before sorting.

    Args:
        db: Current async session.

    Returns:
        ``(station, location)`` pairs ordered by the location's name, the
        charger's label, then UUID.
    """
    result = await db.execute(
        select(ChargingStationModel, ChargingLocationModel)
        .join(
            ChargingLocationModel,
            ChargingStationModel.location_id == ChargingLocationModel.location_id,
        )
        .where(ChargingStationModel.deleted_at.is_(None))
        .order_by(
            ChargingLocationModel.display_name.asc(),
            ChargingStationModel.physical_reference.asc().nulls_last(),
            ChargingStationModel.station_id.asc(),
        )
    )
    return [(station, location) for station, location in result.all()]


def _has_connector(*extra: ColumnElement[bool]) -> ColumnElement[bool]:
    """Build an "at least one in-service connector matches" condition for a station.

    Soft-deleted and ``INACTIVE`` EVSEs and soft-deleted connectors never
    count. The connector state is joined only when a condition needs it.

    Args:
        *extra: Further conditions on the connector or its state row.

    Returns:
        A correlated ``EXISTS`` subquery on ``charging_stations.station_id``,
        to be used inside a ``WHERE`` over ``ChargingStationModel``.
    """
    return exists().where(
        ChargingEvseModel.station_id == ChargingStationModel.station_id,
        ChargingEvseModel.deleted_at.is_(None),
        ChargingEvseModel.status == _ACTIVE,
        ChargingConnectorModel.evse_id == ChargingEvseModel.evse_id,
        ChargingConnectorModel.deleted_at.is_(None),
        ChargingConnectorStateModel.connector_id == ChargingConnectorModel.connector_id,
        *extra,
    )


def _has_available_connector() -> ColumnElement[bool]:
    """Build the "at least one free connector" condition for a station row.

    Rule (decision D3 of the happy-path completion planner): a connector is
    free only when its last reported status is ``Available`` (the busy rule
    of ``ChargingConnectorStatus``); one that never reported (``status IS
    NULL``) is not free. The charger's ``is_online`` is deliberately not
    required.

    Returns:
        A correlated ``EXISTS`` subquery, see ``_has_connector``.
    """
    return _has_connector(
        ChargingConnectorStateModel.status == ChargingConnectorStatus.AVAILABLE.value
    )


def _public_active_location_conditions() -> list[ColumnElement[bool]]:
    """Build the conditions of a location that a driver may be shown.

    Until authentication exists (WP2) a search cannot tell who asks, so only
    public locations are returned (CS-10); the location must be ``ACTIVE``
    and not deleted.

    Returns:
        Conditions on ``ChargingLocationModel``.
    """
    return [
        ChargingLocationModel.deleted_at.is_(None),
        ChargingLocationModel.status == _ACTIVE,
        ChargingLocationModel.is_public.is_(True),
    ]


async def find_nearest_station_by_location(
    db: AsyncSession, point: WKBElement
) -> tuple[ChargingStationModel, ChargingLocationModel, float] | None:
    """Find the nearest available station to a point (F-A2).

    "Available" means the charger is ``ACTIVE`` and not deleted, stands at an
    ``ACTIVE`` public location and has at least one connector whose last
    reported status is ``Available`` (``_has_available_connector``); the
    charger's liveness is not consulted.

    Args:
        db: Current async session.
        point: PostGIS geography point to measure distance from.

    Returns:
        ``(station, location, distance in meters)``, or ``None`` if no
        available station exists.

    Side Effects:
        Orders by the ``<->`` KNN operator so the query can use
        ``ix_charging_locations_coordinates`` (GIST) instead of a full scan.
    """
    distance_meters = func.ST_Distance(ChargingLocationModel.coordinates, point)
    result = await db.execute(
        select(ChargingStationModel, ChargingLocationModel, distance_meters)
        .join(
            ChargingLocationModel,
            ChargingStationModel.location_id == ChargingLocationModel.location_id,
        )
        .where(
            ChargingStationModel.deleted_at.is_(None),
            ChargingStationModel.status == _ACTIVE,
            *_public_active_location_conditions(),
            _has_available_connector(),
        )
        .order_by(ChargingLocationModel.coordinates.distance_centroid(point))
        .limit(1)
    )
    row = result.first()
    if row is None:
        return None
    station, location, distance = row
    return station, location, float(distance)


async def list_nearby_stations(
    db: AsyncSession,
    *,
    point: WKBElement,
    radius_meters: float,
    connector_standard: str | None,
    min_power_kw: Decimal | None,
    is_operational_only: bool,
    is_available_only: bool,
    offset: int,
    limit: int,
) -> list[tuple[ChargingStationModel, ChargingLocationModel, float]]:
    """Find stations within a radius of a point, nearest first (F-D1).

    Args:
        db: Current async session.
        point: PostGIS geography point to search around.
        radius_meters: Maximum distance from ``point``, in meters.
        connector_standard: Exact-match filter on a gun's plug standard (an
            in-service gun of that standard), or ``None`` to not filter by it.
        min_power_kw: Minimum charger ``max_power_kw``, or ``None``.
        is_operational_only: Whether to only return ``ACTIVE`` chargers.
        is_available_only: Whether to only return operational chargers with
            at least one ``Available`` connector (the F-A2 rule).
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        ``(station, location, distance in meters)`` ordered nearest first.

    Side Effects:
        Filters with ``ST_DWithin`` (meters on a geography column) and orders
        by the ``<->`` KNN operator, so the query can use the GIST index of
        ``charging_locations.coordinates``.
    """
    conditions = _nearby_station_conditions(
        point,
        radius_meters=radius_meters,
        connector_standard=connector_standard,
        min_power_kw=min_power_kw,
        is_operational_only=is_operational_only,
        is_available_only=is_available_only,
    )
    distance_meters = func.ST_Distance(ChargingLocationModel.coordinates, point)
    result = await db.execute(
        select(ChargingStationModel, ChargingLocationModel, distance_meters)
        .join(
            ChargingLocationModel,
            ChargingStationModel.location_id == ChargingLocationModel.location_id,
        )
        .where(*conditions)
        .order_by(ChargingLocationModel.coordinates.distance_centroid(point))
        .offset(offset)
        .limit(limit)
    )
    return [
        (station, location, float(distance))
        for station, location, distance in result.all()
    ]


async def count_nearby_stations(
    db: AsyncSession,
    *,
    point: WKBElement,
    radius_meters: float,
    connector_standard: str | None,
    min_power_kw: Decimal | None,
    is_operational_only: bool,
    is_available_only: bool,
) -> int:
    """Count stations within a radius of a point, with the same filters as
    ``list_nearby_stations``.

    Args:
        db: Current async session.
        point: PostGIS geography point to search around.
        radius_meters: Maximum distance from ``point``, in meters.
        connector_standard: Plug standard filter, or ``None``.
        min_power_kw: Minimum charger power, or ``None``.
        is_operational_only: Whether to only count ``ACTIVE`` chargers.
        is_available_only: Whether to only count operational chargers with
            at least one ``Available`` connector.

    Returns:
        Number of matching stations.
    """
    conditions = _nearby_station_conditions(
        point,
        radius_meters=radius_meters,
        connector_standard=connector_standard,
        min_power_kw=min_power_kw,
        is_operational_only=is_operational_only,
        is_available_only=is_available_only,
    )
    result = await db.execute(
        select(func.count(ChargingStationModel.station_id))
        .join(
            ChargingLocationModel,
            ChargingStationModel.location_id == ChargingLocationModel.location_id,
        )
        .where(*conditions)
    )
    return int(result.scalar() or 0)


def _nearby_station_conditions(
    point: WKBElement,
    *,
    radius_meters: float,
    connector_standard: str | None,
    min_power_kw: Decimal | None,
    is_operational_only: bool,
    is_available_only: bool,
) -> list[ColumnElement[bool]]:
    """Build the shared WHERE conditions for a nearby-station query (F-D1).

    Args:
        point: PostGIS geography point to search around.
        radius_meters: Maximum distance from ``point``, in meters.
        connector_standard: Plug standard filter, or ``None`` to skip it.
        min_power_kw: Minimum power filter, or ``None`` to skip it.
        is_operational_only: Whether to require an ``ACTIVE`` charger.
        is_available_only: Whether to require an ``ACTIVE`` charger and at
            least one ``Available`` connector; implies ``is_operational_only``.

    Returns:
        Conditions shared by ``list_nearby_stations`` and
        ``count_nearby_stations``, so the two queries can never drift apart.
        They require a not-deleted, public, ``ACTIVE`` location.
    """
    conditions: list[ColumnElement[bool]] = [
        ChargingStationModel.deleted_at.is_(None),
        *_public_active_location_conditions(),
        func.ST_DWithin(ChargingLocationModel.coordinates, point, radius_meters),
    ]
    if is_operational_only or is_available_only:
        conditions.append(ChargingStationModel.status == _ACTIVE)
    if is_available_only:
        conditions.append(_has_available_connector())
    if connector_standard is not None:
        conditions.append(
            _has_connector(ChargingConnectorModel.standard == connector_standard)
        )
    if min_power_kw is not None:
        conditions.append(ChargingStationModel.max_power_kw >= min_power_kw)
    return conditions


async def count_connectors_by_station_id(db: AsyncSession, station_id: UUID) -> int:
    """Count active connectors across all active EVSEs of a station.

    Args:
        db: Current async session.
        station_id: UUID of the parent station.

    Returns:
        Number of active connectors across all active EVSEs of the station.
    """
    result = await db.execute(
        select(func.count(ChargingConnectorModel.connector_id))
        .select_from(ChargingConnectorModel)
        .join(
            ChargingEvseModel,
            ChargingConnectorModel.evse_id == ChargingEvseModel.evse_id,
        )
        .where(
            ChargingEvseModel.station_id == station_id,
            ChargingEvseModel.deleted_at.is_(None),
            ChargingConnectorModel.deleted_at.is_(None),
        )
    )
    return int(result.scalar() or 0)


async def count_available_connectors_by_station_id(
    db: AsyncSession, station_id: UUID
) -> int:
    """Count a station's connectors whose last reported status is ``Available``.

    Args:
        db: Current async session.
        station_id: UUID of the parent station.

    Returns:
        Number of active connectors, across the station's active EVSEs,
        whose reported status is ``Available`` (F-D1). A connector that never
        reported is not counted.
    """
    result = await db.execute(
        select(func.count(ChargingConnectorModel.connector_id))
        .select_from(ChargingConnectorModel)
        .join(
            ChargingEvseModel,
            ChargingConnectorModel.evse_id == ChargingEvseModel.evse_id,
        )
        .join(
            ChargingConnectorStateModel,
            ChargingConnectorStateModel.connector_id
            == ChargingConnectorModel.connector_id,
        )
        .where(
            ChargingEvseModel.station_id == station_id,
            ChargingEvseModel.deleted_at.is_(None),
            ChargingConnectorModel.deleted_at.is_(None),
            ChargingConnectorStateModel.status
            == ChargingConnectorStatus.AVAILABLE.value,
        )
    )
    return int(result.scalar() or 0)


async def list_connectors_by_station_id(
    db: AsyncSession, station_id: UUID
) -> list[tuple[ChargingConnectorModel, int, ChargingConnectorStateModel | None]]:
    """Get every active connector of a station with its EVSE number and state (F-C2).

    Unpaginated: a station has a handful of guns and the status view needs
    all of them at once.

    Args:
        db: Current async session.
        station_id: UUID of the parent station.

    Returns:
        ``(connector, ocpp_evse_id, state)`` triples for the active connectors
        of the station's active EVSEs, ordered by OCPP EVSE number, then OCPP
        connector number.
    """
    result = await db.execute(
        select(
            ChargingConnectorModel,
            ChargingEvseModel.ocpp_evse_id,
            ChargingConnectorStateModel,
        )
        .join(
            ChargingEvseModel,
            ChargingConnectorModel.evse_id == ChargingEvseModel.evse_id,
        )
        .outerjoin(
            ChargingConnectorStateModel,
            ChargingConnectorStateModel.connector_id
            == ChargingConnectorModel.connector_id,
        )
        .where(
            ChargingEvseModel.station_id == station_id,
            ChargingEvseModel.deleted_at.is_(None),
            ChargingConnectorModel.deleted_at.is_(None),
        )
        .order_by(
            ChargingEvseModel.ocpp_evse_id.asc(),
            ChargingConnectorModel.ocpp_connector_id.asc(),
        )
    )
    return [
        (connector, int(ocpp_evse_id), state)
        for connector, ocpp_evse_id, state in result.all()
    ]


async def update_charging_station(
    db: AsyncSession,
    station_id: UUID,
    update_data: Mapping[str, object],
    *,
    change_reason: str,
) -> ChargingStationModel | None:
    """Update an active station using fields already filtered by the service.

    Args:
        db: Current async session.
        station_id: UUID of the station to update.
        update_data: Mapping containing only fields allowed to be updated.
        change_reason: Why the row changes, recorded in the change history.

    Returns:
        The updated station, or None if it is no longer active.

    Side Effects:
        Sets the change context, assigns fields (``updated_at`` follows by the
        model's ``onupdate``) and flushes; does not commit.
    """
    station = await get_station_by_id(db, station_id)
    if station is None:
        return None
    await set_change_context(db, changed_by=None, change_reason=change_reason)
    for field_name, value in update_data.items():
        setattr(station, field_name, value)
    await db.flush()
    await db.refresh(station)
    return station


async def _soft_delete_station_rows(
    db: AsyncSession, station_id: UUID, status_reason: str, now: datetime
) -> None:
    """Mark a station and its EVSEs and connectors as left the system (DM-25).

    Connectors go before EVSEs and the station last, to keep the child
    topology consistent within the transaction.

    Args:
        db: Current async session; the change context is already set.
        station_id: UUID of the station.
        status_reason: Why the rows left the system.
        now: The deletion time.
    """
    evse_ids = list(
        (
            await db.execute(
                select(ChargingEvseModel.evse_id).where(
                    ChargingEvseModel.station_id == station_id,
                    ChargingEvseModel.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    closed = {
        "status": _INACTIVE,
        "status_reason": status_reason,
        "deleted_at": now,
        "updated_at": now,
    }
    if evse_ids:
        await db.execute(
            update(ChargingConnectorModel)
            .where(
                ChargingConnectorModel.evse_id.in_(evse_ids),
                ChargingConnectorModel.deleted_at.is_(None),
            )
            .values(deleted_at=now, updated_at=now)
        )
        await db.execute(
            update(ChargingEvseModel)
            .where(ChargingEvseModel.evse_id.in_(evse_ids))
            .values(**closed)
        )
    await db.execute(
        update(ChargingStationModel)
        .where(ChargingStationModel.station_id == station_id)
        .values(**closed)
    )


async def soft_delete_station(
    db: AsyncSession, station_id: UUID, *, status_reason: str
) -> bool:
    """Soft-delete a station and all active EVSEs/connectors that belong to it.

    The cascade only updates ``deleted_at`` (and the closing status of
    stations and EVSEs, DM-25); it does not physically delete history or
    topology records, preserving business identity and foreign keys for audit
    purposes.

    Args:
        db: Current async session.
        station_id: UUID of the station to soft-delete.
        status_reason: Why the charger left the system.

    Returns:
        True if an active station existed and was marked; False otherwise.
    """
    station = await get_station_by_id(db, station_id)
    if station is None:
        return False
    await set_change_context(db, changed_by=None, change_reason=status_reason)
    await _soft_delete_station_rows(db, station_id, status_reason, utc_now())
    await db.flush()
    return True


# --- EVSEs -------------------------------------------------------------------


async def create_charging_evse(
    db: AsyncSession,
    *,
    station_id: UUID,
    ocpp_evse_id: int,
    emi3_evse_id: str,
    status: ChargingResourceStatus = ChargingResourceStatus.ACTIVE,
    status_reason: str | None = None,
) -> ChargingEvseModel:
    """Create an EVSE and flush constraints/FKs within the current transaction.

    Args:
        db: Current async session.
        station_id: UUID of the parent station.
        ocpp_evse_id: Positive EVSE identity within the station.
        emi3_evse_id: Public eMI3 ID of the outlet.
        status: Initial status (defaults to ``ACTIVE``).
        status_reason: Why the EVSE has that status, nullable.

    Returns:
        The EVSE ORM object that was just persisted.

    Side Effects:
        Adds the record, flushes, and refreshes generated values; does not
        commit.
    """
    evse = ChargingEvseModel(
        station_id=station_id,
        ocpp_evse_id=ocpp_evse_id,
        emi3_evse_id=emi3_evse_id,
        status=status.value,
        status_reason=status_reason,
    )
    db.add(evse)
    await db.flush()
    await db.refresh(evse)
    return evse


async def get_evse_by_id(db: AsyncSession, evse_id: UUID) -> ChargingEvseModel | None:
    """Find an active (not soft-deleted) EVSE by internal ID.

    Args:
        db: Current async session.
        evse_id: UUID of the EVSE to query.

    Returns:
        The matching active EVSE, or ``None``.
    """
    result = await db.execute(
        select(ChargingEvseModel).where(
            ChargingEvseModel.evse_id == evse_id,
            ChargingEvseModel.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def get_evse_by_identity(
    db: AsyncSession,
    station_id: UUID,
    ocpp_evse_id: int,
    *,
    include_deleted: bool = True,
) -> ChargingEvseModel | None:
    """Find an EVSE by its composite station/OCPP ID identity.

    Args:
        db: Current async session.
        station_id: UUID of the parent station.
        ocpp_evse_id: EVSE identity within the station.
        include_deleted: Whether to include soft-deleted identities.

    Returns:
        The matching EVSE, or ``None``.
    """
    conditions: list[ColumnElement[bool]] = [
        ChargingEvseModel.station_id == station_id,
        ChargingEvseModel.ocpp_evse_id == ocpp_evse_id,
    ]
    if not include_deleted:
        conditions.append(ChargingEvseModel.deleted_at.is_(None))
    result = await db.execute(select(ChargingEvseModel).where(*conditions))
    return result.scalar_one_or_none()


async def find_live_evse_by_emi3_id(
    db: AsyncSession, emi3_evse_id: str
) -> ChargingEvseModel | None:
    """Find the EVSE not deleted that carries a public eMI3 ID.

    Args:
        db: Current async session.
        emi3_evse_id: The public eMI3 ID.

    Returns:
        The matching EVSE, or ``None``.
    """
    result = await db.execute(
        select(ChargingEvseModel).where(
            ChargingEvseModel.emi3_evse_id == emi3_evse_id,
            ChargingEvseModel.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def list_charging_evses(
    db: AsyncSession, *, station_id: UUID, offset: int, limit: int
) -> list[ChargingEvseModel]:
    """Get active EVSEs of a station in a stable order.

    Args:
        db: Current async session.
        station_id: UUID of the parent station.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        List of active EVSEs.
    """
    result = await db.execute(
        select(ChargingEvseModel)
        .where(
            ChargingEvseModel.station_id == station_id,
            ChargingEvseModel.deleted_at.is_(None),
        )
        .order_by(ChargingEvseModel.created_at.asc(), ChargingEvseModel.evse_id.asc())
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_evses(db: AsyncSession, station_id: UUID) -> int:
    """Count active EVSEs belonging to a station.

    Args:
        db: Current async session.
        station_id: UUID of the parent station.

    Returns:
        Number of active EVSEs.
    """
    result = await db.execute(
        select(func.count(ChargingEvseModel.evse_id)).where(
            ChargingEvseModel.station_id == station_id,
            ChargingEvseModel.deleted_at.is_(None),
        )
    )
    return int(result.scalar() or 0)


async def update_charging_evse(
    db: AsyncSession,
    evse_id: UUID,
    update_data: Mapping[str, object],
    *,
    change_reason: str,
) -> ChargingEvseModel | None:
    """Update an active EVSE using fields already validated by the service.

    Args:
        db: Current async session.
        evse_id: UUID of the EVSE to update.
        update_data: Mapping of fields that passed business validation.
        change_reason: Why the row changes, recorded in the change history.

    Returns:
        The updated EVSE, or ``None`` if it is no longer active.

    Side Effects:
        Sets the change context, assigns fields, flushes and refreshes; does
        not commit.
    """
    evse = await get_evse_by_id(db, evse_id)
    if evse is None:
        return None
    await set_change_context(db, changed_by=None, change_reason=change_reason)
    for field_name, value in update_data.items():
        setattr(evse, field_name, value)
    await db.flush()
    await db.refresh(evse)
    return evse


async def soft_delete_evse(
    db: AsyncSession, evse_id: UUID, *, status_reason: str
) -> bool:
    """Soft-delete an EVSE and its active connectors.

    Args:
        db: Current async session.
        evse_id: UUID of the EVSE to soft-delete.
        status_reason: Why the EVSE left the system.

    Returns:
        ``True`` if an active EVSE existed; ``False`` if not found.

    Side Effects:
        Marks the EVSE (``INACTIVE`` with the reason, DM-25) and its child
        connectors, then flushes; does not commit.
    """
    evse = await get_evse_by_id(db, evse_id)
    if evse is None:
        return False
    now = utc_now()
    await set_change_context(db, changed_by=None, change_reason=status_reason)
    await db.execute(
        update(ChargingConnectorModel)
        .where(
            ChargingConnectorModel.evse_id == evse_id,
            ChargingConnectorModel.deleted_at.is_(None),
        )
        .values(deleted_at=now, updated_at=now)
    )
    evse.status = _INACTIVE
    evse.status_reason = status_reason
    evse.deleted_at = now
    evse.updated_at = now
    await db.flush()
    return True


# --- Connectors --------------------------------------------------------------


async def create_charging_connector(
    db: AsyncSession,
    *,
    evse_id: UUID,
    ocpp_connector_id: int,
    standard: str,
    max_power_kw: Decimal,
    max_voltage_v: int,
    max_current_a: int,
) -> ChargingConnectorModel:
    """Create a connector and its (empty) state row, then flush.

    Args:
        db: Current async session.
        evse_id: UUID of the parent EVSE.
        ocpp_connector_id: Positive connector identity within the EVSE.
        standard: Plug standard (``ConnectorStandard`` value).
        max_power_kw: Highest power of the gun, in kW.
        max_voltage_v: Highest output voltage, in volts.
        max_current_a: Highest output current, in amperes.

    Returns:
        The connector ORM object that was just persisted.

    Side Effects:
        Adds the records, flushes, and refreshes generated values; does not
        commit.
    """
    connector = ChargingConnectorModel(
        evse_id=evse_id,
        ocpp_connector_id=ocpp_connector_id,
        standard=standard,
        max_power_kw=max_power_kw,
        max_voltage_v=max_voltage_v,
        max_current_a=max_current_a,
    )
    db.add(connector)
    await db.flush()
    db.add(ChargingConnectorStateModel(connector_id=connector.connector_id))
    await db.flush()
    await db.refresh(connector)
    return connector


async def get_connector_state(
    db: AsyncSession, connector_id: UUID
) -> ChargingConnectorStateModel | None:
    """Read the status a connector reported.

    Args:
        db: Current async session.
        connector_id: Internal UUID of the connector.

    Returns:
        The state row (created with the connector), or ``None``.
    """
    return await db.get(ChargingConnectorStateModel, connector_id)


async def get_connector_by_id(
    db: AsyncSession, connector_id: UUID
) -> ChargingConnectorModel | None:
    """Find an active (not soft-deleted) connector by internal ID.

    Args:
        db: Current async session.
        connector_id: UUID of the connector to query.

    Returns:
        The matching active connector, or ``None``.
    """
    result = await db.execute(
        select(ChargingConnectorModel).where(
            ChargingConnectorModel.connector_id == connector_id,
            ChargingConnectorModel.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def get_connector_by_identity(
    db: AsyncSession,
    evse_id: UUID,
    ocpp_connector_id: int,
    *,
    include_deleted: bool = True,
) -> ChargingConnectorModel | None:
    """Find a connector by its composite EVSE/OCPP ID identity.

    Args:
        db: Current async session.
        evse_id: UUID of the parent EVSE.
        ocpp_connector_id: Connector identity within the EVSE.
        include_deleted: Whether to include soft-deleted identities.

    Returns:
        The matching connector, or ``None``.
    """
    conditions: list[ColumnElement[bool]] = [
        ChargingConnectorModel.evse_id == evse_id,
        ChargingConnectorModel.ocpp_connector_id == ocpp_connector_id,
    ]
    if not include_deleted:
        conditions.append(ChargingConnectorModel.deleted_at.is_(None))
    result = await db.execute(select(ChargingConnectorModel).where(*conditions))
    return result.scalar_one_or_none()


async def list_charging_connectors(
    db: AsyncSession, *, evse_id: UUID, offset: int, limit: int
) -> list[ChargingConnectorModel]:
    """Get active connectors of an EVSE in a stable order.

    Args:
        db: Current async session.
        evse_id: UUID of the parent EVSE.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        List of active connectors.
    """
    result = await db.execute(
        select(ChargingConnectorModel)
        .where(
            ChargingConnectorModel.evse_id == evse_id,
            ChargingConnectorModel.deleted_at.is_(None),
        )
        .order_by(
            ChargingConnectorModel.created_at.asc(),
            ChargingConnectorModel.connector_id.asc(),
        )
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_connectors(db: AsyncSession, evse_id: UUID) -> int:
    """Count active connectors belonging to an EVSE.

    Args:
        db: Current async session.
        evse_id: UUID of the parent EVSE.

    Returns:
        Number of active connectors.
    """
    result = await db.execute(
        select(func.count(ChargingConnectorModel.connector_id)).where(
            ChargingConnectorModel.evse_id == evse_id,
            ChargingConnectorModel.deleted_at.is_(None),
        )
    )
    return int(result.scalar() or 0)


async def update_charging_connector(
    db: AsyncSession,
    connector_id: UUID,
    update_data: Mapping[str, object],
    *,
    change_reason: str,
) -> ChargingConnectorModel | None:
    """Update an active connector using fields already validated by the service.

    Args:
        db: Current async session.
        connector_id: UUID of the connector to update.
        update_data: Mapping of fields that passed business validation.
        change_reason: Why the row changes, recorded in the change history.

    Returns:
        The updated connector, or ``None`` if it is no longer active.

    Side Effects:
        Sets the change context, assigns fields, flushes and refreshes; does
        not commit.
    """
    connector = await get_connector_by_id(db, connector_id)
    if connector is None:
        return None
    await set_change_context(db, changed_by=None, change_reason=change_reason)
    for field_name, value in update_data.items():
        setattr(connector, field_name, value)
    await db.flush()
    await db.refresh(connector)
    return connector


async def soft_delete_connector(
    db: AsyncSession, connector_id: UUID, *, change_reason: str
) -> bool:
    """Mark a connector as soft-deleted without physically deleting the record.

    Args:
        db: Current async session.
        connector_id: UUID of the connector to soft-delete.
        change_reason: Why the gun left the system, recorded in the history.

    Returns:
        ``True`` if an active connector existed; ``False`` if not found.

    Side Effects:
        Updates ``deleted_at`` and ``updated_at``, then flushes; does not
        commit. (A gun has no status column: it is taken out of service
        through its EVSE, CS-16.)
    """
    connector = await get_connector_by_id(db, connector_id)
    if connector is None:
        return False
    now = utc_now()
    await set_change_context(db, changed_by=None, change_reason=change_reason)
    connector.deleted_at = now
    connector.updated_at = now
    await db.flush()
    return True
