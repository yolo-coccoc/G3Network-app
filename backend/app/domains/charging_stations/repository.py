"""Asynchronous repository for charging station topology and directory queries.

Holds the station/EVSE/connector CRUD queries, the identity lookups used for
conflict checks and OCPP resolution, the connector counts, and the PostGIS
nearest/nearby searches (F-A2, F-D1). The queries for state a charger reports
about itself (frame log, liveness, boot info, status, configuration captures)
live in ``ocpp_state_repository.py``.

The repository only queries and flushes data; it never commits or rolls back.
Business checks — parent existence and identity conflicts — belong to the
service. The soft-delete cascade (station -> EVSEs -> connectors, EVSE ->
connectors) is done here, inside one flush, because it is a pure consequence
of the parent row's ``deleted_at`` with no rule to decide.
"""

from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID

from geoalchemy2.elements import WKBElement
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.charging_stations.models import (
    ChargingConnectorModel,
    ChargingEvseModel,
    ChargingStationModel,
)
from app.domains.charging_stations.types import (
    ChargingStationMaintenanceStatus,
)
from app.libs.common.clock import utc_now


async def create_charging_station(
    db: AsyncSession,
    *,
    ocpp_identity: str,
    display_name: str,
    location: WKBElement | None = None,
    power_rating_kw: Decimal | None = None,
    connector_standard: str | None = None,
    operating_hours: str | None = None,
    maintenance_status: ChargingStationMaintenanceStatus = (
        ChargingStationMaintenanceStatus.OPERATIONAL
    ),
) -> ChargingStationModel:
    """Create a station and flush to surface constraint violations within the transaction.

    Args:
        db: Async session owned by the entry boundary.
        ocpp_identity: Unique OCPP identity of the station.
        display_name: Display name.
        location: GPS location as a PostGIS geography point, nullable.
        power_rating_kw: Nominal power rating in kW, nullable.
        connector_standard: Connector standard served (e.g. ``"CCS2"``), nullable.
        operating_hours: Freeform operating hours description, nullable.
        maintenance_status: Admin-set maintenance state; defaults to
            ``OPERATIONAL``.

    Returns:
        The station that was just persisted.
    """
    station = ChargingStationModel(
        ocpp_identity=ocpp_identity,
        display_name=display_name,
        location=location,
        power_rating_kw=power_rating_kw,
        connector_standard=connector_standard,
        operating_hours=operating_hours,
        maintenance_status=maintenance_status,
    )
    db.add(station)
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


async def find_nearest_station_by_location(
    db: AsyncSession, location: WKBElement
) -> tuple[ChargingStationModel, float] | None:
    """Find the nearest active, operational station to a point (F-A2).

    Args:
        db: Current async session.
        location: PostGIS geography point to measure distance from.

    Returns:
        A tuple of the nearest matching station and its distance in meters,
        or ``None`` if no active/operational station has a location set.

    Side Effects:
        Orders by the ``<->`` KNN operator so the query can use
        ``ix_charging_stations_location`` (GIST) instead of a full scan.
    """
    distance_meters = func.ST_Distance(ChargingStationModel.location, location)
    result = await db.execute(
        select(ChargingStationModel, distance_meters)
        .where(
            ChargingStationModel.deleted_at.is_(None),
            ChargingStationModel.location.is_not(None),
            ChargingStationModel.maintenance_status
            == ChargingStationMaintenanceStatus.OPERATIONAL,
        )
        .order_by(ChargingStationModel.location.distance_centroid(location))
        .limit(1)
    )
    row = result.first()
    if row is None:
        return None
    station, distance = row
    return station, float(distance)


async def list_nearby_stations(
    db: AsyncSession,
    *,
    location: WKBElement,
    radius_meters: float,
    connector_standard: str | None,
    min_power_kw: Decimal | None,
    is_operational_only: bool,
    offset: int,
    limit: int,
) -> list[tuple[ChargingStationModel, float]]:
    """Find stations within a radius of a point, nearest first (F-D1).

    Args:
        db: Current async session.
        location: PostGIS geography point to search around.
        radius_meters: Maximum distance from ``location``, in meters.
        connector_standard: Exact-match filter on the connector standard
            (e.g. ``"CCS2"``), or ``None`` to not filter by it.
        min_power_kw: Minimum ``power_rating_kw``, or ``None`` to not
            filter by it.
        is_operational_only: Whether to only return stations with
            ``maintenance_status == OPERATIONAL``. Same approximation of
            "available" as ``find_nearest_station_by_location`` (F-A2) -
            admin-set, not a live occupancy signal.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        Matching stations paired with their distance in meters, ordered
        nearest first.

    Side Effects:
        Filters with ``ST_DWithin`` (native meters on a geography column)
        and orders by the ``<->`` KNN operator, so the query can use
        ``ix_charging_stations_location`` (GIST) for both the radius filter
        and the ordering.
    """
    conditions = _nearby_station_conditions(
        location,
        radius_meters=radius_meters,
        connector_standard=connector_standard,
        min_power_kw=min_power_kw,
        is_operational_only=is_operational_only,
    )
    distance_meters = func.ST_Distance(ChargingStationModel.location, location)
    result = await db.execute(
        select(ChargingStationModel, distance_meters)
        .where(*conditions)
        .order_by(ChargingStationModel.location.distance_centroid(location))
        .offset(offset)
        .limit(limit)
    )
    return [(station, float(distance)) for station, distance in result.all()]


async def count_nearby_stations(
    db: AsyncSession,
    *,
    location: WKBElement,
    radius_meters: float,
    connector_standard: str | None,
    min_power_kw: Decimal | None,
    is_operational_only: bool,
) -> int:
    """Count stations within a radius of a point, with the same filters as
    ``list_nearby_stations``.

    Args:
        db: Current async session.
        location: PostGIS geography point to search around.
        radius_meters: Maximum distance from ``location``, in meters.
        connector_standard: Exact-match filter on the connector standard,
            or ``None`` to not filter by it.
        min_power_kw: Minimum ``power_rating_kw``, or ``None`` to not
            filter by it.
        is_operational_only: Whether to only count stations with
            ``maintenance_status == OPERATIONAL``.

    Returns:
        Number of matching stations.
    """
    conditions = _nearby_station_conditions(
        location,
        radius_meters=radius_meters,
        connector_standard=connector_standard,
        min_power_kw=min_power_kw,
        is_operational_only=is_operational_only,
    )
    result = await db.execute(
        select(func.count(ChargingStationModel.station_id)).where(*conditions)
    )
    return int(result.scalar() or 0)


def _nearby_station_conditions(
    location: WKBElement,
    *,
    radius_meters: float,
    connector_standard: str | None,
    min_power_kw: Decimal | None,
    is_operational_only: bool,
) -> list[ColumnElement[bool]]:
    """Build the shared WHERE conditions for a nearby-station query (F-D1).

    Args:
        location: PostGIS geography point to search around.
        radius_meters: Maximum distance from ``location``, in meters.
        connector_standard: Exact-match filter, or ``None`` to skip it.
        min_power_kw: Minimum power filter, or ``None`` to skip it.
        is_operational_only: Whether to require ``maintenance_status ==
            OPERATIONAL``.

    Returns:
        Conditions shared by ``list_nearby_stations`` and
        ``count_nearby_stations``, so the two queries can never drift apart.
    """
    conditions: list[ColumnElement[bool]] = [
        ChargingStationModel.deleted_at.is_(None),
        ChargingStationModel.location.is_not(None),
        func.ST_DWithin(ChargingStationModel.location, location, radius_meters),
    ]
    if is_operational_only:
        conditions.append(
            ChargingStationModel.maintenance_status
            == ChargingStationMaintenanceStatus.OPERATIONAL
        )
    if connector_standard is not None:
        conditions.append(ChargingStationModel.connector_standard == connector_standard)
    if min_power_kw is not None:
        conditions.append(ChargingStationModel.power_rating_kw >= min_power_kw)
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


async def update_charging_station(
    db: AsyncSession, station_id: UUID, update_data: Mapping[str, object]
) -> ChargingStationModel | None:
    """Update an active station using fields already filtered by the service.

    Args:
        db: Current async session.
        station_id: UUID of the station to update.
        update_data: Mapping containing only fields allowed to be updated.

    Returns:
        The updated station, or None if it is no longer active.

    Side Effects:
        Assigns fields, updates ``updated_at``, and flushes; does not commit.
    """
    station = await get_station_by_id(db, station_id)
    if station is None:
        return None
    for field_name, value in update_data.items():
        setattr(station, field_name, value)
    station.updated_at = utc_now()
    await db.flush()
    await db.refresh(station)
    return station


async def soft_delete_station(db: AsyncSession, station_id: UUID) -> bool:
    """Soft-delete a station and all active EVSEs/connectors that belong to it.

    The cascade only updates ``deleted_at``; it does not physically delete
    history or topology records, preserving business identity and foreign
    keys for audit purposes.

    Args:
        db: Current async session.
        station_id: UUID of the station to soft-delete.

    Returns:
        True if an active station existed and was marked; False otherwise.
    """
    station = await get_station_by_id(db, station_id)
    if station is None:
        return False

    now = utc_now()
    evse_result = await db.execute(
        select(ChargingEvseModel.evse_id).where(
            ChargingEvseModel.station_id == station_id,
            ChargingEvseModel.deleted_at.is_(None),
        )
    )
    evse_ids = list(evse_result.scalars().all())
    if evse_ids:
        # Update connectors before EVSEs to keep the child topology consistent
        # within the same transaction, even if the caller rolls back at the
        # entry boundary.
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
            .values(deleted_at=now, updated_at=now)
        )
    station.deleted_at = now
    station.updated_at = now
    await db.flush()
    return True


async def create_charging_evse(
    db: AsyncSession,
    *,
    station_id: UUID,
    ocpp_evse_id: int,
) -> ChargingEvseModel:
    """Create an EVSE and flush constraints/FKs within the current transaction.

    Args:
        db: Current async session.
        station_id: UUID of the parent station.
        ocpp_evse_id: Positive EVSE identity within the station.

    Returns:
        The EVSE ORM object that was just persisted.

    Side Effects:
        Adds the record, flushes, and refreshes generated values; does not
        commit.
    """
    evse = ChargingEvseModel(
        station_id=station_id,
        ocpp_evse_id=ocpp_evse_id,
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
    db: AsyncSession, evse_id: UUID, update_data: Mapping[str, object]
) -> ChargingEvseModel | None:
    """Update an active EVSE using fields already validated by the service.

    Args:
        db: Current async session.
        evse_id: UUID of the EVSE to update.
        update_data: Mapping of fields that passed business validation.

    Returns:
        The updated EVSE, or ``None`` if it is no longer active.

    Side Effects:
        Assigns fields, updates the timestamp, flushes, and refreshes; does
        not commit.
    """
    evse = await get_evse_by_id(db, evse_id)
    if evse is None:
        return None
    for field_name, value in update_data.items():
        setattr(evse, field_name, value)
    evse.updated_at = utc_now()
    await db.flush()
    await db.refresh(evse)
    return evse


async def soft_delete_evse(db: AsyncSession, evse_id: UUID) -> bool:
    """Soft-delete an EVSE and its active connectors.

    Args:
        db: Current async session.
        evse_id: UUID of the EVSE to soft-delete.

    Returns:
        ``True`` if an active EVSE existed; ``False`` if not found.

    Side Effects:
        Updates the timestamp of the EVSE and its child connectors, then
        flushes; does not commit.
    """
    evse = await get_evse_by_id(db, evse_id)
    if evse is None:
        return False
    now = utc_now()
    await db.execute(
        update(ChargingConnectorModel)
        .where(
            ChargingConnectorModel.evse_id == evse_id,
            ChargingConnectorModel.deleted_at.is_(None),
        )
        .values(deleted_at=now, updated_at=now)
    )
    evse.deleted_at = now
    evse.updated_at = now
    await db.flush()
    return True


async def create_charging_connector(
    db: AsyncSession,
    *,
    evse_id: UUID,
    ocpp_connector_id: int,
) -> ChargingConnectorModel:
    """Create a connector and flush constraints/FKs within the current transaction.

    Args:
        db: Current async session.
        evse_id: UUID of the parent EVSE.
        ocpp_connector_id: Positive connector identity within the EVSE.

    Returns:
        The connector ORM object that was just persisted.

    Side Effects:
        Adds the record, flushes, and refreshes generated values; does not
        commit.
    """
    connector = ChargingConnectorModel(
        evse_id=evse_id,
        ocpp_connector_id=ocpp_connector_id,
    )
    db.add(connector)
    await db.flush()
    await db.refresh(connector)
    return connector


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
    db: AsyncSession, connector_id: UUID, update_data: Mapping[str, object]
) -> ChargingConnectorModel | None:
    """Update an active connector using fields already validated by the service.

    Args:
        db: Current async session.
        connector_id: UUID of the connector to update.
        update_data: Mapping of fields that passed business validation.

    Returns:
        The updated connector, or ``None`` if it is no longer active.

    Side Effects:
        Assigns fields, updates the timestamp, flushes, and refreshes; does
        not commit.
    """
    connector = await get_connector_by_id(db, connector_id)
    if connector is None:
        return None
    for field_name, value in update_data.items():
        setattr(connector, field_name, value)
    connector.updated_at = utc_now()
    await db.flush()
    await db.refresh(connector)
    return connector


async def soft_delete_connector(db: AsyncSession, connector_id: UUID) -> bool:
    """Mark a connector as soft-deleted without physically deleting the record.

    Args:
        db: Current async session.
        connector_id: UUID of the connector to soft-delete.

    Returns:
        ``True`` if an active connector existed; ``False`` if not found.

    Side Effects:
        Updates ``deleted_at`` and ``updated_at``, then flushes; does not
        commit.
    """
    connector = await get_connector_by_id(db, connector_id)
    if connector is None:
        return False
    now = utc_now()
    connector.deleted_at = now
    connector.updated_at = now
    await db.flush()
    return True
