"""Repository bất đồng bộ cho topology charging station.

Repository chỉ truy vấn và flush dữ liệu; không commit hoặc rollback transaction.
Các kiểm tra thuộc ranh giới nghiệp vụ như parent tồn tại, conflict identity và
cascade soft-delete được service điều phối bằng các hàm công khai ở đây.
"""

from collections.abc import Mapping
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.charging_stations.models import (
    ChargingConnector,
    ChargingEvse,
    ChargingStation,
)


def utc_now() -> datetime:
    """Trả về thời điểm soft-delete với timezone UTC."""
    return datetime.now(timezone.utc)


async def create_station(
    db: AsyncSession,
    *,
    ocpp_identity: str,
    display_name: str,
) -> ChargingStation:
    """Tạo station và flush để phát hiện constraint ngay trong transaction.

    Args:
        db: Async session do entry boundary sở hữu.
        ocpp_identity: OCPP identity duy nhất của station.
        display_name: Tên hiển thị.

    Returns:
        Station vừa được persistence.
    """
    station = ChargingStation(
        ocpp_identity=ocpp_identity,
        display_name=display_name,
    )
    db.add(station)
    await db.flush()
    await db.refresh(station)
    return station


async def get_station_by_id(
    db: AsyncSession, station_id: UUID, *, include_deleted: bool = False
) -> ChargingStation | None:
    """Tìm station theo internal ID.

    Args:
        db: Async session hiện tại.
        station_id: UUID nội bộ.
        include_deleted: Có cho phép resolve record soft-delete hay không.

    Returns:
        Station phù hợp hoặc None.
    """
    conditions: list[ColumnElement[bool]] = [ChargingStation.station_id == station_id]
    if not include_deleted:
        conditions.append(ChargingStation.deleted_at.is_(None))
    result = await db.execute(select(ChargingStation).where(and_(*conditions)))
    return result.scalar_one_or_none()


async def get_station_by_identity(
    db: AsyncSession, ocpp_identity: str, *, include_deleted: bool = True
) -> ChargingStation | None:
    """Tìm station theo OCPP identity, mặc định gồm cả soft-delete để giữ unique."""
    conditions: list[ColumnElement[bool]] = [
        ChargingStation.ocpp_identity == ocpp_identity
    ]
    if not include_deleted:
        conditions.append(ChargingStation.deleted_at.is_(None))
    result = await db.execute(select(ChargingStation).where(and_(*conditions)))
    return result.scalar_one_or_none()


async def list_stations(
    db: AsyncSession,
    *,
    offset: int,
    limit: int,
) -> list[ChargingStation]:
    """Lấy station chưa soft-delete theo thứ tự ổn định."""
    conditions: list[ColumnElement[bool]] = [ChargingStation.deleted_at.is_(None)]
    result = await db.execute(
        select(ChargingStation)
        .where(and_(*conditions))
        .order_by(ChargingStation.created_at.desc(), ChargingStation.station_id.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_stations(
    db: AsyncSession,
) -> int:
    """Đếm station active."""
    conditions: list[ColumnElement[bool]] = [ChargingStation.deleted_at.is_(None)]
    result = await db.execute(
        select(func.count(ChargingStation.station_id)).where(and_(*conditions))
    )
    return int(result.scalar() or 0)


async def update_station(
    db: AsyncSession, station_id: UUID, update_data: Mapping[str, object]
) -> ChargingStation | None:
    """Cập nhật station active bằng các field đã được service lọc.

    Args:
        db: Async session hiện tại.
        station_id: UUID station cần cập nhật.
        update_data: Mapping chỉ chứa field được phép update.

    Returns:
        Station sau cập nhật hoặc None nếu không còn active.
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
    """Soft-delete station và toàn bộ EVSE/connector active thuộc station.

    Cascade chỉ cập nhật ``deleted_at``; không physical-delete history hoặc
    topology record để giữ business identity và foreign key cho audit.

    Args:
        db: Async session hiện tại.
        station_id: UUID station cần xoá mềm.

    Returns:
        True nếu station active tồn tại và đã được đánh dấu; False nếu không có.
    """
    station = await get_station_by_id(db, station_id)
    if station is None:
        return False

    now = utc_now()
    evse_result = await db.execute(
        select(ChargingEvse.evse_id).where(
            ChargingEvse.station_id == station_id,
            ChargingEvse.deleted_at.is_(None),
        )
    )
    evse_ids = list(evse_result.scalars().all())
    if evse_ids:
        await db.execute(
            update(ChargingConnector)
            .where(
                ChargingConnector.evse_id.in_(evse_ids),
                ChargingConnector.deleted_at.is_(None),
            )
            .values(deleted_at=now, updated_at=now)
        )
        await db.execute(
            update(ChargingEvse)
            .where(ChargingEvse.evse_id.in_(evse_ids))
            .values(deleted_at=now, updated_at=now)
        )
    station.deleted_at = now
    station.updated_at = now
    await db.flush()
    return True


async def create_evse(
    db: AsyncSession,
    *,
    station_id: UUID,
    ocpp_evse_id: int,
) -> ChargingEvse:
    """Tạo EVSE và flush constraint/FK trong transaction hiện tại."""
    evse = ChargingEvse(
        station_id=station_id,
        ocpp_evse_id=ocpp_evse_id,
    )
    db.add(evse)
    await db.flush()
    await db.refresh(evse)
    return evse


async def get_evse_by_id(
    db: AsyncSession, evse_id: UUID, *, include_deleted: bool = False
) -> ChargingEvse | None:
    """Tìm EVSE theo internal ID, mặc định chỉ resolve record active."""
    conditions: list[ColumnElement[bool]] = [ChargingEvse.evse_id == evse_id]
    if not include_deleted:
        conditions.append(ChargingEvse.deleted_at.is_(None))
    result = await db.execute(select(ChargingEvse).where(and_(*conditions)))
    return result.scalar_one_or_none()


async def get_evse_by_identity(
    db: AsyncSession,
    station_id: UUID,
    ocpp_evse_id: int,
    *,
    include_deleted: bool = True,
) -> ChargingEvse | None:
    """Tìm EVSE theo identity composite station/OCPP ID."""
    conditions: list[ColumnElement[bool]] = [
        ChargingEvse.station_id == station_id,
        ChargingEvse.ocpp_evse_id == ocpp_evse_id,
    ]
    if not include_deleted:
        conditions.append(ChargingEvse.deleted_at.is_(None))
    result = await db.execute(select(ChargingEvse).where(and_(*conditions)))
    return result.scalar_one_or_none()


async def list_evses(
    db: AsyncSession, *, station_id: UUID, offset: int, limit: int
) -> list[ChargingEvse]:
    """Lấy EVSE active của một station theo thứ tự ổn định."""
    result = await db.execute(
        select(ChargingEvse)
        .where(
            ChargingEvse.station_id == station_id,
            ChargingEvse.deleted_at.is_(None),
        )
        .order_by(ChargingEvse.created_at.asc(), ChargingEvse.evse_id.asc())
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_evses(db: AsyncSession, *, station_id: UUID) -> int:
    """Đếm EVSE active thuộc một station."""
    result = await db.execute(
        select(func.count(ChargingEvse.evse_id)).where(
            ChargingEvse.station_id == station_id,
            ChargingEvse.deleted_at.is_(None),
        )
    )
    return int(result.scalar() or 0)


async def update_evse(
    db: AsyncSession, evse_id: UUID, update_data: Mapping[str, object]
) -> ChargingEvse | None:
    """Cập nhật EVSE active bằng các field đã được service kiểm tra."""
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
    """Soft-delete EVSE và connector active thuộc EVSE đó."""
    evse = await get_evse_by_id(db, evse_id)
    if evse is None:
        return False
    now = utc_now()
    await db.execute(
        update(ChargingConnector)
        .where(
            ChargingConnector.evse_id == evse_id,
            ChargingConnector.deleted_at.is_(None),
        )
        .values(deleted_at=now, updated_at=now)
    )
    evse.deleted_at = now
    evse.updated_at = now
    await db.flush()
    return True


async def create_connector(
    db: AsyncSession,
    *,
    evse_id: UUID,
    ocpp_connector_id: int,
) -> ChargingConnector:
    """Tạo connector và flush constraint/FK trong transaction hiện tại."""
    connector = ChargingConnector(
        evse_id=evse_id,
        ocpp_connector_id=ocpp_connector_id,
    )
    db.add(connector)
    await db.flush()
    await db.refresh(connector)
    return connector


async def get_connector_by_id(
    db: AsyncSession, connector_id: UUID, *, include_deleted: bool = False
) -> ChargingConnector | None:
    """Tìm connector theo internal ID, mặc định chỉ resolve active."""
    conditions: list[ColumnElement[bool]] = [
        ChargingConnector.connector_id == connector_id
    ]
    if not include_deleted:
        conditions.append(ChargingConnector.deleted_at.is_(None))
    result = await db.execute(select(ChargingConnector).where(and_(*conditions)))
    return result.scalar_one_or_none()


async def get_connector_by_identity(
    db: AsyncSession,
    evse_id: UUID,
    ocpp_connector_id: int,
    *,
    include_deleted: bool = True,
) -> ChargingConnector | None:
    """Tìm connector theo identity composite EVSE/OCPP ID."""
    conditions: list[ColumnElement[bool]] = [
        ChargingConnector.evse_id == evse_id,
        ChargingConnector.ocpp_connector_id == ocpp_connector_id,
    ]
    if not include_deleted:
        conditions.append(ChargingConnector.deleted_at.is_(None))
    result = await db.execute(select(ChargingConnector).where(and_(*conditions)))
    return result.scalar_one_or_none()


async def list_connectors(
    db: AsyncSession, *, evse_id: UUID, offset: int, limit: int
) -> list[ChargingConnector]:
    """Lấy connector active của một EVSE theo thứ tự ổn định."""
    result = await db.execute(
        select(ChargingConnector)
        .where(
            ChargingConnector.evse_id == evse_id,
            ChargingConnector.deleted_at.is_(None),
        )
        .order_by(
            ChargingConnector.created_at.asc(), ChargingConnector.connector_id.asc()
        )
        .offset(offset)
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_connectors(db: AsyncSession, *, evse_id: UUID) -> int:
    """Đếm connector active thuộc một EVSE."""
    result = await db.execute(
        select(func.count(ChargingConnector.connector_id)).where(
            ChargingConnector.evse_id == evse_id,
            ChargingConnector.deleted_at.is_(None),
        )
    )
    return int(result.scalar() or 0)


async def update_connector(
    db: AsyncSession, connector_id: UUID, update_data: Mapping[str, object]
) -> ChargingConnector | None:
    """Cập nhật connector active bằng các field đã được service kiểm tra."""
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
    """Đánh dấu connector đã xoá mềm mà không physical-delete record."""
    connector = await get_connector_by_id(db, connector_id)
    if connector is None:
        return False
    connector.deleted_at = utc_now()
    connector.updated_at = utc_now()
    await db.flush()
    return True
